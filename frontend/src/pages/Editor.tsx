import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronLeft, ChevronRight, RotateCcw, RotateCw, Save, Undo2 } from "lucide-react";
import { del, get, put } from "../api/client";
import type { AlignmentInfo, Page, Photo } from "../api/types";
import { Empty, PageHeader, Spinner, Toggle } from "../components/ui";
import { toast } from "../lib/toast";
import { useShortcuts } from "../lib/keyboard";
import { fmtDate } from "../lib/format";

interface EditorContext {
  photo_id: number;
  width: number;
  height: number;
  flip_horizontal: boolean;
  category: { id: number; name: string };
  reference_photo_id: number;
  is_reference: boolean;
  canvas_w: number;
  canvas_h: number;
  target: { head: [number, number]; hip: [number, number]; shoulder: [number, number] };
  source_anchors: { head: [number, number]; hip: [number, number]; shoulder: [number, number] } | null;
  auto: { scale: number; rotation_deg: number; tx: number; ty: number; flags: Record<string, unknown> };
  current: AlignmentInfo | null;
  reference_aligned_url: string;
}

type T = { scale: number; rotation_deg: number; tx: number; ty: number };

const rad = (d: number) => (d * Math.PI) / 180;
function apply(t: T, p: [number, number]): [number, number] {
  const c = Math.cos(rad(t.rotation_deg)) * t.scale;
  const s = Math.sin(rad(t.rotation_deg)) * t.scale;
  return [c * p[0] - s * p[1] + t.tx, s * p[0] + c * p[1] + t.ty];
}
function invert(t: T, q: [number, number]): [number, number] {
  const x = q[0] - t.tx;
  const y = q[1] - t.ty;
  const c = Math.cos(rad(t.rotation_deg)) / t.scale;
  const s = Math.sin(rad(t.rotation_deg)) / t.scale;
  return [c * x + s * y, -s * x + c * y];
}
/** rotate/scale about a canvas point so that point stays fixed */
function about(t: T, pivot: [number, number], dScale: number, dRot: number): T {
  const scale = t.scale * dScale;
  const rotation_deg = t.rotation_deg + dRot;
  const src = invert(t, pivot);
  const moved = apply({ scale, rotation_deg, tx: 0, ty: 0 }, src);
  return { scale, rotation_deg, tx: pivot[0] - moved[0], ty: pivot[1] - moved[1] };
}

export default function Editor() {
  const { photoId } = useParams();
  const id = Number(photoId);
  const nav = useNavigate();
  const qc = useQueryClient();
  const ctxQ = useQuery({ queryKey: ["editor", id], queryFn: () => get<EditorContext>(`/alignments/${id}/context`), retry: false });
  const ctx = ctxQ.data;
  const siblings = useQuery({
    queryKey: ["editor-siblings", ctx?.category.id],
    queryFn: () => get<Page<Photo>>("/photos", { pose_id: ctx!.category.id, sort: "taken_at_asc", page_size: 1000 }),
    enabled: !!ctx,
  });

  const [t, setT] = useState<T | null>(null);
  const [opacity, setOpacity] = useState(0.5);
  const [ghost, setGhost] = useState(true);
  const [before, setBefore] = useState(false);
  const [showAnchors, setShowAnchors] = useState(true);
  const [busy, setBusy] = useState(false);
  const [anchors, setAnchors] = useState<{ head: [number, number]; hip: [number, number] } | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const [k, setK] = useState(0.5);

  useEffect(() => {
    if (!ctx) return;
    const c = ctx.current && !ctx.current.stale ? ctx.current : ctx.auto;
    setT({ scale: c.scale, rotation_deg: c.rotation_deg, tx: c.tx, ty: c.ty });
    setAnchors(ctx.source_anchors ? { head: ctx.source_anchors.head, hip: ctx.source_anchors.hip } : null);
  }, [ctx]);

  // fit the canvas into the viewport
  useEffect(() => {
    const el = stageRef.current?.parentElement;
    if (!el || !ctx) return;
    const fit = () => {
      const r = el.getBoundingClientRect();
      setK(Math.min((r.width - 24) / ctx.canvas_w, (r.height - 24) / ctx.canvas_h));
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ctx]);

  const dirty = useMemo(() => {
    if (!ctx || !t) return false;
    const c = ctx.current && !ctx.current.stale ? ctx.current : ctx.auto;
    return Math.abs(c.scale - t.scale) > 1e-6 || Math.abs(c.rotation_deg - t.rotation_deg) > 1e-6 || Math.abs(c.tx - t.tx) > 0.01 || Math.abs(c.ty - t.ty) > 0.01;
  }, [ctx, t]);

  const refresh = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["editor", id] });
    qc.invalidateQueries({ queryKey: ["photo", id] });
    qc.invalidateQueries({ queryKey: ["photos"] });
    qc.invalidateQueries({ queryKey: ["sequence"] });
  }, [qc, id]);

  const save = async () => {
    if (!t) return;
    setBusy(true);
    try {
      await put(`/alignments/${id}`, t);
      toast.success("Alignment saved");
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const saveAnchors = async (a: { head: [number, number]; hip: [number, number] }) => {
    setBusy(true);
    try {
      const r = await put<AlignmentInfo>(`/alignments/${id}`, a);
      setT({ scale: r.scale, rotation_deg: r.rotation_deg, tx: r.tx, ty: r.ty });
      toast.success("Re-solved from corrected anchors");
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const reset = async () => {
    setBusy(true);
    try {
      const r = await del<AlignmentInfo>(`/alignments/${id}`);
      setT({ scale: r.scale, rotation_deg: r.rotation_deg, tx: r.tx, ty: r.ty });
      toast.info("Back to automatic alignment");
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const list = siblings.data?.items ?? [];
  const idx = list.findIndex((p) => p.id === id);
  const go = (d: number) => {
    const p = list[idx + d];
    if (p) nav(`/editor/${p.id}`);
  };

  const nudge = useCallback(
    (fn: (t: T) => T) =>
      setT((cur) => (cur ? fn(cur) : cur)),
    [],
  );
  const centre = (): [number, number] => (ctx ? (ctx.target.head as [number, number]) : [0, 0]);
  useShortcuts(
    useMemo(
      () => ({
        ArrowLeft: (e: KeyboardEvent) => nudge((c) => ({ ...c, tx: c.tx - (e.shiftKey ? 0.5 : 4) })),
        ArrowRight: (e: KeyboardEvent) => nudge((c) => ({ ...c, tx: c.tx + (e.shiftKey ? 0.5 : 4) })),
        ArrowUp: (e: KeyboardEvent) => {
          e.preventDefault();
          nudge((c) => ({ ...c, ty: c.ty - (e.shiftKey ? 0.5 : 4) }));
        },
        ArrowDown: (e: KeyboardEvent) => {
          e.preventDefault();
          nudge((c) => ({ ...c, ty: c.ty + (e.shiftKey ? 0.5 : 4) }));
        },
        q: (e: KeyboardEvent) => nudge((c) => about(c, centre(), 1, e.shiftKey ? -0.1 : -0.5)),
        e: (e: KeyboardEvent) => nudge((c) => about(c, centre(), 1, e.shiftKey ? 0.1 : 0.5)),
        "ctrl+s": (e: KeyboardEvent) => {
          e.preventDefault();
          save();
        },
        b: () => setBefore((b) => !b),
        g: () => setGhost((g) => !g),
        "[": () => go(-1),
        "]": () => go(1),
      }),
      [ctx, list, idx, t], // eslint-disable-line react-hooks/exhaustive-deps
    ),
  );

  // ---- pointer interaction ----
  const drag = useRef<{ kind: "move" | "head" | "hip"; x: number; y: number; t: T } | null>(null);
  const toCanvas = (e: React.PointerEvent | React.WheelEvent): [number, number] => {
    const r = stageRef.current!.getBoundingClientRect();
    return [(e.clientX - r.left) / k, (e.clientY - r.top) / k];
  };
  const onPointerDown = (e: React.PointerEvent, kind: "move" | "head" | "hip" = "move") => {
    if (!t || before) return;
    e.stopPropagation();
    (e.target as Element).setPointerCapture(e.pointerId);
    const [x, y] = toCanvas(e);
    drag.current = { kind, x, y, t };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const [x, y] = toCanvas(e);
    const f = e.shiftKey ? 0.2 : 1;
    if (d.kind === "move") {
      setT({ ...d.t, tx: d.t.tx + (x - d.x) * f, ty: d.t.ty + (y - d.y) * f });
    } else if (anchors && t) {
      const src = invert(t, [x, y]);
      setAnchors({ ...anchors, [d.kind]: src });
    }
  };
  const onPointerUp = () => {
    const d = drag.current;
    drag.current = null;
    if (d && d.kind !== "move" && anchors) saveAnchors(anchors);
  };
  const onWheel = (e: React.WheelEvent) => {
    if (!t || before) return;
    const pivot = toCanvas(e);
    const fine = e.shiftKey ? 0.2 : 1;
    if (e.altKey) {
      setT(about(t, pivot, 1, (e.deltaY > 0 ? 0.5 : -0.5) * fine));
    } else {
      setT(about(t, pivot, Math.pow(1.0015, -e.deltaY * fine), 0));
    }
  };

  if (ctxQ.isError) {
    return (
      <div>
        <PageHeader title="Alignment editor" />
        <div className="p-6">
          <Empty title="Cannot edit this photo's alignment">{(ctxQ.error as Error).message}</Empty>
        </div>
      </div>
    );
  }
  if (!ctx || !t) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted">
        <Spinner /> loading…
      </div>
    );
  }

  const c = Math.cos(rad(t.rotation_deg)) * t.scale;
  const s = Math.sin(rad(t.rotation_deg)) * t.scale;
  const flip = ctx.flip_horizontal ? ` translate(${ctx.width}px,0) scale(-1,1)` : "";
  const photoTransform = before
    ? (() => {
        const f = Math.min(ctx.canvas_w / ctx.width, ctx.canvas_h / ctx.height);
        return `translate(${(ctx.canvas_w - f * ctx.width) / 2}px, ${(ctx.canvas_h - f * ctx.height) / 2}px) scale(${f})${flip}`;
      })()
    : `matrix(${c}, ${s}, ${-s}, ${c}, ${t.tx}, ${t.ty})${flip}`;
  const head = anchors ? apply(t, anchors.head) : null;
  const hip = anchors ? apply(t, anchors.hip) : null;
  const flags = ctx.current?.flags ? Object.keys(ctx.current.flags).filter((f) => ctx.current!.flags[f]) : [];
  const me = list[idx];

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Alignment editor"
        subtitle={`${ctx.category.name} · ${me ? fmtDate(me.taken_at) : ""} · ${idx >= 0 ? `${idx + 1} / ${list.length}` : ""}${ctx.is_reference ? " · reference" : ""}`}
      >
        <Link to="/library" className="btn btn-ghost">
          <ArrowLeft size={14} /> Library
        </Link>
        <button className="btn" disabled={idx <= 0} onClick={() => go(-1)} title="previous ([)">
          <ChevronLeft size={14} />
        </button>
        <button className="btn" disabled={idx < 0 || idx >= list.length - 1} onClick={() => go(1)} title="next (])">
          <ChevronRight size={14} />
        </button>
        <button className="btn" onClick={reset} disabled={busy} title="discard manual changes, re-run automatic alignment">
          <Undo2 size={14} /> Auto
        </button>
        <button className="btn btn-primary" onClick={save} disabled={!dirty || busy} title="Ctrl+S">
          <Save size={14} /> Save
        </button>
      </PageHeader>
      <div className="flex min-h-0 flex-1">
        <div className="checker relative flex min-w-0 flex-1 items-center justify-center overflow-hidden" onWheel={onWheel}>
          <div
            ref={stageRef}
            className="relative overflow-hidden bg-[#18181b] shadow-2xl"
            style={{ width: ctx.canvas_w * k, height: ctx.canvas_h * k, cursor: before ? "default" : "grab" }}
            onPointerDown={(e) => onPointerDown(e, "move")}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
          >
            <div className="absolute left-0 top-0 origin-top-left" style={{ width: ctx.canvas_w, height: ctx.canvas_h, transform: `scale(${k})` }}>
              <img
                src={`/api/photos/${id}/preview`}
                alt=""
                draggable={false}
                className="absolute left-0 top-0 max-w-none origin-top-left select-none"
                style={{ width: ctx.width, height: ctx.height, transform: photoTransform, transformOrigin: "0 0" }}
              />
              {ghost && !ctx.is_reference && (
                <img
                  src={`${ctx.reference_aligned_url}?v=${ctx.reference_photo_id}`}
                  alt=""
                  draggable={false}
                  className="pointer-events-none absolute left-0 top-0 max-w-none select-none"
                  style={{ width: ctx.canvas_w, height: ctx.canvas_h, opacity }}
                  onError={(e) => ((e.target as HTMLImageElement).style.display = "none")}
                />
              )}
            </div>
            {/* overlays in screen px */}
            {showAnchors && (
              <svg className="pointer-events-none absolute inset-0 h-full w-full">
                {(["head", "hip", "shoulder"] as const).map((n) => (
                  <g key={n} transform={`translate(${ctx.target[n][0] * k}, ${ctx.target[n][1] * k})`}>
                    <line x1={-10} x2={10} y1={0} y2={0} stroke="#fbbf24" strokeWidth={1.5} />
                    <line y1={-10} y2={10} x1={0} x2={0} stroke="#fbbf24" strokeWidth={1.5} />
                  </g>
                ))}
                <line
                  x1={ctx.target.head[0] * k}
                  y1={ctx.target.head[1] * k}
                  x2={ctx.target.hip[0] * k}
                  y2={ctx.target.hip[1] * k}
                  stroke="#fbbf24"
                  strokeDasharray="4 4"
                  strokeOpacity={0.6}
                />
              </svg>
            )}
            {showAnchors && !before && head && hip && (
              <>
                {(["head", "hip"] as const).map((n) => {
                  const p = n === "head" ? head : hip;
                  return (
                    <div
                      key={n}
                      title={`drag to the true ${n === "head" ? "head centre" : "hip centre"} in this photo`}
                      className="absolute h-4 w-4 -translate-x-1/2 -translate-y-1/2 cursor-move rounded-full border-2 border-white bg-accent-2 shadow"
                      style={{ left: p[0] * k, top: p[1] * k }}
                      onPointerDown={(e) => onPointerDown(e, n)}
                    />
                  );
                })}
              </>
            )}
          </div>
        </div>
        <aside className="flex w-72 shrink-0 flex-col gap-4 overflow-y-auto border-l border-border bg-panel p-4 text-sm">
          <div className="flex flex-col gap-2">
            <Toggle checked={ghost} onChange={setGhost} label="Reference ghost (G)" />
            <input type="range" min={0} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} disabled={!ghost} />
            <Toggle checked={before} onChange={setBefore} label="Show unaligned (B)" />
            <Toggle checked={showAnchors} onChange={setShowAnchors} label="Anchors and targets" />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1">
              <span className="label">Scale</span>
              <input type="number" step="0.001" className="input" value={t.scale.toFixed(4)} onChange={(e) => setT({ ...t, scale: Math.max(0.01, Number(e.target.value)) })} />
            </label>
            <label className="flex flex-col gap-1">
              <span className="label">Rotation °</span>
              <input type="number" step="0.1" className="input" value={t.rotation_deg.toFixed(2)} onChange={(e) => setT(about(t, centre(), 1, Number(e.target.value) - t.rotation_deg))} />
            </label>
            <label className="flex flex-col gap-1">
              <span className="label">X</span>
              <input type="number" step="1" className="input" value={t.tx.toFixed(1)} onChange={(e) => setT({ ...t, tx: Number(e.target.value) })} />
            </label>
            <label className="flex flex-col gap-1">
              <span className="label">Y</span>
              <input type="number" step="1" className="input" value={t.ty.toFixed(1)} onChange={(e) => setT({ ...t, ty: Number(e.target.value) })} />
            </label>
          </div>
          <div className="flex gap-1">
            <button className="btn btn-sm flex-1 justify-center" onClick={() => setT(about(t, centre(), 1, -0.5))} title="Q (Shift = fine)">
              <RotateCcw size={13} /> −0.5°
            </button>
            <button className="btn btn-sm flex-1 justify-center" onClick={() => setT(about(t, centre(), 1, 0.5))} title="E (Shift = fine)">
              <RotateCw size={13} /> +0.5°
            </button>
          </div>
          <div className="flex flex-col gap-1 text-xs text-muted">
            <div>
              Status: <span className="text-text">{ctx.current ? ctx.current.method : "not aligned"}</span>
              {ctx.current?.stale && <span className="text-warn"> · out of date</span>}
              {ctx.current?.residual_px != null && <span> · residual {ctx.current.residual_px.toFixed(1)} px</span>}
              {ctx.current?.joint_residual_px != null && <span> · joints off by {ctx.current.joint_residual_px.toFixed(0)} px</span>}
            </div>
            {flags.length > 0 && <div className="text-warn">{flags.map((f) => f.replace(/_/g, " ")).join(", ")}</div>}
          </div>
          <div className="text-[11px] leading-relaxed text-muted">
            Drag the photo to move it, wheel to zoom, Alt+wheel or Q/E to rotate, arrows to nudge. Hold Shift for fine control. Yellow crosses are the
            reference's head, shoulder and hip targets. Drag the blue dots onto the true head and hip centre of this photo to re-solve.
          </div>
        </aside>
      </div>
    </div>
  );
}
