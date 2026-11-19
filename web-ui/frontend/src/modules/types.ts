// Oberfläche je Modul (0.110.0, ADR 0039; Anwender: „Oberfläche je Modul“, Roadmap Version 1 Nr. 1). Jedes Modul (ADR 0036)
// hat einen Ordner `src/modules/<modul>/` mit seinen Ansichten (`VIEWS`); Navigation und Anzeige baut die Shell (App.tsx)
// aus dem Verzeichnis `src/modules/index.ts`. Eine neue Ansicht ist ein Eintrag im Ordner ihres Moduls.
import type { ReactNode } from "react";
import type { AuthConfig, Capability, FindingSummary, ModuleKey, RuleSummary, Tenant } from "../api";
import type { Me } from "../auth";

export type View =
  | "uebersicht"
  | "laeufe"
  | "regeln"
  | "objekte"
  | "nat"
  | "routing"
  | "statisch"
  | "vpn"
  | "verkabelung"
  | "spanningtree"
  | "reservierungen"
  | "asn"
  | "vlans"
  | "wege"
  | "vergleich"
  | "apps"
  | "findings"
  | "abgleich"
  | "design"
  | "users"
  | "tenants"
  | "kunden"
  | "kundenbenutzer"
  | "kundenportal"
  | "dict"
  | "agents"
  | "netbox"
  | "system";

// Parameter hinter der Ansicht (#regeln?object=H_web01) - Verlinkung zwischen den Ansichten
export type ViewParams = Record<string, string>;

// Daten der Übersicht + Zähler in der Navigation (je Mandant geladen).
export interface Stats {
  rules: RuleSummary | null; // Kennzahlen aller Regeln (Firewall + ACI)
  nat: number | null; // Anzahl VIPs/SNAT-Pools
  findings: FindingSummary | null; // Zähler (offen/gesamt/je Typ)
  error: unknown;
}

// Was die Shell jeder Ansicht mitgibt
export interface ViewProps {
  cfg: AuthConfig;
  me: Me;
  docMe: Me; // Kunden-Benutzer pflegen nichts als Operator
  tenantId: number | null;
  tenantLabel: string;
  tenants: Tenant[];
  tenantsList: Tenant[] | null; // null = noch nicht geladen
  params: ViewParams;
  paramsKey: string; // neu aufbauen, wenn sich die Parameter ändern
  stats: Stats;
  findingType: string; // Vorfilter aus der Abgleich-Ansicht
  go: (v: View, p?: ViewParams) => void;
  reloadStats: () => void;
  reloadTenants: () => Promise<Tenant[]>;
  selectTenant: (id: number | null) => void;
  customersChanged: () => void;
  openFindings: (type: string) => void;
}

// Netzwerkschicht einer Ansicht bzw. Stelle (1.19.0, ADR 0067); ohne = übergreifend bzw. Administration
export type Layer = "l1" | "l2" | "l3" | "l4-7";

// Stelle in der Navigation (ADR 0067): eine Ansicht bzw. ein Reiter davon in einer Schicht, Adresse `#<schicht>/<slug>`;
// `params` sind fest (z. B. `{ tab: "peers" }` für BGP im Routing)
export interface Place {
  slug: string;
  layer: Layer;
  label: string;
  icon: string;
  order: number;
  params?: ViewParams;
  customer?: boolean; // Kunden-Benutzer: abweichend von der Ansicht (z. B. Inventar nicht)
}

export interface ViewEntry {
  key: View;
  module: ModuleKey; // Modul nach ADR 0036 (`orchestrator/app/modules.py`, `views`)
  label: string;
  icon: string;
  section: "doc" | "admin";
  order: number; // Reihenfolge in der Navigation
  adminOnly?: boolean;
  customer?: boolean; // auch für Kunden-Benutzer (ADR 0018; serverseitig eingeschränkt)
  customerOnly?: boolean; // nur für Kunden-Benutzer (feinere Rollen)
  // ADR 0068: Plattform-Funktion – nie für Benutzer eines Kunden-Realms (auch nicht im eigenen Mandanten)
  platform?: boolean;
  // ADR 0068: mit `adminOnly` – auch für den Admin im eigenen Mandanten (Verwaltung des Mandanten)
  tenantAdmin?: boolean;
  cap?: Capability; // Kunden-Benutzer: nur mit dieser Capability
  needsTenant?: boolean; // erst, wenn der Mandant feststeht
  // Schicht (ADR 0067): die Ansicht steht in der Navigation unter ihrer Schicht (`#<schicht>/<key>`); mit `places` ihre
  // Stellen (die erste ist die Ansicht selbst), ohne beides übergreifend bzw. Administration (`#<key>`)
  layer?: Layer;
  places?: Place[];
  render: (p: ViewProps) => ReactNode;
}
