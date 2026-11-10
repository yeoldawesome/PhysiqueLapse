"""Alignment residual report per pose category.

    python backend/scripts/eval_alignment.py                      # every category
    python backend/scripts/eval_alignment.py --pose "Front Double Bicep" --rows

Residuals: distance (canvas px) between a photo's mapped shoulder-mid / hip-mid /
eye-mid and the reference's. The head anchor is exact by construction unless the
alignment is manual. Same numbers as GET /api/poses/{id}/eval.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.models import PoseCategory  # noqa: E402
from app.services.align import evaluate_category  # noqa: E402
from app.settings import load_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pose", help="category name (default: all)")
    ap.add_argument("--rows", action="store_true", help="print per-photo residuals")
    args = ap.parse_args()
    init_db()
    with Session(engine()) as s:
        settings = load_settings(s)
        cats = s.exec(select(PoseCategory).order_by(PoseCategory.sort_order)).all()
        if args.pose:
            cats = [c for c in cats if c.name.lower() == args.pose.lower()]
            if not cats:
                print(f"no category named {args.pose!r}")
                return 1
        for c in cats:
            r = evaluate_category(s, c, settings)
            print(f"\n== {c.name}")
            if r.get("error"):
                print(f"   {r['error']}")
                continue
            print(f"   reference photo {r['reference_photo_id']}, canvas {r['canvas'][0]}x{r['canvas'][1]}, {r['n']} aligned photos")
            if r["n"]:
                print(f"   {'point':<14}{'mean px':>9}{'median':>9}{'p95':>9}{'mean %H':>9}{'p95 %H':>9}")
                for k, v in r["summary"].items():
                    print(f"   {k:<14}{v['mean_px']:>9.1f}{v['median_px']:>9.1f}{v['p95_px']:>9.1f}{v['mean_pct_h']:>9.2f}{v['p95_pct_h']:>9.2f}")
            if r["flags"]:
                print("   flags: " + ", ".join(f"{k}={v}" for k, v in r["flags"].items()))
            if args.rows:
                for row in r["rows"]:
                    print(f"   photo {row['photo_id']:>6} {row['taken_at'] or '':<20} {row['method']:<7} mean {row['mean']:7.1f}  sh {row['shoulder_mid']:7.1f}  hip {row['hip_mid']:7.1f}  eye {row['eye_mid']:7.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
