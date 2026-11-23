import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, FlipHorizontal2, Image as ImageIcon, RefreshCcw, RotateCcw, SwitchCamera, Timer } from "lucide-react";
import { useGhost, usePatchSeries, type CaptureSeries, type GhostSource, type GhostStyle } from "../../api/capture";
import { addToOutbox, markSaved, useOutbox } from "../../lib/outbox";
import { toast } from "../../lib/toast";
import { captureFilename, cropToAspect, drawSkeleton, getSaveToRoll, isoWithOffset, openCamera, saveToCameraRoll, stopStream, takeStill } from "./camera";

const STYLES: { key: GhostStyle; label: string }[] = [
  { key: "photo", label: "Photo" },
  { key: "edges", label: "Outline" },
  { key: "skeleton", label: "Skeleton" },
];
const SOURCES: { key: GhostSource; label: string }[] = [
  { key: "reference", label: "Reference" },
  { key: "last", label: "Last" },
  { key: "first", label: "First" },
];
const TIMERS = [0, 3, 5, 10] as const;

interface Shot {
  blob: Blob;
  url: string;
  when: Date;
  w: number;
  h: number;
}

function useImage(url: string | null | undefined) {
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  useEffect(() => {
    setImg(null);
    if (!url) return;
    const i = new Image();
    i.onload = () => setImg(i);
    i.src = url;
  }, [url]);
  return img;
}

export default function CameraScreen({ series, onExit, extra }: { series: CaptureSeries; onExit: () => void; extra?: React.ReactNode }) {
  const qc = useQueryClient();
  const save = usePatchSeries(series.id);
  const [opts, setOpts] = useState(series);
  useEffect(() => setOpts(series), [series]);
  const ghostQ = useGhost(series.id, opts.ghost_source);
  const ghost = ghostQ.data?.ghost ?? null;
  const ghostImg = useImage(ghost ? (opts.ghost_style === "edges" ? ghost.edges_url : ghost.image_url) : null);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [vsize, setVsize] = useState<{ w: number; h: number } | null>(null);
  const [camError, setCamError] = useState<string | null>(null);
  const [count, setCount] = useState<number | null>(null);
  const [shot, setShot] = useState<Shot | null>(null);
  const [compare, setCompare] = useState(false);
  const outbox = useOutbox();
  const pending = outbox.filter((i) => !i.uploaded).length;

  // settings change: show at once, save in the background (per series = per pose)
  const saveTimer = useRef<number | undefined>(undefined);
  const set = useCallback(
    (patch: Partial<CaptureSeries>) => {
      setOpts((o) => ({ ...o, ...patch }));
      window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => save.mutate(patch), 400);
    },
    [save],
  );

  // camera on/off; keep the screen awake while it's open
  useEffect(() => {
    let cancelled = false;
    let lock: { release: () => Promise<void> } | null = null;
    setCamError(null);
    openCamera(opts.camera_facing)
      .then((s) => {
        if (cancelled) return stopStream(s);
        streamRef.current = s;
        const v = videoRef.current!;
        v.srcObject = s;
        v.play().catch(() => {});
      })
      .catch((e: Error) => setCamError(e.name === "NotAllowedError" ? "Camera permission was refused. Allow it in the browser settings." : e.message));
    (navigator as Navigator & { wakeLock?: { request: (t: "screen") => Promise<{ release: () => Promise<void> }> } }).wakeLock
      ?.request("screen")
      .then((l) => (lock = l))
      .catch(() => {});
    (screen.orientation as ScreenOrientation & { lock?: (o: string) => Promise<void> })?.lock?.("portrait").catch(() => {});
    return () => {
      cancelled = true;
      stopStream(streamRef.current);
      streamRef.current = null;
      void lock?.release().catch(() => {});
    };
  }, [opts.camera_facing]);

  // draw the ghost cropped to the video's frame shape
  const draw = useCallback(() => {
    const c = canvasRef.current;
    if (!c || !vsize || !ghost) return;
    const scale = Math.min(1, 1280 / Math.max(vsize.w, vsize.h));
    c.width = Math.round(vsize.w * scale);
    c.height = Math.round(vsize.h * scale);
    const ctx = c.getContext("2d")!;
    ctx.clearRect(0, 0, c.width, c.height);
    const gw = ghostImg?.naturalWidth || ghost.width;
    const gh = ghostImg?.naturalHeight || ghost.height;
    const crop = cropToAspect(gw, gh, vsize.w / vsize.h);
    if (opts.ghost_style === "skeleton") {
      if (!ghost.landmarks) return;
      drawSkeleton(ctx, ghost.landmarks, (p) => [((p.x * gw - crop.sx) / crop.sw) * c.width, ((p.y * gh - crop.sy) / crop.sh) * c.height], c.width / 1080);
    } else if (ghostImg) {
      ctx.drawImage(ghostImg, crop.sx, crop.sy, crop.sw, crop.sh, 0, 0, c.width, c.height);
    }
  }, [ghost, ghostImg, vsize, opts.ghost_style]);
  useEffect(draw, [draw]);

  const portraitGhost = ghost ? ghost.height >= ghost.width : true;
  const portraitVideo = vsize ? vsize.h >= vsize.w : true;

  const fire = useCallback(async () => {
    const v = videoRef.current;
    if (!v || !vsize) return;
    const when = new Date();
    try {
      const blob = await takeStill(v, streamRef.current?.getVideoTracks()[0], when, navigator.platform || "browser");
      const bmp = await createImageBitmap(blob);
      setShot({ blob, url: URL.createObjectURL(blob), when, w: bmp.width, h: bmp.height });
      bmp.close();
    } catch (e) {
      toast.error(`Could not take the photo: ${(e as Error).message}`);
    }
  }, [vsize]);

  const shutter = () => {
    if (count != null || shot) return;
    if (!opts.timer_s) return void fire();
    let n = opts.timer_s;
    setCount(n);
    const t = window.setInterval(() => {
      n -= 1;
      if (n <= 0) {
        window.clearInterval(t);
        setCount(null);
        void fire();
      } else setCount(n);
    }, 1000);
  };

  const retake = () => {
    if (shot) URL.revokeObjectURL(shot.url);
    setShot(null);
    setCompare(false);
  };

  // Keep: the share sheet must open inside this tap (no await before it)
  const keep = () => {
    if (!shot) return;
    const name = captureFilename(series.name, shot.when);
    const file = new File([shot.blob], name, { type: "image/jpeg", lastModified: shot.when.getTime() });
    const id = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
    const wantSave = getSaveToRoll();
    const saving = wantSave ? saveToCameraRoll([file]) : null;
    void addToOutbox(
      {
        id,
        blob: shot.blob,
        filename: name,
        seriesId: series.id,
        ghostPhotoId: ghost?.photo_id ?? null,
        capturedAt: isoWithOffset(shot.when),
        meta: { facing: opts.camera_facing, mirror: opts.mirror, style: opts.ghost_style, opacity: opts.ghost_opacity, width: shot.w, height: shot.h },
      },
      !wantSave,
    ).then(() => {
      qc.invalidateQueries({ queryKey: ["capture"] });
      qc.invalidateQueries({ queryKey: ["photos"] });
    });
    // shared, dismissed or downloaded: either way you've decided; the upload continues regardless
    saving?.then(() => markSaved(id));
    retake();
  };

  const mirror = opts.mirror ? "scale-x-[-1]" : "";

  return (
    <div className="fixed inset-0 z-[60] flex flex-col bg-black text-white select-none">
      {/* live view */}
      <div className="relative min-h-0 flex-1 overflow-hidden">
        <video
          ref={videoRef}
          playsInline
          muted
          autoPlay
          className={`absolute inset-0 h-full w-full object-cover ${mirror}`}
          onLoadedMetadata={(e) => setVsize({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight })}
          onResize={(e) => setVsize({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight })}
        />
        <canvas
          ref={canvasRef}
          className={`pointer-events-none absolute inset-0 h-full w-full object-cover ${mirror}`}
          style={{ opacity: opts.ghost_style === "photo" ? opts.ghost_opacity : Math.min(1, opts.ghost_opacity + 0.3) }}
        />
        {camError && (
          <div className="absolute inset-0 flex items-center justify-center p-8 text-center text-sm">
            <div>
              <p className="mb-3">{camError}</p>
              <button className="btn" onClick={onExit}>
                Back
              </button>
            </div>
          </div>
        )}
        {!ghost && !ghostQ.isLoading && !camError && (
          <div className="absolute inset-x-0 top-24 mx-auto w-fit rounded bg-black/60 px-3 py-1.5 text-xs">No ghost yet: this is the first photo.</div>
        )}
        {ghost && opts.ghost_style === "skeleton" && !ghost.landmarks && (
          <div className="absolute inset-x-0 top-24 mx-auto w-fit rounded bg-black/60 px-3 py-1.5 text-xs">The ghost photo has no landmarks yet.</div>
        )}
        {portraitGhost !== portraitVideo && vsize && ghost && (
          <div className="absolute inset-x-0 top-1/3 mx-auto w-fit rounded bg-black/70 px-3 py-2 text-sm">
            Turn the phone {portraitGhost ? "upright" : "sideways"} to match the ghost.
          </div>
        )}
        {count != null && (
          <div className="absolute inset-0 flex items-center justify-center text-[120px] font-bold drop-shadow-lg">{count}</div>
        )}

        {/* top bar */}
        <div className="absolute inset-x-0 top-0 flex items-center gap-2 bg-gradient-to-b from-black/70 to-transparent px-3 pb-6 pt-[max(0.75rem,env(safe-area-inset-top))]">
          <button className="rounded-full bg-black/40 p-2" onClick={onExit} aria-label="back">
            <ArrowLeft size={20} />
          </button>
          <div className="min-w-0 flex-1 truncate text-sm font-medium">{series.name}</div>
          <button
            className="rounded-full bg-black/40 px-3 py-2 text-xs"
            onClick={() => set({ timer_s: TIMERS[(TIMERS.indexOf(opts.timer_s) + 1) % TIMERS.length] })}
            aria-label="self-timer"
          >
            <Timer size={16} className="mr-1 inline" />
            {opts.timer_s ? `${opts.timer_s}s` : "off"}
          </button>
          <button className={`rounded-full p-2 ${opts.mirror ? "bg-white/25" : "bg-black/40"}`} onClick={() => set({ mirror: !opts.mirror })} aria-label="mirror">
            <FlipHorizontal2 size={18} />
          </button>
          <button
            className="rounded-full bg-black/40 p-2"
            onClick={() => {
              const f = opts.camera_facing === "user" ? "environment" : "user";
              set({ camera_facing: f, mirror: f === "user" });
            }}
            aria-label="switch camera"
          >
            <SwitchCamera size={18} />
          </button>
        </div>
      </div>

      {/* controls */}
      <div className="shrink-0 space-y-3 bg-black px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3">
        <div className="flex items-center gap-3 text-xs">
          <span className="w-14 text-white/70">Ghost</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={opts.ghost_opacity}
            onChange={(e) => set({ ghost_opacity: Number(e.target.value) })}
            className="h-8 flex-1"
            aria-label="ghost opacity"
          />
          <span className="w-9 text-right tabular-nums">{Math.round(opts.ghost_opacity * 100)}%</span>
        </div>
        <div className="flex gap-1 text-xs">
          {STYLES.map((s) => (
            <button
              key={s.key}
              className={`flex-1 rounded-md py-1.5 ${opts.ghost_style === s.key ? "bg-white text-black" : "bg-white/10"}`}
              onClick={() => set({ ghost_style: s.key })}
            >
              {s.label}
            </button>
          ))}
        </div>
        <div className="flex gap-1 text-xs">
          {SOURCES.map((s) => (
            <button
              key={s.key}
              className={`flex-1 rounded-md py-1.5 ${opts.ghost_source === s.key ? "bg-white text-black" : "bg-white/10"}`}
              onClick={() => set({ ghost_source: s.key })}
            >
              {s.label}
            </button>
          ))}
        </div>
        {extra}
        <div className="flex items-center justify-between pt-1">
          <div className="relative h-12 w-12 overflow-hidden rounded-md bg-white/10">
            {series.last_thumb_url ? <img src={series.last_thumb_url} alt="" className="h-full w-full object-cover" /> : <ImageIcon className="m-3 opacity-40" size={24} />}
            {pending > 0 && (
              <span className="absolute -right-1 -top-1 rounded-full bg-accent-2 px-1.5 text-[10px]" title="waiting to upload">
                {pending}
              </span>
            )}
          </div>
          <button
            className="flex h-20 w-20 items-center justify-center rounded-full border-4 border-white disabled:opacity-40"
            onClick={shutter}
            disabled={!vsize || !!camError || count != null}
            aria-label="take photo"
          >
            <span className="h-16 w-16 rounded-full bg-white active:scale-90" />
          </button>
          <div className="h-12 w-12" />
        </div>
      </div>

      {/* review */}
      {shot && (
        <div className="absolute inset-0 z-10 flex flex-col bg-black">
          <div className="relative min-h-0 flex-1">
            <div className="absolute inset-0 m-auto max-h-full max-w-full" style={{ aspectRatio: `${shot.w} / ${shot.h}` }}>
              <img src={shot.url} alt="" className="absolute inset-0 h-full w-full object-cover" />
              {compare && ghost && <img src={ghost.image_url} alt="" className="absolute inset-0 h-full w-full object-cover" />}
            </div>
            {compare && <div className="absolute left-3 top-[max(0.75rem,env(safe-area-inset-top))] rounded bg-black/60 px-2 py-1 text-xs">ghost</div>}
          </div>
          <div className="flex shrink-0 items-center justify-between gap-3 px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3">
            <button className="btn flex-1 justify-center py-3" onClick={retake}>
              <RotateCcw size={16} /> Retake
            </button>
            {ghost && (
              <button
                className="btn flex-1 justify-center py-3"
                onPointerDown={() => setCompare(true)}
                onPointerUp={() => setCompare(false)}
                onPointerLeave={() => setCompare(false)}
                onContextMenu={(e) => e.preventDefault()}
              >
                <RefreshCcw size={16} /> Hold: before
              </button>
            )}
            <button className="btn btn-primary flex-1 justify-center py-3" onClick={keep}>
              <Check size={16} /> Keep
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
