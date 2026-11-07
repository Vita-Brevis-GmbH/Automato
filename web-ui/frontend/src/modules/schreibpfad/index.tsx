// Modul Schreibpfad (ADR 0036): der einzige Schreibweg, NetBox-Sync (Vorschau → Übernehmen) – getrennt von allem Lesen.
import { lazy } from "react";
import type { ViewEntry } from "../types";

const NetBoxSync = lazy(() => import("../../NetBoxSync"));

export const VIEWS: ViewEntry[] = [
  {
    key: "netbox",
    module: "schreibpfad",
    label: "NetBox-Sync",
    icon: "upload",
    section: "admin",
    order: 270,
    adminOnly: true,
    render: (p) => <NetBoxSync tenantId={p.tenantId} tenantLabel={p.tenantLabel} onGo={p.go} />,
  },
];
