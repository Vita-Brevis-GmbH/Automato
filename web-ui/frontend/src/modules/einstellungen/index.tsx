// Modul Einstellungen (ADR 0036): Namensprofile, Kategorien, Grundschema – immer an.
import { lazy } from "react";
import { L } from "../../i18n";
import type { ViewEntry } from "../types";

const NamingProfiles = lazy(() => import("../../NamingProfiles"));

export const VIEWS: ViewEntry[] = [
  {
    key: "dict",
    module: "einstellungen",
    label: L("Namensprofile", "Naming profiles"),
    icon: "dict",
    section: "admin",
    order: 240,
    render: (p) => <NamingProfiles me={p.me} tenants={p.tenants} />,
  },
];
