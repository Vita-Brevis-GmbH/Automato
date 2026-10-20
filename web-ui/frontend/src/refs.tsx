// Darstellung von Firewall-Objekten mit Referenzdaten aus dem IPAM (DDI/NetBox), ADR 0010.
import type { ReactNode } from "react";
import { RefInfo, RefValue, Rule, RuleRef } from "./api";
import { Dash, Icon, MatchBadge } from "./ui";

export const PLATFORM_LABEL: Record<string, string> = {
  fortigate: "FortiGate",
  cisco_aci: "Cisco ACI",
  infoblox: "Infoblox DDI",
  netbox: "NetBox",
};
export const REFERENCE_PLATFORMS = ["infoblox", "netbox"];
export const REF_ROLE_LABEL: Record<string, string> = {
  primary: "IPAM führend",
  enrichment: "Anreicherung",
};

// Befund-Typen (Findings) mit verständlicher Bezeichnung
export const FINDING_LABEL: Record<string, string> = {
  unmatched_object: "Nicht im IPAM",
  invalid_name: "Name verletzt Konvention",
  ambiguous_ip: "IP mehrdeutig",
  duplicate_value: "Doppelter Wert",
  name_type_mismatch: "Präfix passt nicht zum Typ",
  epg_no_endpoints: "EPG ohne Endpunkte",
  name_mismatch: "Name ≠ DNS-Name",
  unused_object: "Unbenutztes Objekt",
  subnet_not_in_ipam: "Subnetz fehlt im IPAM",
  vlan_mismatch: "VLAN weicht ab",
  service_mismatch: "Service ≠ Name",
  rule_incomplete: "Regel nicht wirksam",
};
export const findingLabel = (t: string) => FINDING_LABEL[t] ?? t;

// Was der Befund-Typ prüft (Kurzbeschreibung für Kacheln und Tooltips)
export const FINDING_HINT: Record<string, string> = {
  unmatched_object: "Firewall-Objekt ohne Eintrag im IPAM",
  name_mismatch: "Objektname passt nicht zum DNS-Namen im IPAM",
  subnet_not_in_ipam: "Subnetz der Firewall/ACI fehlt im IPAM",
  vlan_mismatch: "VLAN im Netz weicht vom IPAM ab",
  invalid_name: "Name verletzt die Namenskonvention",
  name_type_mismatch: "Präfix passt nicht zum Objekttyp",
  duplicate_value: "gleicher Wert in mehreren Objekten",
  ambiguous_ip: "IP in mehreren Objekten",
  unused_object: "in keiner Regel oder Gruppe verwendet",
  epg_no_endpoints: "EPG ohne gelernte Endpunkte",
  service_mismatch: "Protokoll/Port passt nicht zum Service-Namen",
  rule_incomplete: "Regel/Contract ohne Quelle bzw. Ziel",
};

// Kategorien der Übersicht (Findings-Seite)
export const FINDING_GROUPS: { key: string; label: string; help: string; types: string[] }[] = [
  {
    key: "ipam",
    label: "IPAM-Abgleich",
    help: "Firewall/ACI gegen DDI bzw. NetBox",
    types: ["unmatched_object", "name_mismatch", "subnet_not_in_ipam", "vlan_mismatch", "ambiguous_ip"],
  },
  {
    key: "naming",
    label: "Namenskonvention",
    help: "Präfixe laut Namensprofil, Services nach FT_-Schema",
    types: ["invalid_name", "name_type_mismatch", "service_mismatch"],
  },
  {
    key: "rules",
    label: "Regeln",
    help: "ohne Quelle/Ziel bzw. ohne Consumer/Provider",
    types: ["rule_incomplete"],
  },
  {
    key: "cleanup",
    label: "Bereinigung",
    help: "Doppelte und unbenutzte Objekte",
    types: ["duplicate_value", "unused_object"],
  },
  { key: "aci", label: "ACI", help: "EPGs ohne Endpunkte", types: ["epg_no_endpoints"] },
];

export const CATEGORY_LABEL: Record<string, string> = {
  address: "Adresse",
  address_group: "Adressgruppe",
  vip: "VIP",
  ippool: "IP-Pool",
  service: "Service",
  service_group: "Servicegruppe",
};

const KIND_LABEL: Record<string, string> = {
  prefix: "Netz",
  range: "Range",
  ip_address: "IP-Adresse",
  host: "Host",
  a_record: "A-Record",
  ptr_record: "PTR",
  fixed_address: "DHCP fix",
  vlan: "VLAN",
};

export function IssueChips({ issues }: { issues: string[] }) {
  if (!issues.length) return <span className="chip ok">✓</span>;
  return (
    <span className="chips">
      {issues.map((i) => (
        <span key={i} className={`chip ${i === "unused_object" ? "na" : "warn"}`} title={i}>
          {findingLabel(i)}
        </span>
      ))}
    </span>
  );
}

// Kurzfassung der Referenz: DNS-Name · Netz · VLAN · Standort (für Tabellen)
export function refSummary(r: RefInfo | null): string {
  if (!r) return "";
  return [
    r.dns_name || r.name,
    r.prefix && r.prefix !== r.value ? r.prefix : null,
    r.vlan != null ? `VLAN ${r.vlan}${r.vlan_name ? ` (${r.vlan_name})` : ""}` : null,
    r.site,
  ]
    .filter(Boolean)
    .join(" · ");
}

// Was die Anreicherung zusätzlich oder abweichend zur führenden Referenz weiss
export function enrichmentExtra(enr: RefInfo, ref: RefInfo | null): string {
  const differs = (a: unknown, b: unknown) => a != null && a !== "" && a !== b;
  const dns = (x: string | null | undefined) => (x ?? "").replace(/\.$/, "").toLowerCase();
  return [
    enr.dns_name && dns(enr.dns_name) !== dns(ref?.dns_name) ? `DNS ${enr.dns_name}` : null,
    differs(enr.vlan, ref?.vlan) ? `VLAN ${enr.vlan}` : null,
    differs(enr.site, ref?.site) ? enr.site : null,
    differs(enr.tenant, ref?.tenant) ? enr.tenant : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function SourceLink({ r }: { r: RefInfo }) {
  const label = r.source ?? "Quelle";
  return r.url ? (
    <a className="chip info" href={r.url} target="_blank" rel="noreferrer" title="In der Quelle öffnen">
      {label} <Icon name="external" size={11} />
    </a>
  ) : (
    <span className="chip info">{label}</span>
  );
}

// Ein Wert mit Abgleich: IP/Netz, Match-Status, Referenz (führend) und Anreicherung
export function ValueLine({ v, compact }: { v: RefValue; compact?: boolean }) {
  const ref = v.reference;
  const enr = v.enrichment;
  return (
    <div className="refline">
      <span className="mono">{v.value}</span>
      {v.match_status && v.match_status !== "matched" && <MatchBadge status={v.match_status} />}
      {v.zone && <span className="chip">{v.zone}</span>}
      {ref && (
        <span className="ref-text">
          {refSummary(ref) || KIND_LABEL[ref.kind] || ref.kind}
          {!compact && <SourceLink r={ref} />}
        </span>
      )}
      {!compact && enr && (
        <span className="ref-text faint">
          {ref ? enrichmentExtra(enr, ref) : refSummary(enr)}
          <SourceLink r={enr} />
        </span>
      )}
    </div>
  );
}

// Vollständige Referenzdaten (Detailansicht)
export function RefInfoBlock({ title, r }: { title: string; r: RefInfo }) {
  const rows: [string, ReactNode][] = [
    ["Quelle", <SourceLink r={r} key="s" />],
    ["Art", KIND_LABEL[r.kind] ?? r.kind],
    ["DNS-Name", r.dns_name],
    ["Name", r.name],
    ["Netz", r.prefix],
    ["VLAN", r.vlan != null ? `${r.vlan}${r.vlan_name ? ` (${r.vlan_name})` : ""}` : null],
    ["VRF", r.vrf],
    ["Standort", r.site],
    ["Tenant", r.tenant],
    ["Status", r.status],
    ["Beschreibung", r.description],
  ];
  return (
    <div className="refblock">
      <div className="faint" style={{ fontSize: 11, fontWeight: 600 }}>
        {title}
      </div>
      <dl className="kv tight">
        {rows
          .filter(([, v]) => v !== null && v !== undefined && v !== "")
          .map(([k, v]) => (
            <Pair key={k} k={k} v={v} />
          ))}
      </dl>
    </div>
  );
}

function Pair({ k, v }: { k: string; v: ReactNode }) {
  return (
    <>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </>
  );
}

// Werte eines Objekts, aufklappbar mit allen Referenzdaten
export function ValueList({ values, service }: { values: RefValue[]; service?: boolean }) {
  if (!values.length) return <Dash />;
  if (service) {
    return (
      <span className="chips">
        {values.map((v) => (
          <span className="chip" key={v.value}>
            {v.value}
          </span>
        ))}
      </span>
    );
  }
  return (
    <div className="objs">
      {values.map((v, i) =>
        v.reference || v.enrichment ? (
          <details key={i} className="refdetails">
            <summary>
              <ValueLine v={v} />
            </summary>
            {v.reference && <RefInfoBlock title="Referenz (führend)" r={v.reference} />}
            {v.enrichment && <RefInfoBlock title="Anreicherung" r={v.enrichment} />}
          </details>
        ) : (
          <ValueLine key={i} v={v} />
        ),
      )}
    </div>
  );
}

// Regel-Verweis: Objekt wie auf der Firewall, bei Gruppen mit Mitgliedern
// Name eines Objekts als Link "alle Regeln mit diesem Objekt" (wenn onObject gesetzt)
function ObjLink({ name, onObject }: { name: string; onObject?: (name: string) => void }) {
  if (!onObject) return <span className="mono">{name}</span>;
  return (
    <button className="linkbtn mono objlink" title="Alle Regeln mit diesem Objekt" onClick={() => onObject(name)}>
      {name}
    </button>
  );
}

export function RefCard({ r, onObject }: { r: RuleRef; onObject?: (name: string) => void }) {
  const service = r.role === "service";
  const linkable = !service && r.category ? onObject : undefined;
  return (
    <div className="refcard">
      <div className="refcard-head">
        <b>
          <ObjLink name={r.name} onObject={linkable} />
        </b>
        {r.category ? (
          <span className="faint">
            {CATEGORY_LABEL[r.category] ?? r.category}
            {r.obj_type ? ` · ${r.obj_type}` : ""}
          </span>
        ) : (
          <span className="faint">eingebaut</span>
        )}
        {!service && r.match_status && <MatchBadge status={r.match_status} />}
      </div>
      {r.comment && <div className="faint">{r.comment}</div>}
      {r.values.length > 0 && <ValueList values={r.values} service={service} />}
      {r.members.length > 0 && (
        <div className="members">
          {r.members.map((m) => (
            <div key={m.name} className="member">
              <ObjLink name={m.name} onObject={!service && m.category ? onObject : undefined} />
              {m.category ? null : <span className="faint"> (nicht gelesen)</span>}
              <ValueList values={m.values} service={service} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Seite ohne Endpunkt (ACI: Contract ohne Consumer/Provider = nicht wirksam) bzw. negiert
export function SideMarks({ r, side }: { r: Rule; side: "src" | "dst" | "service" }) {
  const aci = r.domain === "aci_contract";
  const missing = side !== "service" && r.missing?.includes(side);
  const negated = r.negate?.includes(side);
  if (!missing && !negated) return null;
  const label = aci ? (side === "src" ? "kein Consumer" : "kein Provider") : side === "src" ? "keine Quelle" : "kein Ziel";
  return (
    <span className="chips" style={{ marginBottom: 2 }}>
      {missing && (
        <span
          className="chip warn"
          title={aci ? "Contract wird nicht konsumiert bzw. bereitgestellt – nicht wirksam" : "keine lesbare Angabe – Policy prüfen"}
        >
          {label}
          {aci ? " · nicht wirksam" : ""}
        </span>
      )}
      {negated && (
        <span className="chip crit" title="negiert: die Regel gilt für alles AUSSER den genannten Objekten">
          nicht (negiert)
        </span>
      )}
    </span>
  );
}

