// Modul Worker (ADR 0036): Collector-Agenten und Läufe – liest die Geräte, nur lesend.
import { lazy } from "react";
import { L } from "../../i18n";
import type { ViewEntry } from "../types";

const Agents = lazy(() => import("../../Agents"));
const Runs = lazy(() => import("../../Runs"));

export const VIEWS: ViewEntry[] = [
  {
    key: "agents",
    module: "worker",
    label: L("Collector-Agenten", "Collector agents"),
    icon: "agents",
    section: "admin",
    order: 250,
    adminOnly: true,
    platform: true, // Agenten und Agent-CA verwaltet der Betreiber (ADR 0068)
    render: (p) => <Agents tenants={p.tenants} tenantId={p.tenantId} onGo={p.go} />,
  },
  {
    key: "laeufe",
    module: "worker",
    label: L("Läufe", "Runs"),
    icon: "play",
    section: "admin",
    order: 260,
    render: (p) => (
      <Runs key={p.paramsKey} me={p.me} tenantId={p.tenantId} tenantLabel={p.tenantLabel} params={p.params} />
    ),
  },
];
