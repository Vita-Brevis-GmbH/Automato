// Standorte virtuell/physisch (ADR 0015): Anzeige und Auswahl, gruppiert nach Art.
import { Site, SiteHint, SiteKind, SiteRef } from "./api";
import { L } from "./i18n";

export const SITE_KIND_LABEL: Record<SiteKind, string> = {
  virtual: L("virtuell", "virtual"),
  physical: L("physisch", "physical"),
};
export const SITE_KIND_GROUP: Record<SiteKind, string> = {
  virtual: L("Virtuelle Standorte", "Virtual sites"),
  physical: L("Physische Standorte", "Physical sites"),
};

// "000 · Stretched DC (chzrh1, chbsl1)" bzw. "chzrh1 · Zürich RZ1"
export function siteLabel(s: SiteRef | SiteHint | null | undefined): string {
  if (!s) return "";
  const links = (s.kind === "virtual" ? s.physical : s.virtual) ?? [];
  return [s.code, s.name].filter(Boolean).join(" · ") + (links.length ? ` (${links.join(", ")})` : "");
}

// Kennzeichen eines Standorts; aus dem Namen erkannt, aber nicht erfasst: blass mit Hinweis
export function SiteChip({ site, from }: { site: SiteHint | SiteRef | null | undefined; from?: string | null }) {
  if (!site) return null;
  const known = !("known" in site) || site.known;
  const kind = site.kind ?? "virtual";
  const title = known
    ? L(
        `${SITE_KIND_LABEL[kind]}er Standort ${siteLabel(site)}${from === "system" ? " – vom Quellsystem" : from === "name" ? " – aus dem Namen" : ""}`,
        `${SITE_KIND_LABEL[kind]} site ${siteLabel(site)}${from === "system" ? " – from the source system" : from === "name" ? " – from the name" : ""}`,
      )
    : L(
        `Standort ${site.code} steht im Namen, ist aber nicht erfasst (Mandanten & Standorte)`,
        `Site ${site.code} appears in the name but is not recorded (Tenants & sites)`,
      );
  return (
    <span className={`chip ${known ? (kind === "physical" ? "info" : "local") : "na"}`} title={title}>
      {kind === "physical" ? "⌂ " : ""}
      {site.code}
      {known && kind === "virtual" && site.physical?.length ? ` → ${site.physical.join(", ")}` : ""}
      {!known && " ?"}
    </span>
  );
}

// <option>s gruppiert nach Art (für Standort-Auswahlen)
export function SiteOptions({ sites }: { sites: Site[] }) {
  const byCode = new Map(sites.map((s) => [s.id, s.code]));
  return (
    <>
      {(["virtual", "physical"] as SiteKind[]).map((kind) => {
        const list = sites.filter((s) => (s.kind ?? "virtual") === kind);
        if (!list.length) return null;
        return (
          <optgroup key={kind} label={SITE_KIND_GROUP[kind]}>
            {list.map((s) => {
              const links = (kind === "virtual" ? s.physical_site_ids : s.virtual_site_ids) ?? [];
              const codes = links.map((id) => byCode.get(id)).filter(Boolean);
              return (
                <option key={s.id} value={s.id}>
                  {s.code} – {s.name}
                  {codes.length ? ` (${codes.join(", ")})` : ""}
                </option>
              );
            })}
          </optgroup>
        );
      })}
    </>
  );
}
