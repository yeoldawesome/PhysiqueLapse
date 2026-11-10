import { useEffect, useState } from "react";
import { CalendarCheck, Pencil, X } from "lucide-react";
import type { Photo } from "../api/types";
import { DATE_SOURCE_LABEL, fmtDateTime, toDatetimeLocalValue } from "../lib/format";

export function ConfidenceBadge({ photo }: { photo: Pick<Photo, "taken_at_source" | "taken_at_confidence" | "taken_at"> }) {
  const c = !photo.taken_at ? "none" : photo.taken_at_confidence;
  const cls = c === "high" ? "bg-ok/15 text-ok" : c === "medium" ? "bg-accent-2/15 text-accent" : "bg-warn/15 text-warn";
  return (
    <span className={`badge ${cls}`} title={`confidence: ${photo.taken_at_confidence}`}>
      {photo.taken_at ? DATE_SOURCE_LABEL[photo.taken_at_source] ?? photo.taken_at_source : "no date"}
    </span>
  );
}

/**
 * Date taken, with an explicit two-step change: edit -> "Change date" -> confirm panel
 * showing old and new date -> Confirm. Nothing is saved before Confirm.
 */
export default function DateEditor({
  photo,
  onSave,
  onClear,
  busy,
}: {
  photo: Photo;
  onSave: (iso: string) => void;
  onClear: () => void;
  busy?: boolean;
}) {
  const current = toDatetimeLocalValue(photo.taken_at);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(current);
  const [confirm, setConfirm] = useState<"save" | "clear" | null>(null);
  useEffect(() => {
    setValue(toDatetimeLocalValue(photo.taken_at));
    setEditing(false);
    setConfirm(null);
  }, [photo.id, photo.taken_at]);

  const dirty = value !== current && value !== "";
  const lowConfidence = !photo.taken_at || photo.taken_at_confidence === "low";

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <span className="label">Date taken</span>
        <ConfidenceBadge photo={photo} />
        {!editing && (
          <button className="btn btn-ghost btn-sm ml-auto" onClick={() => setEditing(true)} disabled={busy}>
            <Pencil size={13} /> change
          </button>
        )}
      </div>
      <div className={`text-sm ${lowConfidence ? "text-warn" : ""}`}>{fmtDateTime(photo.taken_at)}</div>
      {lowConfidence && !editing && (
        <div className="text-[11px] text-muted">
          {photo.taken_at ? "This date comes from the file's timestamp and may be the download date, not when the photo was taken." : "No date found."}
        </div>
      )}

      {editing && confirm === null && (
        <div className="flex flex-col gap-1.5 rounded-md border border-border bg-bg p-2">
          <input
            type="datetime-local"
            className="input w-full"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && dirty) setConfirm("save");
              if (e.key === "Escape") {
                setValue(current);
                setEditing(false);
              }
            }}
            autoFocus
          />
          <div className="flex flex-wrap gap-1.5">
            <button className="btn btn-sm btn-primary" disabled={!dirty || busy} onClick={() => setConfirm("save")}>
              Change date
            </button>
            <button
              className="btn btn-sm"
              onClick={() => {
                setValue(current);
                setEditing(false);
              }}
            >
              Cancel
            </button>
            {photo.taken_at && (
              <button className="btn btn-sm btn-ghost ml-auto text-muted" disabled={busy} onClick={() => setConfirm("clear")}>
                remove date
              </button>
            )}
          </div>
        </div>
      )}

      {confirm && (
        <div className="fade-in flex flex-col gap-2 rounded-md border border-accent/50 bg-accent/5 p-2.5 text-sm">
          {confirm === "save" ? (
            <div>
              Change the date of this photo
              <div className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 text-xs">
                <span className="text-muted">from</span>
                <span className="tabular-nums line-through decoration-muted/70">{fmtDateTime(photo.taken_at)}</span>
                <span className="text-muted">to</span>
                <span className="tabular-nums font-medium text-text">{fmtDateTime(value + ":00")}</span>
              </div>
            </div>
          ) : (
            <div>Remove the date from this photo? It will be left out of the Viewer until it has a date again.</div>
          )}
          <div className="flex gap-1.5">
            <button
              className={`btn btn-sm ${confirm === "clear" ? "btn-danger" : "btn-primary"}`}
              disabled={busy}
              onClick={() => {
                if (confirm === "save") onSave(value + ":00");
                else onClear();
                setConfirm(null);
                setEditing(false);
              }}
              autoFocus
            >
              <CalendarCheck size={14} /> Confirm
            </button>
            <button className="btn btn-sm" onClick={() => setConfirm(null)}>
              <X size={14} /> Back
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
