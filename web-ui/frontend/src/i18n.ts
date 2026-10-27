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
