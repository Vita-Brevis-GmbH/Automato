import type { View, ViewParams } from "./App";
import Apps from "./Apps";
import type { Me } from "./auth";
import { PageHead } from "./ui";
import { L } from "./i18n";

// Applikationen als fachliche Klammer (ADR 0012) – Klick öffnet ihre Doku (#apps?app=<id>). Die Doku je System und die
// publizierten Dienste sind seit 0.107.0 entfernt (Anwender); VIPs zeigt NAT/VIP.
export default function AppDoc({
  me,
  tenantId,
  params = {},
  onGo,
}: {
  me: Me;
  tenantId: number | null;
  params?: ViewParams;
  onGo?: (v: View, p?: ViewParams) => void;
}) {
  const appId = params.app ? Number(params.app) : null;
  return (
    <>
      <PageHead title={L("Applikationen", "Applications")}>
        <span className="no-print">
          {L(
            <>
              Eine <b>Applikation</b> fasst Application Profiles, EPGs und Server zusammen. Ein Klick öffnet ihre Doku: VMs,
              EPGs und alle Regeln mit Richtung und Bezug.
            </>,
            <>
              An <b>application</b> groups application profiles, EPGs and servers. A click opens its documentation: VMs, EPGs
              and all rules with direction and reference.
            </>,
          )}
        </span>
      </PageHead>
      <Apps
        me={me}
        tenantId={tenantId}
        appId={appId}
        onOpen={(id) => onGo?.("apps", { app: String(id) })}
        onBack={() => onGo?.("apps")}
        onGo={onGo}
      />
    </>
  );
}
