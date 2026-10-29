// Modul Doku (ADR 0036): Applikationen und ihre Doku nach Kategorien, Export Word/Markdown.
import { lazy } from "react";
import { L } from "../../i18n";
import type { ViewEntry } from "../types";

const AppDoc = lazy(() => import("../../AppDoc"));

export const VIEWS: ViewEntry[] = [
  {
    key: "apps",
    module: "doku",
    label: L("Applikationen", "Applications"),
    icon: "apps",
    section: "doc",
    order: 80,
    customer: true,
    needsTenant: true,
    render: (p) => <AppDoc me={p.docMe} tenantId={p.tenantId} params={p.params} onGo={p.go} />,
  },
];
