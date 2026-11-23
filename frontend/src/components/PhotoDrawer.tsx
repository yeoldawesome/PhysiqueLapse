import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Bone, Check, ExternalLink, FlipHorizontal2, RefreshCw, Ruler, Star, Trash2, X } from "lucide-react";
import { MeasureOverlayModal } from "./PhotoMeasurements";
import { useCategories, useCategoryMutations, useDeletePhoto, usePatchPhoto, usePhoto } from "../api/queries";
import { post } from "../api/client";
import { toast } from "../lib/toast";
import { fmtDateTime, POSE_SOURCE_LABEL } from "../lib/format";
import DateEditor from "./DateEditor";
import LandmarkOverlay from "./LandmarkOverlay";
import { ConfirmButton, Toggle } from "./ui";

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="text-muted">{k}</span>
      <span className="truncate text-right">{v}</span>
    </div>
  );
}

export default function PhotoDrawer({ photoId, onClose, onAccepted }: { photoId: number; onClose: () => void; onAccepted?: () => void }) {
  const { data: p, isLoading } = usePhoto(photoId);
  const { data: cats = [] } = useCategories();
  const patch = usePatchPhoto();
  const remove = useDeletePhoto();
  const { setReference } = useCategoryMutations();
  const [overlay, setOverlay] = useState(false);
  const [measureOpen, setMeasureOpen] = useState(false);
  const [notes, setNotes] = useState("");
  useEffect(() => setNotes(p?.notes ?? ""), [p?.id, p?.notes]);

  if (isLoading || !p) {
    return <aside className="fixed inset-0 z-50 bg-panel p-4 text-sm text-muted md:static md:z-auto md:w-[380px] md:shrink-0 md:border-l md:border-border">Loading…</aside>;
  }
  const busy = patch.isPending;
  const cat = cats.find((c) => c.id === p.pose_category_id);
  const flags = p.alignment?.flags ? Object.keys(p.alignment.flags).filter((k) => p.alignment!.flags[k]) : [];
  const lmOk = p.landmarks_status === "ok";

  return (
    <aside className="fixed inset-0 z-50 flex flex-col overflow-y-auto bg-panel pb-[env(safe-area-inset-bottom)] pt-[env(safe-area-inset-top)] md:static md:pt-0 md:z-auto md:w-[380px] md:shrink-0 md:border-l md:border-border">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <span className="truncate text-sm font-medium" title={p.original_filename}>
          {p.original_filename}
        </span>
        <button className="btn btn-ghost btn-sm ml-auto" onClick={onClose} aria-label="close">
          <X size={16} />
        </button>
      </div>

      {/* fixed-height box: any photo shape fits inside (object-contain); the landmark overlay
          uses the same contain fit through its viewBox, so it stays registered */}
      <div className="checker relative h-[min(440px,60vh)] w-full shrink-0 overflow-hidden">
        <div className="absolute inset-0 bottom-11">
          <img
            src={p.preview_url}
            alt=""
            className="absolute inset-0 h-full w-full object-contain"
            style={p.flip_horizontal ? { transform: "scaleX(-1)" } : undefined}
            draggable={false}
          />
          {overlay && lmOk && <LandmarkOverlay photoId={p.id} width={p.width} height={p.height} flip={p.flip_horizontal} />}
        </div>
        <div className="absolute bottom-2 right-2 flex gap-1">
          <button
            className={`btn btn-sm ${overlay ? "btn-primary" : ""}`}
            onClick={() => setOverlay((o) => !o)}
            disabled={!lmOk}
            title={lmOk ? "toggle landmark overlay" : `landmarks: ${p.landmarks_status}`}
          >
            <Bone size={14} /> landmarks
          </button>
          <button className="btn btn-sm" onClick={() => setMeasureOpen(true)} disabled={!lmOk} title="body-size measurements from this photo">
            <Ruler size={14} /> sizes
          </button>
        </div>
      </div>

      <div className="flex flex-col gap-4 p-3">
        <DateEditor
          photo={p}
          busy={busy}
          onSave={(iso) => patch.mutate({ id: p.id, body: { taken_at: iso } })}
          onClear={() => patch.mutate({ id: p.id, body: { clear_date: true } })}
        />

        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <span className="label">Pose</span>
            {p.pose_source !== "none" && (
              <span className={`badge ${p.pose_needs_review ? "bg-warn/15 text-warn" : "bg-panel-2 text-muted"}`}>
                {POSE_SOURCE_LABEL[p.pose_source]}
                {p.pose_confidence != null && p.pose_source !== "manual" ? ` · ${Math.round(p.pose_confidence * 100)}%` : ""}
                {p.pose_needs_review ? " · review" : ""}
              </span>
            )}
          </div>
          {p.pose_needs_review && cat && (
            <button
              className="btn btn-primary justify-center"
              disabled={busy}
              onClick={() => patch.mutate({ id: p.id, body: { pose_category_id: cat.id } }, { onSuccess: () => onAccepted?.() })}
              title="the pose is right: confirm it (shortcut A)"
            >
              <Check size={14} /> Accept "{cat.name}"
            </button>
          )}
          <div className="flex gap-1.5">
            <select
              className="select flex-1"
              value={p.pose_category_id ?? ""}
              disabled={busy}
              onChange={(e) => {
                const v = e.target.value;
                patch.mutate({ id: p.id, body: v === "" ? { clear_pose: true } : { pose_category_id: Number(v) } });
              }}
            >
              <option value="">— unassigned —</option>
              {cats.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            {cat && (
              <button
                className={`btn btn-sm ${p.is_reference ? "btn-primary" : ""}`}
                title={p.is_reference ? "this is the reference for its category" : `use as reference for ${cat.name}`}
                disabled={p.is_reference || setReference.isPending}
                onClick={() => setReference.mutate({ id: cat.id, photo_id: p.id })}
              >
                <Star size={14} fill={p.is_reference ? "currentColor" : "none"} />
              </button>
            )}
          </div>
          {cat && cat.reference_photo_id == null && (
            <div className="text-[11px] text-warn">{cat.name} has no reference yet. Set one to enable alignment.</div>
          )}
        </div>

        <div className="flex items-center justify-between">
          <Toggle
            checked={p.flip_horizontal}
            disabled={busy}
            onChange={(v) => patch.mutate({ id: p.id, body: { flip_horizontal: v } })}
            label="Flip horizontally"
          />
          <FlipHorizontal2 size={14} className="text-muted" />
        </div>
        <div className="flex items-center justify-between gap-2">
          <Toggle
            checked={!!p.abs_visible}
            disabled={busy}
            onChange={(v) => patch.mutate({ id: p.id, body: { abs_visible: v } })}
            label="Abs visible (Abs group)"
          />
          <span className="text-[11px] text-muted">
            {p.abs_source === "manual" ? (
              <button className="underline" onClick={() => patch.mutate({ id: p.id, body: { abs_auto: true } })} title="go back to automatic detection">
                set by you · reset
              </button>
            ) : p.abs_score != null ? (
              `detected ${Math.round(p.abs_score * 100)}%`
            ) : (
              "not checked yet"
            )}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <Toggle
            checked={!!p.face_visible}
            disabled={busy}
            onChange={(v) => patch.mutate({ id: p.id, body: { face_visible: v } })}
            label="Face visible (Face group)"
          />
          <span className="text-[11px] text-muted">
            {p.face_source === "manual" ? (
              <button className="underline" onClick={() => patch.mutate({ id: p.id, body: { face_auto: true } })} title="go back to automatic detection">
                set by you · reset
              </button>
            ) : p.face_score != null ? (
              `detected ${Math.round(p.face_score * 100)}%`
            ) : (
              "not checked yet"
            )}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <Toggle
            checked={!!p.sleeves}
            disabled={busy}
            onChange={(v) => patch.mutate({ id: p.id, body: { sleeves: v } })}
            label="Sleeves cover the arms"
          />
          <span className="text-[11px] text-muted">
            {p.sleeves_source === "manual" ? (
              <button className="underline" onClick={() => patch.mutate({ id: p.id, body: { sleeves_auto: true } })} title="go back to automatic detection">
                set by you · reset
              </button>
            ) : p.sleeves_score != null ? (
              `bare arms ${Math.round(p.sleeves_score * 100)}%`
            ) : (
              "not checked yet"
            )}
          </span>
        </div>
        <Toggle
          checked={p.excluded}
          disabled={busy}
          onChange={(v) => patch.mutate({ id: p.id, body: { excluded: v } })}
          label="Exclude from viewer and exports"
        />

        <div className="flex flex-col gap-1">
          <span className="label">Notes</span>
          <textarea
            className="input min-h-[56px] resize-y"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            onBlur={() => {
              if (notes !== (p.notes ?? "")) patch.mutate({ id: p.id, body: { notes } });
            }}
            placeholder="anything worth remembering about this shot"
          />
        </div>

        <div className="card flex flex-col gap-1 p-2.5">
          <div className="label mb-1">Details</div>
          <Row k="Size" v={`${p.width} × ${p.height} ${p.ext.toUpperCase()}`} />
          <Row k="Source" v={p.source} />
          <Row k="Added" v={fmtDateTime(p.created_at)} />
          <Row
            k="Landmarks"
            v={
              <span className={p.landmarks_status === "ok" ? "text-ok" : p.landmarks_status === "pending" ? "text-muted" : "text-danger"}>
                {p.landmarks_status}
                {p.landmarks_quality != null ? ` · q ${Math.round(p.landmarks_quality * 100)}%` : ""}
              </span>
            }
          />
          <Row
            k="Alignment"
            v={
              p.alignment ? (
                <span className={p.alignment.stale ? "text-warn" : ""}>
                  {p.alignment.method}
                  {p.alignment.stale ? " · out of date" : ""}
                  {p.alignment.residual_px != null ? ` · ${p.alignment.residual_px.toFixed(0)} px` : ""}
                </span>
              ) : (
                <span className="text-muted">not aligned</span>
              )
            }
          />
          {flags.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {flags.map((f) => (
                <span key={f} className="badge bg-warn/15 text-warn">
                  {f.replace(/_/g, " ")}
                </span>
              ))}
            </div>
          )}
          <div className="mt-1 break-all text-[10px] text-muted" title={p.original_path}>
            {p.original_path}
          </div>
        </div>

        <div className="flex flex-wrap gap-1.5">
          <Link to={`/editor/${p.id}`} className="btn btn-sm">
            <ExternalLink size={14} /> Alignment editor
          </Link>
          <button
            className="btn btn-sm"
            title="re-run landmark detection for this photo"
            onClick={() =>
              post(`/photos/${p.id}/reprocess`, { what: "landmarks" })
                .then(() => toast.info("Landmark job queued"))
                .catch((e: Error) => toast.error(e.message))
            }
          >
            <RefreshCw size={14} /> Landmarks
          </button>
          <a className="btn btn-sm" href={`${p.original_url}?download=1`}>
            Download original
          </a>
          <ConfirmButton
            className="btn btn-sm btn-danger ml-auto"
            confirmText="Move to trash?"
            onConfirm={() => remove.mutate(p.id, { onSuccess: onClose })}
          >
            <Trash2 size={14} /> Delete
          </ConfirmButton>
        </div>
      </div>
      <MeasureOverlayModal photoId={measureOpen ? p.id : null} onClose={() => setMeasureOpen(false)} />
    </aside>
  );
}
