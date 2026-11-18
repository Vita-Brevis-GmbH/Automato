// Ansichten nach Netzwerkschichten (1.19.0, ADR 0067; Anwender 2026-11-17: „Modul nach Netzwerkschichten“). Die Schicht
// ist ein Merkmal der Ansicht (`layer`, `places` in `src/modules/<modul>/index.tsx`) – die Module des Servers bleiben.
//
// - Adressen: Stellen einer Schicht `#<schicht>/<slug>[?…]` (z. B. `#l3/bgp` = Routing, Reiter BGP-Nachbarn),
//   übergreifende Ansichten und die Administration weiter `#<ansicht>`.
// - Alte Adressen leiten um (`resolve` meldet `redirect`): `#<ansicht>` auf ihre Stelle, mit `tab` auf die Stelle dieses
//   Reiters, umgezogene Reiter (`MOVED`) auf die neue Ansicht; übrige Parameter bleiben.
// - `hashOf(view, params)` ist die Adresse für Links und `go`, `replaceHash` schreibt sie ohne neuen Eintrag im Verlauf.
import { L } from "./i18n";
import { VIEWS } from "./modules";
import type { Layer, Place, View, ViewEntry, ViewParams } from "./modules";

export const LAYERS: { key: Layer; label: string; hint: string }[] = [
  { key: "l1", label: L("L1 · Physik", "L1 · Physical"), hint: L("Verkabelung, Ports, Transceiver, Kabelnummern", "Cabling, ports, transceivers, cable numbers") },
  { key: "l2", label: "L2 · Switching", hint: L("VLANs, Spanning Tree, VLAN-Reservierungen", "VLANs, Spanning Tree, VLAN reservations") },
  { key: "l3", label: "L3 · Routing", hint: L("Netze, VRF, Routing, statische Routen, BGP, AS-Nummern", "Networks, VRF, routing, static routes, BGP, AS numbers") },
  {
    key: "l4-7",
    label: L("L4–7 · Firewall & Dienste", "L4–7 · Firewall & services"),
    hint: L("Firewall-Regeln, Objekte, NAT/VIP, Load Balancer, VPN", "Firewall rules, objects, NAT/VIP, load balancer, VPN"),
  },
];
const LAYER_KEYS = LAYERS.map((l) => l.key) as string[];

// Stelle mit ihrer Ansicht
export interface NavPlace extends Place {
  view: View;
  entry: ViewEntry;
  home: boolean; // die Ansicht selbst (erste Stelle)
}

// Reiter, die in eine andere Ansicht umgezogen sind (ADR 0067)
const MOVED: { view: View; params: ViewParams; to: View }[] = [{ view: "verkabelung", params: { tab: "vlans" }, to: "vlans" }];

function placesOf(e: ViewEntry): NavPlace[] {
  if (e.places?.length) return e.places.map((p, i) => ({ ...p, view: e.key, entry: e, home: i === 0 }));
  if (e.layer) return [{ slug: e.key, layer: e.layer, label: e.label, icon: e.icon, order: e.order, view: e.key, entry: e, home: true }];
  return [];
}

export const PLACES: NavPlace[] = VIEWS.flatMap(placesOf);
const VIEW_KEYS = new Set<string>(VIEWS.map((v) => v.key));

const matches = (fixed: ViewParams | undefined, p: ViewParams) => Object.entries(fixed ?? {}).every(([k, v]) => p[k] === v);

// Stelle einer Ansicht mit ihren Parametern: die genaueste, deren feste Parameter passen, sonst die Ansicht selbst
export function placeOf(view: View, params: ViewParams = {}): NavPlace | null {
  const own = PLACES.filter((p) => p.view === view);
  if (!own.length) return null;
  const fitting = own.filter((p) => Object.keys(p.params ?? {}).length && matches(p.params, params));
  fitting.sort((a, b) => Object.keys(b.params ?? {}).length - Object.keys(a.params ?? {}).length);
  return fitting[0] ?? own.find((p) => p.home) ?? own[0];
}

function moved(view: View, params: ViewParams): { view: View; params: ViewParams } {
  const m = MOVED.find((x) => x.view === view && matches(x.params, params));
  if (!m) return { view, params };
  const rest = Object.fromEntries(Object.entries(params).filter(([k]) => !(k in m.params)));
  return { view: m.to, params: rest };
}

function query(params: ViewParams): string {
  const q = new URLSearchParams(params).toString();
  return q ? `?${q}` : "";
}

// Adresse einer Ansicht (ohne `#`-Präfix im Ergebnis: mit `#`)
export function hashOf(view: View, params: ViewParams = {}): string {
  const to = moved(view, params);
  const place = placeOf(to.view, to.params);
  if (!place) return `#${to.view}${query(to.params)}`;
  const rest = Object.fromEntries(Object.entries(to.params).filter(([k, v]) => (place.params ?? {})[k] !== v));
  return `#${place.layer}/${place.slug}${query(rest)}`;
}

export interface Resolved {
  view: View;
  params: ViewParams;
  redirect: string | null; // neue Adresse, wenn die gegebene alt bzw. nicht kanonisch ist
}

// Adresse auflösen: neue Adressen (`#l3/bgp`), übergreifende (`#apps`) und alte (`#routing?tab=peers` → `#l3/bgp`)
export function resolve(hash: string): Resolved {
  const raw = hash.replace(/^#\/?/, "");
  const [path, q = ""] = raw.split("?");
  const given = Object.fromEntries(new URLSearchParams(q).entries());
  const [layer, slug] = path.split("/");
  if (slug !== undefined && LAYER_KEYS.includes(layer)) {
    const place = PLACES.find((p) => p.layer === layer && p.slug === slug);
    if (place) {
      const params = { ...(place.params ?? {}), ...given };
      const canonical = hashOf(place.view, params);
      return { view: place.view, params, redirect: canonical !== `#${raw}` ? canonical : null };
    }
    return { view: "uebersicht", params: {}, redirect: "#uebersicht" };
  }
  if (VIEW_KEYS.has(path)) {
    const to = moved(path as View, given);
    const canonical = hashOf(to.view, to.params);
    return { view: to.view, params: to.params, redirect: canonical !== `#${raw}` ? canonical : null };
  }
  return { view: "uebersicht", params: {}, redirect: null };
}

export const HASH_CHANGED = "automato-hash"; // Adresse ohne Wechsel der Ansicht geändert (Reiter)

// Adresse ohne neuen Eintrag im Verlauf schreiben (Reiter innerhalb einer Ansicht); die Navigation zieht nach
export function replaceHash(view: View, params: ViewParams = {}): void {
  window.history.replaceState(null, "", hashOf(view, params));
  window.dispatchEvent(new Event(HASH_CHANGED));
}
