import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronUp, RotateCcw, X } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useJobs } from "../api/queries";
import { post } from "../api/client";
import type { Job } from "../api/types";
import { toast } from "../lib/toast";

function Bar({ job }: { job: Job }) {
  const pct = job.total > 0 ? Math.min(100, (job.done / job.total) * 100) : job.status === "running" ? 100 : 0;
  const indeterminate = job.status === "running" && job.total === 0;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded bg-border">
      <div
        className={`h-full rounded transition-[width] ${
          job.status === "failed" ? "bg-danger" : job.status === "cancelled" ? "bg-muted" : job.status === "done" ? "bg-ok" : "bg-accent-2"
        } ${indeterminate ? "animate-pulse" : ""}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export default function JobDrawer() {
  const { data: jobs } = useJobs();
  const qc = useQueryClient();
  const [collapsed, setCollapsed] = useState(false);
  const [dismissedBefore, setDismissedBefore] = useState<number>(0);
  const prev = useRef<Map<number, Job["status"]>>(new Map());

  // Toast + refresh when a job finishes.
  useEffect(() => {
    if (!jobs) return;
    let finished = false;
    for (const j of jobs) {
      const before = prev.current.get(j.id);
      const activeBefore = before === "queued" || before === "running";
      const activeNow = j.status === "queued" || j.status === "running";
      if (before !== undefined && activeBefore && !activeNow) {
        finished = true;
        if (j.status === "done") toast.success(`${j.title}: ${j.message || "done"}`);
        else if (j.status === "failed") toast.error(`${j.title} failed: ${j.error ?? "unknown error"}`);
        else toast.info(`${j.title} cancelled`);
      }
    }
    prev.current = new Map(jobs.map((j) => [j.id, j.status]));
    if (finished) {
      for (const key of [["photos"], ["photo"], ["dashboard"], ["imports"], ["categories"], ["sequence"], ["exports"], ["review"], ["eval"], ["system"], ["metrics"], ["measure-summary"], ["measure-series"], ["snap-zip"]]) {
        qc.invalidateQueries({ queryKey: key });
      }
    }
  }, [jobs, qc]);

  // finished jobs linger for 15 s and then get out of the way (failed ones stay until dismissed)
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 3000);
    return () => window.clearInterval(t);
  }, []);

  if (!jobs) return null;
  const active = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const fresh = (j: Job) => {
    if (j.status === "failed") return true;
    const t = j.finished_at ? new Date(j.finished_at).getTime() : 0;
    return now - t < 15_000;
  };
  const recent = jobs.filter((j) => j.status !== "queued" && j.status !== "running" && j.id > dismissedBefore && fresh(j)).slice(0, 4);
  const shown = [...active, ...recent];
  if (shown.length === 0) return null;

  return (
    <div className="fixed bottom-3 right-3 z-40 w-80 overflow-hidden rounded-lg border border-border bg-panel shadow-2xl">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs">
        <span className="font-medium">
          {active.length > 0 ? `${active.length} job${active.length > 1 ? "s" : ""} running` : "Recent jobs"}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {recent.length > 0 && (
            <button className="btn btn-ghost btn-sm" title="clear finished" onClick={() => setDismissedBefore(Math.max(...recent.map((j) => j.id)))}>
              <X size={13} />
            </button>
          )}
          <button className="btn btn-ghost btn-sm" onClick={() => setCollapsed((c) => !c)}>
            {collapsed ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>
        </div>
      </div>
      {!collapsed && (
        <ul className="max-h-72 divide-y divide-border overflow-auto">
          {shown.map((j) => (
            <li key={j.id} className="px-3 py-2">
              <div className="flex items-center gap-2 text-xs">
                <span className="truncate font-medium">{j.title}</span>
                <span className="ml-auto shrink-0 tabular-nums text-muted">
                  {j.total > 0 ? `${j.done}/${j.total}` : ""}
                </span>
                {(j.status === "queued" || j.status === "running") && (
                  <button
                    className="btn btn-ghost btn-sm"
                    title="cancel"
                    onClick={() => post(`/jobs/${j.id}/cancel`).catch((e: Error) => toast.error(e.message))}
                  >
                    <X size={12} />
                  </button>
                )}
                {(j.status === "failed" || j.status === "cancelled") && (
                  <button
                    className="btn btn-ghost btn-sm"
                    title="run again"
                    onClick={() => post(`/jobs/${j.id}/retry`).then(() => qc.invalidateQueries({ queryKey: ["jobs"] })).catch((e: Error) => toast.error(e.message))}
                  >
                    <RotateCcw size={12} />
                  </button>
                )}
              </div>
              <div className="my-1">
                <Bar job={j} />
              </div>
              <div className={`truncate text-[11px] ${j.status === "failed" ? "text-danger" : "text-muted"}`} title={j.error ?? j.message}>
                {j.status === "failed" ? j.error : j.message}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
