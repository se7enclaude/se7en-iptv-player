import { useEffect } from "react";
import { Page, useApp } from "../store/app";

export const SHORTCUTS: { keys: string; key: string }[] = [
  { keys: "Ctrl + 1 … 6", key: "shortcuts.nav" },
  { keys: "Ctrl + F", key: "shortcuts.stream.search" },
  { keys: "↑ ↓", key: "shortcuts.stream.zap" },
  { keys: "0 – 9", key: "shortcuts.stream.number" },
  { keys: "Retour arrière", key: "shortcuts.stream.back" },
  { keys: "Espace", key: "shortcuts.stream.play" },
  { keys: "← →", key: "shortcuts.stream.seek" },
  { keys: "F / Entrée", key: "shortcuts.stream.full" },
  { keys: "M", key: "shortcuts.stream.mute" },
  { keys: "I", key: "shortcuts.stream.info" },
  { keys: "Ctrl + ,", key: "shortcuts.settings" },
  { keys: "Ctrl + L", key: "shortcuts.logs" },
  { keys: "?", key: "shortcuts.help" },
  { keys: "Esc", key: "shortcuts.close" },
];

// Same order as the tabs of se7en IPTV Player
const PAGES: Page[] = ["home", "live", "guide", "movies", "series", "recordings"];

/** Global navigation shortcuts. Page-level shortcuts live in the pages. */
export function useGlobalShortcuts(openHelp: () => void) {
  const setPage = useApp((s) => s.setPage);
  const toggleSidebar = useApp((s) => s.toggleSidebar);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (e.ctrlKey && !e.shiftKey && !e.altKey) {
        const n = Number(e.key);
        if (e.key >= "0" && e.key <= "9") {
          const target = PAGES[n === 0 ? 9 : n - 1];
          if (!target) return;
          e.preventDefault();
          setPage(target);
        } else if (e.key === ",") {
          e.preventDefault();
          setPage("settings");
        } else if (e.key.toLowerCase() === "f") {
          e.preventDefault();
          setPage("search");
        } else if (e.key.toLowerCase() === "l") {
          e.preventDefault();
          setPage("logs");
        } else if (e.key.toLowerCase() === "b") {
          e.preventDefault();
          toggleSidebar();
        }
      } else if (!typing && e.key === "?") {
        e.preventDefault();
        openHelp();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPage, toggleSidebar, openHelp]);
}

/** Page-scoped shortcut helper. */
export function useKey(combo: string, handler: (e: KeyboardEvent) => void, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const [mod, key] = combo.includes("+") ? combo.split("+") : ["", combo];
    const onKey = (e: KeyboardEvent) => {
      if (mod === "ctrl" && !e.ctrlKey) return;
      if (mod === "" && (e.ctrlKey || e.altKey || e.metaKey)) return;
      if (e.key.toLowerCase() !== key.toLowerCase()) return;
      handler(e);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [combo, handler, enabled]);
}
