// Begründung der BGP-Abweichungen gegen die Zonen (0.127.0, Anwender: „Bei BGP Findings sehe ich sehr viele Filter,
// welche nicht passen würden, aber ich sehe keine Details, weshalb dies so sein soll“): je Netz die Zone und die
// entscheidende Regel des Filters, dazu woher die Zonen kommen – im Befund und im BGP-Nachbarn.
import type { BgpDecision, BgpWhy, BgpZoneChain } from "../../api";
import { L } from "../../i18n";

const KIND: Record<string, string> = {
  route_map: "Route-Map",
  prefix_list: L("Prefix-Liste", "Prefix list"),
  distribute_list: L("Distribute-Liste", "Distribute list"),
  filter_list: L("Filter-Liste (AS-Path)", "Filter list (AS path)"),
  access_list: L("Access-Liste", "Access list"),
};
export const kindOf = (k: string) => KIND[k.replace(/6$/, "")] ?? k;

// Was ein Filter mit dem Netz macht: entscheidende Regel bzw. keine Regel passt (am Ende gesperrt)
export function decisionText(d: BgpDecision): string {
  const name = `${kindOf(d.kind)} ${d.filter}`;
  if (d.unread) return L(`${name}: nicht gelesen`, `${name}: not read`);
  if (!d.rule) {
    return d.verdict === "deny"
      ? L(`${name}: keine Regel passt – am Ende gesperrt`, `${name}: no rule matches – denied at the end`)
      : name;
  }
  const inner = d.list ? L(` (Liste ${d.list}: ${d.list_rule ?? "–"})`, ` (list ${d.list}: ${d.list_rule ?? "–"})`) : "";
  return L(`${name}: Regel ${d.rule}${inner}`, `${name}: rule ${d.rule}${inner}`);
}

function zonesText(zones: string[] | undefined): string {
  return (zones ?? []).join(", ");
}

function WhyLine({ w, out }: { w: BgpWhy; out: boolean }) {
  const by = (w.by ?? []).map(decisionText).join("; ");
  if (w.kind === "missing") {
    return (
      <>
        <span className="chip warn">{L("gesperrt", "denied")}</span> <span className="mono">{w.prefix}</span>{" "}
        {L(`– Netz der Zone ${zonesText(w.zones)}`, `– network of zone ${zonesText(w.zones)}`)}
        {out
          ? L(", soll angekündigt werden", ", should be announced")
          : L(", soll übernommen werden", ", should be accepted")}
        {by && <span className="faint"> · {by}</span>}
        {(w.parts ?? []).length > 0 && (
          <span className="faint">
            {" "}
            · {L("erlaubt sind nur Teile davon", "only parts of it are permitted")}: <span className="mono">{(w.parts ?? []).join(", ")}</span>
          </span>
        )}
      </>
    );
  }
  if (w.kind === "excluded_allowed") {
    return (
      <>
        <span className="chip warn">{L("Ausschluss erlaubt", "exclusion permitted")}</span> <span className="mono">{w.prefix}</span>{" "}
        {L(`– Ausschluss der Zone ${zonesText(w.zones)}`, `– exclusion of zone ${zonesText(w.zones)}`)}
        {by && <span className="faint"> · {by}</span>}
      </>
    );
  }
  const rule = w.rule ? L(`Regel ${w.rule}`, `rule ${w.rule}`) : "";
  const zones = zonesText(w.zones);
  return (
    <>
      <span className="chip warn">{L("zu offen", "too open")}</span>{" "}
      <span className="mono">{w.all ? L("alles", "everything") : w.prefix}</span>{" "}
      <span className="faint">
        · {w.filter}
        {rule ? `: ${rule}` : ""}
      </span>{" "}
      {w.all
        ? L("– erlaubt jedes Netz", "– permits every network")
        : zones
          ? out
            ? L(`– gehört zur Zone ${zones}, die nicht diesseits liegt`, `– belongs to zone ${zones}, which is not on this side`)
            : L(`– gehört zur Zone ${zones}, die nicht hinter diesem Nachbarn liegt`, `– belongs to zone ${zones}, which is not behind this neighbor`)
          : L("– liegt in keiner Zone", "– lies in no zone")}
    </>
  );
}

// Woher die Zonen kommen: Zone der Firewall, Zone am Nachbarn (gelesenes Gerät bzw. Netz des Übergangs), Zonen dahinter
// bzw. diesseits mit dem Abstand im Zonenplan
export function ZoneChainLine({ c, out }: { c: BgpZoneChain; out: boolean }) {
  const via = c.device
    ? L("gelesenes Gerät", "read device")
    : c.network
      ? L(`Übergang über ${c.network}${c.interface ? ` (Interface ${c.interface})` : ""}`, `transition via ${c.network}${c.interface ? ` (interface ${c.interface})` : ""}`)
      : "";
  const zones = c.zones.map((z) => (z.hops != null ? `${z.name} (${z.hops})` : z.name)).join(", ");
  return (
    <div className="faint bgp-zone-chain" style={{ margin: "4px 0" }}>
      {L("Zone der Firewall", "Zone of the firewall")}: <b>{c.home}</b> · {L("Zone am Nachbarn", "Zone at the neighbor")}: <b>{c.neighbor}</b>
      {via && ` (${via})`} · {out ? L("Zonen diesseits", "Zones on this side") : L("Zonen dahinter", "Zones behind")}: {zones}
      {(c.not_via ?? []).length > 0 && (
        <div className="bgp-not-via">
          {L(
            "Nicht dazu (nur über eine Zone, die gleiche Zonen nicht verbindet – Grundschema „verbindet gleiche Zonen“): ",
            "Not included (only via a zone that does not connect equal zones – base schema “connects equal zones”): ",
          )}
          {(c.not_via ?? []).join(", ")}
        </div>
      )}
      <div>
        {out
          ? L(
              "Diesseits = Zonen, deren kürzester Weg vom Nachbarn über die Zone der Firewall führt (Zahl = Übergänge vom Nachbarn).",
              "On this side = zones whose shortest path from the neighbor leads via the zone of the firewall (number = transitions from the neighbor).",
            )
          : L(
              "Dahinter = Zonen, deren kürzester Weg von der Zone der Firewall über die Zone am Nachbarn führt (Zahl = Übergänge).",
              "Behind = zones whose shortest path from the zone of the firewall leads via the zone at the neighbor (number = transitions).",
            )}
      </div>
    </div>
  );
}

export function BgpWhyList({ why, chain, out }: { why: BgpWhy[]; chain?: BgpZoneChain | null; out: boolean }) {
  if (!why.length && !chain) return null;
  return (
    <div className="bgp-why" style={{ marginTop: 6 }}>
      {chain && <ZoneChainLine c={chain} out={out} />}
      {why.length > 0 && (
        <ul className="bgp-why-list" style={{ margin: "4px 0", paddingLeft: 18 }}>
          {why.map((w, i) => (
            <li key={`${w.kind}-${w.prefix}-${i}`} data-kind={w.kind} data-prefix={w.prefix}>
              <WhyLine w={w} out={out} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
