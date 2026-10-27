// Objekte & Inventar (ADR 0010): Firewall-Objekte wie auf dem Gerät mit Abgleich gegen das IPAM,
// dazu das Netzinventar aus FortiGate/APIC (Subnetze, VLANs, Ports, Geräte, VRFs). Rein lesend.
import { Fragment, ReactNode, useEffect, useMemo, useState } from "react";
import {
  fetchInventory,
  fetchObjectDetail,
  fetchObjects,
  fetchPortgroup,
  fetchPortgroups,
  fetchSystems,
  fetchVms,
  InvDevice,
  InvInterface,
  InvSubnet,
  InvVlan,
  InvVrf,
  ObjectDetail,
  ObjectPage,
  Paged,
  PortgroupDetail,
  PortgroupRow,
  PrefixInfo,
  System,
  VmRow,
} from "./api";
import { L } from "./i18n";
import {
  CATEGORY_LABEL,
  FAMILY_LABEL,
  IssueChips,
  REFERENCE_PLATFORMS,
  useVdomShown,
  ValueList,
  VdomChip,
  VmSection,
} from "./refs";
import { SiteChip } from "./sites";
import type { View, ViewParams } from "./App";
import { ActionBadge, Dash, Drawer, Empty, ErrorBox, Icon, Loading, Note, PageHead, SharedChip } from "./ui";

type Go = (v: View, p?: ViewParams) => void;

type Tab =
  | "addr"
  | "groups"
  | "services"
  | "subnets"
  | "vlans"
  | "ports"
  | "devices"
  | "vrfs"
  | "vms"
  | "portgroups";

const TABS: { key: Tab; label: string; category?: string }[] = [
  { key: "addr", label: L("Adressobjekte", "Address objects"), category: "address,vip,vip_target,ippool" },
  { key: "groups", label: L("Gruppen", "Groups"), category: "address_group,service_group" },
  { key: "services", label: "Services", category: "service" },
  { key: "subnets", label: L("Subnetze", "Subnets") },
  { key: "vlans", label: "VLANs" },
  { key: "ports", label: "Ports" },
  { key: "devices", label: L("Geräte", "Devices") },
  { key: "vrfs", label: "VRFs" },
  { key: "vms", label: "VMs" },
  { key: "portgroups", label: L("Portgruppen", "Port groups") },
];

const INVENTORY_TABS: Tab[] = ["subnets", "vlans", "ports", "devices", "vrfs"];

const PAGE = 200;

export default function Objects({
  tenantId,
  tenantLabel,
  params = {},
  onGo,
  customer = false,
}: {
  tenantId: number | null;
  tenantLabel: string;
  params?: ViewParams; // Link: object_id= öffnet das Objekt-Detail, q= / tab= aus der Applikations-Doku
  onGo?: Go; // Klick auf "Regeln" → Regel-Liste mit diesem Objekt (auch über Gruppen)
  customer?: boolean; // Kunden-Benutzer: ohne Netzinventar der Umgebung (ADR 0018)
}) {
  const tabs = customer ? TABS.filter((t) => !INVENTORY_TABS.includes(t.key)) : TABS;
  const [tab, setTab] = useState<Tab>(tabs.some((t) => t.key === params.tab) ? (params.tab as Tab) : "addr");
  const [systems, setSystems] = useState<System[]>([]);
  const [source, setSource] = useState("");
  const [q, setQ] = useState(params.q ?? "");
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [objType, setObjType] = useState(""); // Typ (obj_type), z. B. dynamic = SDN/FortiManager
  const [types, setTypes] = useState<Record<string, number>>({});
  const [hidePredefined, setHidePredefined] = useState(false); // Standardobjekte ausblenden
  const [linked, setLinked] = useState<number | null>(params.object_id ? Number(params.object_id) : null);
  const [pinned, setPinned] = useState<{ id: number; name: string } | null>(null);

  // „Objekt öffnen“ (0.36.0): Detail und die Liste dahinter auf genau dieses Objekt gefiltert
  useEffect(() => {
    if (!params.object_id) return;
    const id = Number(params.object_id);
    setLinked(id);
    fetchObjectDetail(id)
      .then((o) => {
        const key = tabOf(o.category);
        if (key && tabs.some((t) => t.key === key)) setTab(key);
        setQ("");
        setObjType("");
        setPinned({ id, name: o.name });
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.object_id]);

  // Reiter wechseln (0.74.1): Typ-Filter und seine Werte sofort zurück – sonst lädt der neue
  // Reiter zuerst mit dem Typ des alten (z. B. „ipmask“ bei Gruppen) und zeigt nichts
  const pickTab = (t: Tab) => {
    if (t === tab) return;
    setObjType("");
    setTypes({});
    setTab(t);
  };
  useEffect(() => setObjType(""), [tab]);
  useEffect(() => {
    if (!params.object_id) setSource("");
    if (tenantId === null) return;
    fetchSystems(tenantId)
      .then((list) => setSystems(list.filter((s) => !REFERENCE_PLATFORMS.includes(s.platform))))
      .catch(() => setSystems([]));
  }, [tenantId]);

  if (tenantId === null) {
    return (
      <>
        <PageHead title={L("Objekte & Inventar", "Objects & inventory")} />
        <Note>
          {L(
            "Bitte oben einen Mandanten wählen – Objekte und Inventar werden je Mandant angezeigt.",
            "Please select a tenant at the top – objects and inventory are shown per tenant.",
          )}
        </Note>
      </>
    );
  }

  const current = tabs.find((t) => t.key === tab)!;
  const sourceId = source ? Number(source) : null;
  const canFilterIssues = !!current.category || tab === "subnets";

  return (
    <>
      <PageHead title={L("Objekte & Inventar", "Objects & inventory")}>
        {L(
          <>
            Firewall-Objekte von „{tenantLabel}“ so, wie sie auf dem Gerät stehen, mit Referenzdaten aus dem IPAM
            (DDI/NetBox) – dazu das Netzinventar aus FortiGate und APIC. Rein lesend.
          </>,
          <>
            Firewall objects of “{tenantLabel}” as they are on the device, with reference data from the IPAM
            (DDI/NetBox) – plus the network inventory from FortiGate and APIC. Read-only.
          </>,
        )}
      </PageHead>

      <div className="tabbar sticky">
        {tabs.map((t) => (
          <button key={t.key} className={tab === t.key ? "on" : ""} onClick={() => pickTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      <div className="filterbar">
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input
            placeholder={
              current.category ? L("Name, Wert oder Kommentar…", "Name, value or comment…") : L("Suche…", "Search…")
            }
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPinned(null);
            }}
          />
        </div>
        <select className="sel" value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="">{L("Quelle: alle", "Source: all")}</option>
          {systems.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        {pinned && current.category && (
          <span
            className="chip info"
            title={L("Liste auf das geöffnete Objekt gefiltert", "List filtered to the opened object")}
          >
            {L(<>nur „{pinned.name}“</>, <>only “{pinned.name}”</>)}
            <button
              className="linkbtn"
              style={{ marginLeft: 6 }}
              aria-label={L("Filter aufheben", "Clear filter")}
              onClick={() => setPinned(null)}
            >
              ✕
            </button>
          </span>
        )}
        {current.category && Object.keys(types).length > 1 && (
          <select
            className="sel"
            value={objType}
            onChange={(e) => setObjType(e.target.value)}
            title={L(
              "Typ des Objekts wie auf dem Gerät – z. B. dynamische Objekte aus SDN-Connectoren (FortiManager/ACI)",
              "Object type as on the device – e.g. dynamic objects from SDN connectors (FortiManager/ACI)",
            )}
          >
            <option value="">{L("Typ: alle", "Type: all")}</option>
            {Object.entries(types)
              .sort(([a], [b]) => typeLabel(a).localeCompare(typeLabel(b)))
              .map(([t, n]) => (
                <option key={t || "-"} value={t || "-"}>
                  {typeLabel(t)} ({n})
                </option>
              ))}
          </select>
        )}
        {canFilterIssues && (
          <button className={`toggle ${issuesOnly ? "on" : ""}`} onClick={() => setIssuesOnly((v) => !v)}>
            <span className="dot y" />
            {L("nur Abweichungen", "deviations only")}
          </button>
        )}
        {(tab === "services" || tab === "groups") && (
          <button
            className={`toggle ${hidePredefined ? "on" : ""}`}
            onClick={() => setHidePredefined((v) => !v)}
            title={L(
              "Standardobjekte des Herstellers (FortiOS-Services wie HTTPS/ALL, ACI-Filter in common) – nicht nach Konvention benannt, nicht verwenden; sonst zuunterst",
              "Predefined objects of the vendor (FortiOS services such as HTTPS/ALL, ACI filters in common) – not named by convention, do not use; otherwise at the bottom",
            )}
          >
            {L("Standardobjekte ausblenden", "Hide predefined objects")}
          </button>
        )}
      </div>

      {tab === "vms" ? (
        <VmTable tenantId={tenantId} sourceId={sourceId} q={q} onGo={onGo} />
      ) : tab === "portgroups" ? (
        <PortgroupTable tenantId={tenantId} q={q} onGo={onGo} />
      ) : current.category ? (
        <ObjectTable
          key={tab}
          tenantId={tenantId}
          category={current.category}
          group={tab === "groups"}
          service={tab === "services"}
          sourceId={sourceId}
          q={q}
          issuesOnly={issuesOnly}
          objType={objType}
          hidePredefined={hidePredefined}
          objectId={pinned?.id ?? null}
          onTypes={setTypes}
          onGo={onGo}
        />
      ) : (
        <InventoryTable
          key={tab}
          tab={tab}
          tenantId={tenantId}
          sourceId={sourceId}
          q={q}
          issuesOnly={issuesOnly}
          onGo={onGo}
        />
      )}
      {linked !== null && <ObjectDrawer id={linked} onClose={() => setLinked(null)} onGo={onGo} />}
    </>
  );
}

// Reiter einer Objekt-Kategorie (für „Objekt öffnen“)
function tabOf(category: string): Tab | null {
  if (category.endsWith("_group")) return "groups";
  if (category === "service") return "services";
  if (["address", "vip", "vip_target", "ippool"].includes(category)) return "addr";
  return null;
}

// Typ eines Objekts wie auf dem Gerät, lesbar (FortiGate `type`, ACI-Filter)
const TYPE_LABEL: Record<string, string> = {
  "": L("ohne Typ", "no type"),
  ipmask: L("IP / Netz", "IP / network"),
  iprange: L("Bereich", "Range"),
  fqdn: "FQDN",
  "wildcard-fqdn": "Wildcard-FQDN",
  geography: "Geo",
  dynamic: L("dynamisch (SDN-Connector)", "dynamic (SDN connector)"),
  mac: "MAC",
  "interface-subnet": L("Interface-Subnetz", "Interface subnet"),
  vzFilter: L("ACI-Filter", "ACI filter"),
  "static-nat": L("VIP (statisch)", "VIP (static)"),
  "server-load-balance": "Virtual Server",
};
export const typeLabel = (t: string | null | undefined) => TYPE_LABEL[t ?? ""] ?? t ?? "";

// Link in die Regeln: genau dieses Objekt (IPv4 und IPv6 heissen teils gleich)
function objectLink(o: { id: number; name: string; family: string | null }): ViewParams {
  const p: ViewParams = { object: o.name, object_id: String(o.id) };
  if (o.family) p.family = o.family;
  return p;
}

export function Pager({
  total,
  offset,
  onOffset,
  pageSize = PAGE,
}: {
  total: number;
  offset: number;
  onOffset: (o: number) => void;
  pageSize?: number;
}) {
  if (total <= pageSize) return <div className="pager">{L(<>{total} Einträge</>, <>{total} entries</>)}</div>;
  return (
    <div className="pager">
      {offset + 1}–{Math.min(offset + pageSize, total)} {L("von", "of")} {total}
      <button className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => onOffset(offset - pageSize)}>
        {L("Zurück", "Back")}
      </button>
      <button
        className="btn btn-ghost btn-sm"
        disabled={offset + pageSize >= total}
        onClick={() => onOffset(offset + pageSize)}
      >
        {L("Weiter", "Next")}
      </button>
    </div>
  );
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function ObjectTable({
  tenantId,
  category,
  group,
  service,
  sourceId,
  q,
  issuesOnly,
  objType,
  hidePredefined,
  objectId,
  onTypes,
  onGo,
}: {
  tenantId: number;
  category: string;
  group: boolean;
  service: boolean;
  sourceId: number | null;
  q: string;
  issuesOnly: boolean;
  objType: string;
  hidePredefined: boolean;
  objectId: number | null;
  onTypes: (t: Record<string, number>) => void;
  onGo?: Go;
}) {
  const [data, setData] = useState<ObjectPage | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const query = useDebounced(q);

  useEffect(() => setOffset(0), [query, sourceId, issuesOnly, objType, hidePredefined, objectId]);
  useEffect(() => {
    let current = true; // ältere Antworten verwerfen (schnelles Umschalten)
    setData(null);
    setError(null);
    fetchObjects(tenantId, {
      category,
      source_system_id: sourceId,
      q: query,
      issues_only: issuesOnly,
      obj_type: objType || undefined,
      predefined: hidePredefined ? "exclude" : undefined,
      object_id: objectId,
      limit: PAGE,
      offset,
    })
      .then((d) => {
        if (!current) return;
        setData(d);
        if (d.types) onTypes(d.types);
      })
      .catch((e) => {
        if (!current) return;
        setError(e);
        setData({ total: 0, rows: [] });
      });
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, category, sourceId, query, issuesOnly, objType, hidePredefined, objectId, offset]);
  // VDOM „root“ nur bei Firewalls mit weiteren VDOMs (nur root = die Firewall, 0.17.0)
  const vdomShown = useVdomShown();

  return (
    <>
      <ErrorBox error={error} prefix={L("Objekte laden", "Loading objects")} />
      <div className="card">
        <div className="card-body scroll">
          {data === null ? (
            <Loading>{L("Lade Objekte…", "Loading objects…")}</Loading>
          ) : data.rows.length === 0 ? (
            <Empty
              title={query || issuesOnly ? L("Keine Treffer", "No matches") : L("Noch keine Objekte", "No objects yet")}
            >
              {query || issuesOnly
                ? L("Filter oder Suche anpassen.", "Adjust the filter or search.")
                : L(
                    "Objekte erscheinen nach dem ersten Lauf einer Firewall (FortiGate).",
                    "Objects appear after the first run of a firewall (FortiGate).",
                  )}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>{L("Art", "Kind")}</th>
                  <th>
                    {group
                      ? L("Mitglieder", "Members")
                      : service
                        ? L("Protokoll / Ports", "Protocol / ports")
                        : L("Werte / Referenz", "Values / reference")}
                  </th>
                  {service && <th>{L("Attribute", "Attributes")}</th>}
                  {!group && <th>{L("In Gruppe", "In group")}</th>}
                  <th className="num">{L("Regeln", "Rules")}</th>
                  <th>{L("Quelle", "Source")}</th>
                  <th>{L("Befunde", "Findings")}</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((o, i) => (
                  <Fragment key={o.id}>
                  {o.predefined && !data.rows[i - 1]?.predefined && (
                    <tr className="group-row">
                      <td colSpan={8}>
                        <b>{L("Standardobjekte des Herstellers", "Predefined objects of the vendor")}</b>
                        <span className="faint">
                          {" "}
                          {L(
                            "– FortiOS-Services bzw. ACI-Filter in common: nicht nach Konvention benannt, nicht verwenden",
                            "– FortiOS services or ACI filters in common: not named by convention, do not use",
                          )}
                          {data.predefined ? ` (${data.predefined})` : ""}
                        </span>
                      </td>
                    </tr>
                  )}
                  <tr className={`clickable${o.predefined ? " predefined" : ""}`} onClick={() => setSelected(o.id)}>
                    <td>
                      <b className="mono">{o.name}</b>
                      {o.family && !service && (
                        <span
                          className="chip"
                          style={{ marginLeft: 6 }}
                          title={L(
                            "Adressfamilie (FortiGate führt IPv4 und IPv6 getrennt)",
                            "Address family (FortiGate keeps IPv4 and IPv6 separate)",
                          )}
                        >
                          {FAMILY_LABEL[o.family] ?? o.family}
                        </span>
                      )}
                      {o.comment && <div className="faint">{o.comment}</div>}
                      {o.interface && <div className="faint">Interface {o.interface}</div>}
                      {o.site && (
                        <div>
                          <SiteChip site={o.site} from="name" />
                        </div>
                      )}
                      {(o.category === "vip" || o.category === "ippool") && onGo && (
                        <button
                          className="linkbtn"
                          onClick={(e) => {
                            e.stopPropagation();
                            onGo("nat", { q: o.name });
                          }}
                        >
                          {L("in NAT / VIPs öffnen", "open in NAT / VIPs")}
                        </button>
                      )}
                      {/* VIP-Ziele (0.12.0): internes Ziel einer VIP bzw. Adressobjekt hinter einer VIP */}
                      {[...(o.category === "vip_target" && o.attrs?.vip ? [String(o.attrs.vip)] : []),
                        ...((o.attrs?.vip_of as string[] | undefined) ?? [])].map((vip) => (
                        <div key={vip}>
                          <button
                            className="chip linkchip local"
                            title={L(
                              "Ziel dieser VIP – die Regeln der VIP zählen mit; Klick öffnet NAT / VIPs",
                              "Target of this VIP – the rules of the VIP are counted; click opens NAT / VIPs",
                            )}
                            onClick={(e) => {
                              e.stopPropagation();
                              onGo?.("nat", { q: vip });
                            }}
                          >
                            {L(<>Ziel von VIP {vip}</>, <>Target of VIP {vip}</>)}
                          </button>
                        </div>
                      ))}
                    </td>
                    <td className="nowrap">
                      {CATEGORY_LABEL[o.category] ?? o.category}
                      {o.obj_type && (
                        <div className={o.obj_type === "dynamic" ? "chip info" : "faint"} title={o.obj_type}>
                          {typeLabel(o.obj_type)}
                        </div>
                      )}
                    </td>
                    <td style={{ minWidth: 280 }}>
                      {group ? <Members names={o.members} /> : <ValueList values={o.values} service={service} />}
                    </td>
                    {service && (
                      <td>
                        <AttrSummary attrs={o.attrs} />
                      </td>
                    )}
                    {!group && <td>{o.member_of.length ? <Members names={o.member_of} /> : <Dash />}</td>}
                    <td className="num">
                      {/* Regeln, die das Objekt verwenden - direkt oder über (verschachtelte) Gruppen */}
                      {onGo && o.used_in_rules > 0 ? (
                        <button
                          className="chip local linkchip"
                          title={L(
                            "Regeln mit diesem Objekt anzeigen (direkt und über Gruppen)",
                            "Show rules with this object (directly and via groups)",
                          )}
                          onClick={(e) => {
                            e.stopPropagation();
                            onGo("regeln", objectLink(o));
                          }}
                        >
                          {o.used_in_rules}
                        </button>
                      ) : (
                        <span className="faint">0</span>
                      )}
                    </td>
                    <td className="nowrap">
                      {o.source ?? <Dash />}
                      {vdomShown(o.source_system_id, o.vdom) && (
                        <div>
                          <VdomChip vdom={o.vdom} always />
                        </div>
                      )}
                    </td>
                    <td>
                      {o.predefined && !o.issues.length ? (
                        <span
                          className="chip na"
                          title={L(
                            "Standardobjekt des Herstellers – nicht nach der Namenskonvention benannt; nicht verwenden",
                            "Predefined object of the vendor – not named by the naming convention; do not use",
                          )}
                        >
                          {L("Standard", "Predefined")}
                        </span>
                      ) : (
                        <IssueChips issues={o.issues} />
                      )}
                    </td>
                  </tr>
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {data && data.total > 0 && <Pager total={data.total} offset={offset} onOffset={setOffset} />}
      </div>
      {selected !== null && <ObjectDrawer id={selected} onClose={() => setSelected(null)} onGo={onGo} />}
    </>
  );
}

// Service-Attribute (FortiGate) bzw. ACI-Filter mit Klartext-Bezeichnung
const ATTR_LABEL: Record<string, string> = {
  protocol: L("Protokoll", "Protocol"),
  "protocol-number": L("IP-Protokoll", "IP protocol"),
  icmptype: L("ICMP-Typ", "ICMP type"),
  icmpcode: L("ICMP-Code", "ICMP code"),
  "tcp-portrange": L("TCP-Ports", "TCP ports"),
  "udp-portrange": L("UDP-Ports", "UDP ports"),
  "sctp-portrange": L("SCTP-Ports", "SCTP ports"),
  category: L("Kategorie", "Category"),
  iprange: L("nur Ziel-IP", "destination IP only"),
  fqdn: L("nur Ziel-FQDN", "destination FQDN only"),
  "session-ttl": "Session-TTL (s)",
  "tcp-halfclose-timer": "TCP-Halfclose (s)",
  "tcp-halfopen-timer": "TCP-Halfopen (s)",
  "tcp-timewait-timer": "TCP-Timewait (s)",
  "tcp-rst-timer": "TCP-RST (s)",
  "udp-idle-timer": "UDP-Idle (s)",
  "check-reset-range": L("Reset-Prüfung", "Reset check"),
  helper: L("Protokoll-Helper", "Protocol helper"),
  proxy: "Proxy",
  "app-service-type": "App-Service",
  tenant: "ACI-Tenant",
  dn: "DN",
};

// FortiGate-Portbereich "443 8443:1024-65535" -> "443, 8443 (Quellport 1024-65535)"
function fmtPorts(v: unknown): string {
  return String(v)
    .split(/\s+/)
    .filter(Boolean)
    .map((p) => {
      const [dst, src] = p.split(":");
      return src ? L(`${dst} (Quellport ${src})`, `${dst} (source port ${src})`) : dst;
    })
    .join(", ");
}

function attrText(k: string, v: unknown): string {
  if (k.endsWith("portrange")) return fmtPorts(v);
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

function AttrSummary({ attrs }: { attrs?: Record<string, unknown> }) {
  const a = attrs ?? {};
  const bits: string[] = [];
  if (a.category) bits.push(String(a.category));
  for (const k of ["tcp-portrange", "udp-portrange", "sctp-portrange"])
    if (a[k] && String(a[k]).includes(":")) bits.push(`${ATTR_LABEL[k]} ${fmtPorts(a[k])}`);
  if (a.icmptype !== undefined) bits.push(`ICMP ${a.icmptype}${a.icmpcode !== undefined ? `/${a.icmpcode}` : ""}`);
  if (a["protocol-number"]) bits.push(L(`IP-Protokoll ${a["protocol-number"]}`, `IP protocol ${a["protocol-number"]}`));
  if (a.iprange) bits.push(L(`nur Ziel ${a.iprange}`, `destination only ${a.iprange}`));
  if (a.fqdn) bits.push(L(`nur Ziel ${a.fqdn}`, `destination only ${a.fqdn}`));
  if (Array.isArray(a.entries)) bits.push(L(`${a.entries.length} Einträge`, `${a.entries.length} entries`));
  if (a.proxy) bits.push("Proxy");
  return bits.length ? <span className="faint">{bits.join(" · ")}</span> : <Dash />;
}

// ACI-Filtereinträge (vzEntry)
function EntryTable({ entries }: { entries: Record<string, string>[] }) {
  const range = (lo?: string, hi?: string) => (lo ? (hi && hi !== lo ? `${lo}-${hi}` : lo) : "–");
  return (
    <table>
      <thead>
        <tr>
          <th>{L("Eintrag", "Entry")}</th>
          <th>Ether</th>
          <th>{L("Protokoll", "Protocol")}</th>
          <th>{L("Zielport", "Destination port")}</th>
          <th>{L("Quellport", "Source port")}</th>
          <th>{L("weitere", "other")}</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((e, i) => (
          <tr key={i}>
            <td className="mono">{e.name ?? "–"}</td>
            <td>{e.etherT ?? "–"}</td>
            <td>{e.prot ?? "–"}</td>
            <td className="mono">{range(e.dFromPort, e.dToPort)}</td>
            <td className="mono">{range(e.sFromPort, e.sToPort)}</td>
            <td className="faint">
              {[
                e.stateful === "yes" ? "stateful" : null,
                e.tcpRules ? `TCP ${e.tcpRules}` : null,
                e.icmpv4T ? `ICMP ${e.icmpv4T}` : null,
                e.icmpv6T ? `ICMPv6 ${e.icmpv6T}` : null,
                e.applyToFrag === "yes" ? L("Fragmente", "fragments") : null,
              ]
                .filter(Boolean)
                .join(" · ") || "–"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// Objekt-Detail wie in der NAT-Sicht: Attribute, Werte, Mitglieder, Befunde und die Regeln; seit
// 0.95.0 auch aus der Applikations-Doku (`extra`: Knöpfe über dem Detail)
export function ObjectDrawer({ id, onClose, onGo, extra }: { id: number; onClose: () => void; onGo?: Go; extra?: ReactNode }) {
  const vdomShown = useVdomShown();
  const [d, setD] = useState<ObjectDetail | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    setD(null);
    fetchObjectDetail(id).then(setD).catch(setErr);
  }, [id]);
  const service = d ? d.category === "service" || d.category === "service_group" : false;
  const attrs = Object.entries(d?.attrs ?? {}).filter(([k]) => k !== "entries");
  const entries = Array.isArray(d?.attrs?.entries) ? (d!.attrs!.entries as Record<string, string>[]) : [];
  return (
    <Drawer
      title={d?.name ?? L("Objekt", "Object")}
      sub={
        d
          ? `${CATEGORY_LABEL[d.category] ?? d.category}${d.obj_type ? ` · ${d.obj_type}` : ""} · ${d.source ?? ""}` +
            (vdomShown(d.source_system_id, d.vdom) ? ` · VDOM ${d.vdom}` : "")
          : undefined
      }
      onClose={onClose}
    >
      <ErrorBox error={err} />
      {extra}
      {!d && !err && <Loading />}
      {d && (
        <>
          <dl className="kv">
            {d.comment && (
              <>
                <dt>{L("Kommentar", "Comment")}</dt>
                <dd>{d.comment}</dd>
              </>
            )}
            {!d.members.length && (
              <>
                <dt>{service ? L("Protokoll / Ports", "Protocol / ports") : L("Werte", "Values")}</dt>
                <dd>
                  <ValueList values={d.values} service={service} />
                </dd>
              </>
            )}
            {attrs.map(([k, v]) => (
              <Fragment key={k}>
                <dt>{ATTR_LABEL[k] ?? k}</dt>
                <dd className="mono">{attrText(k, v)}</dd>
              </Fragment>
            ))}
            {d.fqdn && !service && (
              <>
                <dt>FQDN</dt>
                <dd className="mono">{d.fqdn}</dd>
              </>
            )}
            {d.interface && (
              <>
                <dt>Interface</dt>
                <dd className="mono">{d.interface}</dd>
              </>
            )}
            {d.site && (
              <>
                <dt>{L("Standort", "Site")}</dt>
                <dd>
                  <SiteChip site={d.site} from="name" />
                </dd>
              </>
            )}
            <dt>{L("In Gruppe", "In group")}</dt>
            <dd>{d.member_of.length ? <Members names={d.member_of} /> : <Dash />}</dd>
            <dt>{L("Befunde", "Findings")}</dt>
            <dd>
              <IssueChips issues={d.issues} />
            </dd>
          </dl>

          <VmSection items={d.values.filter((v) => v.vm).map((v) => ({ vm: v.vm!, role: v.value }))} />

          {entries.length > 0 && (
            <>
              <div className="sub-h">
                {L(<>Filter-Einträge ({entries.length})</>, <>Filter entries ({entries.length})</>)}
              </div>
              <EntryTable entries={entries} />
            </>
          )}

          {d.members_detail.length > 0 && (
            <>
              <div className="sub-h">
                {L(<>Mitglieder ({d.members_detail.length})</>, <>Members ({d.members_detail.length})</>)}
              </div>
              <table>
                <tbody>
                  {d.members_detail.map((m) => (
                    <tr key={m.name}>
                      <td className="mono">
                        <b>{m.name}</b>
                        {m.comment && <div className="faint">{m.comment}</div>}
                      </td>
                      <td className="faint">
                        {m.category ? (CATEGORY_LABEL[m.category] ?? m.category) : L("nicht gelesen", "not read")}
                        {m.family ? ` · ${FAMILY_LABEL[m.family] ?? m.family}` : ""}
                      </td>
                      <td className="mono">{m.values.join(", ") || "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <div className="sub-h" style={{ display: "flex", alignItems: "center" }}>
            {L(<>Firewall-Regeln ({d.rule_count})</>, <>Firewall rules ({d.rule_count})</>)}
            <div style={{ flex: 1 }} />
            {d.rule_count > 0 && onGo && (
              <button className="btn btn-ghost btn-sm" onClick={() => onGo("regeln", objectLink(d))}>
                {L("In Regeln anzeigen", "Show in rules")}
              </button>
            )}
          </div>
          {d.rules.length === 0 ? (
            <div className="faint">
              {L(
                "Keine Regel verwendet dieses Objekt (auch nicht über eine Gruppe).",
                "No rule uses this object (not even via a group).",
              )}
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>{L("Regel", "Rule")}</th>
                  <th>{L("Aktion", "Action")}</th>
                  <th>{L("Quelle", "Source")}</th>
                  <th>{L("Ziel", "Destination")}</th>
                  <th>{L("Dienste", "Services")}</th>
                </tr>
              </thead>
              <tbody>
                {d.rules.map((r) => (
                  <tr key={r.id} className={onGo ? "clickable" : undefined} onClick={() => onGo?.("regeln", { rule: String(r.id) })}>
                    <td>
                      <b>{r.name}</b>
                      <div className="faint mono">#{r.id}</div>
                    </td>
                    <td>
                      <ActionBadge action={r.action} />
                    </td>
                    <td className="mono">{r.sources.join(", ") || "–"}</td>
                    <td className="mono">{r.destinations.join(", ") || "–"}</td>
                    <td className="mono">{r.service_names.join(", ") || "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </Drawer>
  );
}

function Members({ names }: { names: string[] }) {
  if (!names.length) return <Dash />;
  const shown = names.slice(0, 8);
  return (
    <div className="objs">
      {shown.map((n) => (
        <span className="mono" key={n}>
          {n}
        </span>
      ))}
      {names.length > shown.length && (
        <span className="faint">
          {L(<>+{names.length - shown.length} weitere</>, <>+{names.length - shown.length} more</>)}
        </span>
      )}
    </div>
  );
}

const cell = (v: ReactNode) => (v === null || v === undefined || v === "" ? <Dash /> : v);

function IpamCell({ info, vlan }: { info: PrefixInfo | null; vlan?: number | null }) {
  if (!info) return <span className="faint">{L("keine Quelle", "no source")}</span>;
  if (!info.found) return <span className="chip crit">{L(<>fehlt in {info.source}</>, <>missing in {info.source}</>)}</span>;
  const vlanDiff = vlan != null && info.vlan != null && vlan !== info.vlan;
  return (
    <div className="refline">
      {info.url ? (
        <a className="chip ok" href={info.url} target="_blank" rel="noreferrer">
          {info.source} <Icon name="external" size={11} />
        </a>
      ) : (
        <span className="chip ok">{info.source}</span>
      )}
      {info.vlan != null && <span className={`chip ${vlanDiff ? "warn" : ""}`}>VLAN {info.vlan}</span>}
      {info.site && <span className="faint">{info.site}</span>}
      {info.description && <span className="faint">{info.description}</span>}
    </div>
  );
}

function InventoryTable({
  tab,
  tenantId,
  sourceId,
  q,
  issuesOnly,
  onGo,
}: {
  tab: Tab;
  tenantId: number;
  sourceId: number | null;
  q: string;
  issuesOnly: boolean;
  onGo?: (v: View, p?: ViewParams) => void;
}) {
  const kind = ({ subnets: "subnets", vlans: "vlans", ports: "interfaces", devices: "devices", vrfs: "vrfs" } as const)[
    tab as "subnets" | "vlans" | "ports" | "devices" | "vrfs"
  ];
  const [rows, setRows] = useState<unknown[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [offset, setOffset] = useState(0);
  const query = useDebounced(q);

  useEffect(() => setOffset(0), [query, sourceId, issuesOnly]);
  useEffect(() => {
    setRows(null);
    setError(null);
    let current = true; // nur die Antwort zur letzten Anfrage (Filter schnell gewechselt)
    fetchInventory(kind, tenantId, { source_system_id: sourceId, q: query })
      .then((r) => current && setRows(r))
      .catch((e) => {
        if (!current) return;
        setError(e);
        setRows([]);
      });
    return () => {
      current = false;
    };
  }, [kind, tenantId, sourceId, query]);

  let all = rows ?? [];
  if (kind === "subnets" && issuesOnly) {
    all = (all as InvSubnet[]).filter(
      (s) => (s.ipam && !s.ipam.found) || (s.vlan != null && s.ipam?.vlan != null && s.vlan !== s.ipam.vlan),
    );
  }
  const page = all.slice(offset, offset + PAGE);

  const table = (() => {
    switch (kind) {
      case "subnets":
        return (
          <table>
            <thead>
              <tr>
                <th>{L("Subnetz", "Subnet")}</th>
                <th>Gateway</th>
                <th>{L("Kontext", "Context")}</th>
                <th>VRF</th>
                <th>VLAN</th>
                <th>{L("IPAM (führend)", "IPAM (primary)")}</th>
                <th>{L("Anreicherung", "Enrichment")}</th>
                <th>{L("Quelle", "Source")}</th>
              </tr>
            </thead>
            <tbody>
              {(page as InvSubnet[]).map((s) => (
                <tr key={s.id}>
                  <td className="mono nowrap">
                    <b>{s.prefix}</b>
                    {s.description && <div className="faint">{s.description}</div>}
                    {onGo && (
                      <div>
                        <button
                          className="linkbtn"
                          title={L(
                            "Firewall-Regeln und Contracts, deren Quelle oder Ziel sich mit dem Subnetz überschneidet",
                            "Firewall rules and contracts whose source or destination overlaps with the subnet",
                          )}
                          onClick={() => onGo("regeln", { net: s.prefix })}
                        >
                          {L("Regeln im Subnetz", "Rules in the subnet")}
                        </button>
                      </div>
                    )}
                  </td>
                  <td className="mono">{cell(s.gateway)}</td>
                  <td>
                    <span className="faint">{s.context === "bd" ? "BD" : s.context === "epg" ? "EPG" : s.context}</span>{" "}
                    <span className="mono">{s.context_name}</span>
                    {s.site && (
                      <>
                        {" "}
                        <SiteChip site={s.site} from="name" />
                      </>
                    )}
                    {(s.epg || s.ap || s.aci_tenant) && (
                      <div className="faint">{[s.aci_tenant, s.ap, s.epg].filter(Boolean).join(" / ")}</div>
                    )}
                  </td>
                  <td className="mono">{cell(s.vrf)}</td>
                  <td className="mono">{cell(s.vlan)}</td>
                  <td>
                    <IpamCell info={s.ipam} vlan={s.vlan} />
                  </td>
                  <td>
                    <IpamCell info={s.enrichment} />
                  </td>
                  <td className="nowrap">
                    {cell(s.source)}
                    <SharedChip shared={s.shared} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        );
      case "vlans":
        return (
          <table>
            <thead>
              <tr>
                <th className="num">VLAN</th>
                <th>Name</th>
                <th>{L("Gruppe", "Group")}</th>
                <th>EPGs</th>
                <th>{L("Beschreibung", "Description")}</th>
                <th>{L("Quelle", "Source")}</th>
              </tr>
            </thead>
            <tbody>
              {(page as InvVlan[]).map((v) => (
                <tr key={v.id}>
                  <td className="num mono">
                    <b>{v.vid}</b>
                  </td>
                  <td className="mono">{v.name}</td>
                  <td>{cell(v.group)}</td>
                  <td className="mono">
                    <Short items={v.epgs} />
                  </td>
                  <td className="muted">{cell(v.description)}</td>
                  <td className="nowrap">
                    {cell(v.source)}
                    <SharedChip shared={v.shared} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        );
      case "interfaces":
        return <PortsByDevice ports={all as InvInterface[]} expanded={!!query} />;
      case "devices":
        return (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>{L("Rolle", "Role")}</th>
                <th>{L("Modell", "Model")}</th>
                <th>{L("Seriennummer", "Serial number")}</th>
                <th>Node</th>
                <th>{L("Mgmt-IP", "Mgmt IP")}</th>
                <th>Version</th>
                <th>{L("Standort", "Site")}</th>
                <th>{L("Quelle", "Source")}</th>
              </tr>
            </thead>
            <tbody>
              {(page as InvDevice[]).map((d) => (
                <tr key={d.id}>
                  <td className="mono">
                    <b>{d.name}</b>
                  </td>
                  <td>{d.role}</td>
                  <td>{cell(d.model)}</td>
                  <td className="mono">{cell(d.serial)}</td>
                  <td className="mono">{cell(d.node_id)}</td>
                  <td className="mono">{cell(d.mgmt_ip)}</td>
                  <td className="mono">{cell(d.version)}</td>
                  <td>{d.site ? <SiteChip site={d.site} from={d.site_from} /> : <Dash />}</td>
                  <td className="nowrap">
                    {cell(d.source)}
                    <SharedChip shared={d.shared} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        );
      default:
        return (
          <table>
            <thead>
              <tr>
                <th>VRF</th>
                <th>{L("Quelle", "Source")}</th>
              </tr>
            </thead>
            <tbody>
              {(page as InvVrf[]).map((v) => (
                <tr key={v.id}>
                  <td className="mono">
                    <b>{v.name}</b>
                  </td>
                  <td>
                    {cell(v.source)}
                    <SharedChip shared={v.shared} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        );
    }
  })();

  return (
    <>
      <ErrorBox error={error} prefix={L("Inventar laden", "Loading inventory")} />
      <div className="card">
        <div className="card-body scroll">
          {rows === null ? (
            <Loading>{L("Lade Inventar…", "Loading inventory…")}</Loading>
          ) : page.length === 0 ? (
            <Empty
              title={query || issuesOnly ? L("Keine Treffer", "No matches") : L("Noch kein Inventar", "No inventory yet")}
            >
              {query || issuesOnly
                ? L("Filter oder Suche anpassen.", "Adjust the filter or search.")
                : L(
                    "Das Netzinventar erscheint nach dem ersten Lauf einer FortiGate oder eines APIC.",
                    "The network inventory appears after the first run of a FortiGate or an APIC.",
                  )}
            </Empty>
          ) : (
            table
          )}
        </div>
        {all.length > 0 && kind !== "interfaces" && <Pager total={all.length} offset={offset} onOffset={setOffset} />}
      </div>
    </>
  );
}

const POWER: Record<string, [string, string]> = {
  poweredOn: ["ok", L("an", "on")],
  poweredOff: ["na", L("aus", "off")],
  suspended: ["warn", L("pausiert", "suspended")],
};

// VMs aus dem vCenter (ADR 0017): NICs mit Portgruppe → EPG, IPs mit IPAM-Abgleich
function VmTable({ tenantId, sourceId, q, onGo }: { tenantId: number; sourceId: number | null; q: string; onGo?: Go }) {
  const [data, setData] = useState<Paged<VmRow> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [offset, setOffset] = useState(0);
  const query = useDebounced(q);
  useEffect(() => setOffset(0), [query, sourceId]);
  useEffect(() => {
    setData(null);
    setError(null);
    let current = true;
    fetchVms(tenantId, { q: query, source_system_id: sourceId, limit: PAGE, offset })
      .then((d) => current && setData(d))
      .catch((e) => {
        if (!current) return;
        setError(e);
        setData({ total: 0, rows: [] });
      });
    return () => {
      current = false;
    };
  }, [tenantId, sourceId, query, offset]);
  return (
    <>
      <ErrorBox error={error} prefix={L("VMs laden", "Loading VMs")} />
      <div className="card">
        <div className="card-body scroll">
          {data === null ? (
            <Loading>{L("Lade VMs…", "Loading VMs…")}</Loading>
          ) : data.rows.length === 0 ? (
            <Empty title={query ? L("Keine Treffer", "No matches") : L("Noch keine VMs", "No VMs yet")}>
              {query
                ? L("Suche anpassen.", "Adjust the search.")
                : L(
                    "VMs erscheinen nach dem ersten Lauf eines vCenter-Quellsystems (VMware).",
                    "VMs appear after the first run of a vCenter source system (VMware).",
                  )}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>VM</th>
                  <th>Status</th>
                  <th>{L("Netzwerk (Portgruppe → EPG, IPs)", "Network (port group → EPG, IPs)")}</th>
                  <th>Cluster / Host</th>
                  <th>{L("Ressourcen", "Resources")}</th>
                  <th>{L("Quelle", "Source")}</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((v) => {
                  const [cls, label] = POWER[v.power_state ?? ""] ?? ["na", v.power_state ?? "–"];
                  return (
                    <tr key={v.id}>
                      <td>
                        <b className="mono">{v.name}</b>
                        {v.template && (
                          <span className="chip" style={{ marginLeft: 6 }}>
                            {L("Vorlage", "Template")}
                          </span>
                        )}
                        {v.hostname && <div className="faint mono">{v.hostname}</div>}
                        {v.guest_os && <div className="faint">{v.guest_os}</div>}
                        {v.annotation && <div className="faint">{v.annotation}</div>}
                        {onGo && (
                          <button
                            className="linkbtn"
                            title={L(
                              "Regeln, deren Quelle oder Ziel eine IP der VM enthält",
                              "Rules whose source or destination contains an IP of the VM",
                            )}
                            onClick={() => onGo("regeln", { q: v.name })}
                          >
                            {L("Regeln der VM", "Rules of the VM")}
                          </button>
                        )}
                      </td>
                      <td>
                        <span className={`chip ${cls}`}>{label}</span>
                      </td>
                      <td style={{ minWidth: 300 }}>
                        {v.nics.length === 0 && <Dash />}
                        {v.nics.map((n) => (
                          <div key={n.name} style={{ marginBottom: 4 }}>
                            <span className="mono">{n.portgroup ?? n.name}</span>
                            {n.vlan != null && <span className="faint"> · VLAN {n.vlan}</span>}
                            {n.epg && (
                              <span
                                className="chip local"
                                style={{ marginLeft: 6 }}
                                title={
                                  n.epg_match === "vlan"
                                    ? L("über das VLAN zugeordnet", "assigned via the VLAN")
                                    : L("über den Portgruppen-Namen zugeordnet", "assigned via the port group name")
                                }
                              >
                                EPG {n.epg}
                              </span>
                            )}
                            {n.connected === false && <span className="chip na">{L("getrennt", "disconnected")}</span>}
                            <div>
                              {n.ips.map((ip) => (
                                <span key={ip.ip} className="refline">
                                  <span className="mono">{ip.ip}</span>
                                  {ip.dns.length > 0 && <span className="faint"> {ip.dns.join(", ")}</span>}
                                  {ip.in_ipam === false && (
                                    <span className="chip warn">{L("nicht im IPAM", "not in IPAM")}</span>
                                  )}
                                </span>
                              ))}
                            </div>
                          </div>
                        ))}
                      </td>
                      <td>
                        <div className="mono">{v.cluster ?? <Dash />}</div>
                        {v.host && <div className="faint mono">{v.host}</div>}
                        {v.datacenter && <div className="faint">{v.datacenter}</div>}
                      </td>
                      <td className="nowrap faint">
                        {[
                          v.vcpus != null ? `${v.vcpus} vCPU` : null,
                          v.memory_mb != null ? `${Math.round(v.memory_mb / 1024)} GB RAM` : null,
                          v.disk_gb != null ? `${v.disk_gb} GB Disk` : null,
                        ]
                          .filter(Boolean)
                          .join(" · ") || "–"}
                      </td>
                      <td className="nowrap">{v.source ?? <Dash />}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        {data && data.total > 0 && <Pager total={data.total} offset={offset} onOffset={setOffset} />}
      </div>
    </>
  );
}

// Portgruppen mit VLAN und zugeordneter EPG (Name Tenant|AP|EPG bzw. eindeutiges VLAN); Klick
// öffnet das Detail mit Subnetzen und VMs (0.13.0)
function PortgroupTable({ tenantId, q, onGo }: { tenantId: number; q: string; onGo?: Go }) {
  const [rows, setRows] = useState<PortgroupRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const query = useDebounced(q);
  useEffect(() => {
    setRows(null);
    let current = true;
    fetchPortgroups(tenantId, query)
      .then((r) => current && setRows(r))
      .catch((e) => {
        if (!current) return;
        setError(e);
        setRows([]);
      });
    return () => {
      current = false;
    };
  }, [tenantId, query]);
  return (
    <>
      <ErrorBox error={error} prefix={L("Portgruppen laden", "Loading port groups")} />
      <div className="card">
        <div className="card-body scroll">
          {rows === null ? (
            <Loading>{L("Lade Portgruppen…", "Loading port groups…")}</Loading>
          ) : rows.length === 0 ? (
            <Empty title={query ? L("Keine Treffer", "No matches") : L("Noch keine Portgruppen", "No port groups yet")}>
              {query
                ? L("Suche anpassen.", "Adjust the search.")
                : L(
                    "Portgruppen erscheinen nach dem ersten Lauf eines vCenter-Quellsystems.",
                    "Port groups appear after the first run of a vCenter source system.",
                  )}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>{L("Portgruppe", "Port group")}</th>
                  <th>Switch</th>
                  <th>VLAN</th>
                  <th className="num">VMs</th>
                  <th>EPG</th>
                  <th>{L("Quelle", "Source")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr
                    key={p.id}
                    className="clickable"
                    title={L("Details: EPG, Subnetze, VMs", "Details: EPG, subnets, VMs")}
                    onClick={() => setSelected(p.id)}
                  >
                    <td className="nowrap">
                      <b className="mono">{p.name}</b>{" "}
                      <span className="faint">{p.kind === "dvs" ? L("verteilt", "distributed") : L("Standard", "standard")}</span>
                    </td>
                    <td className="mono">{p.switch ?? <Dash />}</td>
                    <td className="mono">{p.vlan ?? p.vlan_ranges ?? <Dash />}</td>
                    <td className="num">{p.vm_count}</td>
                    <td className="nowrap">
                      {p.epg ? (
                        <span
                          className="chip local"
                          title={p.epg_match === "vlan" ? L("über das VLAN", "via the VLAN") : L("über den Namen", "via the name")}
                        >
                          {p.epg}
                        </span>
                      ) : (
                        <Dash />
                      )}
                    </td>
                    <td className="nowrap">{p.source ?? <Dash />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {rows && rows.length > 0 && (
          <div className="pager">{L(<>{rows.length} Portgruppen</>, <>{rows.length} port groups</>)}</div>
        )}
      </div>
      {selected !== null && <PortgroupDrawer id={selected} onClose={() => setSelected(null)} onGo={onGo} />}
    </>
  );
}

const SUBNET_SOURCE_HINT: Record<string, string> = {
  "ACI-EPG": L("Subnetz an der EPG", "Subnet on the EPG"),
  "ACI-BD": L("Subnetz der Bridge Domain der EPG", "Subnet of the bridge domain of the EPG"),
  "ACI-L3Out": L("Subnetz der externen EPG", "Subnet of the external EPG"),
};

function PortgroupDrawer({ id, onClose, onGo }: { id: number; onClose: () => void; onGo?: Go }) {
  const [d, setD] = useState<PortgroupDetail | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    setD(null);
    setErr(null);
    fetchPortgroup(id).then(setD).catch(setErr);
  }, [id]);
  return (
    <Drawer
      title={d?.name ?? L("Portgruppe", "Port group")}
      sub={
        d
          ? [
              d.kind === "dvs"
                ? L("verteilte Portgruppe", "distributed port group")
                : L("Standard-Portgruppe (Host)", "standard port group (host)"),
              d.switch,
              d.source,
            ]
              .filter(Boolean)
              .join(" · ")
          : undefined
      }
      onClose={onClose}
    >
      <ErrorBox error={err} />
      {!d && !err && <Loading />}
      {d && (
        <>
          <dl className="kv">
            <dt>VLAN</dt>
            <dd className="mono">{d.vlan ?? (d.vlan_ranges ? `Trunk ${d.vlan_ranges}` : <Dash />)}</dd>
            <dt>EPG</dt>
            <dd>
              {d.epg ? (
                <>
                  <span className="chip local">{d.epg.label}</span>{" "}
                  <span className="faint">
                    {d.epg_match === "vlan"
                      ? L("über das VLAN zugeordnet", "assigned via the VLAN")
                      : L("über den Namen zugeordnet", "assigned via the name")}
                  </span>
                  <div className="faint mono">
                    {[d.epg.tenant && `Tenant ${d.epg.tenant}`, d.epg.ap && `AP ${d.epg.ap}`, d.epg.bd && `BD ${d.epg.bd}`, d.epg.vrf && `VRF ${d.epg.vrf}`]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                  {d.epg.dn && <div className="faint mono">{d.epg.dn}</div>}
                </>
              ) : (
                <span className="faint">
                  {L(
                    "keine EPG zugeordnet (Name nicht Tenant|AP|EPG, VLAN nicht eindeutig)",
                    "no EPG assigned (name not Tenant|AP|EPG, VLAN not unique)",
                  )}
                </span>
              )}
            </dd>
            <dt>VMs</dt>
            <dd>{d.vms.length}</dd>
          </dl>

          <div className="sub-h">{L(<>Subnetze ({d.subnets.length})</>, <>Subnets ({d.subnets.length})</>)}</div>
          {d.subnets.length === 0 ? (
            <div className="faint">
              {L(
                <>
                  Kein Subnetz bekannt – weder an der EPG/BD noch im Netzinventar (gleiches VLAN) noch im IPAM für die IPs der VMs.
                </>,
                <>
                  No subnet known – neither on the EPG/BD nor in the network inventory (same VLAN) nor in the IPAM for the
                  IPs of the VMs.
                </>,
              )}
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>{L("Subnetz", "Subnet")}</th>
                  <th>Gateway / VRF</th>
                  <th>{L("Quelle", "Source")}</th>
                </tr>
              </thead>
              <tbody>
                {d.subnets.map((s) => (
                  <tr key={s.prefix}>
                    <td className="mono nowrap">
                      <b>{s.prefix}</b>
                      {s.description && <div className="faint">{s.description}</div>}
                      {onGo && (
                        <div>
                          <button
                            className="linkbtn"
                            title={L(
                              "Firewall-Regeln und Contracts, deren Quelle oder Ziel sich mit dem Subnetz überschneidet",
                              "Firewall rules and contracts whose source or destination overlaps with the subnet",
                            )}
                            onClick={() => onGo("regeln", { net: s.prefix })}
                          >
                            {L("Regeln im Subnetz", "Rules in the subnet")}
                          </button>
                        </div>
                      )}
                    </td>
                    <td className="mono">
                      {cell(s.gateway)}
                      {s.vrf && <div className="faint">{s.vrf}</div>}
                    </td>
                    <td>
                      <div className="objs">
                        {s.sources.map((src) => (
                          <span
                            key={src}
                            className="chip"
                            title={
                              SUBNET_SOURCE_HINT[src] ??
                              (src.startsWith("VLAN ")
                                ? L(
                                    "gleiches VLAN im Netzinventar – VLAN-Nummern gelten je Gerät/Domäne",
                                    "same VLAN in the network inventory – VLAN numbers apply per device/domain",
                                  )
                                : L(
                                    "Netz im IPAM, das die IP einer VM enthält",
                                    "Network in the IPAM that contains the IP of a VM",
                                  ))
                            }
                          >
                            {src}
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div className="sub-h">VMs ({d.vms.length})</div>
          {d.vms.length === 0 ? (
            <div className="faint">
              {L(
                "Keine VM mit einer Netzwerkkarte an dieser Portgruppe.",
                "No VM with a network adapter on this port group.",
              )}
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>VM</th>
                  <th>NIC</th>
                  <th>IPs</th>
                  <th>Cluster / Host</th>
                </tr>
              </thead>
              <tbody>
                {d.vms.map((v) => {
                  const [tone, label] = POWER[v.power_state ?? ""] ?? ["na", v.power_state ?? "–"];
                  return (
                    <tr key={`${v.id}-${v.nic}`}>
                      <td className="nowrap">
                        <b className="mono">{v.name}</b>
                        <div>
                          <span className={`chip ${tone}`}>{label}</span>
                          {v.template && <span className="chip">{L("Vorlage", "Template")}</span>}
                        </div>
                      </td>
                      <td className="mono">
                        {v.nic}
                        {v.connected === false && <span className="chip na">{L("getrennt", "disconnected")}</span>}
                        {v.mac && <div className="faint">{v.mac}</div>}
                      </td>
                      <td className="mono">
                        {v.ips.length === 0 ? (
                          <Dash />
                        ) : (
                          v.ips.map((ip) => (
                            <div key={ip.ip}>
                              {ip.ip}
                              {ip.in_ipam === false && (
                                <span
                                  className="chip warn"
                                  title={L("IP nicht im führenden IPAM erfasst", "IP not recorded in the primary IPAM")}
                                >
                                  {L("nicht im IPAM", "not in IPAM")}
                                </span>
                              )}
                              {ip.dns.length > 0 && <div className="faint">{ip.dns.join(", ")}</div>}
                            </div>
                          ))
                        )}
                      </td>
                      <td className="mono">
                        {cell(v.cluster)}
                        {v.host && <div className="faint">{v.host}</div>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </>
      )}
    </Drawer>
  );
}

// Ports je Gerät (Dev-Befund 0.12.0): Gerät als Kopfzeile (auf-/zuklappbar), darunter die Ports;
// Subinterfaces unter ihrem Parent, LAG-Mitglieder unter dem LAG.
const byName = (a: InvInterface, b: InvInterface) => a.name.localeCompare(b.name, undefined, { numeric: true });

function portTree(ports: InvInterface[]): { port: InvInterface; depth: number }[] {
  const names = new Set(ports.map((p) => p.name));
  const children = new Map<string, InvInterface[]>();
  const roots: InvInterface[] = [];
  for (const p of ports) {
    const up = p.parent && names.has(p.parent) && p.parent !== p.name ? p.parent : p.lag && names.has(p.lag) && p.lag !== p.name ? p.lag : null;
    if (up) children.set(up, [...(children.get(up) ?? []), p]);
    else roots.push(p);
  }
  const out: { port: InvInterface; depth: number }[] = [];
  const seen = new Set<string>();
  const walk = (p: InvInterface, depth: number) => {
    if (seen.has(p.name)) return;
    seen.add(p.name);
    out.push({ port: p, depth });
    for (const c of (children.get(p.name) ?? []).sort(byName)) walk(c, depth + 1);
  };
  roots.sort(byName).forEach((p) => walk(p, 0));
  ports.filter((p) => !seen.has(p.name)).forEach((p) => walk(p, 0)); // Zyklen: flach anhängen
  return out;
}

// VLAN-Liste als Bereiche (100-199, 300) – wie die Trunks der Portgruppen
function vlanRanges(vids: number[]): string[] {
  const v = [...new Set(vids)].sort((a, b) => a - b);
  const out: string[] = [];
  for (let i = 0; i < v.length; ) {
    let j = i;
    while (j + 1 < v.length && v[j + 1] === v[j] + 1) j++;
    out.push(j > i ? `${v[i]}-${v[j]}` : `${v[i]}`);
    i = j + 1;
  }
  return out;
}

// Lange Listen kurz: die ersten Einträge, der Rest als „+N“ (alle im Tooltip bzw. im Detail)
function Short({ items, max = 2 }: { items: string[] | null | undefined; max?: number }) {
  if (!items?.length) return <Dash />;
  const rest = items.length - max;
  return (
    <span title={items.length > max ? items.join("\n") : undefined}>
      {items.slice(0, max).join(", ")}
      {rest > 0 && <span className="faint"> +{rest}</span>}
    </span>
  );
}

const isSubinterface = (i: InvInterface) =>
  i.kind === "vlan" && !!i.parent && i.tagged_vlans?.length === 1 && i.untagged_vlan == null;

// VLANs eines Ports in einer Zeile: wenige Bereiche ausgeschrieben, sonst nur die Anzahl (Detail beim Klick)
function VlanCell({ i }: { i: InvInterface }) {
  if (isSubinterface(i)) {
    return (
      <span title={L(`Subinterface – sendet getaggt über ${i.parent}`, `Subinterface – sends tagged via ${i.parent}`)}>
        VLAN {i.tagged_vlans![0]}{" "}
        <span className="faint">{L(<>getaggt über {i.parent}</>, <>tagged via {i.parent}</>)}</span>
      </span>
    );
  }
  const tagged = i.tagged_vlans ?? [];
  const ranges = vlanRanges(tagged);
  if (i.untagged_vlan == null && !tagged.length) return <Dash />;
  return (
    <span className="nowrap">
      {i.untagged_vlan != null && `untagged ${i.untagged_vlan}`}
      {i.untagged_vlan != null && tagged.length > 0 && " · "}
      {tagged.length > 0 &&
        (ranges.length <= 3 ? `tagged ${ranges.join(", ")}` : <span title={ranges.join(", ")}>tagged {tagged.length} VLANs</span>)}
    </span>
  );
}

function PortDrawer({
  port: i,
  ports,
  onPick,
  onClose,
}: {
  port: InvInterface;
  ports: InvInterface[]; // Ports desselben Geräts (Parent, Subinterfaces, LAG-Mitglieder)
  onPick: (p: InvInterface) => void;
  onClose: () => void;
}) {
  const vdomShown = useVdomShown();
  const children = ports.filter((p) => p.name !== i.name && (p.parent === i.name || p.lag === i.name)).sort(byName);
  const up = ports.find((p) => p.name !== i.name && (p.name === i.parent || p.name === i.lag));
  const tagged = i.tagged_vlans ?? [];
  const link = (p: InvInterface) => (
    <button key={p.id} className="linkbtn mono" onClick={() => onPick(p)}>
      {p.name}
    </button>
  );
  return (
    <Drawer title={`${i.device} · ${i.name}`} sub={[i.kind, i.mode, i.source].filter(Boolean).join(" · ")} onClose={onClose}>
      <dl className="kv">
        <dt>Status</dt>
        <dd>
          {i.enabled === false ? (
            <span className="chip na">{L("deaktiviert", "disabled")}</span>
          ) : i.enabled ? (
            L("aktiv", "active")
          ) : (
            <Dash />
          )}
        </dd>
        {i.description && (
          <>
            <dt>{L("Beschreibung", "Description")}</dt>
            <dd>{i.description}</dd>
          </>
        )}
        {(i.parent || i.lag) && (
          <>
            <dt>{i.parent ? "Parent" : "LAG"}</dt>
            <dd className="mono">
              {up ? link(up) : (i.parent ?? i.lag)}
              {i.parent && i.lag && i.lag !== i.parent && <span className="faint"> · LAG {i.lag}</span>}
            </dd>
          </>
        )}
        {children.length > 0 && (
          <>
            <dt>{children.some((c) => c.parent === i.name) ? "Subinterfaces" : L("Mitglieder", "Members")}</dt>
            <dd>
              <div className="objs">{children.map(link)}</div>
            </dd>
          </>
        )}
        <dt>VLAN untagged</dt>
        <dd className="mono">{cell(isSubinterface(i) ? null : i.untagged_vlan)}</dd>
        <dt>VLANs tagged{tagged.length > 1 ? ` (${tagged.length})` : ""}</dt>
        <dd className="mono">
          {tagged.length ? vlanRanges(tagged).join(", ") : <Dash />}
          {isSubinterface(i) && (
            <div className="faint">
              {L(<>Subinterface – sendet getaggt über {i.parent}</>, <>Subinterface – sends tagged via {i.parent}</>)}
            </div>
          )}
        </dd>
        <dt>IPs</dt>
        <dd className="mono">{i.ips?.length ? i.ips.map((ip) => <div key={ip}>{ip}</div>) : <Dash />}</dd>
        {vdomShown(i.source_system_id, i.vdom) && (
          <>
            <dt>VDOM</dt>
            <dd>
              <VdomChip vdom={i.vdom} always />
            </dd>
          </>
        )}
        {i.zone && (
          <>
            <dt>Zone</dt>
            <dd className="mono">{i.zone}</dd>
          </>
        )}
        {i.policy_group && (
          <>
            <dt>{L("Policy-Gruppe", "Policy group")}</dt>
            <dd className="mono">{i.policy_group}</dd>
          </>
        )}
        <dt>{L("Nachbar", "Neighbor")}</dt>
        <dd className="mono">{cell([i.neighbor, i.neighbor_port].filter(Boolean).join(" : "))}</dd>
        <dt>{L("Quelle", "Source")}</dt>
        <dd>{cell(i.source)}</dd>
      </dl>
      <div className="sub-h">EPGs ({i.epgs?.length ?? 0})</div>
      {i.epgs?.length ? (
        <div className="objs">
          {[...i.epgs].sort().map((e) => (
            <span key={e} className="mono">
              {e}
            </span>
          ))}
        </div>
      ) : (
        <div className="faint">{L("Keine EPG an diesem Port.", "No EPG on this port.")}</div>
      )}
    </Drawer>
  );
}

const DEVICES_PER_PAGE = 50;

// Kompakt (Dev-Befund 0.13.0): eine Zeile je Port, lange VLAN-/EPG-Listen gekürzt; Klick öffnet
// das Detail mit allen Angaben.
function PortsByDevice({ ports, expanded }: { ports: InvInterface[]; expanded: boolean }) {
  const devices = useMemo(() => {
    const m = new Map<string, InvInterface[]>();
    for (const p of ports) {
      const key = `${p.source ?? ""}\u0000${p.device}`;
      m.set(key, [...(m.get(key) ?? []), p]);
    }
    return [...m.entries()]
      .map(([key, list]) => ({ key, device: list[0].device, source: list[0].source, ports: list }))
      .sort((a, b) => a.device.localeCompare(b.device, undefined, { numeric: true }));
  }, [ports]);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<{ port: InvInterface; key: string } | null>(null);
  useEffect(() => setOffset(0), [ports]);
  const isOpen = (key: string) => expanded || devices.length <= 3 || open.has(key);
  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const page = devices.slice(offset, offset + DEVICES_PER_PAGE);
  return (
    <>
      <table className="compact">
        <thead>
          <tr>
            <th>Port</th>
            <th>{L("Typ / Modus", "Type / mode")}</th>
            <th>VLANs</th>
            <th>IPs</th>
            <th>{L("Nachbar", "Neighbor")}</th>
            <th>EPGs</th>
          </tr>
        </thead>
        <tbody>
          {page.map((d) => (
            <Fragment key={d.key}>
              <tr className="clickable group-row" onClick={() => toggle(d.key)}>
                <td colSpan={6}>
                  <span className="mono">{isOpen(d.key) ? "▾" : "▸"}</span> <b className="mono">{d.device}</b>{" "}
                  <span className="faint">
                    · {d.ports.length} Port{d.ports.length === 1 ? "" : "s"}{d.source ? ` · ${d.source}` : ""}
                  </span>
                </td>
              </tr>
              {isOpen(d.key) &&
                portTree(d.ports).map(({ port: i, depth }) => (
                  <tr
                    key={i.id}
                    className={`clickable ${selected?.port.id === i.id ? "selected" : ""}`}
                    title={i.description ?? "Details"}
                    onClick={() => setSelected({ port: i, key: d.key })}
                  >
                    <td className="mono nowrap" style={{ paddingLeft: 12 + depth * 18 }}>
                      {depth > 0 && <span className="faint">└ </span>}
                      <b>{i.name}</b>
                      {i.enabled === false && <span className="chip na">{L("aus", "off")}</span>}
                      {i.lag && depth > 0 && i.lag !== i.parent && <span className="chip">LAG {i.lag}</span>}
                    </td>
                    <td className="nowrap">
                      {i.kind}
                      {i.mode && <span className="faint"> · {i.mode}</span>}
                    </td>
                    <td className="mono">
                      <VlanCell i={i} />
                    </td>
                    <td className="mono nowrap">
                      <Short items={i.ips} max={1} />
                    </td>
                    <td className="mono nowrap">{cell([i.neighbor, i.neighbor_port].filter(Boolean).join(" : "))}</td>
                    <td className="mono nowrap">
                      {i.epgs && i.epgs.length > 1 ? (
                        <span className="chip" title={i.epgs.join("\n")}>
                          {i.epgs.length} EPGs
                        </span>
                      ) : (
                        <Short items={i.epgs} max={1} />
                      )}
                    </td>
                  </tr>
                ))}
            </Fragment>
          ))}
        </tbody>
      </table>
      {devices.length > DEVICES_PER_PAGE && (
        <Pager total={devices.length} offset={offset} onOffset={setOffset} pageSize={DEVICES_PER_PAGE} />
      )}
      {selected && (
        <PortDrawer
          port={selected.port}
          ports={devices.find((d) => d.key === selected.key)?.ports ?? []}
          onPick={(port) => setSelected({ port, key: selected.key })}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
