import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { Category } from "../api/types";

/** One reference photo per group, shown above the needs-review list.
 *  Clicking a reference assigns that group to the selected photo(s). */
export default function PoseExamples({
  categories,
  selectedCount,
  onPick,
}: {
  categories: Category[];
  selectedCount: number;
  onPick: (poseId: number) => void;
}) {
  const [shown, setShown] = useState<boolean>(() => {
    try {
      return localStorage.getItem("pl.poseExamples") !== "0";
    } catch {
      return true;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("pl.poseExamples", shown ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [shown]);
  const withExample = categories.filter((c) => c.reference_photo_id != null);
  if (withExample.length === 0) return null;
  return (
    <div className="card mb-4">
      <button className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-sm font-medium" onClick={() => setShown((v) => !v)}>
        {shown ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        Group references
        <span className="ml-2 text-xs font-normal text-muted">
          {selectedCount
            ? `click a reference to move the ${selectedCount === 1 ? "selected photo" : `${selectedCount} selected photos`} to that group`
            : "select a photo, then click a reference to move it to that group · press A or ✓ to accept its current group"}
        </span>
      </button>
      {shown && (
        <div className="flex gap-2 overflow-x-auto px-3 pb-3">
          {withExample.map((c) => (
            <button
              key={c.id}
              className="group flex w-[112px] shrink-0 flex-col gap-1 rounded-md border border-border p-1 text-left hover:border-accent disabled:cursor-default disabled:hover:border-border"
              disabled={selectedCount === 0}
              onClick={() => onPick(c.id)}
              title={selectedCount ? `move to ${c.name}` : c.name}
            >
              <img src={`/api/photos/${c.reference_photo_id}/thumb`} alt="" className="aspect-[4/5] w-full rounded object-cover" draggable={false} />
              <span className="line-clamp-2 text-[11px] leading-tight">{c.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
