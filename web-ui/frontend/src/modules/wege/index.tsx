// Modul Wegsucher (ADR 0036): Weg-Suche und Verbindungen Ende zu Ende.
import { lazy } from "react";
import { L } from "../../i18n";
import type { ViewEntry } from "../types";

const Paths = lazy(() => import("../../Paths"));

export const VIEWS: ViewEntry[] = [
  {
    key: "wege",
    module: "wege",
    label: L("Weg-Suche", "Path search"),
    icon: "path",
    section: "doc",
    order: 50,
    render: (p) => (
      <Paths
        key={p.paramsKey}
        tenantId={p.tenantId}
        tenantLabel={p.tenantLabel}
        canEdit={p.me.isAdmin || p.me.isOperator}
        params={p.params}
      />
    ),
  },
];
