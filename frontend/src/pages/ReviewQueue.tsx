import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Undo2, X, ZoomIn } from "lucide-react";
import { get, post } from "../api/client";
import type { Candidate, Page } from "../api/types";
import { Empty, Modal, PageHeader, Spinner } from "../components/ui";
import { useShortcuts } from "../lib/keyboard";
import { fmtDate } from "../lib/format";
import { toast } from "../lib/toast";

interface Histogram {
  bins: number;
  counts: number[];
  total: number;
  accept: number;
  reject: number;
}

function Chip({ label, v }: { label: string; v: number | null }) {
  return (
    <span className="badge bg-black/60 tabular-nums text-text">
      {label} {v == null ? "–" : v.toFixed(2)}
    </span>
  );
}

/** Score distribution for threshold calibration: one series, thresholds marked. */
function ScoreHistogram({ h }: { h: Histogram }) {
  const max = Math.max(1, ...h.counts);
  const W = 100 / h.bins;
  const [hover, setHover] = useState<number | null>(null);
  return (
    <div className="relative">
      <div className="mb-1 flex items-center text-xs text-muted">
        <span>Score distribution ({h.total} scored)</span>
        {hover != null && (
          <span className="ml-auto tabular-nums text-text">
            {(hover / h.bins).toFixed(2)}–{((hover + 1) / h.bins).toFixed(2)}: {h.counts[hover]}
          </span>
        )}
      </div>
      <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="h-20 w-full" onMouseLeave={() => setHover(null)}>
        {h.counts.map((c, i) => {
          const ht = (c / max) * 38;
          return (
            <g key={i} onMouseEnter={() => setHover(i)}>
              <rect x={i * W} y={0} width={W} height={40} fill="transparent" />
              <rect x={i * W + 0.3} y={40 - ht} width={W - 0.6} height={ht} rx={0.6} fill={hover === i ? "var(--color-accent)" : "var(--color-accent-2)"} />
            </g>
          );
        })}
        {[h.reject, h.accept].map((t, i) => (
          <line key={i} x1={t * 100} x2={t * 100} y1={0} y2={40} stroke="var(--color-warn)" strokeWidth={0.4} strokeDasharray="1 1" vectorEffect="non-scaling-stroke" />
        ))}
      </svg>
      <div className="flex justify-between text-[10px] text-muted">
        <span>0</span>
        <span>reject &lt; {h.reject} · accept ≥ {h.accept}</span>
        <span>1</span>
      </div>
    </div>
  );
}

export default function ReviewQueue() {
  const qc = useQueryClient();
  const [band, setBand] = useState<"all" | "high" | "mid" | "low">("all");
  const [cursor, setCursor] = useState(0);
  const [zoom, setZoom] = useState(false);
  const [above, setAbove] = useState(0.6);
  const bounds = { all: [undefined, undefined], high: [0.55, undefined], mid: [0.4, 0.55], low: [undefined, 0.4] }[band];
  const q = useQuery({
    queryKey: ["review", band],
    queryFn: () => get<Page<Candidate>>("/review", { decision: "review", min_score: bounds[0], max_score: bounds[1], page_size: 300 }),
  });
  const hist = useQuery({ queryKey: ["review", "hist"], queryFn: () => get<Histogram>("/review/histogram") });
  const items = useMemo(() => q.data?.items ?? [], [q.data]);
  useEffect(() => setCursor((c) => Math.min(c, Math.max(0, items.length - 1))), [items.length]);
  const cur = items[cursor];

  const refresh = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["review"] });
    qc.invalidateQueries({ queryKey: ["photos"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  }, [qc]);

  const decide = useCallback(
    async (c: Candidate | undefined, decision: "accept" | "reject") => {
      if (!c) return;
      // optimistic: drop from the list immediately
      qc.setQueryData<Page<Candidate>>(["review", band], (old) => (old ? { ...old, items: old.items.filter((x) => x.id !== c.id), total: old.total - 1 } : old));
      try {
        await post(`/review/${c.id}`, { decision });
      } catch (e) {
        toast.error((e as Error).message);
      }
      refresh();
    },
    [qc, band, refresh],
  );
  const undo = useCallback(async () => {
    try {
      const c = await post<Candidate>("/review/undo");
      toast.info(`Undone: ${c.file_name}`);
    } catch (e) {
      toast.error((e as Error).message);
    }
    refresh();
  }, [refresh]);

  useShortcuts(
    useMemo(
      () => ({
        a: () => decide(cur, "accept"),
        r: () => decide(cur, "reject"),
        u: () => undo(),
        ArrowRight: () => setCursor((c) => Math.min(items.length - 1, c + 1)),
        ArrowLeft: () => setCursor((c) => Math.max(0, c - 1)),
        j: () => setCursor((c) => Math.min(items.length - 1, c + 1)),
        k: () => setCursor((c) => Math.max(0, c - 1)),
        Enter: () => setZoom((z) => !z),
      }),
      [cur, items.length, decide, undo],
    ),
  );

  return (
    <div>
      <PageHeader title="Review queue" subtitle={q.data ? `${q.data.total} images the physique filter was unsure about` : ""}>
        <select className="select" value={band} onChange={(e) => setBand(e.target.value as typeof band)}>
          <option value="all">all scores</option>
          <option value="high">score ≥ 0.55</option>
          <option value="mid">0.40 – 0.55</option>
          <option value="low">score &lt; 0.40</option>
        </select>
        <span className="flex items-center gap-1">
          <input type="number" className="input w-20" step={0.05} min={0} max={1} value={above} onChange={(e) => setAbove(Number(e.target.value))} />
          <button
            className="btn"
            onClick={() =>
              post<{ updated: number }>("/review/bulk", { decision: "accept", min_score: above })
                .then((r) => {
                  toast.success(`Accepted ${r.updated}`);
                  refresh();
                })
                .catch((e: Error) => toast.error(e.message))
            }
          >
            accept all ≥ {above}
          </button>
        </span>
        <button className="btn" onClick={undo} title="U">
          <Undo2 size={14} /> Undo
        </button>
      </PageHeader>
      <div className="flex flex-col gap-4 p-6">
        {hist.data && hist.data.total > 0 && (
          <div className="card max-w-xl p-3">
            <ScoreHistogram h={hist.data} />
          </div>
        )}
        {q.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Spinner /> loading…
          </div>
        ) : items.length === 0 ? (
          <Empty title="Nothing to review">Run a Snapchat import on the Import page; uncertain images land here.</Empty>
        ) : (
          <>
            <div className="text-xs text-muted">
              <span className="kbd">A</span> accept · <span className="kbd">R</span> reject · <span className="kbd">←</span>/<span className="kbd">→</span> or{" "}
              <span className="kbd">J</span>/<span className="kbd">K</span> move · <span className="kbd">U</span> undo · <span className="kbd">Enter</span> zoom
            </div>
            <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
              {items.map((c, i) => (
                <div
                  key={c.id}
                  className={`card group relative cursor-pointer overflow-hidden ${i === cursor ? "ring-2 ring-accent" : ""}`}
                  onClick={() => setCursor(i)}
                  onDoubleClick={() => setZoom(true)}
                >
                  <img src={c.thumb_url ?? ""} alt="" loading="lazy" className="aspect-[4/5] w-full bg-bg object-cover" />
                  <div className="absolute left-1.5 top-1.5 flex flex-wrap gap-1">
                    <Chip label="score" v={c.score} />
                    <Chip label="clip" v={c.clip_score} />
                    <Chip label="pose" v={c.pose_score} />
                  </div>
                  <div className="flex items-center gap-1 p-1.5 text-xs">
                    <span className="truncate text-muted">{fmtDate(c.source_date)}</span>
                    <button className="btn btn-sm ml-auto text-danger" onClick={(e) => (e.stopPropagation(), decide(c, "reject"))} title="reject (R)">
                      <X size={13} />
                    </button>
                    <button className="btn btn-sm text-ok" onClick={(e) => (e.stopPropagation(), decide(c, "accept"))} title="accept (A)">
                      <Check size={13} />
                    </button>
                    <button className="btn btn-sm" onClick={(e) => (e.stopPropagation(), setCursor(i), setZoom(true))} title="zoom (Enter)">
                      <ZoomIn size={13} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
      <Modal open={zoom && !!cur} onClose={() => setZoom(false)} width="max-w-3xl">
        {cur && (
          <div className="flex flex-col gap-2">
            <img src={`/api/imports/candidates/${cur.id}/thumb`} alt="" className="max-h-[75vh] w-full object-contain" />
            <div className="flex items-center gap-2 text-sm">
              <span className="truncate text-muted">{cur.file_name}</span>
              <button className="btn btn-danger ml-auto" onClick={() => decide(cur, "reject")}>
                <X size={14} /> Reject
              </button>
              <button className="btn btn-primary" onClick={() => decide(cur, "accept")}>
                <Check size={14} /> Accept
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
