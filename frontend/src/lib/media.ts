import { useSyncExternalStore } from "react";

function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(query);
      m.addEventListener("change", cb);
      return () => m.removeEventListener("change", cb);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/** Phone-width layout (below Tailwind's `md`). */
export const useNarrow = () => useMedia("(max-width: 767px)");

/** Touch screen as the main pointer (no hover, no right-click, no Shift/Ctrl). */
export const useCoarsePointer = () => useMedia("(pointer: coarse)");
