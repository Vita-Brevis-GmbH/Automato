// Modul Findings (ADR 0036): Befunde mit Triage und die Prüfungen, die sie liefern (0.110.0, ADR 0039).
import { lazy } from "react";
import type { ViewEntry } from "../types";

const Findings = lazy(() => import("../../Findings"));

export const VIEWS: ViewEntry[] = [
  {
    key: "findings",
    module: "findings",
    label: "Findings",
    icon: "findings",
    section: "doc",
    order: 90,
    customer: true,
    needsTenant: true,
    render: (p) => (
      <Findings
        key={p.paramsKey}
        me={p.me}
        tenantId={p.tenantId}
        tenantLabel={p.tenantLabel}
        initialType={p.findingType || p.params.type || ""}
        initialQuery={p.params.q ?? ""}
        initialTab={p.params.tab === "pruefungen" ? "pruefungen" : "befunde"}
        onChanged={p.reloadStats}
        onGo={p.go}
      />
    ),
  },
];
