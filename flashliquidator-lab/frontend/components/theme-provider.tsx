"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type ThemePreference = "system" | "light" | "dark";
type Resolved = "light" | "dark";

const STORAGE_KEY = "fll-theme";

interface ThemeCtx {
  preference: ThemePreference;
  resolved: Resolved;
  setPreference: (p: ThemePreference) => void;
}

const Ctx = createContext<ThemeCtx>({ preference: "system", resolved: "light", setPreference: () => {} });

/** Runs before paint so the first render already has the right data-theme. */
export const themeInitScript = `(function(){try{var p=localStorage.getItem(${JSON.stringify(STORAGE_KEY)});var d=window.matchMedia('(prefers-color-scheme: dark)').matches;var t=(p==='light'||p==='dark')?p:(d?'dark':'light');document.documentElement.setAttribute('data-theme',t);}catch(e){}})();`;

function readPreference(): ThemePreference {
  try {
    const p = localStorage.getItem(STORAGE_KEY);
    if (p === "light" || p === "dark") return p;
  } catch {
    /* storage unavailable */
  }
  return "system";
}

function systemTheme(): Resolved {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPref] = useState<ThemePreference>("system");
  const [system, setSystem] = useState<Resolved>("light");

  useEffect(() => {
    // Sync with the pre-paint script and the OS preference after hydration.
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const sync = () => {
      setPref(readPreference());
      setSystem(mq.matches ? "dark" : "light");
    };
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const resolved: Resolved = preference === "system" ? system : preference;

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", resolved);
  }, [resolved]);

  const setPreference = useCallback((p: ThemePreference) => {
    setPref(p);
    try {
      if (p === "system") localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, p);
    } catch {
      /* storage unavailable */
    }
    setSystem(systemTheme());
  }, []);

  const value = useMemo(() => ({ preference, resolved, setPreference }), [preference, resolved, setPreference]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme() {
  return useContext(Ctx);
}
