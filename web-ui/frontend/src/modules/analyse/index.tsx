// Modul Analyse (ADR 0036): Routing, Verkabelung, Abgleich mit dem IPAM und Datenqualität.
import { lazy } from "react";
import { L } from "../../i18n";
import type { ViewEntry } from "../types";

const Cabling = lazy(() => import("../../Cabling"));
const Reconcile = lazy(() => import("../../Reconcile"));
const Routing = lazy(() => import("../../Routing"));

export const VIEWS: ViewEntry[] = [
  {
    key: "routing",
    module: "analyse",
    label: "Routing",
    icon: "route",
    section: "doc",
    order: 60,
    render: (p) => (
      <Routing
        key={p.paramsKey}
        tenantId={p.tenantId}
        tenantLabel={p.tenantLabel}
        canEdit={p.me.isAdmin || p.me.isOperator}
        params={p.params}
      />
    ),
  },
  {
    key: "verkabelung",
    module: "analyse",
    label: L("Verkabelung", "Cabling"),
    icon: "cable",
    section: "doc",
    order: 70,
    render: (p) => <Cabling tenantId={p.tenantId} tenantLabel={p.tenantLabel} />,
  },
  {
    key: "abgleich",
    module: "analyse",
    label: L("Abgleich", "Reconciliation"),
    icon: "reconcile",
    section: "doc",
    order: 100,
    needsTenant: true,
    render: (p) => (
      <Reconcile
        me={p.me}
        tenantId={p.tenantId}
        tenantLabel={p.tenantLabel}
        params={p.params}
        onGo={p.go}
        onChanged={p.reloadStats}
        onOpenFindings={p.openFindings}
      />
    ),
  },
];
