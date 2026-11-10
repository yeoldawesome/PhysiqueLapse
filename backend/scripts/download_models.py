"""Download the model files once, into data/models/. Idempotent.

    python backend/scripts/download_models.py            # pose (heavy) + face + CLIP
    python backend/scripts/download_models.py --pose all # lite, full and heavy
    python backend/scripts/download_models.py --no-clip

These are the only network calls the app ever needs besides pip/npm. The desktop app does
the same on first launch (job "download_models"); the list lives in app/services/model_files.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.services import model_files as MF  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pose", default="heavy", help="lite|full|heavy|all")
    ap.add_argument("--no-clip", action="store_true")
    args = ap.parse_args()
    config.ensure_dirs()
    print(f"Model folder: {config.MODELS_DIR}")
    variants = ["lite", "full", "heavy"] if args.pose == "all" else [args.pose]
    files = {m.key: m for v in variants for m in MF.required(v)}
    if args.no_clip:
        files.pop("clip", None)
    ok = True
    for m in files.values():
        if MF.present(m):
            print(f"  ok      {m.label}  (already present)")
            continue
        print(f"  fetch   {m.label}  {m.url}")
        try:
            MF.download(m.url, m.path, lambda got, total: print(f"\r          {got / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", flush=True))
            print(f"\n  saved   {m.path}")
        except Exception as e:
            ok = False
            print(f"\n  FAILED  {m.label}: {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
