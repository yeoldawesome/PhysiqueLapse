import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Camera, Check, CloudUpload, Download, Flame, Play, Plus, Trash2 } from "lucide-react";
import { Link } from "react-router";
import { get } from "../api/client";
import { createDailySeries, openPoseSeries, useCaptureListing, useSeries, type CaptureSeries } from "../api/capture";
import type { Page, Photo } from "../api/types";
import CameraScreen from "../components/capture/CameraScreen";
import { getSaveToRoll, saveToCameraRoll, setSaveToRoll } from "../components/capture/camera";
import { Empty, PageHeader, Spinner, Toggle } from "../components/ui";
import { discard, flush, markSaved, useOutbox } from "../lib/outbox";
import { toast } from "../lib/toast";
import { fmtDate } from "../lib/format";

function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Today's captures as files, from the outbox if still there, else downloaded from the server. */
async function todaysFiles(local: { blob: Blob; filename: string; photoId: number | null }[]): Promise<File[]> {
  const files: File[] = local.map((i) => new File([i.blob], i.filename, { type: "image/jpeg" }));
  const have = new Set(local.map((i) => i.photoId).filter(Boolean));
  const page = await get<Page<Photo>>("/photos", { date_source: "capture", from: todayStr(), to: todayStr(), page_size: 60 });
  for (const p of page.items) {
    if (have.has(p.id)) continue;
    const r = await fetch(`/api/photos/${p.id}/original`);
    if (r.ok) files.push(new File([await r.blob()], p.original_filename.split("/").pop() || `photo_${p.id}.jpg`, { type: "image/jpeg" }));
  }
  return files;
}

function SaveToday() {
  const outbox = useOutbox();
  const [files, setFiles] = useState<File[] | null>(null);
  const [busy, setBusy] = useState(false);
  const today = new Date().toDateString();
  const local = outbox.filter((i) => new Date(i.capturedAt).toDateString() === today);
  return (
    <div className="flex flex-wrap items-center gap-2">
      {files == null ? (
        <button
          className="btn"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              const f = await todaysFiles(local);
              if (f.length === 0) toast.info("No photos captured today yet.");
              else setFiles(f);
            } catch (e) {
              toast.error((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? <Spinner size={14} /> : <Download size={14} />} Save today's photos to camera roll
        </button>
      ) : (
        // second tap: the share sheet needs a fresh tap, after the files were fetched
        <button
          className="btn btn-primary"
          onClick={() => {
            const ids = local.map((i) => i.id);
            void saveToCameraRoll(files).then(() => ids.forEach((id) => void markSaved(id)));
            setFiles(null);
          }}
        >
          <Download size={14} /> Save {files.length} photo{files.length === 1 ? "" : "s"}
        </button>
      )}
    </div>
  );
}

function OutboxStatus() {
  const outbox = useOutbox();
  const waiting = outbox.filter((i) => !i.uploaded && !i.error);
  const failed = outbox.filter((i) => i.error);
  if (waiting.length === 0 && failed.length === 0) return null;
  return (
    <div className="card space-y-2 p-3 text-sm">
      {waiting.length > 0 && (
        <div className="flex items-center gap-2">
          <CloudUpload size={16} className="text-accent" />
          <span className="flex-1">
            {waiting.length} photo{waiting.length === 1 ? "" : "s"} waiting to upload (kept on this phone until they do)
          </span>
          <button className="btn btn-sm" onClick={() => void flush()}>
            Retry now
          </button>
        </div>
      )}
      {failed.map((i) => (
        <div key={i.id} className="flex items-center gap-2 text-danger">
          <span className="flex-1 truncate">
            {i.filename}: {i.error}
          </span>
          <button className="btn btn-ghost btn-sm" onClick={() => void discard(i.id)} title="remove from this phone">
            <Trash2 size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}

/** On the camera screen of a daily series: streak and the last 7 days. */
function Filmstrip({ s }: { s: CaptureSeries }) {
  if (!s.streak) return null;
  return (
    <div className="flex items-center gap-2 text-xs">
      <div className="flex w-14 shrink-0 flex-col text-white/70">
        <span className="flex items-center gap-0.5 text-warn">
          <Flame size={12} /> {s.streak.streak}
        </span>
        {s.streak.taken_today ? <span className="text-ok">today ✓</span> : <span>today</span>}
      </div>
      <div className="flex flex-1 gap-1">
        {s.streak.days.map((d) => (
          <div key={d.date} className="aspect-[4/5] flex-1 overflow-hidden rounded bg-white/10" title={d.date}>
            {d.thumb_url && <img src={d.thumb_url} alt="" className="h-full w-full object-cover" />}
          </div>
        ))}
      </div>
      {s.photo_count > 1 && (
        <Link to={`/viewer?pose_id=series:${s.id}&autoplay=1`} className="rounded-md bg-white/10 p-2" title="play as a flipbook">
          <Play size={16} />
        </Link>
      )}
    </div>
  );
}

export default function Capture() {
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const sid = params.get("series") ? Number(params.get("series")) : null;
  const series = useSeries(sid);
  const listing = useCaptureListing();
  const [saveRoll, setSaveRoll] = useState(getSaveToRoll);
  const [newName, setNewName] = useState("");
  useEffect(() => setSaveToRoll(saveRoll), [saveRoll]);

  if (sid != null) {
    if (series.isLoading) return <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black text-white"><Spinner /></div>;
    if (series.data)
      return <CameraScreen series={series.data} onExit={() => nav("/capture")} extra={series.data.mode === "daily" ? <Filmstrip s={series.data} /> : undefined} />;
  }

  const open = async (poseId: number) => {
    try {
      const s = await openPoseSeries(poseId);
      setParams({ series: String(s.id) });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <div>
      <PageHeader title="Capture" subtitle="Line up with a ghost of an earlier photo, then shoot" />
      <div className="flex max-w-4xl flex-col gap-4 p-3 md:p-6">
        <OutboxStatus />
        <section className="card space-y-3 p-4">
          <div className="flex flex-wrap items-center gap-3">
            <Toggle checked={saveRoll} onChange={setSaveRoll} label="Save to camera roll after each capture" />
          </div>
          <SaveToday />
        </section>

        <section>
          <h2 className="mb-2 font-medium">Daily series</h2>
          <p className="mb-3 text-sm text-muted">One photo a day; the ghost is yesterday's photo, so each day lines up with the last.</p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
            {(listing.data?.daily ?? []).map((d) => (
              <div key={d.id} className="card overflow-hidden">
                <button className="relative block aspect-[4/5] w-full bg-bg" onClick={() => setParams({ series: String(d.id) })} title="take today's photo">
                  {d.last_thumb_url ? <img src={d.last_thumb_url} alt="" className="h-full w-full object-cover" /> : <Camera className="m-auto mt-10 text-muted" />}
                  {d.streak?.taken_today ? (
                    <span className="absolute left-1.5 top-1.5 flex items-center gap-1 rounded bg-ok/90 px-1.5 py-0.5 text-[11px] font-medium text-black">
                      <Check size={12} /> today
                    </span>
                  ) : (
                    <span className="absolute left-1.5 top-1.5 rounded bg-black/70 px-1.5 py-0.5 text-[11px]">not yet today</span>
                  )}
                </button>
                <div className="flex items-center gap-1 p-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{d.name}</div>
                    <div className="flex items-center gap-1 text-xs text-muted">
                      {d.streak && d.streak.streak > 0 && (
                        <span className="flex items-center text-warn" title="days in a row">
                          <Flame size={12} />
                          {d.streak.streak}
                        </span>
                      )}
                      {d.photo_count} photos{d.last_taken_at ? ` · ${fmtDate(d.last_taken_at)}` : ""}
                    </div>
                  </div>
                  {d.photo_count > 1 && (
                    <Link to={`/viewer?pose_id=series:${d.id}&autoplay=1`} className="btn btn-sm" title="play as a flipbook">
                      <Play size={14} />
                    </Link>
                  )}
                </div>
              </div>
            ))}
            <form
              className="card flex flex-col justify-center gap-2 p-3"
              onSubmit={async (e) => {
                e.preventDefault();
                if (!newName.trim()) return;
                try {
                  const s = await createDailySeries(newName.trim(), null);
                  setNewName("");
                  qc.invalidateQueries({ queryKey: ["capture"] });
                  setParams({ series: String(s.id) });
                } catch (err) {
                  toast.error((err as Error).message);
                }
              }}
            >
              <input className="input w-full" placeholder="New daily series" value={newName} onChange={(e) => setNewName(e.target.value)} />
              <button className="btn justify-center" disabled={!newName.trim()}>
                <Plus size={14} /> Start
              </button>
            </form>
          </div>
        </section>

        <section>
          <h2 className="mb-2 font-medium">Poses</h2>
          <p className="mb-3 text-sm text-muted">The ghost is the pose's reference photo; the new photo joins the pose and is aligned to it.</p>
          {listing.isLoading ? (
            <Spinner />
          ) : (listing.data?.poses.length ?? 0) === 0 ? (
            <Empty title="No poses yet" />
          ) : (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-6">
              {listing.data!.poses.map((p) => (
                <button key={p.pose_id} className="card overflow-hidden text-left" onClick={() => void open(p.pose_id)}>
                  <div className="aspect-[4/5] bg-bg">
                    {p.reference_thumb_url ? <img src={p.reference_thumb_url} alt="" className="h-full w-full object-cover" loading="lazy" /> : <Camera className="m-auto mt-8 text-muted" />}
                  </div>
                  <div className="line-clamp-2 p-1.5 text-[11px] leading-tight">{p.name}</div>
                </button>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
