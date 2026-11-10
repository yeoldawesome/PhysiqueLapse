import { useState } from "react";
import { CalendarClock, EyeOff, FlipHorizontal2, Trash2, X, Check } from "lucide-react";
import type { Category } from "../api/types";
import { useBulk } from "../api/queries";
import { ConfirmButton } from "./ui";
import { fmtDateTime } from "../lib/format";

export default function BulkBar({
  ids,
  categories,
  onClear,
  total,
  onSelectAll,
}: {
  ids: number[];
  categories: Category[];
  onClear: () => void;
  total: number;
  onSelectAll: () => void;
}) {
  const bulk = useBulk();
  const [date, setDate] = useState("");
  const [shift, setShift] = useState("1");
  const busy = bulk.isPending;
  const run = (body: Parameters<typeof bulk.mutate>[0]) => bulk.mutate(body);
  if (ids.length === 0) return null;
  return (
    <div className="sticky bottom-0 z-20 flex flex-wrap items-center gap-2 border-t border-border bg-panel/95 px-4 py-2 text-sm backdrop-blur">
      <span className="font-medium tabular-nums">{ids.length} selected</span>
      {ids.length < total && (
        <button className="btn btn-ghost btn-sm" onClick={onSelectAll}>
          select all {total}
        </button>
      )}
      <span className="mx-1 h-5 w-px bg-border" />

      <select
        className="select"
        defaultValue=""
        disabled={busy}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "") return;
          run({ ids, patch: v === "clear" ? { clear_pose: true } : { pose_category_id: Number(v) } });
          e.target.value = "";
        }}
      >
        <option value="">assign pose…</option>
        {categories.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
        <option value="clear">— unassign —</option>
      </select>
      <button className="btn btn-sm" disabled={busy} onClick={() => run({ ids, accept_pose: true })} title="confirm the pose each selected photo already has">
        <Check size={14} /> Accept poses
      </button>

      <span className="mx-1 h-5 w-px bg-border" />
      <input type="datetime-local" className="input" value={date} onChange={(e) => setDate(e.target.value)} />
      <ConfirmButton
        className="btn btn-sm"
        disabled={!date || busy}
        confirmText={`Set ${ids.length} to ${date ? fmtDateTime(date) : ""}?`}
        onConfirm={() => run({ ids, patch: { taken_at: date + ":00" } })}
      >
        set date
      </ConfirmButton>
      <span className="flex items-center gap-1">
        <CalendarClock size={14} className="text-muted" />
        <input type="number" step="1" className="input w-16" value={shift} onChange={(e) => setShift(e.target.value)} />
        <ConfirmButton
          className="btn btn-sm"
          disabled={busy || !shift || Number(shift) === 0}
          confirmText={`Move ${ids.length} photo${ids.length === 1 ? "" : "s"} ${Math.abs(Number(shift))} day${Math.abs(Number(shift)) === 1 ? "" : "s"} ${Number(shift) > 0 ? "later" : "earlier"}?`}
          onConfirm={() => run({ ids, date_shift_days: Number(shift) })}
        >
          shift days
        </ConfirmButton>
      </span>
      <select
        className="select"
        defaultValue=""
        disabled={busy}
        onChange={(e) => {
          const v = e.target.value as "" | "exif" | "filename" | "mtime" | "auto";
          if (v) run({ ids, rederive_date_from: v });
          e.target.value = "";
        }}
      >
        <option value="">re-derive date…</option>
        <option value="auto">auto (EXIF → filename → file time)</option>
        <option value="exif">from EXIF</option>
        <option value="filename">from filename</option>
        <option value="mtime">from file time</option>
      </select>

      <span className="mx-1 h-5 w-px bg-border" />
      <button className="btn btn-sm" disabled={busy} onClick={() => run({ ids, toggle_flip: true })} title="toggle horizontal flip">
        <FlipHorizontal2 size={14} /> flip
      </button>
      <button className="btn btn-sm" disabled={busy} onClick={() => run({ ids, patch: { excluded: true } })} title="exclude from viewer">
        <EyeOff size={14} /> exclude
      </button>
      <button className="btn btn-sm" disabled={busy} onClick={() => run({ ids, patch: { excluded: false } })}>
        include
      </button>
      <ConfirmButton
        className="btn btn-sm btn-danger"
        confirmText={`Trash ${ids.length}?`}
        disabled={busy}
        onConfirm={() => run({ ids, delete: true })}
      >
        <Trash2 size={14} /> delete
      </ConfirmButton>
      <button className="btn btn-ghost btn-sm ml-auto" onClick={onClear} title="clear selection (Esc)">
        <X size={14} /> clear
      </button>
    </div>
  );
}
