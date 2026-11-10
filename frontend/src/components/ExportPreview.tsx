import { useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, RotateCcw } from "lucide-react";

export type PreviewFrame = { url: string; taken_at: string };

/** "2026-03-05T..." -> "Mar 5, 2026" (same as the server's caption) */
const fmtCaptionDate = (iso: string) => {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m - 1]} ${d}, ${y}`;
};
const dayIndex = (iso: string) => {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return Math.round(Date.UTC(y, m - 1, d) / 86400000); // Date.UTC months are 0-based
};

/**
 * Plays the time-lapse in the browser the way the export will render it: the same photos in the
 * same order, the same timing (holds, speed, crossfade, boomerang), shape, captions and progress
 * bar. Stats lines are only drawn in the real export.
 */
export default function ExportPreview({
  frames,
  firstDate,
  fps,
  holdStart,
  holdEnd,
  crossfade,
  boomerang,
  ratio,
  objectFit,
  background,
  title,
  showDate,
  showDays,
  capPosition,
  capSize,
  progressBar,
}: {
  frames: PreviewFrame[];
  /** the earliest photo of the whole sequence: day numbers count from it */
  firstDate: string | null;
  fps: number;
  holdStart: number;
  holdEnd: number;
  crossfade: number;
  boomerang: boolean;
  ratio: number;
  objectFit: "fill" | "cover" | "contain";
  background: string;
  title: string;
  showDate: boolean;
  showDays: boolean;
  capPosition: "top" | "bottom";
  capSize: number;
  progressBar: boolean;
}) {
  const n = frames.length;
  // playing order: forward, then back down with boomerang (ends not repeated) — as play_order()
  const order = useMemo(() => {
    const o = Array.from({ length: n }, (_, i) => i);
    if (boomerang && n > 2) for (let i = n - 2; i >= 0; i--) o.push(i);
    else if (boomerang && n === 2) o.push(0);
    return o;
  }, [n, boomerang]);
  const [pos, setPos] = useState(0); // position in `order`
  const [prev, setPrev] = useState<number | null>(null); // photo fading out
  const [playing, setPlaying] = useState(true);
  const timer = useRef<number | null>(null);

  // restart when what is being played changes
  const sig = `${n}|${boomerang}|${frames[0]?.url}|${frames[n - 1]?.url}`;
  useEffect(() => {
    setPos(0);
    setPrev(null);
  }, [sig]);

  const step = 1000 / Math.max(0.05, fps);
  const durationAt = (p: number) => (p === order.length - 1 ? Math.max(1, holdEnd * 1000) : step + (p === 0 ? holdStart * 1000 : 0));
  const fadeMs = crossfade > 0 ? Math.min(crossfade * 1000, 0.9 * step) : 0;

  useEffect(() => {
    if (!playing || order.length === 0) return;
    timer.current = window.setTimeout(() => {
      setPrev(order[pos]);
      setPos((p) => (p + 1) % order.length);
    }, durationAt(pos));
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, pos, order, fps, holdStart, holdEnd]);

  // preload the next few photos so fast speeds do not stutter
  useEffect(() => {
    for (let k = 1; k <= 4; k++) {
      const f = frames[order[(pos + k) % Math.max(1, order.length)]];
      if (f) new Image().src = f.url;
    }
  }, [pos, order, frames]);

  if (n === 0) {
    return (
      <div className="flex aspect-[4/5] w-full items-center justify-center rounded border border-border text-xs text-muted">
        no photos to preview
      </div>
    );
  }
  const idx = order[Math.min(pos, order.length - 1)];
  const cur = frames[idx];
  const capLines = [
    title.trim(),
    [showDate && fmtCaptionDate(cur.taken_at), showDays && firstDate && `Day ${dayIndex(cur.taken_at) - dayIndex(firstDate)}`].filter(Boolean).join("  ·  "),
  ].filter(Boolean);
  const imgStyle = { objectFit } as const;

  return (
    <div className="flex flex-col gap-1.5">
      <div
        className="relative mx-auto w-full overflow-hidden rounded border border-border"
        style={{ aspectRatio: `${ratio}`, maxWidth: ratio < 0.7 ? 230 : 300, background }}
      >
        {prev != null && prev !== idx && fadeMs > 0 && <img src={frames[prev]?.url} alt="" className="absolute inset-0 h-full w-full" style={imgStyle} />}
        <img
          key={`${pos}-${idx}`}
          src={cur.url}
          alt=""
          className="absolute inset-0 h-full w-full"
          style={{ ...imgStyle, animation: fadeMs > 0 && prev != null ? `pl-fade-in ${fadeMs}ms linear` : undefined }}
        />
        {capLines.length > 0 && (
          <div
            className={`absolute inset-x-0 px-2 py-1.5 text-white ${capPosition === "top" ? "top-0 bg-gradient-to-b" : "bottom-0 bg-gradient-to-t"} from-black/70 to-transparent`}
            style={{ fontSize: `${Math.max(7, 12 * capSize * Math.min(1, ratio * 1.25))}px`, lineHeight: 1.3 }}
          >
            {capLines.map((l, i) => (
              <div key={i} className="truncate font-semibold [text-shadow:0_1px_2px_rgba(0,0,0,.6)]">
                {l}
              </div>
            ))}
          </div>
        )}
        {progressBar && (
          <div className="absolute inset-x-0 bottom-0 h-1 bg-neutral-800">
            <div className="h-full bg-white" style={{ width: `${n > 1 ? (100 * idx) / (n - 1) : 100}%` }} />
          </div>
        )}
      </div>
      <div className="flex items-center gap-1.5">
        <button className="btn btn-sm" onClick={() => setPlaying((p) => !p)} title={playing ? "pause" : "play"}>
          {playing ? <Pause size={12} /> : <Play size={12} />}
        </button>
        <button
          className="btn btn-sm"
          onClick={() => {
            setPrev(null);
            setPos(0);
            setPlaying(true);
          }}
          title="from the start"
        >
          <RotateCcw size={12} />
        </button>
        <input
          type="range"
          className="min-w-0 flex-1"
          min={0}
          max={Math.max(0, order.length - 1)}
          value={pos}
          onChange={(e) => {
            setPlaying(false);
            setPrev(null);
            setPos(Number(e.target.value));
          }}
          title="scrub"
        />
        <span className="w-12 text-right text-[11px] tabular-nums text-muted">
          {idx + 1}/{n}
        </span>
      </div>
    </div>
  );
}
