// Bereiche der Kategorien der Applikations-Doku (0.101.0): Anzeige je Bereich mit den Kategorien
import type { AppdocArea } from "./api";
import { L } from "./i18n";

export function areaLabel(area: AppdocArea): string {
  return {
    inet: L("Internet (1/2)", "Internet (1/2)"),
    bit: L("Unternehmensnetzwerk (3/4)", "Corporate network (3/4)"),
    vpn: L("VPN (5)", "VPN (5)"),
    sra: L("Remote Access (6)", "Remote access (6)"),
    res: L("andere Security Tenants (9)", "Other security tenants (9)"),
  }[area];
}
