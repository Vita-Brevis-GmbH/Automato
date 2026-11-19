// Modul Kern (ADR 0036): Übersicht, Regeln & Contracts, Objekte, NAT/VIPs, Benutzer, Mandanten, Kunden, System – immer an.
// Seit 1.19.0 (ADR 0067) stehen Regeln, Objekte und NAT/VIPs unter L4–7, Netze und VRFs des Inventars unter L3.
import { lazy } from "react";
import { canManage, customerCan } from "../../auth";
import { L } from "../../i18n";
import type { ViewEntry } from "../types";

const Customers = lazy(() => import("../../Customers"));
const Portal = lazy(() => import("../../Portal"));
const OwnCustomerUsers = lazy(() => import("../../Customers").then((m) => ({ default: m.OwnCustomerUsers })));
const NatTable = lazy(() => import("../../Nat"));
const Objects = lazy(() => import("../../Objects"));
const Overview = lazy(() => import("../../Overview"));
const RulesTable = lazy(() => import("../../RulesTable"));
const System = lazy(() => import("../../System"));
const Tenants = lazy(() => import("../../Tenants"));
const Users = lazy(() => import("../../Users"));

export const VIEWS: ViewEntry[] = [
  {
    key: "uebersicht",
    module: "kern",
    label: L("Übersicht", "Overview"),
    icon: "overview",
    section: "doc",
    order: 10,
    customer: true,
    render: (p) => (
      <Overview
        me={p.me}
        tenantId={p.tenantId}
        tenantLabel={p.tenantLabel}
        tenants={p.tenants}
        stats={p.stats}
        onGo={p.go}
        onReload={p.reloadStats}
      />
    ),
  },
  {
    key: "regeln",
    module: "kern",
    label: L("Regeln & Contracts", "Rules & contracts"),
    icon: "rules",
    section: "doc",
    order: 20,
    places: [{ slug: "regeln", layer: "l4-7", label: L("Regeln & Contracts", "Rules & contracts"), icon: "rules", order: 410 }],
    customer: true,
    needsTenant: true,
    render: (p) => (
      <RulesTable
        key={p.paramsKey}
        tenantId={p.tenantId}
        tenantLabel={p.tenantLabel}
        params={p.params}
        onGo={p.go}
        isAdmin={canManage(p.me)}
        customer={p.me.isCustomer}
        canAssign={(p.me.isOperator && !p.me.isCustomer) || customerCan(p.me, "applications.edit")}
      />
    ),
  },
  {
    key: "objekte",
    module: "kern",
    label: L("Objekte & Inventar", "Objects & inventory"),
    icon: "objects",
    section: "doc",
    order: 30,
    places: [
      { slug: "objekte", layer: "l4-7", label: L("Objekte & Inventar", "Objects & inventory"), icon: "objects", order: 420 },
      // Inventar (nicht für Kunden-Benutzer): Subnetze und VRFs aus FortiGate/APIC
      { slug: "netze", layer: "l3", label: L("Netze", "Networks"), icon: "net", order: 310, params: { tab: "subnets" }, customer: false },
      { slug: "vrf", layer: "l3", label: "VRF", icon: "route", order: 320, params: { tab: "vrfs" }, customer: false },
    ],
    customer: true,
    needsTenant: true,
    render: (p) => (
      <Objects
        key={p.paramsKey}
        tenantId={p.tenantId}
        tenantLabel={p.tenantLabel}
        params={p.params}
        onGo={p.go}
        customer={p.me.isCustomer}
      />
    ),
  },
  {
    key: "nat",
    module: "kern",
    label: "NAT / VIPs",
    icon: "nat",
    section: "doc",
    order: 40,
    places: [
      { slug: "nat", layer: "l4-7", label: "NAT / VIPs", icon: "nat", order: 430 },
      { slug: "loadbalancer", layer: "l4-7", label: "Load Balancer", icon: "lb", order: 440, params: { type: "lb" } },
    ],
    customer: true,
    needsTenant: true,
    render: (p) => (
      <NatTable key={p.paramsKey} tenantId={p.tenantId} tenantLabel={p.tenantLabel} params={p.params} onGo={p.go} />
    ),
  },
  {
    key: "users",
    module: "kern",
    label: L("Benutzer & Rollen", "Users & roles"),
    icon: "users",
    section: "admin",
    order: 200,
    platform: true, // Benutzer und Rollen kommen aus dem Cockpit (ADR 0058, 0068)
    render: (p) => <Users cfg={p.cfg} me={p.me} tenants={p.tenants} />,
  },
  {
    key: "tenants",
    module: "kern",
    label: L("Mandanten & Standorte", "Tenants & sites"),
    icon: "tenants",
    section: "admin",
    order: 210,
    render: (p) => (
      <Tenants
        me={p.me}
        tenants={p.tenantsList}
        tenantId={p.tenantId}
        onSelectTenant={p.selectTenant}
        onTenantsChanged={p.reloadTenants}
      />
    ),
  },
  {
    key: "kunden",
    module: "kern",
    label: L("Kunden", "Customers"),
    icon: "customers",
    section: "admin",
    order: 220,
    platform: true, // Kunden kommen aus dem Cockpit (ADR 0058, 0068)
    render: (p) => (
      <Customers
        tenantId={p.tenantId}
        canEdit={p.me.isAdmin || p.me.isOperator}
        isAdmin={p.me.isAdmin}
        onChanged={p.customersChanged}
      />
    ),
  },
  // Kunden-Admin (feinere Rollen): die Kunden-Benutzer des eigenen Kunden
  {
    key: "kundenbenutzer",
    module: "kern",
    label: L("Kunden-Benutzer", "Customer users"),
    icon: "users",
    section: "admin",
    order: 230,
    customer: true,
    customerOnly: true,
    cap: "customer_users.manage",
    render: (p) => <OwnCustomerUsers me={p.me} tenantId={p.tenantId} />,
  },
  // Kunden-Portal (1.3.0, ADR 0050): Kunden des Front-Proxys, Werkzeuge und Realm je Kunde
  {
    key: "kundenportal",
    module: "kern",
    label: L("Kunden-Portal", "Customer portal"),
    icon: "customers",
    section: "admin",
    order: 240,
    adminOnly: true,
    platform: true,
    render: (p) => <Portal tenants={p.tenantsList ?? p.tenants} />,
  },
  {
    key: "system",
    module: "kern",
    label: "System",
    icon: "server",
    section: "admin",
    order: 290,
    adminOnly: true,
    platform: true,
    render: () => <System />,
  },
];
