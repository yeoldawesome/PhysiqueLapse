"""Download the model files once, into data/models/. Idempotent.

    python backend/scripts/download_models.py            # pose (heavy) + CLIP
    python backend/scripts/download_models.py --pose all # lite, full and heavy
    python backend/scripts/download_models.py --no-clip

These are the only network calls the app ever needs besides pip/npm.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402


def download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  ok      {dest}  ({dest.stat().st_size / 1e6:.1f} MB, already present)")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  fetch   {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "physiquelapse-setup"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            got += len(b)
            if total:
                print(f"\r          {got / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", flush=True)
    print()
    tmp.replace(dest)
    print(f"  saved   {dest}")


def fetch_clip() -> None:
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ["HF_HOME"] = str(config.CLIP_CACHE_DIR / "hf")
    from app.services.clip_backend import local_weights

    if local_weights() is not None:
        print(f"  ok      {local_weights()}  (already present)")
        return
    import open_clip

    print(f"  CLIP    {config.CLIP_MODEL_NAME} / {config.CLIP_PRETRAINED} -> {config.CLIP_CACHE_DIR}")
    open_clip.create_model_and_transforms(
        config.CLIP_MODEL_NAME, pretrained=config.CLIP_PRETRAINED, cache_dir=str(config.CLIP_CACHE_DIR)
    )
    from app.services.clip_backend import local_weights

    w = local_weights()
    print(f"  ok      CLIP weights: {w}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pose", default="heavy", help="lite|full|heavy|all")
    ap.add_argument("--no-clip", action="store_true")
    args = ap.parse_args()
    config.ensure_dirs()
    print(f"Model folder: {config.MODELS_DIR}")
    variants = ["lite", "full", "heavy"] if args.pose == "all" else [args.pose]
    ok = True
    for v in variants:
        try:
            download(config.POSE_MODEL_URLS[v], config.pose_model_path(v))
        except Exception as e:
            ok = False
            print(f"  FAILED  pose {v}: {e}")
    if not args.no_clip:
        try:
            fetch_clip()
        except Exception as e:
            ok = False
            print(f"  FAILED  CLIP: {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
