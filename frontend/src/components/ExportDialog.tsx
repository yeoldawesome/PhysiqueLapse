import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Film, Trash2 } from "lucide-react";
import { del, get, post } from "../api/client";
import type { ExportItem, Job, Metric } from "../api/types";
import { fmtBytes, fmtDateTime } from "../lib/format";
import { toast } from "../lib/toast";
import { ConfirmButton, Field, Modal, Toggle } from "./ui";

export function useExports() {
  return useQuery({ queryKey: ["exports"], queryFn: () => get<ExportItem[]>("/exports") });
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
}) {
  const qc = useQueryClient();
  const metrics = useQuery({ queryKey: ["metrics"], queryFn: () => get<Metric[]>("/metrics") });
  const exports = useExports();
  const [format, setFormat] = useState<"mp4" | "gif">("mp4");
  const [fps, setFps] = useState(defaultFps);
  const [width, setWidth] = useState(1080);
  const [crossfade, setCrossfade] = useState(0.2);
  const [capDate, setCapDate] = useState(true);
  const [capDays, setCapDays] = useState(true);
  const [capMetrics, setCapMetrics] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setBusy(true);
    try {
      await post<{ job: Job }>("/exports", {
        pose_id: poseId,
        group: group ?? null,
        from: from || null,
        to: to || null,
        fps,
        format,
        width: format === "gif" ? Math.min(width, 720) : width,
        crossfade,
        caption: { date: capDate, days: capDays, metrics: capMetrics },
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
    <Modal open={open} onClose={onClose} title="Export time-lapse" width="max-w-2xl">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Field label="Format">
          <select className="select" value={format} onChange={(e) => setFormat(e.target.value as "mp4" | "gif")}>
            <option value="mp4">MP4 (H.264)</option>
            <option value="gif">GIF</option>
          </select>
        </Field>
        <Field label="Photos per second">
          <input type="number" className="input" min={0.25} max={30} step={0.25} value={fps} onChange={(e) => setFps(Number(e.target.value))} />
        </Field>
        <Field label="Width (px)" hint={format === "gif" ? "GIF is capped at 720" : undefined}>
          <select className="select" value={width} onChange={(e) => setWidth(Number(e.target.value))}>
            {[1080, 720, 540, 360].map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Crossfade (s)">
          <input type="number" className="input" min={0} max={2} step={0.05} value={crossfade} onChange={(e) => setCrossfade(Number(e.target.value))} />
        </Field>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-4">
        <span className="label">Caption</span>
        <Toggle checked={capDate} onChange={setCapDate} label="date" />
        <Toggle checked={capDays} onChange={setCapDays} label="day number" />
        {(metrics.data ?? []).map((m) => (
          <Toggle
            key={m.id}
            checked={capMetrics.includes(m.id)}
            onChange={(v) => setCapMetrics(v ? [...capMetrics, m.id] : capMetrics.filter((x) => x !== m.id))}
            label={m.name}
          />
        ))}
      </div>
      <div className="mt-2 text-xs text-muted">
        Range: {from || "first photo"} → {to || "last photo"}. Only aligned photos are included
        {maxPoseDiff != null ? `, and only those whose pose is within ${maxPoseDiff}% of the reference (the Viewer's pose-match setting)` : ""}{hideSleeves ? ", leaving out photos with sleeves" : ""}. Rendering runs in the background.
      </div>
      <div className="mt-3 flex justify-end">
        <button className="btn btn-primary" onClick={start} disabled={busy || fps <= 0}>
          <Film size={14} /> Start export
        </button>
      </div>

      <div className="mt-5">
        <div className="label mb-1">Exports</div>
        {(exports.data ?? []).length === 0 ? (
          <div className="text-sm text-muted">None yet.</div>
        ) : (
          <ul className="divide-y divide-border rounded border border-border">
            {exports.data!.map((x) => (
              <li key={x.id} className="flex items-center gap-2 px-3 py-2 text-sm">
                <span className="badge bg-panel-2 uppercase text-muted">{x.format}</span>
                <span className="truncate">{x.path?.split("/").pop() ?? `export #${x.id}`}</span>
                <span className="text-xs text-muted">
                  {x.frames ? `${x.frames} photos · ` : ""}
                  {x.size_bytes ? fmtBytes(x.size_bytes) + " · " : ""}
                  {fmtDateTime(x.created_at)}
                </span>
                <span className={`ml-auto text-xs ${x.status === "failed" ? "text-danger" : x.status === "done" ? "text-ok" : "text-muted"}`}>{x.status}</span>
                {x.status === "done" && (
                  <a className="btn btn-sm" href={x.download_url}>
                    <Download size={13} />
                  </a>
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
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
