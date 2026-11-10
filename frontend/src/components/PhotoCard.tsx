import { memo } from "react";
import { AlertTriangle, Check, EyeOff, FlipHorizontal2, Star } from "lucide-react";
import type { Category, Photo } from "../api/types";
import { DATE_SOURCE_LABEL, fmtDate } from "../lib/format";

export type SelectMode = "single" | "toggle" | "range";

function PhotoCardInner({
  photo,
  category,
  selected,
  isReference,
  onSelect,
  onOpen,
}: {
  photo: Photo;
  category?: Category;
  selected: boolean;
  isReference?: boolean;
  onSelect: (id: number, mode: SelectMode) => void;
  onOpen: (id: number) => void;
}) {
  const lowDate = photo.taken_at_confidence === "low" || !photo.taken_at;
  const lmBad = photo.landmarks_status === "none" || photo.landmarks_status === "failed";
  const flags = photo.alignment?.flags ? Object.keys(photo.alignment.flags).filter((k) => photo.alignment!.flags[k]) : [];
  const stale = photo.alignment?.stale;
  return (
    <div
      className={`group relative cursor-pointer select-none overflow-hidden rounded-md border bg-panel transition-shadow ${
        selected ? "border-accent ring-2 ring-accent/50" : "border-border hover:border-muted/60"
      } ${photo.excluded ? "opacity-50" : ""}`}
      onClick={(e) => {
        if (e.shiftKey) onSelect(photo.id, "range");
        else if (e.ctrlKey || e.metaKey) onSelect(photo.id, "toggle");
        else {
          onSelect(photo.id, "single");
          onOpen(photo.id);
        }
      }}
      onDoubleClick={() => onOpen(photo.id)}
      title={photo.original_filename}
    >
      <div className="aspect-[4/5] w-full overflow-hidden bg-bg">
        <img
          src={photo.thumb_url}
          loading="lazy"
          decoding="async"
          alt=""
          className="h-full w-full object-cover"
          style={photo.flip_horizontal ? { transform: "scaleX(-1)" } : undefined}
          draggable={false}
        />
      </div>
      {/* selection checkbox */}
      <button
        className={`absolute left-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded border transition-opacity ${
          selected ? "border-accent bg-accent text-white opacity-100" : "border-white/60 bg-black/40 text-transparent opacity-0 group-hover:opacity-100"
        }`}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(photo.id, e.shiftKey ? "range" : "toggle");
        }}
        aria-label="select"
      >
        <Check size={12} strokeWidth={3} />
      </button>
      {/* top-right indicators */}
      <div className="absolute right-1.5 top-1.5 flex items-center gap-1">
        {isReference && (
          <span className="rounded bg-black/60 p-0.5 text-warn" title="reference photo">
            <Star size={12} fill="currentColor" />
          </span>
        )}
        {photo.flip_horizontal && (
          <span className="rounded bg-black/60 p-0.5 text-text" title="flipped">
            <FlipHorizontal2 size={12} />
          </span>
        )}
        {photo.excluded && (
          <span className="rounded bg-black/60 p-0.5 text-muted" title="excluded from viewer">
            <EyeOff size={12} />
          </span>
        )}
        {(lmBad || flags.length > 0 || stale) && (
          <span
            className={`rounded bg-black/60 p-0.5 ${lmBad ? "text-danger" : "text-warn"}`}
            title={lmBad ? `landmarks: ${photo.landmarks_status}` : stale ? "alignment out of date" : flags.join(", ")}
          >
            <AlertTriangle size={12} />
          </span>
        )}
      </div>
      {/* bottom strip */}
      <div className="flex items-center gap-1 px-1.5 py-1 text-[11px]">
        <span className={`truncate ${lowDate ? "text-warn" : "text-text"}`} title={`${fmtDate(photo.taken_at)} · ${DATE_SOURCE_LABEL[photo.taken_at_source]}`}>
          {fmtDate(photo.taken_at)}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1">
          {category && (
            <span
              className={`badge max-w-[90px] truncate ${photo.pose_needs_review ? "bg-warn/15 text-warn" : "bg-panel-2 text-muted"}`}
              title={`${category.name}${photo.pose_confidence != null ? ` · ${Math.round(photo.pose_confidence * 100)}%` : ""} · ${photo.pose_source}`}
            >
              {category.name}
            </span>
          )}
        </span>
      </div>
    </div>
  );
}

const PhotoCard = memo(PhotoCardInner);
export default PhotoCard;
