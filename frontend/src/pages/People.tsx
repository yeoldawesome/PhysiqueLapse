import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CheckSquare, ScanFace, Square, Trash2, UserCheck, UserX, Undo2 } from "lucide-react";
import { get, post } from "../api/client";
import type { Job } from "../api/types";
import { ConfirmButton, Empty, PageHeader, Spinner } from "../components/ui";
import { fmtDate } from "../lib/format";
import { toast } from "../lib/toast";

type Status = "not_me" | "unsure" | "no_face" | "me" | "examples";
type Item = {
  id: number;
  taken_at: string | null;
  thumb_url: string;
  face_url: string | null;
  flip_horizontal: boolean;
  status: string | null;
  label: string | null;
  score: number | null;
  original_filename: string;
};
type Summary = { me: number; not_me: number; unsure: number; no_face: number; unscanned: number; me_examples: number; total: number; models: number };

const TABS: { key: Status; label: string; hint: string }[] = [
  {
    key: "unsure",
    label: "Doesn't look like you",
    hint: "faces that don't clearly match yours. Often it's still you (cap over the eyes, looking down, odd angle), so check by eye: select the strangers and press Not me (or delete them), and press That's me on the rest",
  },
  { key: "not_me", label: "Not you", hint: "photos you marked Not me, and photos with the same face as someone you marked" },
  { key: "no_face", label: "Face hidden", hint: "no usable face (phone in front, turned away, too small): the app can't tell, so these are never counted as not you" },
  { key: "me", label: "You", hint: "faces that match yours" },
  { key: "examples", label: "Your examples", hint: "photos you marked as you; the more (and the more varied) the better the matching" },
];

export function usePeopleSummary() {
  return useQuery({ queryKey: ["people", "summary"], queryFn: () => get<Summary>("/people/summary") });
}

function Card({ it, selected, onToggle }: { it: Item; selected: boolean; onToggle: (shift: boolean) => void }) {
  return (
    <div
      className={`group relative cursor-pointer overflow-hidden rounded-md border bg-panel ${selected ? "border-accent ring-2 ring-accent" : "border-border"}`}
      onClick={(e) => onToggle(e.shiftKey)}
      title={`${it.original_filename}${it.score != null ? ` · face match ${(it.score * 100).toFixed(0)}%` : ""}`}
    >
      <img
        src={it.thumb_url}
        alt=""
        loading="lazy"
        className="aspect-[3/4] w-full object-cover"
        style={it.flip_horizontal ? { transform: "scaleX(-1)" } : undefined}
      />
      {it.face_url && (
        <img src={it.face_url} alt="" loading="lazy" className="absolute bottom-6 left-1 h-14 w-14 rounded border-2 border-white/80 object-cover shadow" />
      )}
      <div className={`absolute left-1 top-1 rounded p-0.5 ${selected ? "bg-accent text-white" : "bg-black/50 text-white/80"}`}>
        {selected ? <CheckSquare size={14} /> : <Square size={14} />}
      </div>
      {it.label === "me" && <span className="badge absolute right-1 top-1 bg-ok/80 text-black">you</span>}
      {it.label === "not_me" && <span className="badge absolute right-1 top-1 bg-danger/80 text-white">not you</span>}
      <div className="flex items-center justify-between px-1.5 py-1 text-[11px] text-muted">
        <span>{it.taken_at ? fmtDate(it.taken_at) : "no date"}</span>
        {it.score != null && <span className="tabular-nums">{(it.score * 100).toFixed(0)}%</span>}
      </div>
      <Link
        to={`/library?photo=${it.id}`}
        onClick={(e) => e.stopPropagation()}
        className="absolute bottom-7 right-1 hidden rounded bg-black/60 px-1 text-[10px] text-white group-hover:block"
      >
        open
      </Link>
    </div>
  );
}

export default function People() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Status) || "unsure";
  const summary = usePeopleSummary();
  const sm = summary.data;
  const setup = !!sm && sm.me_examples === 0;
  const list = useQuery({
    queryKey: ["people", "list", tab],
    queryFn: () => get<Item[]>("/people/list", { status: tab }),
    enabled: !!sm && !setup,
  });
  const sugg = useQuery({ queryKey: ["people", "suggestions"], queryFn: () => get<Item[]>("/people/suggestions", { limit: 150 }), enabled: setup });
  const items = useMemo(() => (setup ? sugg.data : list.data) ?? [], [setup, sugg.data, list.data]);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  useEffect(() => setSel(new Set()), [tab, setup]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["people"] });
    qc.invalidateQueries({ queryKey: ["photos"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const label = useMutation({
    mutationFn: ({ ids, label }: { ids: number[]; label: "me" | "not_me" | null }) => post<Summary>("/people/label", { ids, label }),
    onSuccess: (_r, v) => {
      toast.info(`${v.ids.length} photo${v.ids.length === 1 ? "" : "s"} ${v.label === "me" ? "marked as you" : v.label === "not_me" ? "marked not you" : "back to automatic"}`);
      setSel(new Set());
      refresh();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const remove = useMutation({
    mutationFn: (ids: number[]) => post<{ updated: number }>("/photos/bulk", { ids, delete: true }),
    onSuccess: (r) => {
      toast.info(`${r.updated} photo${r.updated === 1 ? "" : "s"} deleted (moved to the app's trash folder)`);
      setSel(new Set());
      refresh();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const scan = useMutation({
    mutationFn: () => post<Job>("/people/scan", {}),
    onSuccess: () => {
      toast.info("Face scan started (see the job panel)");
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const toggle = (id: number, shift: boolean) => {
    const next = new Set(sel);
    if (shift && anchor != null) {
      const ids = items.map((x) => x.id);
      const [a, b] = [ids.indexOf(anchor), ids.indexOf(id)].sort((x, y) => x - y);
      if (a >= 0) for (let i = a; i <= b; i++) next.add(ids[i]);
    } else if (next.has(id)) next.delete(id);
    else next.add(id);
    setAnchor(id);
    setSel(next);
  };
  const ids = [...sel];
  const busy = label.isPending || remove.isPending;

  if (!sm) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted">
        <Spinner /> loading…
      </div>
    );
  }
  const scanned = sm.total - sm.unscanned;

  return (
    <div>
      <PageHeader title="Filter people" subtitle="find photos that aren't of you and remove them">
        <button
          className={`btn ${sm.unscanned > 0 ? "btn-primary" : ""}`}
          disabled={!sm.models || scan.isPending}
          onClick={() => scan.mutate()}
          title="find the face in every photo not scanned yet"
        >
          <ScanFace size={14} /> {sm.unscanned > 0 ? `Scan ${sm.unscanned} photos` : "Scan new photos"}
        </button>
      </PageHeader>

      <div className="flex flex-col gap-4 p-3 md:p-6">
        {!sm.models && (
          <Empty title="Face models missing">
            Run <code>.venv\Scripts\python backend\scripts\download_models.py</code> once (about 38 MB), then reload.
          </Empty>
        )}
        {sm.models > 0 && scanned === 0 && (
          <Empty title="Scan your photos first">
            Press <b>Scan {sm.unscanned} photos</b> above. It finds the face in each photo (about a minute per few hundred photos, all on this computer).
          </Empty>
        )}

        {scanned > 0 && setup && (
          <div className="card p-4">
            <div className="mb-1 font-medium">Step 1 · Which of these are you?</div>
            <p className="mb-3 text-sm text-muted">
              These are the faces that appear most in your library, most typical first. Select the ones that are you, then press <b>These are me</b>. The more
              you pick, and the more different looks (caps, angles, lighting), the better it recognises you: if they're all you, <b>Select all</b> is the quickest.
              Shift-click selects a range.
            </p>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <button className="btn btn-primary" disabled={!ids.length || busy} onClick={() => label.mutate({ ids, label: "me" })}>
                <UserCheck size={14} /> These are me ({ids.length})
              </button>
              <button className="btn" onClick={() => setSel(new Set(items.map((x) => x.id)))}>
                Select all
              </button>
              <button className="btn" disabled={!ids.length} onClick={() => setSel(new Set())}>
                Clear
              </button>
            </div>
            {sugg.isLoading ? (
              <Spinner />
            ) : (
              <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))" }}>
                {items.map((it) => (
                  <Card key={it.id} it={it} selected={sel.has(it.id)} onToggle={(sh) => toggle(it.id, sh)} />
                ))}
              </div>
            )}
          </div>
        )}

        {scanned > 0 && !setup && (
          <>
            <div className="flex flex-wrap gap-1 border-b border-border">
              {TABS.map((t) => {
                const n = t.key === "examples" ? sm.me_examples : sm[t.key];
                return (
                  <button
                    key={t.key}
                    title={t.hint}
                    onClick={() => setParams({ tab: t.key }, { replace: true })}
                    className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === t.key ? "border-accent text-text" : "border-transparent text-muted hover:text-text"}`}
                  >
                    {t.label} <span className={`badge ${t.key === "not_me" && n ? "bg-danger/20 text-danger" : "bg-panel-2 text-muted"}`}>{n}</span>
                  </button>
                );
              })}
            </div>
            <p className="-mt-2 text-xs text-muted">
              {TABS.find((t) => t.key === tab)?.hint}. Sorted least like you first; every photo you mark teaches the filter and the lists update.
            </p>

            <div className="sticky top-[57px] z-10 -mx-6 flex flex-wrap items-center gap-2 border-b border-border bg-bg/90 px-6 py-2 backdrop-blur">
              <span className="text-sm text-muted">{ids.length ? `${ids.length} selected` : `${items.length} photos`}</span>
              <button className="btn btn-sm" disabled={!items.length} onClick={() => setSel(new Set(items.map((x) => x.id)))}>
                Select all
              </button>
              <button className="btn btn-sm" disabled={!ids.length} onClick={() => setSel(new Set())}>
                Clear
              </button>
              <span className="mx-1 h-5 w-px bg-border" />
              {tab !== "examples" && (
                <button className="btn btn-sm" disabled={!ids.length || busy} onClick={() => label.mutate({ ids, label: "me" })} title="these are you: keep them and learn from them">
                  <UserCheck size={13} /> That's me
                </button>
              )}
              {tab !== "not_me" && tab !== "examples" && (
                <button className="btn btn-sm" disabled={!ids.length || busy} onClick={() => label.mutate({ ids, label: "not_me" })} title="these are someone else: they move to Not you, with other photos of the same person">
                  <UserX size={13} /> Not me
                </button>
              )}
              {(tab === "examples" || items.some((x) => sel.has(x.id) && x.label)) && (
                <button className="btn btn-sm" disabled={!ids.length || busy} onClick={() => label.mutate({ ids, label: null })} title="forget what you said about these photos">
                  <Undo2 size={13} /> Back to automatic
                </button>
              )}
              <ConfirmButton
                className="btn btn-sm btn-danger"
                disabled={!ids.length || busy}
                confirmText={`Delete ${ids.length}?`}
                onConfirm={() => remove.mutate(ids)}
                title="delete the selected photos from the library"
              >
                <Trash2 size={13} /> Delete selected
              </ConfirmButton>
              {tab === "not_me" && sm.not_me > 0 && (
                <ConfirmButton
                  className="btn btn-sm btn-danger ml-auto"
                  disabled={busy}
                  confirmText={`Delete all ${sm.not_me}? Click again`}
                  onConfirm={() => remove.mutate(items.map((x) => x.id))}
                  title="delete every photo in Not you"
                >
                  <Trash2 size={13} /> Delete all not-you ({sm.not_me})
                </ConfirmButton>
              )}
            </div>

            {list.isLoading ? (
              <Spinner />
            ) : items.length === 0 ? (
              <Empty title={tab === "not_me" ? "No one else yet" : tab === "unsure" ? "Every face looks like you" : "Nothing here"}>
                {tab === "not_me" ? (
                  <>
                    Photos land here when you mark them <b>Not me</b> (in <b>Doesn't look like you</b>), together with other photos of the same person.
                  </>
                ) : tab === "unsure" ? (
                  <>
                    <Check size={14} className="inline" /> Nothing left to check.
                  </>
                ) : (
                  "Nothing in this group."
                )}
              </Empty>
            ) : (
              <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(130px, 1fr))" }}>
                {items.map((it) => (
                  <Card key={it.id} it={it} selected={sel.has(it.id)} onToggle={(sh) => toggle(it.id, sh)} />
                ))}
              </div>
            )}
            <p className="text-xs text-muted">
              Deleted photos are removed from the library and their files moved to the app's trash folder; your original files elsewhere are never touched.
              {sm.unscanned > 0 ? ` ${sm.unscanned} photos are not scanned yet.` : ""}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
