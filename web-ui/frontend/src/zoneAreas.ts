// Bereiche der Kategorien der Applikations-Doku (0.101.0): Anzeige je Bereich mit den Kategorien;
// seit 0.102.0 Vorschlag nach dem Namen der Zone
import type { AppdocArea } from "./api";
import { L } from "./i18n";

export function areaLabel(area: AppdocArea): string {
  return {
    inet: L("Internet (1/2)", "Internet (1/2)"),
    bit: L("Unternehmensnetzwerk (3/4)", "Corporate network (3/4)"),
    vpn: L("VPN (5)", "VPN (5)"),
    sra: L("Remote Access (6)", "Remote access (6)"),
    res: L("andere Security Tenants (9)", "Other security tenants (9)"),
    mgmt: L("DC Management (11)", "DC management (11)"),
  }[area];
}

// Vorschlag nach dem Namen bzw. Kurznamen der Zone (0.102.0): Edge Internet → Internet, Edge Office →
// Unternehmensnetzwerk, Edge VPN → VPN, SRA/Remote → Remote Access, RES-… → andere Security Tenants,
// … Management → DC Management; sonst keiner (z. B. DC)
export function suggestArea(name: string, code?: string | null): AppdocArea | null {
  const t = `${name} ${code ?? ""}`.toLowerCase();
  if (/manage?ment|mgmt/.test(t)) return "mgmt";
  if (/internet|inet|public/.test(t)) return "inet";
  if (/\bsra\b|remote/.test(t)) return "sra";
  if (/vpn/.test(t)) return "vpn";
  if (/office|eoff|\bbit\b|büro|buero|unternehmen/.test(t)) return "bit";
  if (/(^|[^a-z])res([^a-z]|$)/.test(t)) return "res";
  return null;
}
