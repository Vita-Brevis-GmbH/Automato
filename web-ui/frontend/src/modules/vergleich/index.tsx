// Modul Wege vergleichen (1.19.0, ADR 0067): ein L3-Weg gegen L1/L2 – VLAN auf allen Trunks erlaubt, Kabel und Nachbarn,
// Spanning Tree; übergreifend (neben der Weg-Suche), braucht Analyse und Wegsucher.
import { lazy } from "react";
import { L } from "../../i18n";
import type { ViewEntry } from "../types";

const PathCompare = lazy(() => import("./PathCompare"));

export const VIEWS: ViewEntry[] = [
  {
    key: "vergleich",
    module: "vergleich",
    label: L("Wege vergleichen", "Compare paths"),
    icon: "compare",
    section: "doc",
    order: 55,
    render: (p) => <PathCompare key={p.paramsKey} tenantId={p.tenantId} tenantLabel={p.tenantLabel} params={p.params} go={p.go} />,
  },
];
