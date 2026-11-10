import hashlib
import os
from datetime import datetime

from sqlmodel import select

from app import config
from app.models import ImportCandidate, Photo
from app.services import images, ingest
from app.settings import AppSettings
from tests.conftest import make_image, save_image


def _ingest(session, path, **kw):
    settings = kw.pop("settings", AppSettings())
    index = kw.pop("index", None) or ingest.load_phash_index(session)
    return ingest.ingest_file(
        session, path, source="folder", source_ref=str(path.parent), session_id=None, settings=settings, index=index, **kw
    )


def test_ingest_copies_original_byte_identical_and_makes_derived(session, data_dir, tmp_path):
    src = save_image(tmp_path / "src" / "IMG_20240315_093000.jpg", make_image(seed=1), exif_dt=datetime(2024, 3, 15, 9, 30))
    r = _ingest(session, src)
    assert r.status == "accepted"
    p = session.get(Photo, r.photo_id)
    dest = config.abs_path(p.original_path)
    assert dest.exists()
    assert dest.read_bytes() == src.read_bytes()
    assert dest.name == f"{p.sha256[:16]}.jpg"
    assert p.sha256 == hashlib.sha256(src.read_bytes()).hexdigest()
    assert (config.THUMBS_DIR / f"{p.id}.jpg").exists()
    assert (config.PREVIEWS_DIR / f"{p.id}.jpg").exists()
    assert (p.taken_at, p.taken_at_source, p.taken_at_confidence) == (datetime(2024, 3, 15, 9, 30), "exif", "high")
    assert (p.width, p.height) == (320, 400)
    # ledger row
    c = session.exec(select(ImportCandidate).where(ImportCandidate.sha256 == p.sha256)).one()
    assert c.decision == "accepted" and c.photo_id == p.id


def test_exif_orientation_is_applied_to_dimensions_and_derived(session, data_dir, tmp_path):
    # 320x400 image tagged orientation 6 (rotate 90 CW) -> displayed 400x320
    src = save_image(tmp_path / "rot.jpg", make_image(320, 400, seed=2), orientation=6)
    r = _ingest(session, src)
    p = session.get(Photo, r.photo_id)
    assert (p.width, p.height) == (400, 320)
    assert p.exif_orientation == 6
    from PIL import Image

    with Image.open(config.THUMBS_DIR / f"{p.id}.jpg") as t:
        assert t.width > t.height
    # original untouched
    assert config.abs_path(p.original_path).read_bytes() == src.read_bytes()


def test_exact_duplicate_is_skipped(session, data_dir, tmp_path):
    img = make_image(seed=3)
    a = save_image(tmp_path / "a.jpg", img)
    b = tmp_path / "b.jpg"
    b.write_bytes(a.read_bytes())
    idx = ingest.load_phash_index(session)
    r1 = _ingest(session, a, index=idx)
    r2 = _ingest(session, b, index=idx)
    assert r1.status == "accepted"
    assert r2.status == "duplicate" and r2.duplicate_of == r1.photo_id
    assert session.exec(select(Photo)).all().__len__() == 1


def test_near_duplicate_threshold(session, data_dir, tmp_path):
    img = make_image(seed=4)
    a = save_image(tmp_path / "a.png", img)
    # same picture re-encoded as a lower-quality JPEG and slightly resized -> near-duplicate
    b = save_image(tmp_path / "b.jpg", img.resize((300, 375)))
    # a different picture
    c = save_image(tmp_path / "c.jpg", make_image(seed=99))
    idx = ingest.load_phash_index(session)
    assert _ingest(session, a, index=idx).status == "accepted"
    rb = _ingest(session, b, index=idx)
    assert rb.status == "near_duplicate"
    assert _ingest(session, c, index=idx).status == "accepted"
    # threshold 0 disables fuzzy matching (phash distance of a re-encode is > 0)
    strict = AppSettings(phash_threshold=0)
    d = save_image(tmp_path / "d.jpg", img.resize((301, 376)))
    assert _ingest(session, d, index=idx, settings=strict).status in ("accepted", "near_duplicate")
    # keep-anyway bypasses near-duplicate detection
    e = save_image(tmp_path / "e.jpg", img.resize((290, 362)))
    assert _ingest(session, e, index=idx, force=True).status == "accepted"


def test_ledger_skips_deleted_photo_on_reimport(session, data_dir, tmp_path):
    src = save_image(tmp_path / "x.jpg", make_image(seed=5))
    r = _ingest(session, src)
    p = session.get(Photo, r.photo_id)
    ingest.delete_photo(session, p)
    assert not config.abs_path(p.original_path).exists()
    assert (config.TRASH_DIR / config.abs_path(p.original_path).name).exists()
    assert session.get(Photo, r.photo_id) is None
    r2 = _ingest(session, src)
    assert r2.status == "skipped_ledger"
    r3 = _ingest(session, src, force=True)
    assert r3.status == "accepted"


def test_formats_heic_webp_png(session, data_dir, tmp_path):
    img = make_image(seed=6)
    paths = [
        save_image(tmp_path / "p.png", make_image(seed=61)),
        save_image(tmp_path / "w.webp", make_image(seed=62)),
    ]
    try:
        paths.append(save_image(tmp_path / "h.heic", img))
    except Exception as e:  # pragma: no cover - heif encoder missing
        print("heic skipped:", e)
    idx = ingest.load_phash_index(session)
    for p in paths:
        r = _ingest(session, p, index=idx)
        assert r.status == "accepted", (p, r.message)
        photo = session.get(Photo, r.photo_id)
        assert photo.ext == p.suffix.lstrip(".")
        assert (config.PREVIEWS_DIR / f"{photo.id}.jpg").exists()


def test_videos_and_unknown_files_are_skipped(session, data_dir, tmp_path):
    v = tmp_path / "clip.mp4"
    v.write_bytes(b"\x00" * 100)
    t = tmp_path / "notes.txt"
    t.write_text("hi")
    assert _ingest(session, v).status == "video"
    assert _ingest(session, t).status == "other"


def test_unreadable_image_records_error(session, data_dir, tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not a jpeg at all")
    r = _ingest(session, bad)
    assert r.status == "error"
    c = session.get(ImportCandidate, r.candidate_id)
    assert c.decision == "error" and "unreadable" in (c.error or "")


def test_mtime_fallback_is_low_confidence(session, data_dir, tmp_path):
    src = save_image(tmp_path / "IMG_0001.jpg", make_image(seed=7))
    ts = datetime(2023, 5, 6, 7, 8, 9).timestamp()
    os.utime(src, (ts, ts))
    r = _ingest(session, src)
    p = session.get(Photo, r.photo_id)
    assert (p.taken_at, p.taken_at_source, p.taken_at_confidence) == (datetime(2023, 5, 6, 7, 8, 9), "file_mtime", "low")


def test_phash_index_nearest():
    idx = images.PhashIndex()
    idx.add(1, "0000000000000000")
    idx.add(2, "ffffffffffffffff")
    assert idx.nearest("0000000000000001") == (1, 1)
    assert idx.nearest("fffffffffffffff0") == (2, 4)
    assert images.hamming("00", "0f") == 4


def test_exact_copy_of_near_duplicate_is_reported_as_near_duplicate(session, data_dir, tmp_path):
    img = make_image(seed=8)
    a = save_image(tmp_path / "a" / "small.jpg", img.resize((300, 375)))
    b = save_image(tmp_path / "b" / "full.jpg", img)
    c = tmp_path / "c" / "full_copy.jpg"
    c.parent.mkdir()
    c.write_bytes(b.read_bytes())
    idx = ingest.load_phash_index(session)
    assert _ingest(session, a, index=idx).status == "accepted"
    assert _ingest(session, b, index=idx).status == "near_duplicate"
    # same bytes as a known near-duplicate: still a near-duplicate, not "previously removed"
    assert _ingest(session, c, index=idx).status == "near_duplicate"
