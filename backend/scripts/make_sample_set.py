"""Generate a synthetic sample folder for manual checks of the import pipeline.

    python backend/scripts/make_sample_set.py OUT_DIR

Writes 24 images: EXIF-dated JPEGs (one rotated by EXIF orientation), files dated
only by filename in every supported pattern, mtime-only files, PNG / WEBP / HEIC,
one exact duplicate, one near-duplicate, plus a fake video and a text file.
No people are in these pictures; they exercise ingest, dates and dedupe only.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conftest import make_image, save_image  # noqa: E402


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    start = datetime(2024, 1, 1, 8, 0, 0)
    n = 0
    for i in range(8):  # EXIF dated
        save_image(out / "exif" / f"IMG_{1000 + i}.jpg", make_image(480, 600, seed=i), exif_dt=start + timedelta(days=7 * i))
        n += 1
    save_image(out / "exif" / "IMG_rotated.jpg", make_image(600, 480, seed=50), exif_dt=start + timedelta(days=60), orientation=6)
    n += 1
    names = [
        "2024-03-15 09.30.00.jpg",
        "20240322_093000.jpg",
        "IMG_20240329_093000.jpg",
        "PXL_20240405_093000123.jpg",
        "Screenshot_20240412-093000.png",
        "2024-04-19.webp",
    ]
    for i, name in enumerate(names):
        save_image(out / "filenames" / name, make_image(480, 600, seed=100 + i))
        n += 1
    for i in range(4):  # only the file time knows the date
        p = save_image(out / "mtime" / f"photo_{i}.png", make_image(480, 600, seed=200 + i))
        ts = (start + timedelta(days=200 + i)).timestamp()
        os.utime(p, (ts, ts))
        n += 1
    try:
        save_image(out / "heic" / "20240501_120000.heic", make_image(480, 600, seed=300))
        save_image(out / "heic" / "IMG_9000.heic", make_image(480, 600, seed=301), exif_dt=datetime(2024, 5, 8, 12))
        n += 2
    except Exception as e:  # pragma: no cover
        print("HEIC encoder unavailable:", e)
    # exact duplicate and near-duplicate of IMG_1000
    (out / "dupes").mkdir(exist_ok=True)
    (out / "dupes" / "copy_of_IMG_1000.jpg").write_bytes((out / "exif" / "IMG_1000.jpg").read_bytes())
    save_image(out / "dupes" / "IMG_1000_resized.jpg", make_image(480, 600, seed=0).resize((360, 450)))
    n += 2
    (out / "dupes" / "clip.mp4").write_bytes(b"\x00" * 64)
    (out / "dupes" / "notes.txt").write_text("not an image")
    print(f"wrote {n} images (+1 video, +1 text file) to {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    main(Path(sys.argv[1]))
