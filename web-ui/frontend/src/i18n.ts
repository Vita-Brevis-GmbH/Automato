// Sprache der Oberfläche (0.86.0): Deutsch (Standard) oder Englisch, je Browser gemerkt.
//
// Der deutsche Text bleibt im Code stehen, der englische direkt daneben: `L("Speichern", "Save")`
// bzw. für JSX `L(<>…</>, <>…</>)`. Die Sprache steht beim Laden fest; das Umschalten lädt die
// Seite neu - so brauchen Konstanten auf Modulebene (Navigation, Beschriftungen) keine Hooks.
// Begriffe einheitlich nach `docs/GLOSSAR.md`. Texte vom Server folgen über `Accept-Language`.

export type Lang = "de" | "en";

const KEY = "automato_lang";

function initial(): Lang {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "de" || v === "en") return v;
  } catch {
    /* localStorage nicht verfügbar */
  }
  return "de";
}

export const lang: Lang = initial();

if (typeof document !== "undefined") document.documentElement.lang = lang;

/** Text in der gewählten Sprache - Deutsch zuerst, Englisch daneben. */
export function L<T>(de: T, en: T): T {
  return lang === "en" ? en : de;
}

/** Sprache wechseln (gemerkt im Browser) und die Seite neu laden. */
export function setLang(next: Lang): void {
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* localStorage nicht verfügbar */
  }
  window.location.reload();
}

/** Gebietsschema für Datum und Zahlen. */
export const locale = lang === "en" ? "en-GB" : "de-CH";

// Sprache der Dateien vom Server (0.87.0: Word, PDF, draw.io, SVG) - eigene Wahl im Export-Dialog,
// Standard = Sprache der Oberfläche; so entsteht z. B. eine englische Doku aus der deutschen Oberfläche
const EXPORT_KEY = "automato_export_lang";

export function exportLang(): Lang {
  try {
    const v = localStorage.getItem(EXPORT_KEY);
    if (v === "de" || v === "en") return v;
  } catch {
    /* localStorage nicht verfügbar */
  }
  return lang;
}

export function setExportLang(next: Lang): void {
  try {
    localStorage.setItem(EXPORT_KEY, next);
  } catch {
    /* localStorage nicht verfügbar */
  }
}
