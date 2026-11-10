"""Image I/O: open any supported format, apply EXIF orientation, resize, hash."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

try:  # HEIC/HEIF support (iPhone photos)
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except Exception:  # pragma: no cover
    HEIF_AVAILABLE = False

Image.MAX_IMAGE_PIXELS = 200_000_000  # 200 MP; protects against decompression bombs but allows big panoramas

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".avif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp", ".mts"}

EXIF_ORIENTATION = 0x0112
EXIF_IFD = 0x8769
EXIF_DATETIME_ORIGINAL = 0x9003
EXIF_DATETIME_DIGITIZED = 0x9004
EXIF_DATETIME = 0x0132


def is_image(path: Path | str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def is_video(path: Path | str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def sha256_file(path: Path | str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def read_exif(img: Image.Image) -> dict[str, str | int]:
    """Return the few EXIF fields we care about, from the raw (untransposed) image."""
    out: dict[str, str | int] = {}
    try:
        exif = img.getexif()
    except Exception:
        return out
    if not exif:
        return out
    o = exif.get(EXIF_ORIENTATION)
    if isinstance(o, int):
        out["Orientation"] = o
    dt = exif.get(EXIF_DATETIME)
    if isinstance(dt, str):
        out["DateTime"] = dt
    try:
        ifd = exif.get_ifd(EXIF_IFD)
    except Exception:
        ifd = {}
    for tag, name in ((EXIF_DATETIME_ORIGINAL, "DateTimeOriginal"), (EXIF_DATETIME_DIGITIZED, "DateTimeDigitized")):
        v = ifd.get(tag) if ifd else None
        if isinstance(v, bytes):
            v = v.decode("ascii", "ignore")
        if isinstance(v, str):
            out[name] = v
    return out


def open_image(path: Path | str) -> tuple[Image.Image, dict[str, str | int]]:
    """Open an image, apply EXIF orientation, return (RGB image, exif dict).

    The returned image's width/height are the *displayed* dimensions.
    """
    img = Image.open(path)
    exif = read_exif(img)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        if has_alpha:
            bg = Image.new("RGB", img.size, (24, 24, 27))
            rgba = img.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
    return img, exif


def resize_long_side(img: Image.Image, long_side: int) -> Image.Image:
    w, h = img.size
    m = max(w, h)
    if m <= long_side:
        return img
    scale = long_side / m
    return img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)


def save_jpeg(img: Image.Image, path: Path, quality: int = 88) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    img.save(tmp, "JPEG", quality=quality, optimize=True, progressive=False)
    tmp.replace(path)


def jpeg_bytes(img: Image.Image, quality: int = 88) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


# ----------------------------------------------------------------------------- perceptual hash


def phash_hex(img: Image.Image) -> str:
    import imagehash

    return str(imagehash.phash(img))  # 16 hex chars = 64 bits


def hamming(a_hex: str, b_hex: str) -> int:
    return bin(int(a_hex, 16) ^ int(b_hex, 16)).count("1")


_POPCOUNT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


class PhashIndex:
    """In-memory near-duplicate index over 64-bit perceptual hashes."""

    def __init__(self) -> None:
        self._ids: list[int] = []
        self._rows: list[np.ndarray] = []
        self._mat: np.ndarray | None = None

    @staticmethod
    def _row(h: str) -> np.ndarray:
        return np.frombuffer(int(h, 16).to_bytes(8, "big"), dtype=np.uint8)

    def add(self, photo_id: int, h: str | None) -> None:
        if not h:
            return
        self._ids.append(photo_id)
        self._rows.append(self._row(h))
        self._mat = None

    def nearest(self, h: str) -> tuple[int | None, int]:
        """(photo_id, distance) of the closest stored hash, or (None, 64)."""
        if not self._rows:
            return None, 64
        if self._mat is None:
            self._mat = np.stack(self._rows)
        q = self._row(h)
        d = _POPCOUNT[np.bitwise_xor(self._mat, q)].sum(axis=1)
        i = int(np.argmin(d))
        return self._ids[i], int(d[i])

    def __len__(self) -> int:
        return len(self._ids)
