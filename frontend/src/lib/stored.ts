"use client";

import { useCallback, useMemo, useSyncExternalStore } from "react";

// Small per-browser preferences (localStorage). Nothing here is sent anywhere by itself.

const EVENT = "ml:stored";
const mem = new Map<string, string>();

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", cb);
  return () => { window.removeEventListener(EVENT, cb); window.removeEventListener("storage", cb); };
}
function read(key: string): string | null {
  try { return window.localStorage.getItem(key) ?? mem.get(key) ?? null; } catch { return mem.get(key) ?? null; }
}

/** `initial` must be a stable reference (a module-level constant). */
export function useStored<T>(key: string, initial: T): [T, (v: T) => void] {
  const raw = useSyncExternalStore(subscribe, () => read(key), () => null);
  const value = useMemo<T>(() => {
    if (raw === null) return initial;
    try { return { ...initial, ...(JSON.parse(raw) as T) }; } catch { return initial; }
  }, [raw, initial]);
  const set = useCallback((v: T) => {
    const s = JSON.stringify(v);
    mem.set(key, s);
    try { window.localStorage.setItem(key, s); } catch { /* private mode: keep it in memory for this visit */ }
    window.dispatchEvent(new Event(EVENT));
  }, [key]);
  return [value, set];
}
