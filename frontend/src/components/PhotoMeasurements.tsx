import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Ruler, RefreshCw } from "lucide-react";
import { get, post } from "../api/client";
import type { Job } from "../api/types";
import MetricChart from "./MetricChart";
import { Empty, Modal, Spinner } from "./ui";
import { fmtDate } from "../lib/format";
import { toast } from "../lib/toast";

export interface MeasureSummary {
  name: string;
  label: string;
  description: string;
  tape_metric_names: string[];
  by_pose: { pose_id: number; pose: string; count: number }[];
  default_pose_id: number | null;
}

interface SeriesPoint {
  photo_id: number;
  date: string;
  ratio: number;
  value?: number;
}

interface Series {
  name: string;
  label: string;
  points: SeriesPoint[];
  smooth: { date: string; ratio: number; value?: number }[];
  calibration: { metric: string; unit: string; k: number; pairs: number; mean_abs_error_pct: number | null } | null;
  tape_metric_names: string[];
}

const LOW_CONFIDENCE = new Set(["forearm"]);

/** ratios are widths in torso lengths; show them as % of torso length (V-taper stays a plain ratio) */
export function fmtRatio(name: string, r: number): string {
  return name === "vtaper" ? r.toFixed(2) : `${(r * 100).toFixed(1)}%`;
}

function MeasureCard({ m, onOpenPhoto }: { m: MeasureSummary; onOpenPhoto: (id: number) => void }) {
  const [poseId, setPoseId] = useState<number | null>(m.default_pose_id);
  const series = useQuery({
    queryKey: ["measure-series", m.name, poseId],
    queryFn: () => get<Series>("/measurements/series", { name: m.name, pose_id: poseId ?? undefined }),
    enabled: poseId != null,
  });
  const s = series.data;
  const cal = s?.calibration ?? null;
  const val = (p: { ratio: number; value?: number }) => (cal && p.value != null ? p.value : p.ratio);
  const first = s?.smooth[0];
  const last = s?.smooth[s.smooth.length - 1];
  const change = first && last ? ((val(last) - val(first)) / val(first)) * 100 : null;
  const show = (v: number) => (cal ? `${v.toFixed(1)} ${cal.unit}` : fmtRatio(m.name, v));

  return (
    <div className="card flex flex-col gap-2 p-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2 font-medium">
            {m.label}
            {LOW_CONFIDENCE.has(m.name) && (
              <span className="badge bg-warn/15 text-warn" title="the forearm is rarely clear of the body in mirror selfies; treat as a rough indicator">
                low confidence
              </span>
            )}
          </div>
          <div className="text-xs text-muted">{m.description}</div>
        </div>
        {last && (
          <div className="ml-auto shrink-0 text-right">
            <div className="text-lg font-semibold tabular-nums">{show(val(last))}</div>
            {change != null && Math.abs(change) >= 0.05 && (
              <div className="text-xs tabular-nums text-muted">
                {change > 0 ? "+" : ""}
                {change.toFixed(1)}% since {fmtDate(first!.date)}
              </div>
            )}
          </div>
        )}
      </div>
      {m.by_pose.length === 0 ? (
        <div className="py-6 text-center text-xs text-muted">No photo shows this clearly yet.</div>
      ) : (
        <>
          <select className="select text-xs" value={poseId ?? ""} onChange={(e) => setPoseId(Number(e.target.value))}>
            {m.by_pose.map((b) => (
              <option key={b.pose_id} value={b.pose_id}>
                {b.pose} ({b.count} photos)
              </option>
            ))}
          </select>
          {series.isLoading || !s ? (
            <div className="flex h-40 items-center justify-center">
              <Spinner />
            </div>
          ) : (
            <MetricChart
              points={s.points.map((p) => ({ date: p.date, value: val(p) }))}
              trend={s.smooth.map((p) => ({ date: p.date, value: val(p) }))}
              unit={cal ? cal.unit : ""}
              format={(v) => show(v)}
              height={170}
              onPickIndex={(i) => onOpenPhoto(s.points[i].photo_id)}
            />
          )}
          <div className="text-[11px] text-muted">
            {cal ? (
              <>
                Inches estimated from {cal.pairs} tape measurement{cal.pairs === 1 ? "" : "s"} of "{cal.metric}"
                {cal.mean_abs_error_pct != null ? ` (fits them within ±${cal.mean_abs_error_pct}%)` : ""}.
              </>
            ) : m.tape_metric_names.length > 0 ? (
              <>
                Shown as % of your torso length. Log a tape measurement named "{capitalize(m.tape_metric_names[0])}" on the Tape &amp; scale tab on
                a couple of days to see real units.
              </>
            ) : (
              <>Shoulder width divided by waist width.</>
            )}{" "}
            Dots are single photos; the line is the 3-week trend. Click a dot to see how it was measured.
          </div>
        </>
      )}
    </div>
  );
}

function capitalize(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function MeasureOverlayModal({ photoId, onClose }: { photoId: number | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["photo-measure", photoId],
    queryFn: () =>
      get<{ items: Record<string, { label: string; valid: boolean; ratio?: number; why?: string }>; reason?: string }>(`/photos/${photoId}/measurements`),
    enabled: photoId != null,
  });
  return (
    <Modal open={photoId != null} onClose={onClose} title="How this photo was measured" width="max-w-4xl">
      {photoId != null && (
        <div className="flex flex-col gap-3 md:flex-row">
          <img src={`/api/photos/${photoId}/measure-overlay?t=${Date.now()}`} alt="" className="max-h-[70vh] rounded object-contain md:w-2/3" />
          <div className="flex-1 text-sm">
            <p className="mb-2 text-xs text-muted">Blue is the detected body outline. Green lines were used; red ones were rejected (reason below).</p>
            {q.data?.reason && <div className="text-warn">{q.data.reason}</div>}
            <ul className="space-y-1">
              {Object.entries(q.data?.items ?? {}).map(([k, v]) => (
                <li key={k} className="flex gap-2">
                  <span className={v.valid ? "text-ok" : "text-danger"}>●</span>
                  <span className="w-28">{v.label}</span>
                  <span className="tabular-nums">{v.valid && v.ratio != null ? fmtRatio(k, v.ratio) : ""}</span>
                  {!v.valid && <span className="text-xs text-muted">{v.why}</span>}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Modal>
  );
}

export default function PhotoMeasurements() {
  const qc = useQueryClient();
  const summary = useQuery({ queryKey: ["measure-summary"], queryFn: () => get<MeasureSummary[]>("/measurements/summary") });
  const [open, setOpen] = useState<number | null>(null);
  const run = (force: boolean) =>
    post<Job>("/measurements/run", { force })
      .then(() => {
        toast.info("Measuring photos");
        qc.invalidateQueries({ queryKey: ["jobs"] });
      })
      .catch((e: Error) => toast.error(e.message));

  const any = (summary.data ?? []).some((m) => m.by_pose.length > 0);
  return (
    <div className="flex flex-col gap-4">
      <div className="card flex flex-wrap items-center gap-3 p-3 text-sm">
        <Ruler size={16} className="text-accent" />
        <div className="min-w-0 flex-1 text-xs text-muted">
          Sizes are read from the body outline in each photo and divided by your torso length, which doesn't change as you grow, so photos taken
          at any distance compare. Each size is tracked within one pose type, because a flex and a relaxed shot measure differently. Expect a few
          percent of noise per photo; trust the trend line, not single dots. Clothes count as body, so compare like with like.
        </div>
        <button className="btn btn-sm" onClick={() => run(false)}>
          <Ruler size={13} /> Measure new photos
        </button>
        <button className="btn btn-sm btn-ghost" onClick={() => run(true)} title="re-measure every photo">
          <RefreshCw size={13} /> Re-measure all
        </button>
      </div>
      {summary.isLoading ? (
        <Spinner />
      ) : !any ? (
        <Empty title="No measurements yet">Run landmark extraction on the Dashboard, then click "Measure new photos".</Empty>
      ) : (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {summary.data!.map((m) => (
            <MeasureCard key={m.name} m={m} onOpenPhoto={setOpen} />
          ))}
        </div>
      )}
      <MeasureOverlayModal photoId={open} onClose={() => setOpen(null)} />
    </div>
  );
}
