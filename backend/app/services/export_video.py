"""Aligned frames -> MP4 (bundled ffmpeg via imageio-ffmpeg) or GIF (Pillow), with captions."""

from __future__ import annotations

import logging
import os
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from sqlmodel import Session, select

from .. import config
from ..jobs import JobContext, job
from ..models import Alignment, Export, Photo, PoseCategory
from ..settings import load_settings

log = logging.getLogger(__name__)

MP4_FPS = 30
GIF_FPS = 15
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def load_font(size: int) -> ImageFont.ImageFont:
    for f in _FONT_CANDIDATES:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def caption_lines(p: Photo, first: datetime, opts: dict[str, Any], stats: list[dict] | None) -> list[str]:
    lines = []
    parts = []
    if opts.get("date", True) and p.taken_at:
        parts.append(p.taken_at.strftime("%b %d, %Y").replace(" 0", " "))
    if opts.get("days", True) and p.taken_at:
        parts.append(f"Day {(p.taken_at.date() - first.date()).days}")
    if parts:
        lines.append("  ·  ".join(parts))
    if stats:
        vals = [f"{st['name']} {st['value']:.{st['decimals']}f}{(' ' + st['unit']) if st['unit'] else ''}" for st in stats if st.get("value") is not None]
        if vals:
            lines.append("  ·  ".join(vals))
    return lines


def draw_caption(img: Image.Image, lines: list[str]) -> Image.Image:
    if not lines:
        return img
    W, H = img.size
    size = max(14, round(H * 0.032))
    font = load_font(size)
    pad = round(size * 0.6)
    line_h = round(size * 1.3)
    box_h = pad * 2 + line_h * len(lines)
    overlay = Image.new("RGBA", (W, box_h), (0, 0, 0, 0))
    grad = np.linspace(0, 170, box_h, dtype=np.uint8)
    overlay.putalpha(Image.fromarray(np.repeat(grad[:, None], W, axis=1)))
    out = img.convert("RGBA")
    out.alpha_composite(overlay, (0, H - box_h))
    d = ImageDraw.Draw(out)
    y = H - box_h + pad
    for ln in lines:
        d.text((pad + 2, y + 2), ln, font=font, fill=(0, 0, 0, 160))
        d.text((pad, y), ln, font=font, fill=(255, 255, 255, 255))
        y += line_h
    return out.convert("RGB")


def frame_schedule(n: int, fps: float, crossfade: float, out_fps: int) -> list[tuple[int, int, float]]:
    """Output frame list of (index_a, index_b, alpha): alpha 0 -> pure a.

    Photo i starts at output frame round(i * out_fps / fps), so the total length is
    exact for any fps. When crossfade > 0 the last min(crossfade, 0.9/fps) seconds of
    each hold blend into the next photo. The final photo lingers for one extra hold.
    """
    if n <= 0:
        return []
    starts = [round(i * out_fps / fps) for i in range(n + 2)]
    want = 0 if crossfade <= 0 else max(1, round(min(crossfade, 0.9 / fps) * out_fps))
    out: list[tuple[int, int, float]] = []
    for i in range(n):
        length = max(1, starts[i + 1] - starts[i])
        last = i == n - 1
        fade = 0 if last else min(length - 1, want)
        out.extend((i, i, 0.0) for _ in range(length - fade))
        out.extend((i, i + 1, (k + 1) / (fade + 1)) for k in range(fade))
    out.extend((n - 1, n - 1, 0.0) for _ in range(max(1, starts[n + 1] - starts[n])))
    return out


def collect_frames(
    s: Session, pose_id: int, date_from: date | None, date_to: date | None, max_pose_diff_pct: float | None = None
) -> tuple[list[Photo], int]:
    from ..api.viewer import sequence_photos

    photos = sequence_photos(s, pose_id, date_from, date_to)
    torso_canvas = None
    if max_pose_diff_pct is not None:
        from ..models import PoseCategory
        from ..settings import load_settings as _ls
        from .align import category_context

        try:
            torso_canvas = category_context(s, s.get(PoseCategory, pose_id), _ls(s), auto_pick=False).target.lengths["torso"]
        except LookupError:
            torso_canvas = None
    usable, skipped = [], 0
    for p in photos:
        a = s.get(Alignment, p.id)
        if a is None or not a.output_path or a.pose_category_id != pose_id or not config.abs_path(a.output_path).exists():
            skipped += 1
            continue
        if torso_canvas and a.joint_residual_px is not None and 100 * a.joint_residual_px / torso_canvas > max_pose_diff_pct:
            skipped += 1
            continue
        usable.append(p)
    return usable, skipped


def _stats_for(s: Session, p: Photo, metric_ids: list[int], window: int) -> list[dict] | None:
    if not metric_ids or not p.taken_at:
        return None
    try:
        from . import stats as ST
    except ImportError:
        return None
    rows = ST.stats_at(s, p.taken_at.date(), window, metric_ids=metric_ids)
    return [r for r in rows if r.get("value") is not None]


@job("export_video", lane="cv", title="Export time-lapse")
def job_export(ctx: JobContext, params: dict) -> dict:
    export_id = int(params["export_id"])
    with ctx.session() as s:
        ex = s.get(Export, export_id)
        if ex is None:
            raise ValueError("export not found")
        prm = dict(ex.params or {})
        pose_id = int(prm["pose_id"])
        cat = s.get(PoseCategory, pose_id)
        cat_name = cat.name if cat else "pose"
        settings = load_settings(s)
        dfrom = date.fromisoformat(prm["from"]) if prm.get("from") else None
        dto = date.fromisoformat(prm["to"]) if prm.get("to") else None
        mpd = prm.get("max_pose_diff_pct")
        photos, skipped = collect_frames(s, pose_id, dfrom, dto, float(mpd) if mpd is not None else None)
        if not photos:
            raise ValueError("no aligned frames in this range; run alignment first")
        fmt = prm.get("format", "mp4")
        fps = float(prm.get("fps", 2.0))
        crossfade = float(prm.get("crossfade", 0.0))
        cap = prm.get("caption") or {}
        width = int(prm.get("width") or settings.canvas_w)
        W = max(64, width - width % 2)
        H = round(settings.canvas_h * W / settings.canvas_w)
        H -= H % 2
        first = photos[0].taken_at
        metric_ids = [int(m) for m in cap.get("metrics", [])]
        ctx.progress(0, len(photos), "Rendering frames")
        frames: list[np.ndarray] = []
        for i, p in enumerate(photos, 1):
            ctx.check_cancelled()
            a = s.get(Alignment, p.id)
            with Image.open(config.abs_path(a.output_path)) as im:
                img = im.convert("RGB").resize((W, H), Image.Resampling.LANCZOS)
            lines = caption_lines(p, first, cap, _stats_for(s, p, metric_ids, settings.stats_window_days))
            frames.append(np.asarray(draw_caption(img, lines)))
            ctx.progress(i, len(photos))
        ex.status = "running"
        s.add(ex)
        s.commit()

    out_fps = MP4_FPS if fmt == "mp4" else GIF_FPS
    sched = frame_schedule(len(frames), fps, crossfade, out_fps)
    name = f"{cat_name.replace(' ', '_')}_{datetime.now():%Y%m%d_%H%M%S}.{fmt}"
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "._-")
    path = config.EXPORTS_DIR / safe
    path.parent.mkdir(parents=True, exist_ok=True)
    ctx.progress(0, len(sched), f"Encoding {fmt.upper()}")

    def blended(a: int, b: int, t: float) -> np.ndarray:
        if a == b or t <= 0:
            return frames[a]
        return (frames[a].astype(np.float32) * (1 - t) + frames[b].astype(np.float32) * t).astype(np.uint8)

    tmp = path.with_suffix(path.suffix + ".part")
    try:
        if fmt == "mp4":
            import imageio_ffmpeg

            os.environ.setdefault("IMAGEIO_FFMPEG_EXE", ffmpeg_exe())
            w = imageio_ffmpeg.write_frames(
                str(tmp), (W, H), fps=out_fps, codec="libx264", pix_fmt_out="yuv420p", quality=8, macro_block_size=2,
                output_params=["-movflags", "+faststart", "-f", "mp4"],
            )
            w.send(None)
            for i, (a, b, t) in enumerate(sched, 1):
                ctx.check_cancelled()
                w.send(np.ascontiguousarray(blended(a, b, t)).tobytes())
                if i % 30 == 0:
                    ctx.progress(i, len(sched))
            w.close()
        else:
            # collapse runs of identical frames into durations to keep GIFs small
            imgs, durs = [], []
            prev = None
            for a, b, t in sched:
                key = (a, b, round(t, 3))
                if key == prev and b == a:
                    durs[-1] += 1000 / out_fps
                    continue
                prev = key
                imgs.append(Image.fromarray(blended(a, b, t)).quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
                durs.append(1000 / out_fps)
            imgs[0].save(tmp, format="GIF", save_all=True, append_images=imgs[1:], duration=[round(d) for d in durs], loop=0, optimize=False, disposal=1)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        with ctx.session() as s:
            ex = s.get(Export, export_id)
            ex.status = "failed"
            s.add(ex)
            s.commit()
        raise
    with ctx.session() as s:
        ex = s.get(Export, export_id)
        ex.path = config.rel(path)
        ex.frames = len(frames)
        ex.size_bytes = path.stat().st_size
        ex.status = "done"
        s.add(ex)
        s.commit()
    msg = f"{fmt.upper()} with {len(frames)} photos ({path.stat().st_size / 1e6:.1f} MB)"
    if skipped:
        msg += f"; {skipped} photos left out (not aligned or outside the pose-match limit)"
    return {"message": msg, "export_id": export_id, "frames": len(frames), "skipped": skipped, "path": str(path)}


def export_out(ex: Export) -> dict[str, Any]:
    return {
        "id": ex.id,
        "pose_category_id": ex.pose_category_id,
        "format": ex.format,
        "path": ex.path,
        "params": ex.params or {},
        "frames": ex.frames,
        "size_bytes": ex.size_bytes,
        "status": ex.status,
        "job_id": ex.job_id,
        "created_at": ex.created_at.isoformat() if ex.created_at else None,
        "download_url": f"/api/exports/{ex.id}/download",
    }


def list_exports(s: Session) -> list[dict[str, Any]]:
    return [export_out(e) for e in s.exec(select(Export).order_by(Export.id.desc())).all()]


def delete_export_file(ex: Export) -> None:
    if ex.path:
        Path(config.abs_path(ex.path)).unlink(missing_ok=True)
