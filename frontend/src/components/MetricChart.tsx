import { useEffect, useMemo, useRef, useState } from "react";
import { fmtDate, parseLocal } from "../lib/format";

export interface Point {
  date: string; // YYYY-MM-DD
  value: number;
}

const PAD = { l: 44, r: 12, t: 10, b: 22 };

function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) return [min];
  const span = max - min;
  const step0 = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) ?? step0;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

/** Single-series line chart with hover crosshair + tooltip and an optional synced marker date. */
export default function MetricChart({
  points,
  unit,
  decimals = 1,
  markerDate,
  height = 160,
  onPick,
  title,
}: {
  points: Point[];
  unit: string;
  decimals?: number;
  markerDate?: string | null;
  height?: number;
  onPick?: (date: string) => void;
  title?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(600);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const data = useMemo(
    () =>
      [...points]
        .map((p) => ({ ...p, t: parseLocal(p.date)!.getTime() }))
        .sort((a, b) => a.t - b.t),
    [points],
  );
  if (data.length === 0) {
    return (
      <div ref={ref} className="flex items-center justify-center text-xs text-muted" style={{ height }}>
        no entries yet
      </div>
    );
  }
  const markerT = markerDate ? parseLocal(markerDate)?.getTime() ?? null : null;
  let t0 = data[0].t;
  let t1 = data[data.length - 1].t;
  if (markerT != null) {
    t0 = Math.min(t0, markerT);
    t1 = Math.max(t1, markerT);
  }
  if (t1 === t0) t1 = t0 + 86_400_000;
  let vmin = Math.min(...data.map((d) => d.value));
  let vmax = Math.max(...data.map((d) => d.value));
  const padV = (vmax - vmin || Math.abs(vmax) * 0.05 || 1) * 0.12;
  vmin -= padV;
  vmax += padV;
  const iw = Math.max(10, w - PAD.l - PAD.r);
  const ih = height - PAD.t - PAD.b;
  const x = (t: number) => PAD.l + ((t - t0) / (t1 - t0)) * iw;
  const y = (v: number) => PAD.t + (1 - (v - vmin) / (vmax - vmin)) * ih;
  const path = data.map((d, i) => `${i ? "L" : "M"}${x(d.t).toFixed(1)},${y(d.value).toFixed(1)}`).join("");
  const ticks = niceTicks(vmin, vmax);
  const showDots = data.length <= 60;
  const h = hover != null ? data[hover] : null;

  const nearestIdx = (px: number) => {
    const t = t0 + ((px - PAD.l) / iw) * (t1 - t0);
    let best = 0;
    for (let i = 1; i < data.length; i++) if (Math.abs(data[i].t - t) < Math.abs(data[best].t - t)) best = i;
    return best;
  };

  return (
    <div ref={ref} className="relative w-full select-none">
      {title && <div className="mb-1 text-xs font-medium text-muted">{title}</div>}
      <svg
        width={w}
        height={height}
        className="block"
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          setHover(nearestIdx(e.clientX - r.left));
        }}
        onMouseLeave={() => setHover(null)}
        onClick={(e) => {
          if (!onPick) return;
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          onPick(data[nearestIdx(e.clientX - r.left)].date);
        }}
        style={{ cursor: onPick ? "pointer" : "default" }}
        role="img"
        aria-label={`${title ?? "metric"} over time`}
      >
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={PAD.l + iw} y1={y(v)} y2={y(v)} stroke="var(--color-border)" strokeWidth={1} />
            <text x={PAD.l - 6} y={y(v) + 3} textAnchor="end" fontSize={10} fill="var(--color-muted)">
              {v.toFixed(Math.max(0, decimals - 1))}
            </text>
          </g>
        ))}
        <text x={PAD.l} y={height - 6} fontSize={10} fill="var(--color-muted)">
          {fmtDate(new Date(t0).toISOString().slice(0, 10))}
        </text>
        <text x={PAD.l + iw} y={height - 6} fontSize={10} fill="var(--color-muted)" textAnchor="end">
          {fmtDate(new Date(t1).toISOString().slice(0, 10))}
        </text>
        <path d={path} fill="none" stroke="var(--color-accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {showDots &&
          data.map((d) => <circle key={d.date} cx={x(d.t)} cy={y(d.value)} r={3} fill="var(--color-accent)" stroke="var(--color-panel)" strokeWidth={2} />)}
        {markerT != null && (
          <line x1={x(markerT)} x2={x(markerT)} y1={PAD.t} y2={PAD.t + ih} stroke="var(--color-warn)" strokeWidth={2} strokeDasharray="3 3" />
        )}
        {h && (
          <g pointerEvents="none">
            <line x1={x(h.t)} x2={x(h.t)} y1={PAD.t} y2={PAD.t + ih} stroke="var(--color-muted)" strokeWidth={1} />
            <circle cx={x(h.t)} cy={y(h.value)} r={5} fill="var(--color-accent)" stroke="var(--color-panel)" strokeWidth={2} />
          </g>
        )}
      </svg>
      {h && (
        <div
          className="pointer-events-none absolute rounded border border-border bg-panel-2 px-2 py-1 text-xs shadow"
          style={{ left: Math.min(x(h.t) + 8, w - 130), top: (title ? 18 : 0) + Math.max(0, y(h.value) - 34) }}
        >
          <div className="font-medium tabular-nums text-text">
            {h.value.toFixed(decimals)} {unit}
          </div>
          <div className="text-muted">{fmtDate(h.date)}</div>
        </div>
      )}
    </div>
  );
}
