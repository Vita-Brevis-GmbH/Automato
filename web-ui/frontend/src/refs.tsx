// Darstellung von Firewall-Objekten mit Referenzdaten aus dem IPAM (DDI/NetBox), ADR 0010.
import { ReactNode, useEffect, useState } from "react";
import { fetchSystems, RefInfo, RefValue, Rule, RuleRef, VmInfo } from "./api";
import { Dash, Icon, MatchBadge } from "./ui";

export const PLATFORM_LABEL: Record<string, string> = {
  fortigate: "FortiGate",
  cisco_aci: "Cisco ACI",
  vmware: "VMware vCenter",
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
  predefined_service_used: "Standardobjekt verwendet",
  rule_incomplete: "Regel nicht wirksam",
  vm_not_in_ipam: "VM-IP nicht im IPAM",
  portgroup_no_epg: "Portgruppe ohne EPG",
  portgroup_name_mismatch: "Portgruppe: Name ≠ EPG",
  mapped_not_in_ipam: "Ziel nicht im IPAM",
  parallel_rule_mismatch: "Parallele Regel abweichend",
  parallel_rule_missing: "Regel fehlt parallel",
  parallel_object_mismatch: "Paralleles Objekt abweichend",
  aci_scope_unpaired: "Contract: ohne Gegenstelle im Scope",
  epg_assignment_conflict: "EPG: gelernt ≠ konfiguriert",
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
  predefined_service_used: "Standard-Service des Herstellers (z. B. HTTPS, ALL) in einer erlaubenden Regel – nicht nach Konvention",
  rule_incomplete: "Regel/Contract ohne Quelle bzw. Ziel",
  vm_not_in_ipam: "IP einer VM (vCenter) fehlt im IPAM",
  portgroup_no_epg: "Portgruppe nach ACI-Schema ohne eingelesene EPG",
  portgroup_name_mismatch: "Portgruppe nur über das VLAN einer anders benannten EPG zugeordnet",
  parallel_rule_mismatch: "gleiche Policy auf der parallelen Firewall anders konfiguriert",
  parallel_rule_missing: "Policy fehlt auf einer parallelen Firewall",
  parallel_object_mismatch: "gleichnamiges Objekt auf der parallelen Firewall anders definiert",
  aci_scope_unpaired: "Consumer bzw. Provider, die der Scope des Contracts (AP, VRF, Tenant) mit keiner Gegenstelle verbindet",
  epg_assignment_conflict: "IP im ACI in einer anderen EPG gelernt als konfiguriert (Portgruppe der VM bzw. statischer Endpunkt)",
};

// Kategorien der Übersicht (Findings-Seite)
export const FINDING_GROUPS: { key: string; label: string; help: string; types: string[] }[] = [
  {
    key: "ipam",
    label: "IPAM-Abgleich",
    help: "Firewall/ACI gegen DDI bzw. NetBox",
    types: [
      "unmatched_object",
      "name_mismatch",
      "subnet_not_in_ipam",
      "vlan_mismatch",
      "ambiguous_ip",
      "vm_not_in_ipam",
    ],
  },
  {
    key: "naming",
    label: "Namenskonvention",
    help: "Präfixe laut Namensprofil, Services nach FT_-Schema",
    types: ["invalid_name", "name_type_mismatch", "service_mismatch", "predefined_service_used"],
  },
  {
    key: "rules",
    label: "Regeln",
    help: "ohne Quelle/Ziel bzw. ohne Consumer/Provider, Contract-Scope ohne Gegenstelle",
    types: ["rule_incomplete", "aci_scope_unpaired"],
  },
  {
    key: "parallel",
    label: "Parallele Firewalls",
    help: "Regeln und Objekte im Verbund nicht gleich",
    types: ["parallel_rule_mismatch", "parallel_rule_missing", "parallel_object_mismatch"],
  },
  {
    key: "cleanup",
    label: "Bereinigung",
    help: "Doppelte und unbenutzte Objekte",
    types: ["duplicate_value", "unused_object"],
  },
  {
    key: "aci",
    label: "ACI / VMware",
    help: "EPGs ohne Endpunkte, Portgruppen ohne EPG, gelernt ≠ konfiguriert",
    types: ["epg_no_endpoints", "portgroup_no_epg", "portgroup_name_mismatch", "epg_assignment_conflict"],
  },
];

export const CATEGORY_LABEL: Record<string, string> = {
  address: "Adresse",
  address_group: "Adressgruppe",
  vip: "VIP",
  ippool: "IP-Pool",
  service: "Service",
  service_group: "Servicegruppe",
  vip_target: "VIP-Ziel",
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
      {v.vm && <VmChip vm={v.vm} />}
    </div>
  );
}

// VM hinter einer IP (vCenter, ADR 0017): Name, Portgruppe → EPG im Tooltip
export function VmChip({ vm }: { vm: VmInfo }) {
  const title = [
    `VM ${vm.name}`,
    vm.power_state === "poweredOff" ? "ausgeschaltet" : null,
    vm.cluster ? `Cluster ${vm.cluster}` : null,
    vm.portgroup ? `Portgruppe ${vm.portgroup}${vm.vlan != null ? ` (VLAN ${vm.vlan})` : ""}` : null,
    vm.epg ? `EPG ${vm.epg}` : null,
    vm.others?.length ? `gleiche IP auch an: ${vm.others.join(", ")}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <span className={`chip ${vm.power_state === "poweredOff" ? "na" : "info"}`} title={title}>
      VM {vm.name}
      {vm.epg ? ` · ${vm.epg.split("/").pop()}` : ""}
    </span>
  );
}

const POWER_STATE: Record<string, [string, string]> = {
  poweredOn: ["ok", "an"],
  poweredOff: ["na", "aus"],
  suspended: ["warn", "pausiert"],
};

// VM im Detail (vCenter, 0.14.0): Status, Gast-OS, Netzwerk (NIC, Portgruppe → EPG), Lage und
// Ressourcen – im Regel-, Objekt- und NAT-Detail
export function VmCard({ vm, role }: { vm: VmInfo; role?: string }) {
  const [tone, state] = POWER_STATE[vm.power_state ?? ""] ?? ["na", vm.power_state ?? "–"];
  const res = [
    vm.vcpus != null ? `${vm.vcpus} vCPU` : null,
    vm.memory_mb != null ? `${Math.round((vm.memory_mb / 1024) * 10) / 10} GB RAM` : null,
    vm.disk_gb != null ? `${Math.round(vm.disk_gb)} GB Disk` : null,
  ].filter(Boolean);
  const rows: [string, ReactNode][] = [
    ["Gast-OS", vm.guest_os],
    ["Hostname", vm.hostname],
    ["IPs", vm.ips?.length ? vm.ips.join(", ") : null],
    [
      "Netzwerkkarte",
      vm.nic ? `${vm.nic}${vm.mac ? ` · ${vm.mac}` : ""}${vm.connected === false ? " · getrennt" : ""}` : null,
    ],
    ["Portgruppe", vm.portgroup ? `${vm.portgroup}${vm.vlan != null ? ` (VLAN ${vm.vlan})` : ""}` : null],
    [
      "EPG",
      vm.epg ? (
        <>
          <span className="mono">{vm.epg}</span>
          {vm.epg_match && <span className="faint"> · über {vm.epg_match === "vlan" ? "das VLAN" : "den Namen"}</span>}
        </>
      ) : null,
    ],
    ["Cluster / Host", [vm.cluster, vm.host].filter(Boolean).join(" · ") || null],
    ["Datacenter", vm.datacenter],
    ["Ressourcen", res.length ? res.join(" · ") : null],
    ["Notiz", vm.notes],
    ["Gleiche IP", vm.others?.length ? `auch an ${vm.others.join(", ")}` : null],
  ];
  return (
    <div className="refblock">
      <div className="refline">
        <span className="chip info">VM</span> <b className="mono">{vm.name}</b>
        <span className={`chip ${tone}`}>{state}</span>
        {vm.template && <span className="chip">Vorlage</span>}
        {role && <span className="faint">{role}</span>}
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

// VMs eines Detail-Fensters (jede VM einmal, mit Rolle wie „Quelle“ oder „Ziel 10.0.0.5“)
export function VmSection({ items }: { items: { vm: VmInfo; role?: string }[] }) {
  const seen = new Set<number>();
  const list = items.filter(({ vm }) => !seen.has(vm.id) && seen.add(vm.id));
  if (!list.length) return null;
  return (
    <>
      <div className="sub-h">VMs ({list.length})</div>
      {list.map(({ vm, role }) => (
        <VmCard key={vm.id} vm={vm} role={role} />
      ))}
    </>
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
        {r.family === "v6" && (
          <span className="chip" title="IPv6-Objekt (address6/addrgrp6) – eigenes Objekt neben einem gleichnamigen IPv4-Objekt">
            IPv6
          </span>
        )}
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

// Dienste mit dem Objektnamen der Konvention (0.36.0): ACI-Filter stehen als „Tenant/FT_x“ im
// Store – angezeigt wird „FT_x“; FortiGate-Services wie auf der Firewall
export function serviceNames(r: Pick<Rule, "service_names" | "aci">): string[] {
  const names = r.service_names ?? [];
  return r.aci ? [...new Set(names.map((n) => n.split("/").pop() ?? n))] : names;
}

export const FAMILY_LABEL: Record<string, string> = { v4: "IPv4", v6: "IPv6", dual: "IPv4+IPv6" };

// VDOMs je Quellsystem (GET /systems, hoechstens einmal je Minute geladen)
let vdomCache: { at: number; map: Promise<Map<number, string[]>> } | null = null;
function loadVdoms(): Promise<Map<number, string[]>> {
  if (!vdomCache || Date.now() - vdomCache.at > 60_000) {
    const map = fetchSystems()
      .then((list) => new Map(list.map((s) => [s.id, s.vdoms ?? []] as [number, string[]])))
      .catch(() => new Map<number, string[]>());
    vdomCache = { at: Date.now(), map };
  }
  return vdomCache.map;
}

// VDOM anzeigen? Eine FortiGate nur mit „root“ ist die Firewall selbst – „VDOM root“ nur bei
// Firewalls mit weiteren VDOMs, andere VDOMs immer (0.17.0)
export function useVdomShown(): (sourceSystemId: number | null | undefined, vdom?: string | null) => boolean {
  const [map, setMap] = useState<Map<number, string[]> | null>(null);
  useEffect(() => {
    let active = true;
    loadVdoms().then((m) => active && setMap(m));
    return () => {
      active = false;
    };
  }, []);
  return (sid, vdom) => !!vdom && (vdom !== "root" || (map?.get(sid ?? -1)?.length ?? 0) > 1);
}

// FortiGate-VDOM (ADR 0019): Policy-IDs und Objektnamen gelten je VDOM. Den Standard-VDOM
// „root“ nur zeigen, wenn es weitere VDOMs gibt (`always`, siehe useVdomShown).
export function VdomChip({ vdom, always }: { vdom?: string | null; always?: boolean }) {
  if (!vdom || (vdom === "root" && !always)) return null;
  return (
    <span className="chip" title="FortiGate-VDOM – Policy-IDs und Objektnamen gelten je VDOM">
      VDOM {vdom}
    </span>
  );
}
const DIFF_LABEL: Record<string, string> = {
  src: "Quelle",
  dst: "Ziel",
  service: "Dienste",
  name: "Name",
  zone: "Interfaces/Zonen",
  action: "Aktion",
};

// Adressfamilie und IPv4/IPv6-Gegenstück (FortiGate führt beide als eigene Policies)
export function FamilyMarks({ r, onPair }: { r: Rule; onPair?: (id: number) => void }) {
  if (!r.family && !r.pair) return null;
  const p = r.pair;
  const other = p ? FAMILY_LABEL[p.family ?? ""] ?? "Gegenstück" : "";
  const diff = p?.diff.length ? `abweichend: ${p.diff.map((d) => DIFF_LABEL[d] ?? d).join(", ")}` : "";
  const title = p
    ? `${other}-Gegenstück „${p.name}“ (#${p.rule_id}) – ` +
      (p.match === "same"
        ? "deckt dasselbe ab (gleiche Objektnamen, Dienste und Interfaces)"
        : p.match === "confirmed"
          ? `von Hand als Paar bestätigt${diff ? `, ${diff}` : ""}`
          : `ähnlich, ${diff}`) +
      ". Eigene Regel wie auf der FortiGate – nur zur Orientierung verbunden."
    : "";
  return (
    <div className="chips" style={{ marginTop: 3 }}>
      {r.family && (
        <span className="chip" title="Adressfamilie der Policy">
          {FAMILY_LABEL[r.family] ?? r.family}
        </span>
      )}
      {p && (
        <button
          className={`chip linkchip ${p.match === "similar" ? "warn" : "ok"}`}
          title={title}
          onClick={(e) => {
            e.stopPropagation();
            onPair?.(p.rule_id);
          }}
        >
          ↔ {other} #{p.rule_id}
          {p.match === "similar" ? " ähnlich" : p.match === "confirmed" ? " ✓ bestätigt" : ""}
        </button>
      )}
    </div>
  );
}
