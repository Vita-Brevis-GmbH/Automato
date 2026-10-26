// Zuklappbare Bereiche (0.73.0, Anwender: Übersicht in der Applikations-Doku). Der Zustand je
// Bereich bleibt im Browser (localStorage); beim Drucken sind die gewählten Bereiche offen.
import { useCallback, useState } from "react";

export interface Fold {
  open: boolean;
  toggle: () => void;
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* localStorage nicht verfügbar */
  }
}

export function readStored<T>(key: string, fallback: T): T {
  return readJson(key, fallback);
}

// Bereiche einer Ansicht: fold(key, offen?) und alle auf einmal auf- bzw. zuklappen
export function useFolds(storageKey: string, forced: Set<string> | null = null) {
  const [state, setState] = useState<Record<string, boolean>>(() => readJson(storageKey, {}));
  const fold = useCallback(
    (key: string, initial = true): Fold => ({
      open: forced ? forced.has(key) : (state[key] ?? initial),
      toggle: () =>
        setState((prev) => {
          const next = { ...prev, [key]: !(prev[key] ?? initial) };
          writeJson(storageKey, next);
          return next;
        }),
    }),
    [state, forced, storageKey],
  );
  const setAll = (keys: string[], open: boolean) => {
    const next = { ...state, ...Object.fromEntries(keys.map((k) => [k, open])) };
    writeJson(storageKey, next);
    setState(next);
  };
  return { fold, setAll };
}

// Titel eines Bereichs: Klick klappt auf bzw. zu (Pfeil per CSS, der Titel bleibt reiner Text)
export function FoldTitle({ fold, children, id }: { fold?: Fold; children: React.ReactNode; id?: string }) {
  if (!fold) return <h2>{children}</h2>;
  return (
    <h2
      className="fold-title"
      id={id}
      role="button"
      tabIndex={0}
      aria-expanded={fold.open}
      title={fold.open ? "zuklappen" : "aufklappen"}
      onClick={fold.toggle}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          fold.toggle();
        }
      }}
    >
      {children}
    </h2>
  );
}
