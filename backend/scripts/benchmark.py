"""Time each processing stage per photo on a sample of the library (read-only).

    python -m scripts.benchmark --n 50 --json bench.json          (from backend/)

Stages (per photo unless noted): decode (open + EXIF rotate), thumbs (thumbnail + preview
JPEGs), landmarks (pose model), classify (features + rules), face (YuNet detect + SFace
fingerprint), clip (CLIP image scoring, batched), align (similarity warp onto the canvas),
export (MP4 encoding of the aligned frames, per frame). Model load times are reported apart.
Nothing is written to the library: results stay in memory, files go to a temp folder.
Prints a table and an estimate for a 1,000-photo import (decode + thumbs + landmarks +
classify + align, which is what the import + auto-processing chain does; face/CLIP are
separate jobs).
"""

from __future__ import annotations

import argparse
import io
import json
import platform
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app import config  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import Photo  # noqa: E402
from app.settings import load_settings  # noqa: E402

IMPORT_STAGES = ("decode", "thumbs", "landmarks", "classify", "align")


def _devices() -> dict[str, str]:
    out = {"cpu_threads": str(config.cpu_threads()), "python": platform.python_version()}
    try:
        import torch

        out["torch"] = torch.__version__
        out["torch_cuda"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "no"
    except Exception as e:  # pragma: no cover
        out["torch"] = f"unavailable ({e})"
    try:
        import cv2

        out["opencv"] = cv2.__version__
        out["opencv_cuda_devices"] = str(cv2.cuda.getCudaEnabledDeviceCount()) if hasattr(cv2, "cuda") else "0"
    except Exception:
        pass
    try:
        import onnxruntime as ort

        out["onnxruntime"] = f"{ort.__version__} {ort.get_available_providers()}"
    except Exception:
        out["onnxruntime"] = "not installed"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=50, help="photos to sample (default 50)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--stages", default="decode,thumbs,landmarks,classify,face,clip,align,export")
    ap.add_argument("--clip-batch", type=int, default=16)
    ap.add_argument("--json", help="write the results here")
    a = ap.parse_args(argv)
    stages = [x.strip() for x in a.stages.split(",") if x.strip()]

    init_db()
    with Session(engine()) as s:
        settings = load_settings(s)
        rows = list(s.exec(select(Photo).where(Photo.excluded == False)).all())  # noqa: E712
    random.Random(a.seed).shuffle(rows)
    photos = [p for p in rows if config.abs_path(p.original_path).exists()][: a.n]
    if not photos:
        print("no photos in the library", file=sys.stderr)
        return 1
    print(f"{len(photos)} photos from {config.DATA_DIR}, stages: {', '.join(stages)}")

    from app.services import images

    times: dict[str, list[float]] = {k: [] for k in stages}
    loads: dict[str, float] = {}

    def timed(stage: str, fn):
        t = time.perf_counter()
        r = fn()
        times[stage].append(time.perf_counter() - t)
        return r

    # ---- model loads (first use), reported apart
    if "landmarks" in stages:
        from app.services import landmarks as _L
        from app.services.pose_backend import get_backend

        t = time.perf_counter()
        get_backend(settings.pose_model)
        _L.detect_image(np.zeros((512, 384, 3), np.uint8), variant=settings.pose_model, tta=False)  # builds the graph
        loads["pose model"] = time.perf_counter() - t
    if "face" in stages:
        from app.services import people

        t = time.perf_counter()
        det, rec = people._get_models()
        loads["face models"] = time.perf_counter() - t
    if "clip" in stages:
        from app.services.clip_backend import get_scorer

        t = time.perf_counter()
        scorer = get_scorer()
        from PIL import Image as _I

        scorer.positive_probability([_I.new("RGB", (224, 224))], settings.clip_positive_prompts, settings.clip_negative_prompts)
        loads["CLIP"] = time.perf_counter() - t

    from app.services import align as A
    from app.services import classify as CL
    from app.services import features as F
    from app.services import landmarks as L

    clip_batch: list = []
    aligned_frames: list[np.ndarray] = []
    W, H = settings.canvas_w, settings.canvas_h
    for i, p in enumerate(photos, 1):
        src = config.abs_path(p.original_path)
        img, _ = timed("decode", lambda src=src: images.open_image(src)) if "decode" in stages else images.open_image(src)
        if "thumbs" in stages:
            def thumbs(img=img):
                for side, q in ((config.THUMB_LONG_SIDE, 85), (config.PREVIEW_LONG_SIDE, 90)):
                    b = io.BytesIO()
                    images.resize_long_side(img, side).save(b, "JPEG", quality=q)
            timed("thumbs", thumbs)
        data = None
        if "landmarks" in stages:
            def lm(img=img, p=p):
                small = images.resize_long_side(img, settings.landmark_input_long_side)
                pose, n, _mask = L.detect_image(np.asarray(small), variant=settings.pose_model, tta=settings.flip_tta)
                sx = img.width / small.width
                pts = [{**q.as_dict(), "x_px": q.x_px * sx, "y_px": q.y_px * sx} for q in (pose or [])]
                return {"image_w": img.width, "image_h": img.height, "detected": bool(pose), "points": pts}
            data = timed("landmarks", lm)
        else:
            data = L.load_raw(p.id)
        if "classify" in stages:
            timed("classify", lambda data=data: CL.rule_scores(F.compute(data)) if data and data.get("detected") and F.compute(data) else None)
        if "face" in stages:
            import cv2

            def face(img=img):
                small = images.resize_long_side(img, 640).convert("RGB")
                bgr = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2BGR)
                det.setInputSize((bgr.shape[1], bgr.shape[0]))
                _, faces = det.detect(bgr)
                if faces is not None and len(faces):
                    f = max(faces, key=lambda f: f[2] * f[3])
                    rec.feature(rec.alignCrop(bgr, f))
            timed("face", face)
        if "clip" in stages:
            clip_batch.append(images.resize_long_side(img, 448).copy())
            if len(clip_batch) >= a.clip_batch or i == len(photos):
                n = len(clip_batch)
                t = time.perf_counter()
                scorer.positive_probability(clip_batch, settings.clip_positive_prompts, settings.clip_negative_prompts)
                per = (time.perf_counter() - t) / n
                times["clip"].extend([per] * n)
                clip_batch = []
        if "align" in stages:
            def al(p=p, data=data):
                tr = A.fit_transform(p.width, p.height, W, H)
                if data and data.get("detected"):
                    # a realistic solve onto itself plus the full-resolution warp
                    tgt = A.make_target(L.points_dict(data), p.width, p.height, _SPEC, W, H)
                    tr, _flags, _ = A.solve(L.points_dict(data), tgt, _SPEC, settings.rotation_clamp_deg)
                return A.render(p, tr, W, H)
            frame = timed("align", al)
            if "export" in stages and len(aligned_frames) < 60:
                aligned_frames.append(frame)
        img.close()
        if i % 10 == 0:
            print(f"  {i}/{len(photos)}")

    if "export" in stages and aligned_frames:
        import os

        import imageio_ffmpeg

        from app.services.export_video import ffmpeg_exe

        os.environ.setdefault("IMAGEIO_FFMPEG_EXE", ffmpeg_exe())
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "bench.mp4"
            t = time.perf_counter()
            w = imageio_ffmpeg.write_frames(str(out), (W, H), fps=30, codec="libx264", pix_fmt_out="yuv420p", quality=8, macro_block_size=2)
            w.send(None)
            reps = max(1, 300 // len(aligned_frames))  # ~300 frames: a 10 s video
            for _ in range(reps):
                for f in aligned_frames:
                    w.send(np.ascontiguousarray(f))
            w.close()
            n = reps * len(aligned_frames)
            times["export"] = [(time.perf_counter() - t) / n] * n

    # ---- report
    rows_out = []
    for k in stages:
        v = times.get(k) or []
        if not v:
            continue
        rows_out.append({"stage": k, "n": len(v), "mean_ms": 1000 * statistics.fmean(v), "median_ms": 1000 * statistics.median(v), "p95_ms": 1000 * sorted(v)[int(0.95 * (len(v) - 1))]})
    per_import = sum(r["mean_ms"] for r in rows_out if r["stage"] in IMPORT_STAGES)
    result = {
        "photos": len(photos),
        "devices": _devices(),
        "model_load_s": {k: round(v, 2) for k, v in loads.items()},
        "stages": rows_out,
        "import_ms_per_photo": per_import,
        "import_1000_photos_min": per_import * 1000 / 60000,
        "settings": {"pose_model": settings.pose_model, "landmark_input_long_side": settings.landmark_input_long_side, "canvas": [W, H]},
    }
    print()
    print(f"{'stage':<10} {'n':>4} {'mean ms':>9} {'median':>8} {'p95':>8}")
    for r in rows_out:
        print(f"{r['stage']:<10} {r['n']:>4} {r['mean_ms']:>9.1f} {r['median_ms']:>8.1f} {r['p95_ms']:>8.1f}")
    print(f"\nmodel loads: {result['model_load_s']}")
    print(f"import + processing: {per_import:.0f} ms/photo -> 1,000 photos ~ {result['import_1000_photos_min']:.1f} min (one worker)")
    print(f"devices: {result['devices']}")
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=2))
        print(f"written to {a.json}")
    return 0


class _Spec:
    """Alignment settings for the benchmark (the defaults of a pose)."""

    anchor_mode = "auto"
    scale_basis = "torso"
    rotation_basis = "torso"
    align_method = "joints"
    normalize_facing = False
    framing_zoom = 1.0
    framing_dx = 0.0
    framing_dy = 0.0


_SPEC = _Spec()

if __name__ == "__main__":
    raise SystemExit(main())
