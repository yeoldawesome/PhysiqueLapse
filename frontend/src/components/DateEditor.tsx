import { useEffect, useState } from "react";
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
  const [value, setValue] = useState(toDatetimeLocalValue(photo.taken_at));
  useEffect(() => setValue(toDatetimeLocalValue(photo.taken_at)), [photo.id, photo.taken_at]);
  const dirty = value !== toDatetimeLocalValue(photo.taken_at);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <span className="label">Date taken</span>
        <ConfidenceBadge photo={photo} />
      </div>
      <div className="flex items-center gap-1.5">
        <input
          type="datetime-local"
          className="input flex-1"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && dirty && value) onSave(value + ":00");
          }}
        />
        <button className="btn btn-sm" disabled={!dirty || !value || busy} onClick={() => onSave(value + ":00")}>
          Save
        </button>
        {photo.taken_at && (
          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={onClear} title="remove date">
            Clear
          </button>
        )}
      </div>
      <div className="text-[11px] text-muted">{fmtDateTime(photo.taken_at)}</div>
    </div>
  );
}
