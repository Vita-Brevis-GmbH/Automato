// Modul Design (ADR 0036): logische Zonen, Zonentyp, Zonenmatrix, Zonenplan.
import { lazy } from "react";
import { L } from "../../i18n";
import type { ViewEntry } from "../types";

const Design = lazy(() => import("../../Design"));

export const VIEWS: ViewEntry[] = [
  {
    key: "design",
    module: "design",
    label: L("Netzwerkdesign", "Network design"),
    icon: "design",
    section: "doc",
    order: 110,
    customer: true,
    render: (p) => (
      <Design
        key={p.paramsKey}
        params={p.params}
        tenantId={p.tenantId}
        tenantLabel={p.tenantLabel}
        canEdit={!p.me.isCustomer && (p.me.isAdmin || p.me.isOperator)}
        isAdmin={p.me.isAdmin}
        isCustomer={p.me.isCustomer}
      />
    ),
  },
];
