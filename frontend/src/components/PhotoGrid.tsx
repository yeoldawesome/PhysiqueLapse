import { useCallback, useRef } from "react";
import type { Category, Photo } from "../api/types";
import PhotoCard, { type SelectMode } from "./PhotoCard";

export default function PhotoGrid({
  photos,
  categories,
  selected,
  setSelected,
  onOpen,
  onAccept,
  size = 168,
}: {
  photos: Photo[];
  categories: Category[];
  selected: Set<number>;
  setSelected: (next: Set<number>) => void;
  onOpen: (id: number) => void;
  onAccept?: (id: number, poseId: number) => void;
  size?: number;
}) {
  const anchor = useRef<number | null>(null);
  const catById = new Map(categories.map((c) => [c.id, c]));
  const refIds = new Set(categories.map((c) => c.reference_photo_id).filter((x): x is number => x != null));

  const onSelect = useCallback(
    (id: number, mode: SelectMode) => {
      const next = new Set(selected);
      if (mode === "single") {
        next.clear();
        next.add(id);
        anchor.current = id;
      } else if (mode === "toggle") {
        if (next.has(id)) next.delete(id);
        else next.add(id);
        anchor.current = id;
      } else {
        const ids = photos.map((p) => p.id);
        const a = anchor.current != null ? ids.indexOf(anchor.current) : -1;
        const b = ids.indexOf(id);
        if (a < 0 || b < 0) {
          next.add(id);
        } else {
          const [lo, hi] = a < b ? [a, b] : [b, a];
          for (let i = lo; i <= hi; i++) next.add(ids[i]);
        }
      }
      setSelected(next);
    },
    [photos, selected, setSelected],
  );

  return (
    <div
      className="grid gap-2"
      style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${size}px, 1fr))` }}
    >
      {photos.map((p) => (
        <PhotoCard
          key={p.id}
          photo={p}
          category={p.pose_category_id != null ? catById.get(p.pose_category_id) : undefined}
          selected={selected.has(p.id)}
          isReference={refIds.has(p.id)}
          onSelect={onSelect}
          onOpen={onOpen}
          onAccept={onAccept}
        />
      ))}
    </div>
  );
}
