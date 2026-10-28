// Bereiche der Kategorien der Applikations-Doku (0.101.0): Anzeige je Bereich mit den Kategorien. Seit 0.104.0
// aus dem Katalog des Mandanten (Namenskonzept, `GET /zones/appdoc/catalog`); der Vorschlag nach dem Namen der
// Zone kommt vom Server (Muster je Bereich).
import type { AppdocArea, AppdocAreaDef, AppdocCatalog } from "./api";
import { L } from "./i18n";

export function areaLabel(catalog: AppdocCatalog | null, area: AppdocArea): string {
  const a = catalog?.areas.find((x) => x.key === area);
  if (!a) return area;
  const tables = [...new Set([a.inbound, a.outbound])].join("/");
  return `${L(a.label, a.label_en || a.label)} (${tables})`;
}

// angebotene Bereiche; ein gespeicherter, inzwischen nicht mehr angebotener bleibt sichtbar
export function areaChoices(catalog: AppdocCatalog | null, current?: AppdocArea | null): AppdocAreaDef[] {
  const active = (catalog?.areas ?? []).filter((a) => !a.off);
  if (!current || active.some((a) => a.key === current)) return active;
  const stale = catalog?.areas.find((a) => a.key === current);
  return [...active, stale ?? { key: current, label: current, label_en: current, inbound: 0, outbound: 0, markers: [], match: [], role: "", off: true, builtin: false }];
}
