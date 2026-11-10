import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Film, Play, RotateCcw, Trash2 } from "lucide-react";
import { del, get, post } from "../api/client";
import type { ExportItem, Job, Metric } from "../api/types";
import { fmtBytes, fmtDateTime } from "../lib/format";
import { toast } from "../lib/toast";
import ExportPreview, { type PreviewFrame } from "./ExportPreview";
import { ConfirmButton, Field, Modal, Toggle } from "./ui";

export function useExports() {
  return useQuery({ queryKey: ["exports"], queryFn: () => get<ExportItem[]>("/exports") });
}

type Every = "all" | "day" | "week" | "month";
type Aspect = "original" | "1:1" | "4:5" | "9:16" | "16:9";

/** Everything the dialog remembers between exports (per browser). */
type Opts = {
  format: "mp4" | "gif";
  speedBy: "fps" | "length";
  fps: number;
  lengthSec: number;
  width: number;
  aspect: Aspect;
  fit: "fill" | "fit";
  background: string;
  quality: number;
  crossfade: number;
  holdStart: number;
  holdEnd: number;
  every: Every;
  step: number;
  reverse: boolean;
  boomerang: boolean;
  progressBar: boolean;
  capDate: boolean;
  capDays: boolean;
  capMetrics: number[];
  title: string;
  capPosition: "top" | "bottom";
  capSize: number;
};

const DEFAULTS: Opts = {
  format: "mp4",
  speedBy: "fps",
  fps: 2,
  lengthSec: 15,
  width: 1080,
  aspect: "original",
  fit: "fill",
  background: "#000000",
  quality: 8,
  crossfade: 0.2,
  holdStart: 0,
  holdEnd: 1,
  every: "all",
  step: 1,
  reverse: false,
  boomerang: false,
  progressBar: false,
  capDate: true,
  capDays: true,
  capMetrics: [],
  title: "",
  capPosition: "bottom",
  capSize: 1,
};
const STORE = "pl.exportOpts";
const SPEEDS = [0.5, 1, 2, 4, 8, 15, 30, 60];
const ASPECT_RATIO: Record<Exclude<Aspect, "original">, number> = { "1:1": 1, "4:5": 4 / 5, "9:16": 9 / 16, "16:9": 16 / 9 };

/** Last-used settings; the first time, the Viewer's playback speed. */
function load(defaultFps: number): Opts {
  try {
    const raw = localStorage.getItem(STORE);
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch {
    /* ignore */
  }
  return { ...DEFAULTS, fps: defaultFps || DEFAULTS.fps };
}

/** Same rule as the server (pick_photos): at most one photo per period (the first), then every
 * Nth, optionally newest first. Input sorted oldest first. */
function pickFrames(frames: PreviewFrame[], every: Every, step: number, reverse: boolean): PreviewFrame[] {
  let out = frames;
  if (every !== "all") {
    const seen = new Set<string>();
    out = [];
    for (const f of frames) {
      const d = f.taken_at;
      let key: string;
      if (every === "day") key = d.slice(0, 10);
      else if (every === "month") key = d.slice(0, 7);
      else {
        // ISO week
        const [y, m, dd] = d.slice(0, 10).split("-").map(Number);
        const u = new Date(Date.UTC(y, m - 1, dd));
        const day = u.getUTCDay() || 7;
        u.setUTCDate(u.getUTCDate() + 4 - day);
        const y0 = new Date(Date.UTC(u.getUTCFullYear(), 0, 1));
        key = `${u.getUTCFullYear()}-${Math.ceil(((u.getTime() - y0.getTime()) / 86400000 + 1) / 7)}`;
      }
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(f);
    }
  }
  out = out.filter((_, i) => i % Math.max(1, step) === 0);
  return reverse ? [...out].reverse() : out;
}

const fmtSec = (s: number) => (s >= 60 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${s.toFixed(s < 10 ? 1 : 0)} s`);

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-md border border-border p-3">
      <div className="label mb-2">{title}</div>
      {children}
    </div>
  );
}

export default function ExportDialog({
  open,
  onClose,
  poseId,
  from,
  to,
  defaultFps,
  maxPoseDiff,
  group,
  hideSleeves,
  frames = [],
  canvasW = 1080,
  canvasH = 1350,
}: {
  open: boolean;
  onClose: () => void;
  poseId: number | null;
  group?: "abs" | "face" | null;
  from?: string;
  to?: string;
  defaultFps: number;
  maxPoseDiff?: number | null;
  hideSleeves?: boolean;
  /** the aligned photos the Viewer is showing (after its filters): what the export starts from */
  frames?: PreviewFrame[];
  canvasW?: number;
  canvasH?: number;
}) {
  const qc = useQueryClient();
  const metrics = useQuery({ queryKey: ["metrics"], queryFn: () => get<Metric[]>("/metrics") });
  const exports = useExports();
  const [o, setO] = useState<Opts>(() => load(defaultFps));
  const [busy, setBusy] = useState(false);
  const [watching, setWatching] = useState<number | null>(null); // finished export playing in the list
  const set = <K extends keyof Opts>(k: K, v: Opts[K]) => setO((p) => ({ ...p, [k]: v }));
  useEffect(() => {
    try {
      localStorage.setItem(STORE, JSON.stringify(o));
    } catch {
      /* ignore */
    }
  }, [o]);

  // ---- what will come out
  const sorted = useMemo(() => [...frames].sort((a, b) => a.taken_at.localeCompare(b.taken_at)), [frames]);
  const picked = useMemo(() => pickFrames(sorted, o.every, o.step, o.reverse), [sorted, o.every, o.step, o.reverse]);
  const n = picked.length;
  const shown = o.boomerang && n > 1 ? 2 * n - 2 : n; // photos played, counting the way back
  const steps = Math.max(0, shown - 1);
  const fixed = o.holdStart + o.holdEnd;
  const fps = o.speedBy === "fps" ? o.fps : steps > 0 ? Math.min(60, Math.max(0.05, steps / Math.max(0.1, o.lengthSec - fixed))) : 1;
  const seconds = o.holdStart + steps / fps + o.holdEnd;
  const ratio = o.aspect === "original" ? canvasW / canvasH : ASPECT_RATIO[o.aspect];
  const width = o.format === "gif" ? Math.min(o.width, 720) : o.width;
  const height = Math.round(width / ratio / 2) * 2;
  const lengthTooShort = o.speedBy === "length" && o.lengthSec <= fixed;

  const start = async () => {
    setBusy(true);
    try {
      await post<{ job: Job }>("/exports", {
        pose_id: poseId,
        group: group ?? null,
        from: from || null,
        to: to || null,
        fps: Math.round(fps * 1000) / 1000,
        format: o.format,
        width,
        crossfade: o.crossfade,
        hold_start: o.holdStart,
        hold_end: o.holdEnd,
        every: o.every,
        step: o.step,
        reverse: o.reverse,
        boomerang: o.boomerang,
        aspect: o.aspect,
        fit: o.fit,
        background: o.background,
        quality: o.quality,
        progress_bar: o.progressBar,
        caption: { date: o.capDate, days: o.capDays, metrics: o.capMetrics, title: o.title, position: o.capPosition, size: o.capSize },
        max_pose_diff_pct: maxPoseDiff ?? null,
        hide_sleeves: !!hideSleeves,
      });
      toast.info("Export started");
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["exports"] });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };


  return (
    <Modal open={open} onClose={onClose} title="Export time-lapse" width="max-w-5xl">
      <div className="flex flex-col gap-4 md:flex-row">
        {/* settings */}
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <Section title="Speed">
            <div className="mb-2 flex gap-1">
              <button className={`btn btn-sm ${o.speedBy === "fps" ? "btn-primary" : ""}`} onClick={() => set("speedBy", "fps")}>
                Photos per second
              </button>
              <button className={`btn btn-sm ${o.speedBy === "length" ? "btn-primary" : ""}`} onClick={() => set("speedBy", "length")}>
                Total length
              </button>
            </div>
            {o.speedBy === "fps" ? (
              <>
                <div className="flex items-center gap-2">
                  <input
                    type="range"
                    className="flex-1"
                    min={0}
                    max={SPEEDS.length - 1}
                    step={0.01}
                    value={(() => {
                      // log-ish slider over SPEEDS
                      const i = SPEEDS.findIndex((v) => v >= o.fps);
                      if (i <= 0) return 0;
                      const a = SPEEDS[i - 1], b = SPEEDS[i];
                      return i - 1 + Math.log(o.fps / a) / Math.log(b / a);
                    })()}
                    onChange={(e) => {
                      const x = Number(e.target.value);
                      const i = Math.min(SPEEDS.length - 2, Math.floor(x));
                      const a = SPEEDS[i], b = SPEEDS[i + 1];
                      const v = a * Math.pow(b / a, x - i);
                      set("fps", Math.round(v * (v < 2 ? 100 : 10)) / (v < 2 ? 100 : 10));
                    }}
                  />
                  <input type="number" className="input w-24" min={0.05} max={60} step={0.05} value={o.fps} onChange={(e) => set("fps", Number(e.target.value))} />
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {SPEEDS.map((v) => (
                    <button key={v} className={`btn btn-sm ${o.fps === v ? "btn-primary" : ""}`} onClick={() => set("fps", v)}>
                      {v}/s
                    </button>
                  ))}
                </div>
                <div className="mt-1 text-[11px] text-muted">Each photo is on screen for {fmtSec(1 / Math.max(0.05, o.fps))}. Anything from 0.05 to 60 photos per second.</div>
              </>
            ) : (
              <>
                <div className="flex items-center gap-2">
                  <input type="range" className="flex-1" min={3} max={180} step={1} value={Math.min(180, o.lengthSec)} onChange={(e) => set("lengthSec", Number(e.target.value))} />
                  <input type="number" className="input w-24" min={1} max={3600} step={1} value={o.lengthSec} onChange={(e) => set("lengthSec", Number(e.target.value))} />
                  <span className="text-xs text-muted">s</span>
                </div>
                <div className={`mt-1 text-[11px] ${lengthTooShort ? "text-warn" : "text-muted"}`}>
                  {lengthTooShort ? "Longer than the first + last holds, please." : `That's ${fps.toFixed(fps < 2 ? 2 : 1)} photos per second.`}
                  {fps >= 60 && !lengthTooShort ? " (60 is the fastest; the video will be a bit longer.)" : ""}
                </div>
              </>
            )}
          </Section>

          <Section title="Timing">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              <Field label="Hold first photo (s)" hint="extra time before it starts moving">
                <input type="number" className="input" min={0} max={30} step={0.25} value={o.holdStart} onChange={(e) => set("holdStart", Number(e.target.value))} />
              </Field>
              <Field label="Hold last photo (s)" hint="how long the end result stays">
                <input type="number" className="input" min={0} max={30} step={0.25} value={o.holdEnd} onChange={(e) => set("holdEnd", Number(e.target.value))} />
              </Field>
              <Field label="Crossfade (s)" hint="blend between photos; 0 = hard cuts">
                <input type="number" className="input" min={0} max={2} step={0.05} value={o.crossfade} onChange={(e) => set("crossfade", Number(e.target.value))} />
              </Field>
            </div>
            <div className="mt-2 flex flex-wrap gap-4">
              <Toggle checked={o.boomerang} onChange={(v) => set("boomerang", v)} label="Boomerang (forward, then back)" />
              <Toggle checked={o.reverse} onChange={(v) => set("reverse", v)} label="Newest first" />
            </div>
          </Section>

          <Section title="Which photos">
            <div className="grid grid-cols-2 gap-3">
              <Field label="At most one photo per">
                <select className="select" value={o.every} onChange={(e) => set("every", e.target.value as Every)}>
                  <option value="all">— use every photo</option>
                  <option value="day">day</option>
                  <option value="week">week</option>
                  <option value="month">month</option>
                </select>
              </Field>
              <Field label="Then keep every Nth">
                <input type="number" className="input" min={1} max={100} step={1} value={o.step} onChange={(e) => set("step", Math.max(1, Math.round(Number(e.target.value))))} />
              </Field>
            </div>
            <div className="mt-1 text-[11px] text-muted">
              Starts from the photos the Viewer is showing{maxPoseDiff != null ? `: within ${maxPoseDiff}% pose match` : ""}
              {hideSleeves ? ", without sleeves" : ""}, {from || "first photo"} → {to || "last photo"}.
            </div>
          </Section>

          <Section title="Frame">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Field label="Format">
                <select className="select" value={o.format} onChange={(e) => set("format", e.target.value as Opts["format"])}>
                  <option value="mp4">MP4 video</option>
                  <option value="gif">GIF</option>
                </select>
              </Field>
              <Field label="Shape">
                <select className="select" value={o.aspect} onChange={(e) => set("aspect", e.target.value as Aspect)}>
                  <option value="original">original</option>
                  <option value="1:1">square 1:1</option>
                  <option value="4:5">portrait 4:5</option>
                  <option value="9:16">story 9:16</option>
                  <option value="16:9">wide 16:9</option>
                </select>
              </Field>
              <Field label="Width (px)" hint={o.format === "gif" ? "GIF is capped at 720" : undefined}>
                <input type="number" className="input" min={128} max={4096} step={2} value={o.width} onChange={(e) => set("width", Number(e.target.value))} list="export-widths" />
                <datalist id="export-widths">
                  {[2160, 1440, 1080, 720, 540, 360].map((w) => (
                    <option key={w} value={w} />
                  ))}
                </datalist>
              </Field>
              {o.format === "mp4" ? (
                <Field label={`Quality ${o.quality}/10`} hint="higher = sharper, bigger file">
                  <input type="range" min={1} max={10} step={1} value={o.quality} onChange={(e) => set("quality", Number(e.target.value))} />
                </Field>
              ) : (
                <span />
              )}
            </div>
            {o.aspect !== "original" && (
              <div className="mt-2 flex flex-wrap items-center gap-4">
                <div className="flex gap-1">
                  <button className={`btn btn-sm ${o.fit === "fill" ? "btn-primary" : ""}`} onClick={() => set("fit", "fill")} title="scale up and crop the edges">
                    Fill (crop)
                  </button>
                  <button className={`btn btn-sm ${o.fit === "fit" ? "btn-primary" : ""}`} onClick={() => set("fit", "fit")} title="show the whole photo with bars">
                    Fit (bars)
                  </button>
                </div>
                {o.fit === "fit" && (
                  <label className="flex items-center gap-2 text-sm">
                    Bar colour <input type="color" value={o.background} onChange={(e) => set("background", e.target.value)} className="h-7 w-10 cursor-pointer rounded border border-border bg-transparent" />
                  </label>
                )}
              </div>
            )}
          </Section>

          <Section title="Text on the video">
            <div className="flex flex-wrap items-center gap-4">
              <Toggle checked={o.capDate} onChange={(v) => set("capDate", v)} label="date" />
              <Toggle checked={o.capDays} onChange={(v) => set("capDays", v)} label="day number" />
              {(metrics.data ?? []).map((m) => (
                <Toggle
                  key={m.id}
                  checked={o.capMetrics.includes(m.id)}
                  onChange={(v) => set("capMetrics", v ? [...o.capMetrics, m.id] : o.capMetrics.filter((x) => x !== m.id))}
                  label={m.name}
                />
              ))}
              <Toggle checked={o.progressBar} onChange={(v) => set("progressBar", v)} label="progress bar" />
            </div>
            <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-4">
              <Field label="Title" className="col-span-2">
                <input className="input" maxLength={120} placeholder="optional, e.g. 2025 bulk" value={o.title} onChange={(e) => set("title", e.target.value)} />
              </Field>
              <Field label="Position">
                <select className="select" value={o.capPosition} onChange={(e) => set("capPosition", e.target.value as Opts["capPosition"])}>
                  <option value="bottom">bottom</option>
                  <option value="top">top</option>
                </select>
              </Field>
              <Field label={`Text size ${Math.round(o.capSize * 100)}%`}>
                <input type="range" min={0.5} max={2.5} step={0.1} value={o.capSize} onChange={(e) => set("capSize", Number(e.target.value))} />
              </Field>
            </div>
          </Section>
        </div>

        {/* preview + summary */}
        <div className="flex w-full shrink-0 flex-col gap-3 md:w-72">
          <div className="label">Preview</div>
          <ExportPreview
            frames={picked}
            firstDate={sorted[0]?.taken_at ?? null}
            fps={fps}
            holdStart={o.holdStart}
            holdEnd={o.holdEnd}
            crossfade={o.crossfade}
            boomerang={o.boomerang}
            ratio={ratio}
            objectFit={o.aspect === "original" ? "fill" : o.fit === "fill" ? "cover" : "contain"}
            background={o.aspect !== "original" && o.fit === "fit" ? o.background : "#000"}
            title={o.title}
            showDate={o.capDate}
            showDays={o.capDays}
            capPosition={o.capPosition}
            capSize={o.capSize}
            progressBar={o.progressBar}
          />
          {o.capMetrics.length > 0 && <div className="-mt-1 text-[11px] text-muted">Stats lines are drawn in the video, not in this preview.</div>}
          <div className="rounded-md border border-border p-3 text-sm">
            <div className="flex justify-between">
              <span className="text-muted">Photos</span>
              <span className="tabular-nums">
                {n}
                {n < frames.length ? <span className="text-muted"> of {frames.length}</span> : null}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Length</span>
              <span className="tabular-nums">{n ? fmtSec(seconds) : "–"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Speed</span>
              <span className="tabular-nums">{fps.toFixed(fps < 2 ? 2 : 1)} /s</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Size</span>
              <span className="tabular-nums">
                {width} × {height}
              </span>
            </div>
          </div>
          <button className="btn btn-primary justify-center" onClick={start} disabled={busy || n === 0 || fps <= 0 || lengthTooShort}>
            <Film size={14} /> Start export
          </button>
          <button className="btn btn-sm justify-center" onClick={() => setO({ ...DEFAULTS, fps: defaultFps || DEFAULTS.fps })} title="back to the default settings">
            <RotateCcw size={12} /> Reset settings
          </button>
          <div className="text-[11px] text-muted">Your settings are remembered for next time. Rendering runs in the background (see the job panel).</div>
        </div>
      </div>

      <div className="mt-5">
        <div className="label mb-1">Exports</div>
        {(exports.data ?? []).length === 0 ? (
          <div className="text-sm text-muted">None yet.</div>
        ) : (
          <ul className="divide-y divide-border rounded border border-border">
            {exports.data!.map((x) => (
              <li key={x.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
                <span className="badge bg-panel-2 uppercase text-muted">{x.format}</span>
                <span className="truncate">{x.path?.split("/").pop() ?? `export #${x.id}`}</span>
                <span className="text-xs text-muted">
                  {x.frames ? `${x.frames} photos · ` : ""}
                  {x.size_bytes ? fmtBytes(x.size_bytes) + " · " : ""}
                  {fmtDateTime(x.created_at)}
                </span>
                <span className={`ml-auto text-xs ${x.status === "failed" ? "text-danger" : x.status === "done" ? "text-ok" : "text-muted"}`}>{x.status}</span>
                {x.status === "done" && (
                  <>
                    <button
                      className={`btn btn-sm ${watching === x.id ? "btn-primary" : ""}`}
                      onClick={() => setWatching(watching === x.id ? null : x.id)}
                      title="play the finished export here"
                    >
                      <Play size={13} />
                    </button>
                    <a className="btn btn-sm" href={x.download_url} title="save to your computer">
                      <Download size={13} />
                    </a>
                  </>
                )}
                {x.status !== "running" && x.status !== "queued" && (
                  <ConfirmButton
                    className="btn btn-sm btn-danger"
                    confirmText="Delete?"
                    onConfirm={() =>
                      del(`/exports/${x.id}`)
                        .then(() => qc.invalidateQueries({ queryKey: ["exports"] }))
                        .catch((e: Error) => toast.error(e.message))
                    }
                  >
                    <Trash2 size={13} />
                  </ConfirmButton>
                )}
                {watching === x.id && x.status === "done" && (
                  <div className="w-full pt-2">
                    {x.format === "mp4" ? (
                      <video src={x.download_url} controls autoPlay loop className="mx-auto max-h-[60vh] rounded border border-border bg-black" />
                    ) : (
                      <img src={x.download_url} alt="" className="mx-auto max-h-[60vh] rounded border border-border" />
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
