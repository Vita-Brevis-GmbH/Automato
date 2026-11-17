// Modul Analyse (ADR 0036): Routing, VPN-Tunnel (1.19.0), Verkabelung, Spanning Tree (1.19.0), Abgleich mit dem IPAM und
// Datenqualität.
import { lazy } from "react";
import { L } from "../../i18n";
import type { ViewEntry } from "../types";

const Cabling = lazy(() => import("../../Cabling"));
const Reconcile = lazy(() => import("../../Reconcile"));
const Routing = lazy(() => import("../../Routing"));
const VpnTunnels = lazy(() => import("./VpnTunnels"));
const SpanningTree = lazy(() => import("./SpanningTree")); // 1.19.0

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
    key: "vpn",
    module: "analyse",
    label: L("VPN-Tunnel", "VPN tunnels"),
    icon: "vpn",
    section: "doc",
    order: 65,
    render: (p) => <VpnTunnels key={p.paramsKey} tenantId={p.tenantId} tenantLabel={p.tenantLabel} params={p.params} go={p.go} />,
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
    key: "spanningtree",
    module: "analyse",
    label: "Spanning Tree",
    icon: "stp",
    section: "doc",
    order: 75,
    render: (p) => (
      <SpanningTree
        key={p.paramsKey}
        tenantId={p.tenantId}
        tenantLabel={p.tenantLabel}
        canEdit={p.me.isAdmin || p.me.isOperator}
        params={p.params}
        onOpenFindings={p.openFindings}
      />
    ),
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
