import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronFirst, ChevronLast, Columns2, Crosshair, Film, Layers, Shirt, Pause, Pencil, Play, SlidersHorizontal, Square, StepBack, StepForward } from "lucide-react";
import { get, patch, post } from "../api/client";
import { useCategories, useDashboard, useRunJob, useSettings } from "../api/queries";
import type { Category, Job, Metric, MetricEntry, Sequence, SequenceFrame } from "../api/types";
import { Empty, PageHeader, Spinner, Toggle } from "../components/ui";
import MetricChart from "../components/MetricChart";
import ExportDialog from "../components/ExportDialog";
import { useShortcuts } from "../lib/keyboard";
import { fmtRatio } from "../components/PhotoMeasurements";
import { fmtDate, toDateValue } from "../lib/format";
import { toast } from "../lib/toast";

type Mode = "single" | "onion" | "split";

function frameSrc(f: SequenceFrame): string {
  return f.aligned_url ?? f.preview_url;
}

/** Keeps decoded images around so stepping is instant; loads outward from the current frame. */
function usePreload(frames: SequenceFrame[], index: number) {
  const cache = useRef<Map<string, HTMLImageElement>>(new Map());
  useEffect(() => {
    let cancelled = false;
    const order: number[] = [];
    for (let d = 0; d < frames.length; d++) {
      if (index + d < frames.length) order.push(index + d);
      if (d > 0 && index - d >= 0) order.push(index - d);
    }
    let i = 0;
    const next = () => {
      if (cancelled || i >= order.length) return;
      const batch = order.slice(i, i + 6);
      i += 6;
      Promise.all(
        batch.map((k) => {
          const src = frameSrc(frames[k]);
          if (cache.current.has(src)) return Promise.resolve();
          const img = new Image();
          img.decoding = "async";
          img.src = src;
          cache.current.set(src, img);
          return img.decode().catch(() => undefined);
        }),
      ).then(next);
    };
    next();
    return () => {
      cancelled = true;
    };
  }, [frames, index]);
}

function StatsStrip({ frame }: { frame: SequenceFrame | undefined }) {
  if (!frame) return null;
  if (frame.stats.length === 0) return <div className="text-xs text-muted">No measurements within the stats window of this date.</div>;
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
      {frame.stats.map((s) => (
        <div key={s.metric_id} className="flex items-baseline gap-1.5">
          <span className="text-muted">{s.name}</span>
          <span className="font-semibold tabular-nums">
            {s.value!.toFixed(s.decimals)} {s.unit}
          </span>
          {s.offset_days !== 0 && <span className="text-xs text-muted">({s.offset_label})</span>}
          {s.change_since_first != null && s.change_since_first !== 0 && (
            <span className="text-xs tabular-nums text-muted">
              {s.change_since_first > 0 ? "+" : ""}
              {s.change_since_first.toFixed(s.decimals)} since start
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

export default function Viewer() {
  const [params, setParams] = useSearchParams();
  const qc = useQueryClient();
  const { data: cats = [] } = useCategories();
  const { data: dash } = useDashboard();
  const { data: settings } = useSettings();
  const run = useRunJob();
  // close-up groups (attributes across every pose) instead of a pose category
  const rawPose = params.get("pose_id") ?? "";
  const group = rawPose === "abs" ? "abs" : rawPose === "face" ? "face" : null;
  const isAbs = group != null;
  const poseId = isAbs ? undefined : Number(params.get("pose_id")) || cats.find((c) => c.photo_count > 0 && c.rule_key !== "other")?.id || cats[0]?.id;
  const activeCategory = cats.find((category) => category.id === poseId);
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const seq = useQuery({
    queryKey: ["sequence", group ?? poseId, from, to],
    queryFn: () =>
      get<Sequence>(
        group === "abs" ? "/abs/sequence" : group === "face" ? "/faces/sequence" : `/poses/${poseId}/sequence`,
        { from, to },
      ),
    enabled: isAbs || !!poseId,
  });
  const [maxDiff, setMaxDiff] = useState<number | null>(() => {
    try {
      const v = localStorage.getItem("pl.poseMatch");
      return v ? Number(v) : null;
    } catch {
      return null;
    }
  });
  useEffect(() => {
    try {
      if (maxDiff == null) localStorage.removeItem("pl.poseMatch");
      else localStorage.setItem("pl.poseMatch", String(maxDiff));
    } catch {
      /* ignore */
    }
  }, [maxDiff]);
  // "Hide sleeves": leave out photos where clothing covers the arms (remembered per browser)
  const [hideSleeves, setHideSleeves] = useState<boolean>(() => {
    try {
      return localStorage.getItem("pl.hideSleeves") === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("pl.hideSleeves", hideSleeves ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [hideSleeves]);
  const allFrames = useMemo(() => seq.data?.frames ?? [], [seq.data]);
  const sleeveCount = useMemo(() => allFrames.filter((f) => f.sleeves).length, [allFrames]);
  const uncheckedSleeves = useMemo(() => allFrames.some((f) => f.sleeves == null), [allFrames]);
  // photos not checked yet (e.g. right after an import): check them when the toggle is on
  useEffect(() => {
    if (hideSleeves && uncheckedSleeves) post("/sleeves/detect", {}).catch(() => undefined);
  }, [hideSleeves, uncheckedSleeves]);
  const frames = useMemo(
    () =>
      allFrames.filter(
        (f) => (maxDiff == null || f.pose_diff_pct == null || f.pose_diff_pct <= maxDiff) && !(hideSleeves && f.sleeves),
      ),
    [allFrames, maxDiff, hideSleeves],
  );
  const hidden = allFrames.length - frames.length;
  // what an export starts from (stable between playback ticks, so the export preview keeps playing)
  const exportFrames = useMemo(
    () => frames.filter((f) => f.aligned_url && f.taken_at).map((f) => ({ url: f.aligned_url as string, taken_at: f.taken_at as string })),
    [frames],
  );
  const [index, setIndex] = useState(0);
  const [compare, setCompare] = useState(0);
  const [mode, setMode] = useState<Mode>("single");
  const [framingZoomDraft, setFramingZoomDraft] = useState<number | null>(null);
  const [savingFramingZoom, setSavingFramingZoom] = useState(false);
  const [opacity, setOpacity] = useState(0.5);
  const [split, setSplit] = useState(0.5);
  // ?autoplay=1: start the flipbook straight away
  const [playing, setPlaying] = useState(() => new URLSearchParams(window.location.search).get("autoplay") === "1");
  const [fps, setFps] = useState(2);
  const [fade, setFade] = useState(true);
  const [exportOpen, setExportOpen] = useState(() => new URLSearchParams(window.location.search).get("export") === "1"); // ?export=1 opens the export window
  const [metricId, setMetricId] = useState<number | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const zoomSaveTimer = useRef<number | null>(null);
  const pendingZoom = useRef<{ categoryId: number; zoom: number } | null>(null);
  const [panel, setPanel] = useState(false); // phone: the side panel opens below the photo
  const swipe = useRef<{ x: number; y: number } | null>(null);
  const framingZoom = framingZoomDraft ?? activeCategory?.framing_zoom ?? 1;

  const saveFramingZoom = useCallback(
    async (categoryId: number, zoom: number) => {
      setSavingFramingZoom(true);
      try {
        const category = await patch<Category>(`/poses/${categoryId}`, { framing_zoom: zoom });
        qc.setQueryData<Category[]>(["categories"], (old) => old?.map((item) => (item.id === category.id ? category : item)));
        try {
          await post<Job>(`/poses/${categoryId}/align`, { force: true });
          toast.info(`Saved ${category.name} framing. Alignment is queued.`);
          qc.invalidateQueries({ queryKey: ["jobs"] });
        } catch (e) {
          toast.error(`Framing saved, but alignment could not be queued: ${(e as Error).message}`);
        }
      } catch (e) {
        toast.error(`Could not save group framing: ${(e as Error).message}`);
      } finally {
        setSavingFramingZoom(false);
        if (pendingZoom.current?.categoryId === categoryId && pendingZoom.current.zoom === zoom) {
          pendingZoom.current = null;
          setFramingZoomDraft(null);
        }
      }
    },
    [qc],
  );
  const changeFramingZoom = (zoom: number) => {
    if (!activeCategory) return;
    if (zoom === activeCategory.framing_zoom) {
      if (zoomSaveTimer.current != null) window.clearTimeout(zoomSaveTimer.current);
      zoomSaveTimer.current = null;
      pendingZoom.current = null;
      setFramingZoomDraft(null);
      return;
    }
    setFramingZoomDraft(zoom);
    pendingZoom.current = { categoryId: activeCategory.id, zoom };
    if (zoomSaveTimer.current != null) window.clearTimeout(zoomSaveTimer.current);
    const categoryId = activeCategory.id;
    zoomSaveTimer.current = window.setTimeout(() => {
      zoomSaveTimer.current = null;
      void saveFramingZoom(categoryId, zoom);
    }, 500);
  };

  useEffect(() => {
    if (zoomSaveTimer.current != null) window.clearTimeout(zoomSaveTimer.current);
    zoomSaveTimer.current = null;
    pendingZoom.current = null;
    setFramingZoomDraft(null);
  }, [poseId]);
  useEffect(
    () => () => {
      if (zoomSaveTimer.current != null) window.clearTimeout(zoomSaveTimer.current);
    },
    [],
  );
  useEffect(() => {
    if (settings) {
      setFps(settings.playback_fps);
      setFade(settings.playback_crossfade);
    }
  }, [settings]);
  useEffect(() => {
    setIndex((i) => Math.min(i, Math.max(0, frames.length - 1)));
    setCompare((c) => Math.min(c, Math.max(0, frames.length - 1)));
  }, [frames.length]);
  useEffect(() => {
    // new pose: start at the latest photo, compare with the first
    setIndex(Math.max(0, frames.length - 1));
    setCompare(0);
  }, [poseId, group]); // eslint-disable-line react-hooks/exhaustive-deps

  usePreload(frames, index);

  // playback
  useEffect(() => {
    if (!playing || frames.length < 2) return;
    const t = window.setInterval(() => setIndex((i) => (i + 1) % frames.length), 1000 / fps);
    return () => window.clearInterval(t);
  }, [playing, fps, frames.length]);

  const step = useCallback((d: number) => setIndex((i) => Math.max(0, Math.min(frames.length - 1, i + d))), [frames.length]);
  useShortcuts(
    useMemo(
      () => ({
        ArrowLeft: (e: KeyboardEvent) => {
          e.preventDefault();
          setPlaying(false);
          step(e.shiftKey ? -10 : -1);
        },
        ArrowRight: (e: KeyboardEvent) => {
          e.preventDefault();
          setPlaying(false);
          step(e.shiftKey ? 10 : 1);
        },
        " ": (e: KeyboardEvent) => {
          e.preventDefault();
          setPlaying((p) => !p);
        },
        Home: () => setIndex(0),
        End: () => setIndex(Math.max(0, frames.length - 1)),
        "[": () => setCompare(index),
        "]": () => setCompare(index),
        o: () => setMode((m) => (m === "onion" ? "single" : "onion")),
        s: () => setMode((m) => (m === "split" ? "single" : "split")),
      }),
      [step, frames.length, index],
    ),
  );

  const metrics = useQuery({ queryKey: ["metrics"], queryFn: () => get<Metric[]>("/metrics") });
  const activeMetric = metrics.data?.find((m) => m.id === metricId) ?? metrics.data?.find((m) => (m.entry_count ?? 0) > 0) ?? null;
  const entries = useQuery({
    queryKey: ["metrics", "entries", activeMetric?.id],
    queryFn: () => get<MetricEntry[]>("/metrics/entries", { metric_id: activeMetric!.id }),
    enabled: !!activeMetric,
  });

  const cur = frames[index];
  const cmp = frames[compare];
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: true });
  };

  // split-view divider drag
  const dragSplit = (e: React.PointerEvent) => {
    const el = stageRef.current;
    if (!el) return;
    (e.target as Element).setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) => {
      const r = el.getBoundingClientRect();
      setSplit(Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const cw = seq.data?.canvas_w ?? 1080;
  const ch = seq.data?.canvas_h ?? 1350;
  const fadeMs = fade && playing ? Math.min(400, 800 / fps) : 0;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Viewer"
        subtitle={
          seq.data
            ? `${frames.length} photos${seq.data.unaligned ? ` · ${seq.data.unaligned} not aligned (showing unaligned preview)` : ""}${
                seq.data.without_date ? ` · ${seq.data.without_date} without a date are left out` : ""
              }`
            : ""
        }
      >
        <select className="select max-w-full" value={group ?? poseId ?? ""} onChange={(e) => set("pose_id", e.target.value)}>
          {cats.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} ({c.photo_count})
            </option>
          ))}
          <option value="abs">Abs ({dash?.abs.visible ?? 0})</option>
          <option value="face">Face ({dash?.face.visible ?? 0})</option>
        </select>
        <button
          className={`btn ${hideSleeves ? "btn-primary" : ""}`}
          onClick={() => setHideSleeves((v) => !v)}
          aria-pressed={hideSleeves}
          title={
            hideSleeves
              ? "showing only photos with bare arms (click to show all)"
              : "hide photos where a sleeve or other clothing covers the arm"
          }
        >
          <Shirt size={14} /> <span className="hidden sm:inline">{hideSleeves ? `Sleeves hidden (${sleeveCount})` : "Hide sleeves"}</span>
        </button>
        <input type="date" className="input hidden md:block" value={from} onChange={(e) => set("from", e.target.value)} title="from" />
        <input type="date" className="input hidden md:block" value={to} onChange={(e) => set("to", e.target.value)} title="to" />
        <button className={`btn md:hidden ${panel ? "btn-primary" : ""}`} onClick={() => setPanel((v) => !v)} title="modes, playback, measurements">
          <SlidersHorizontal size={14} />
        </button>
        {!isAbs && seq.data && seq.data.unaligned > 0 && (
          <button className="btn" onClick={() => run.mutate({ path: `/poses/${poseId}/align` })}>
            <Crosshair size={14} /> Align
          </button>
        )}
        <button className="btn btn-primary" disabled={frames.length === 0} onClick={() => setExportOpen(true)}>
          <Film size={14} /> Export
        </button>
      </PageHeader>

      {seq.isLoading ? (
        <div className="flex items-center gap-2 p-6 text-sm text-muted">
          <Spinner /> loading…
        </div>
      ) : frames.length === 0 ? (
        <div className="p-3 md:p-6">
          {group === "face" ? (
            <Empty title="No face close-ups yet">
              Photos are checked for a visible face automatically when poses are classified. You can also add photos by hand with the "Face visible" switch in the Library photo drawer.
            </Empty>
          ) : isAbs ? (
            <Empty title="No photos with visible abs yet">
              Photos are checked for visible abs automatically when poses are classified. You can also mark photos by hand with the "Abs visible" switch in the Library photo drawer.
            </Empty>
          ) : (
            <Empty title="No dated photos in this pose yet">Assign photos to this pose in the Library, give them dates, and run alignment.</Empty>
          )}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto md:flex-row md:overflow-visible">
          <div className="flex min-h-[60%] min-w-0 flex-1 flex-col md:min-h-0">
            <div
              className="checker flex min-h-0 flex-1 touch-pan-y items-center justify-center p-1 md:p-3"
              onPointerDown={(e) => {
                if (e.pointerType === "touch" && mode !== "split") swipe.current = { x: e.clientX, y: e.clientY };
              }}
              onPointerUp={(e) => {
                const s = swipe.current;
                swipe.current = null;
                if (!s) return;
                const dx = e.clientX - s.x;
                if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(e.clientY - s.y)) {
                  setPlaying(false);
                  step(dx < 0 ? 1 : -1); // swipe left = next photo
                }
              }}
              onPointerCancel={() => (swipe.current = null)}
            >
              <div
                ref={stageRef}
                className="relative h-full max-h-full overflow-hidden bg-[#18181b] shadow-2xl"
                style={{ aspectRatio: `${cw} / ${ch}` }}
              >
                {/* base layer: the current frame; previous frame fades out underneath while playing */}
                {frames.map((f, i) => {
                  const visible = i === index;
                  if (!visible && !(fadeMs > 0 && Math.abs(i - index) <= 1)) return null;
                  return (
                    <img
                      key={f.id}
                      src={frameSrc(f)}
                      alt=""
                      draggable={false}
                      className="absolute inset-0 h-full w-full object-contain"
                      style={{
                        opacity: visible ? 1 : 0,
                        transition: fadeMs ? `opacity ${fadeMs}ms linear` : undefined,
                        transform: !f.aligned_url && f.flip_horizontal ? "scaleX(-1)" : undefined,
                      }}
                    />
                  );
                })}
                {mode === "onion" && cmp && (
                  <img
                    src={frameSrc(cmp)}
                    alt=""
                    className="pointer-events-none absolute inset-0 h-full w-full object-contain"
                    style={{ opacity }}
                    draggable={false}
                  />
                )}
                {mode === "split" && cmp && (
                  <>
                    <img
                      src={frameSrc(cmp)}
                      alt=""
                      draggable={false}
                      className="pointer-events-none absolute inset-0 h-full w-full object-contain"
                      style={{ clipPath: `inset(0 ${100 - split * 100}% 0 0)` }}
                    />
                    <div className="absolute inset-y-0 w-1 -translate-x-1/2 cursor-ew-resize bg-white/80" style={{ left: `${split * 100}%` }} onPointerDown={dragSplit} />
                    <div className="absolute left-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-xs">{fmtDate(cmp.taken_at)}</div>
                    <div className="absolute right-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-xs">{fmtDate(cur?.taken_at)}</div>
                  </>
                )}
                {cur && Object.keys(cur.flags).some((k) => cur.flags[k]) && (
                  <div className="absolute bottom-2 left-2 rounded bg-black/60 px-1.5 py-0.5 text-[11px] text-warn">
                    {Object.keys(cur.flags)
                      .filter((k) => cur.flags[k])
                      .map((k) => k.replace(/_/g, " "))
                      .join(" · ")}
                  </div>
                )}
              </div>
            </div>

            {/* transport */}
            <div className="border-t border-border bg-panel px-2 py-2 md:px-4">
              <div className="mb-1 text-center text-sm tabular-nums md:hidden">
                <span className="font-medium">{fmtDate(cur?.taken_at)}</span>
                <span className="text-muted"> · day {cur?.days_since_start ?? "–"} · {index + 1}/{frames.length}</span>
              </div>
              <div className="flex items-center gap-1 md:gap-2">
                <button className="btn btn-sm hidden sm:inline-flex" onClick={() => setIndex(0)} title="first (Home)">
                  <ChevronFirst size={14} />
                </button>
                <button className="btn btn-sm" onClick={() => step(-1)} title="previous (←)">
                  <StepBack size={14} />
                </button>
                <button className="btn btn-sm btn-primary w-16 justify-center" onClick={() => setPlaying((p) => !p)} title="play / pause (Space)">
                  {playing ? <Pause size={14} /> : <Play size={14} />}
                </button>
                <button className="btn btn-sm" onClick={() => step(1)} title="next (→)">
                  <StepForward size={14} />
                </button>
                <button className="btn btn-sm hidden sm:inline-flex" onClick={() => setIndex(frames.length - 1)} title="last (End)">
                  <ChevronLast size={14} />
                </button>
                <input
                  type="range"
                  className="mx-1 h-8 min-w-0 flex-1 md:mx-2"
                  min={0}
                  max={frames.length - 1}
                  value={index}
                  onChange={(e) => {
                    setPlaying(false);
                    setIndex(Number(e.target.value));
                  }}
                />
                <div className="hidden w-64 shrink-0 text-right text-sm tabular-nums md:block">
                  <span className="font-medium">{fmtDate(cur?.taken_at)}</span>
                  <span className="text-muted"> · day {cur?.days_since_start ?? "–"} · {index + 1}/{frames.length}</span>
                </div>
              </div>
              <div className="mt-2">
                <StatsStrip frame={cur} />
              </div>
              {cur && Object.keys(cur.measurements ?? {}).length > 0 && (
                <div className="mt-1 flex flex-wrap gap-x-5 text-xs text-muted" title="estimated from this photo: widths as % of torso length (Stats → From photos)">
                  {(
                    [
                      ["shoulders", "shoulders"],
                      ["lats", "lats"],
                      ["waist", "waist"],
                      ["vtaper", "V-taper"],
                      ["arm", "upper arm"],
                      ["forearm", "forearm"],
                    ] as const
                  )
                    .filter(([k]) => cur.measurements[k] != null)
                    .map(([k, label]) => (
                      <span key={k}>
                        {label} <span className="tabular-nums text-text">{fmtRatio(k, cur.measurements[k])}</span>
                      </span>
                    ))}
                </div>
              )}
            </div>
          </div>

          {/* side panel */}
          <aside className={`${panel ? "flex" : "hidden"} w-full shrink-0 flex-col gap-4 border-t border-border bg-panel p-4 text-sm md:flex md:w-80 md:overflow-y-auto md:border-l md:border-t-0`}>
            <div>
              <div className="label mb-1.5">Mode</div>
              <div className="flex gap-1">
                {(
                  [
                    ["single", Square, "Single"],
                    ["onion", Layers, "Onion (O)"],
                    ["split", Columns2, "Split (S)"],
                  ] as const
                ).map(([m, Icon, label]) => (
                  <button key={m} className={`btn btn-sm flex-1 justify-center ${mode === m ? "btn-primary" : ""}`} onClick={() => setMode(m)}>
                    <Icon size={13} /> {label}
                  </button>
                ))}
              </div>
            </div>
            {mode !== "single" && (
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <span className="label">Compare with</span>
                  <button className="btn btn-sm ml-auto" onClick={() => setCompare(index)} title="[ or ]">
                    use current
                  </button>
                </div>
                <select className="select" value={compare} onChange={(e) => setCompare(Number(e.target.value))}>
                  {frames.map((f, i) => (
                    <option key={f.id} value={i}>
                      {i + 1}. {fmtDate(f.taken_at)} (day {f.days_since_start})
                    </option>
                  ))}
                </select>
                {mode === "onion" && (
                  <label className="flex flex-col gap-1">
                    <span className="label">Overlay opacity {Math.round(opacity * 100)}%</span>
                    <input type="range" min={0} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} />
                  </label>
                )}
              </div>
            )}
            {!isAbs && (
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <span className="label">Group framing</span>
                  {savingFramingZoom ? <Spinner size={12} /> : <span className="ml-auto tabular-nums text-muted">{Math.round(framingZoom * 100)}%</span>}
                  {framingZoom !== 1 && (
                    <button className="btn btn-sm" disabled={savingFramingZoom} onClick={() => changeFramingZoom(1)}>
                      Reset
                    </button>
                  )}
                </div>
                <input
                  type="range"
                  min={0.25}
                  max={4}
                  step={0.05}
                  value={framingZoom}
                  onChange={(e) => changeFramingZoom(Number(e.target.value))}
                  disabled={!activeCategory || savingFramingZoom}
                  aria-label="group framing zoom"
                />
                <span className="text-[11px] text-muted">Updates the aligned composition for every photo in this group.</span>
              </div>
            )}
            {isAbs && (
              <div className="text-[11px] text-muted">
                {group === "face"
                  ? "Each frame is a face close-up scaled and rotated (never stretched) so your eyes and mouth land in the same spots, across every pose type."
                  : "Each frame is a torso close-up scaled and rotated (never stretched) so your shoulders and hips land in the same spots, which lines up the abs across every pose type."}{" "}
                {cur && (
                  <Link to={`/library?photo=${cur.id}`} className="underline">
                    Open this photo
                  </Link>
                )}
              </div>
            )}
            <div className={`flex flex-col gap-1.5 ${isAbs ? "hidden" : ""}`}>
              <span className="label">Pose match</span>
              <select className="select" value={maxDiff ?? ""} onChange={(e) => setMaxDiff(e.target.value === "" ? null : Number(e.target.value))}>
                <option value="">all photos</option>
                <option value="12">close (joints within 12% of torso length)</option>
                <option value="8">closer (within 8%)</option>
                <option value="5">very close (within 5%)</option>
                <option value="3">nearly identical (within 3%)</option>
              </select>
              <span className="text-[11px] text-muted">
                {hidden > 0 ? `${hidden} photo${hidden === 1 ? "" : "s"} hidden because the arms are posed differently from the reference. ` : ""}
                Measures how far shoulders, elbows and wrists land from the reference after alignment.
                {cur?.pose_diff_pct != null ? ` This photo: ${cur.pose_diff_pct}%.` : ""}
              </span>
            </div>
            <div className="flex flex-col gap-2">
              <span className="label">Playback</span>
              <label className="flex items-center gap-2">
                <span className="w-20 text-muted">{fps} fps</span>
                <input type="range" min={0.5} max={10} step={0.5} value={fps} onChange={(e) => setFps(Number(e.target.value))} className="flex-1" />
              </label>
              <Toggle checked={fade} onChange={setFade} label="Crossfade while playing" />
            </div>
            {cur && !isAbs && (
              <div className="flex gap-1.5">
                <Link to={`/editor/${cur.id}`} className="btn btn-sm">
                  <Pencil size={13} /> Edit alignment
                </Link>
              </div>
            )}
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="label">Measurements</span>
                {(metrics.data?.length ?? 0) > 1 && (
                  <select className="select ml-auto py-0.5 text-xs" value={activeMetric?.id ?? ""} onChange={(e) => setMetricId(Number(e.target.value))}>
                    {metrics.data!.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              {activeMetric && (entries.data?.length ?? 0) > 0 ? (
                <MetricChart
                  title={`${activeMetric.name} (${activeMetric.unit})`}
                  points={entries.data!.map((e) => ({ date: e.date, value: e.value }))}
                  unit={activeMetric.unit}
                  decimals={activeMetric.decimals}
                  markerDate={cur?.taken_at ? toDateValue(cur.taken_at) : null}
                  height={150}
                  onPick={(d) => {
                    // jump to the photo closest to the clicked date
                    const t = new Date(d).getTime();
                    let best = 0;
                    frames.forEach((f, i) => {
                      if (f.taken_at && Math.abs(new Date(f.taken_at).getTime() - t) < Math.abs(new Date(frames[best].taken_at!).getTime() - t)) best = i;
                    });
                    setPlaying(false);
                    setIndex(best);
                  }}
                />
              ) : (
                <div className="text-xs text-muted">
                  No measurements yet. Add them on the{" "}
                  <Link to="/stats" className="underline">
                    Stats
                  </Link>{" "}
                  page.
                </div>
              )}
            </div>
            <div className="mt-auto text-[11px] leading-relaxed text-muted">
              <span className="kbd">←</span> <span className="kbd">→</span> step (Shift ×10) · <span className="kbd">Space</span> play · <span className="kbd">Home</span>/
              <span className="kbd">End</span> · <span className="kbd">[</span> set comparison · <span className="kbd">O</span> onion · <span className="kbd">S</span> split
            </div>
          </aside>
        </div>
      )}
      {(poseId || isAbs) && (
        <ExportDialog
          open={exportOpen}
          onClose={() => setExportOpen(false)}
          poseId={poseId ?? null}
          group={group}
          from={from}
          to={to}
          defaultFps={fps}
          maxPoseDiff={isAbs ? null : maxDiff}
          hideSleeves={hideSleeves}
          frames={exportFrames}
          canvasW={seq.data?.canvas_w}
          canvasH={seq.data?.canvas_h}
        />
      )}
    </div>
  );
}
