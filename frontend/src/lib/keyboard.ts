import { useEffect } from "react";

export type KeyHandler = (e: KeyboardEvent) => void;

function isTyping(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null;
  if (!t) return false;
  const tag = t.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable;
}

/**
 * Register keyboard shortcuts for the lifetime of a component.
 * Map keys are `key` values, optionally prefixed with "ctrl+" / "shift+" (e.g. "ctrl+a", "ArrowLeft", " ").
 * Shortcuts are ignored while typing in a form field unless the key is prefixed with "!".
 */
export function useShortcuts(map: Record<string, KeyHandler>, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      const typing = isTyping(e);
      for (const [spec, handler] of Object.entries(map)) {
        let s = spec;
        let force = false;
        if (s.startsWith("!")) {
          force = true;
          s = s.slice(1);
        }
        if (typing && !force) continue;
        const parts = s.split("+");
        const key = parts.pop()!;
        const needCtrl = parts.includes("ctrl");
        const needShift = parts.includes("shift");
        const ctrl = e.ctrlKey || e.metaKey;
        if (needCtrl !== ctrl) continue;
        if (needShift && !e.shiftKey) continue;
        if (!needShift && e.shiftKey && key.length === 1 && key.toLowerCase() !== key.toUpperCase()) {
          // letter shortcuts without shift must not fire on shift+letter
          if (key === key.toLowerCase()) continue;
        }
        if (e.key.toLowerCase() === key.toLowerCase() || e.key === key) {
          handler(e);
          return;
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [map, enabled]);
}
