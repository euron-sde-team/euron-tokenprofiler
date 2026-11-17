"use client";

import { useCallback, useEffect, useState } from "react";

export type Theme = "dark" | "light";
const KEY = "tokenprofiler.theme";

export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>("dark");
  useEffect(() => {
    setTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark");
  }, []);
  const toggle = useCallback(() => {
    setTheme((t) => {
      const next: Theme = t === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { window.localStorage.setItem(KEY, next); } catch { /* ignore */ }
      return next;
    });
  }, []);
  return { theme, toggle };
}

/** Inline script run before first paint so the saved theme never flashes. */
export const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem("${KEY}");if(!t){t=window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"}document.documentElement.dataset.theme=t}catch(e){document.documentElement.dataset.theme="dark"}})();`;
