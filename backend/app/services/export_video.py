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
from ..fsutil import replace_retry
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


def draw_caption(img: Image.Image, lines: list[str], position: str = "bottom", scale: float = 1.0) -> Image.Image:
    if not lines:
        return img
    W, H = img.size
    size = max(10, round(min(H, W * 1.6) * 0.032 * scale))
    font = load_font(size)
    pad = round(size * 0.6)
    line_h = round(size * 1.3)
    box_h = min(H, pad * 2 + line_h * len(lines))
    overlay = Image.new("RGBA", (W, box_h), (0, 0, 0, 0))
    grad = np.linspace(0, 170, box_h, dtype=np.uint8)
    if position == "top":
        grad = grad[::-1]
    overlay.putalpha(Image.fromarray(np.repeat(grad[:, None], W, axis=1)))
    out = img.convert("RGBA")
    top = 0 if position == "top" else H - box_h
    out.alpha_composite(overlay, (0, top))
    d = ImageDraw.Draw(out)
    y = top + pad
    for ln in lines:
        d.text((pad + 2, y + 2), ln, font=font, fill=(0, 0, 0, 160))
        d.text((pad, y), ln, font=font, fill=(255, 255, 255, 255))
        y += line_h
    return out.convert("RGB")


def draw_progress(img: Image.Image, frac: float) -> Image.Image:
    """Thin bar along the bottom edge, filled to `frac`."""
    W, H = img.size
    h = max(3, round(H * 0.006))
    out = img.copy()
    d = ImageDraw.Draw(out)
    d.rectangle((0, H - h, W, H), fill=(40, 40, 40))
    d.rectangle((0, H - h, round(W * max(0.0, min(1.0, frac))), H), fill=(255, 255, 255))
    return out


ASPECTS = {"1:1": 1.0, "4:5": 4 / 5, "9:16": 9 / 16, "16:9": 16 / 9}


def output_size(width: int, canvas_w: int, canvas_h: int, aspect: str) -> tuple[int, int]:
    """Even (W, H) for the chosen width and shape."""
    W = max(64, width - width % 2)
    ratio = ASPECTS.get(aspect, canvas_w / canvas_h)
    H = round(W / ratio)
    return W, H - H % 2


def compose(img: Image.Image, W: int, H: int, fit: str = "fill", background: str = "#000000") -> Image.Image:
    """Put a frame into a W x H output: "fill" scales to cover and crops the centre, "fit"
    scales to fit inside and adds bars in the background colour."""
    iw, ih = img.size
    if (iw, ih) == (W, H):
        return img
    if abs(iw / ih - W / H) < 1e-3:
        return img.resize((W, H), Image.Resampling.LANCZOS)
    if fit == "fit":
        sc = min(W / iw, H / ih)
        im = img.resize((max(1, round(iw * sc)), max(1, round(ih * sc))), Image.Resampling.LANCZOS)
        bg = tuple(int(background[i : i + 2], 16) for i in (1, 3, 5))
        out = Image.new("RGB", (W, H), bg)
        out.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
        return out
    sc = max(W / iw, H / ih)
    im = img.resize((max(W, round(iw * sc)), max(H, round(ih * sc))), Image.Resampling.LANCZOS)
    x, y = (im.width - W) // 2, (im.height - H) // 2
    return im.crop((x, y, x + W, y + H))


def pick_photos(photos: list[Photo], every: str = "all", step: int = 1, reverse: bool = False) -> list[Photo]:
    """At most one photo per day / week / month (the first of each), then every Nth, optionally newest first."""
    if every != "all":
        seen, kept = set(), []
        for p in photos:
            d = p.taken_at.date() if p.taken_at else None
            key = None if d is None else d if every == "day" else d.isocalendar()[:2] if every == "week" else (d.year, d.month)
            if key is not None and key in seen:
                continue
            seen.add(key)
            kept.append(p)
        photos = kept
    photos = photos[:: max(1, step)]
    return photos[::-1] if reverse else photos


def play_order(n: int, boomerang: bool = False) -> list[int]:
    """Indices in playing order: 0..n-1, and with boomerang back down to 0 (ends not repeated)."""
    order = list(range(n))
    if boomerang and n > 2:
        order += list(range(n - 2, -1, -1))
    elif boomerang and n == 2:
        order += [0]
    return order


def frame_schedule(
    n: int, fps: float, crossfade: float, out_fps: int, hold_start: float = 0.0, hold_end: float | None = None
) -> list[tuple[int, int, float]]:
    """Output frame list of (index_a, index_b, alpha): alpha 0 -> pure a.

    Photo i starts at output frame round(i * out_fps / fps), so the total length is
    exact for any fps. When crossfade > 0 the last min(crossfade, 0.9/fps) seconds of
    each hold blend into the next photo. The first photo stays `hold_start` seconds
    longer; the last one is shown for `hold_end` seconds in all (default: two photos' time).
    Above out_fps photos per second some photos get no frame of their own (the video
    cannot show more photos per second than it has frames).
    """
    if n <= 0:
        return []
    starts = [round(i * out_fps / fps) for i in range(n + 1)]
    want = 0 if crossfade <= 0 else max(1, round(min(crossfade, 0.9 / fps) * out_fps))
    out: list[tuple[int, int, float]] = []
    out.extend((0, 0, 0.0) for _ in range(round(hold_start * out_fps)))
    for i in range(n - 1):
        length = starts[i + 1] - starts[i]
        if length <= 0:
            continue  # faster than the frame rate: this photo is skipped
        fade = min(length - 1, want)
        out.extend((i, i, 0.0) for _ in range(length - fade))
        out.extend((i, i + 1, (k + 1) / (fade + 1)) for k in range(fade))
    end = round(2 * out_fps / fps) if hold_end is None else round(hold_end * out_fps)
    out.extend((n - 1, n - 1, 0.0) for _ in range(max(1, end)))
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
        group = prm.get("group")
        pose_id = int(prm["pose_id"]) if prm.get("pose_id") is not None else -1
        cat = s.get(PoseCategory, pose_id) if pose_id > 0 else None
        series = None
        if group == "series":
            from ..models import CaptureSeries

            series = s.get(CaptureSeries, int(prm["series_id"]))
            if series is None:
                raise ValueError("series not found")
        cat_name = series.name if series else ({"abs": "Abs", "face": "Face"}.get(group) or (cat.name if cat else "pose"))
        settings = load_settings(s)
        dfrom = date.fromisoformat(prm["from"]) if prm.get("from") else None
        dto = date.fromisoformat(prm["to"]) if prm.get("to") else None
        mpd = prm.get("max_pose_diff_pct")
        if group == "series":
            from .align import series_output_path
            from .capture import series_sequence

            seq = series_sequence(s, series, dfrom, dto)
            photos = [s.get(Photo, f["id"]) for f in seq["frames"] if f["aligned_url"]]
            skipped = len(seq["frames"]) - len(photos)
            sid = series.id
            frame_path = lambda p: series_output_path(sid, p.id)  # noqa: E731
            canvas_w, canvas_h = settings.canvas_w, settings.canvas_h
        elif group in ("abs", "face"):
            from . import abs_view, face_view

            mod = abs_view if group == "abs" else face_view
            path_of = abs_view.abs_path if group == "abs" else face_view.face_path
            seq = mod.sequence(s, dfrom, dto)
            photos = [s.get(Photo, f["id"]) for f in seq["frames"] if f["aligned_url"]]
            skipped = len(seq["frames"]) - len(photos)
            if prm.get("hide_sleeves"):
                kept = [p for p in photos if not p.sleeves]
                skipped += len(photos) - len(kept)
                photos = kept
            frame_path = lambda p: path_of(p.id)  # noqa: E731
            canvas_w = canvas_h = mod.CANVAS
        else:
            photos, skipped = collect_frames(s, pose_id, dfrom, dto, float(mpd) if mpd is not None else None)
            if prm.get("hide_sleeves"):
                kept = [p for p in photos if not p.sleeves]
                skipped += len(photos) - len(kept)
                photos = kept
            frame_path = lambda p: config.abs_path(s.get(Alignment, p.id).output_path)  # noqa: E731
            canvas_w, canvas_h = settings.canvas_w, settings.canvas_h
        if not photos:
            raise ValueError("no aligned frames in this range; run alignment first")
        before = len(photos)
        first = photos[0].taken_at  # day numbers count from the first photo, whatever is picked below
        photos = pick_photos(photos, prm.get("every", "all"), int(prm.get("step", 1)), bool(prm.get("reverse")))
        thinned = before - len(photos)
        fmt = prm.get("format", "mp4")
        fps = float(prm.get("fps", 2.0))
        crossfade = float(prm.get("crossfade", 0.0))
        cap = prm.get("caption") or {}
        width = int(prm.get("width") or canvas_w)
        W, H = output_size(width, canvas_w, canvas_h, prm.get("aspect", "original"))
        fit, bg = prm.get("fit", "fill"), prm.get("background", "#000000")
        title = (cap.get("title") or "").strip()
        bar = bool(prm.get("progress_bar"))
        metric_ids = [int(m) for m in cap.get("metrics", [])]
        ctx.progress(0, len(photos), "Rendering frames")
        frames: list[np.ndarray] = []
        for i, p in enumerate(photos, 1):
            ctx.check_cancelled()
            with Image.open(frame_path(p)) as im:
                img = compose(im.convert("RGB"), W, H, fit, bg)
            lines = caption_lines(p, first, cap, _stats_for(s, p, metric_ids, settings.stats_window_days))
            if title:
                lines = [title, *lines]
            img = draw_caption(img, lines, cap.get("position", "bottom"), float(cap.get("size", 1.0)))
            if bar:
                img = draw_progress(img, (i - 1) / max(1, len(photos) - 1))
            frames.append(np.asarray(img))
            ctx.progress(i, len(photos))
        ex.status = "running"
        s.add(ex)
        s.commit()

    import math

    # the user's frame rate, else enough output frames per second to show every photo when the speed is high
    if prm.get("frame_rate"):
        out_fps = int(prm["frame_rate"]) if fmt == "mp4" else min(50, int(prm["frame_rate"]))
    else:
        out_fps = max(MP4_FPS, min(60, math.ceil(fps))) if fmt == "mp4" else max(GIF_FPS, min(50, math.ceil(fps)))
    order = play_order(len(frames), bool(prm.get("boomerang")))
    hold_end = prm.get("hold_end")
    sched = [
        (order[a], order[b], t)
        for a, b, t in frame_schedule(len(order), fps, crossfade, out_fps, float(prm.get("hold_start") or 0), None if hold_end is None else float(hold_end))
    ]
    quality = int(prm.get("quality", 8))
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
                str(tmp), (W, H), fps=out_fps, codec="libx264", pix_fmt_out="yuv420p", quality=quality, macro_block_size=2,
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
        replace_retry(tmp, path)
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
    msg = f"{fmt.upper()}, {len(sched) / out_fps:.1f} s, {len(frames)} photos ({path.stat().st_size / 1e6:.1f} MB)"
    if skipped:
        msg += f"; {skipped} photos left out (not aligned or outside the pose-match limit)"
    if thinned:
        msg += f"; {thinned} skipped by the one-per-period / every-Nth setting"
    return {
        "message": msg,
        "export_id": export_id,
        "frames": len(frames),
        "skipped": skipped,
        "thinned": thinned,
        "seconds": round(len(sched) / out_fps, 2),
        "frame_rate": out_fps,
        "path": str(path),
    }


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
