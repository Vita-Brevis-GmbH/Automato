// Modul Analyse (ADR 0036): Routing, statische Routen und VPN-Tunnel (1.19.0), Verkabelung, Spanning Tree (1.19.0),
// Reservierungen und AS-Nummern (1.19.0, ADR 0066), Abgleich mit dem IPAM und Datenqualität. Seit 1.19.0 (ADR 0067) stehen
// die Ansichten in der Navigation unter ihrer Netzwerkschicht (`places`): L1 Verkabelung, L2 VLANs/Spanning Tree,
// L3 Routing/statisch/BGP/AS, L4–7 VPN; der Abgleich bleibt übergreifend. Seit 1.23.0 je Schicht L1–L3 ein Plan (`plaene`).
import { lazy } from "react";
import { L } from "../../i18n";
import type { ViewEntry } from "../types";

const Cabling = lazy(() => import("../../Cabling"));
const Reconcile = lazy(() => import("../../Reconcile"));
const Routing = lazy(() => import("../../Routing"));
const StaticRoutes = lazy(() => import("./StaticRoutes"));
const VpnTunnels = lazy(() => import("./VpnTunnels"));
const SpanningTree = lazy(() => import("./SpanningTree")); // 1.19.0
const Reservations = lazy(() => import("./Reservations")); // 1.19.0, ADR 0066
const AsNumbers = lazy(() => import("./AsNumbers")); // 1.19.0, ADR 0066
const Vlans = lazy(() => import("./Vlans")); // 1.19.0, ADR 0067 (vorher Reiter der Verkabelung)
const LayerPlan = lazy(() => import("./LayerPlan")); // 1.23.0, ADR 0067 Nachtrag: Pläne je Schicht

export const VIEWS: ViewEntry[] = [
  {
    key: "routing",
    module: "analyse",
    label: "Routing",
    icon: "route",
    section: "doc",
    order: 60,
    places: [
      { slug: "routing", layer: "l3", label: "Routing", icon: "route", order: 330 },
      { slug: "bgp", layer: "l3", label: "BGP", icon: "asn", order: 340, params: { tab: "peers" } },
    ],
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
    key: "statisch",
    module: "analyse",
    label: L("Statische Routen", "Static routes"),
    icon: "route",
    section: "doc",
    order: 61, // 1.19.0: statische Routen je Gerät und VRF
    places: [{ slug: "statisch", layer: "l3", label: L("Statische Routen", "Static routes"), icon: "route", order: 335 }],
    render: (p) => <StaticRoutes key={p.paramsKey} tenantId={p.tenantId} tenantLabel={p.tenantLabel} params={p.params} />,
  },
  {
    key: "vpn",
    module: "analyse",
    label: L("VPN-Tunnel", "VPN tunnels"),
    icon: "vpn",
    section: "doc",
    order: 65,
    places: [{ slug: "vpn", layer: "l4-7", label: L("VPN-Tunnel", "VPN tunnels"), icon: "vpn", order: 450 }],
    render: (p) => <VpnTunnels key={p.paramsKey} tenantId={p.tenantId} tenantLabel={p.tenantLabel} params={p.params} go={p.go} />,
  },
  {
    key: "verkabelung",
    module: "analyse",
    label: L("Verkabelung", "Cabling"),
    icon: "cable",
    section: "doc",
    order: 70,
    places: [
      { slug: "verkabelung", layer: "l1", label: L("Verkabelung", "Cabling"), icon: "cable", order: 110 },
      { slug: "ports", layer: "l1", label: "Ports", icon: "port", order: 120, params: { tab: "ports" } },
      { slug: "transceiver", layer: "l1", label: "Transceiver", icon: "sfp", order: 130, params: { tab: "transceiver" } },
      { slug: "kabelnummern", layer: "l1", label: L("Kabelnummern", "Cable numbers"), icon: "cable", order: 140, params: { tab: "labels" } },
    ],
    render: (p) => (
      <Cabling key={p.paramsKey} tenantId={p.tenantId} tenantLabel={p.tenantLabel} canEdit={p.me.isAdmin || p.me.isOperator} params={p.params} />
    ),
  },
  // 1.19.0 (ADR 0067): VLANs je Standort als eigene Ansicht in L2 (vorher Reiter der Verkabelung, alte Adresse leitet um)
  {
    key: "vlans",
    module: "analyse",
    label: "VLANs",
    icon: "vlan",
    section: "doc",
    order: 72,
    places: [{ slug: "vlans", layer: "l2", label: "VLANs", icon: "vlan", order: 210 }],
    render: (p) => <Vlans key={p.paramsKey} tenantId={p.tenantId} tenantLabel={p.tenantLabel} />,
  },
  {
    key: "spanningtree",
    module: "analyse",
    label: "Spanning Tree",
    icon: "stp",
    section: "doc",
    order: 75,
    places: [{ slug: "spanningtree", layer: "l2", label: "Spanning Tree", icon: "stp", order: 220 }],
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
    key: "asn",
    module: "analyse",
    label: L("AS-Nummern", "AS numbers"),
    icon: "asn",
    section: "doc",
    order: 62, // 1.19.0 (ADR 0066): je AS Nachbarn, Firewalls und Router
    places: [{ slug: "asn", layer: "l3", label: L("AS-Nummern", "AS numbers"), icon: "asn", order: 350 }],
    render: (p) => <AsNumbers key={p.paramsKey} tenantId={p.tenantId} tenantLabel={p.tenantLabel} params={p.params} go={p.go} />,
  },
  {
    key: "reservierungen",
    module: "analyse",
    label: L("Reservierungen", "Reservations"),
    icon: "reserve",
    section: "doc",
    order: 78, // 1.19.0 (ADR 0066): VLANs und AS-Nummern reservieren – je Schicht eine Stelle (ADR 0067)
    places: [
      { slug: "vlan-reservierungen", layer: "l2", label: L("VLAN-Reservierungen", "VLAN reservations"), icon: "reserve", order: 230, params: { tab: "vlan" } },
      { slug: "as-reservierungen", layer: "l3", label: L("AS-Reservierungen", "AS reservations"), icon: "reserve", order: 360, params: { tab: "asn" } },
    ],
    render: (p) => (
      <Reservations
        key={p.paramsKey}
        tenantId={p.tenantId}
        tenantLabel={p.tenantLabel}
        canEdit={p.me.isAdmin || p.me.isOperator}
        params={p.params}
        go={p.go}
      />
    ),
  },
  // 1.23.0 (ADR 0067 Nachtrag 1.23.0, Anwender 2026-11-20): je Schicht ein Plan für den ganzen Mandanten, Filter nach Standort
  {
    key: "plaene",
    module: "analyse",
    label: L("Pläne", "Plans"),
    icon: "net",
    section: "doc",
    order: 69,
    places: [
      { slug: "plan-physik", layer: "l1", label: L("L1-Plan", "L1 plan"), icon: "design", order: 105, params: { plan: "l1" } },
      { slug: "plan-switching", layer: "l2", label: L("L2-Plan", "L2 plan"), icon: "design", order: 205, params: { plan: "l2" } },
      { slug: "plan-routing", layer: "l3", label: L("L3-Plan", "L3 plan"), icon: "design", order: 305, params: { plan: "l3" } },
    ],
    render: (p) => <LayerPlan key={p.paramsKey} tenantId={p.tenantId} tenantLabel={p.tenantLabel} params={p.params} />,
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
