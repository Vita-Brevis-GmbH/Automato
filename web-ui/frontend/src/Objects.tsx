// Objekte & Inventar (ADR 0010): Firewall-Objekte wie auf dem Gerät mit Abgleich gegen das IPAM,
// dazu das Netzinventar aus FortiGate/APIC (Subnetze, VLANs, Ports, Geräte, VRFs). Rein lesend.
import { Fragment, ReactNode, useEffect, useState } from "react";
import {
  DeviceObject,
  fetchInventory,
  fetchObjectDetail,
  fetchObjects,
  fetchPortgroups,
  fetchSystems,
  fetchVms,
  InvDevice,
  InvInterface,
  InvSubnet,
  InvVlan,
  InvVrf,
  ObjectDetail,
  Paged,
  PortgroupRow,
  PrefixInfo,
  System,
  VmRow,
} from "./api";
import { CATEGORY_LABEL, FAMILY_LABEL, IssueChips, REFERENCE_PLATFORMS, ValueList } from "./refs";
import { SiteChip } from "./sites";
import type { View, ViewParams } from "./App";
import { ActionBadge, Dash, Drawer, Empty, ErrorBox, Icon, Loading, Note, PageHead } from "./ui";

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
  { key: "addr", label: "Adressobjekte", category: "address,vip,ippool" },
  { key: "groups", label: "Gruppen", category: "address_group,service_group" },
  { key: "services", label: "Services", category: "service" },
  { key: "subnets", label: "Subnetze" },
  { key: "vlans", label: "VLANs" },
  { key: "ports", label: "Ports" },
  { key: "devices", label: "Geräte" },
  { key: "vrfs", label: "VRFs" },
  { key: "vms", label: "VMs" },
  { key: "portgroups", label: "Portgruppen" },
];

const PAGE = 200;

export default function Objects({
  tenantId,
  tenantLabel,
  onGo,
}: {
  tenantId: number | null;
  tenantLabel: string;
  onGo?: Go; // Klick auf "Regeln" → Regel-Liste mit diesem Objekt (auch über Gruppen)
}) {
  const [tab, setTab] = useState<Tab>("addr");
  const [systems, setSystems] = useState<System[]>([]);
  const [source, setSource] = useState("");
  const [q, setQ] = useState("");
  const [issuesOnly, setIssuesOnly] = useState(false);

  useEffect(() => {
    setSource("");
    if (tenantId === null) return;
    fetchSystems(tenantId)
      .then((list) => setSystems(list.filter((s) => !REFERENCE_PLATFORMS.includes(s.platform))))
      .catch(() => setSystems([]));
  }, [tenantId]);

  if (tenantId === null) {
    return (
      <>
        <PageHead title="Objekte & Inventar" />
        <Note>Bitte oben einen Mandanten wählen – Objekte und Inventar werden je Mandant angezeigt.</Note>
      </>
    );
  }

  const current = TABS.find((t) => t.key === tab)!;
  const sourceId = source ? Number(source) : null;
  const canFilterIssues = !!current.category || tab === "subnets";

  return (
    <>
      <PageHead title="Objekte & Inventar">
        Firewall-Objekte von „{tenantLabel}“ so, wie sie auf dem Gerät stehen, mit Referenzdaten aus dem IPAM
        (DDI/NetBox) – dazu das Netzinventar aus FortiGate und APIC. Rein lesend.
      </PageHead>

      <div className="tabbar">
        {TABS.map((t) => (
          <button key={t.key} className={tab === t.key ? "on" : ""} onClick={() => setTab(t.key)}>
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
            placeholder={current.category ? "Name, Wert oder Kommentar…" : "Suche…"}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <select className="sel" value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="">Quelle: alle</option>
          {systems.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        {canFilterIssues && (
          <button className={`toggle ${issuesOnly ? "on" : ""}`} onClick={() => setIssuesOnly((v) => !v)}>
            <span className="dot y" />
            nur Abweichungen
          </button>
        )}
      </div>

      {tab === "vms" ? (
        <VmTable tenantId={tenantId} sourceId={sourceId} q={q} onGo={onGo} />
      ) : tab === "portgroups" ? (
        <PortgroupTable tenantId={tenantId} q={q} />
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
    </>
  );
}

// Link in die Regeln: genau dieses Objekt (IPv4 und IPv6 heissen teils gleich)
function objectLink(o: { id: number; name: string; family: string | null }): ViewParams {
  const p: ViewParams = { object: o.name, object_id: String(o.id) };
  if (o.family) p.family = o.family;
  return p;
}

function Pager({ total, offset, onOffset }: { total: number; offset: number; onOffset: (o: number) => void }) {
  if (total <= PAGE) return <div className="pager">{total} Einträge</div>;
  return (
    <div className="pager">
      {offset + 1}–{Math.min(offset + PAGE, total)} von {total}
      <button className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => onOffset(offset - PAGE)}>
        Zurück
      </button>
      <button
        className="btn btn-ghost btn-sm"
        disabled={offset + PAGE >= total}
        onClick={() => onOffset(offset + PAGE)}
      >
        Weiter
      </button>
    </div>
  );
}

function useDebounced<T>(value: T, ms = 300): T {
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
  onGo,
}: {
  tenantId: number;
  category: string;
  group: boolean;
  service: boolean;
  sourceId: number | null;
  q: string;
  issuesOnly: boolean;
  onGo?: Go;
}) {
  const [data, setData] = useState<{ total: number; rows: DeviceObject[] } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const query = useDebounced(q);

  useEffect(() => setOffset(0), [query, sourceId, issuesOnly]);
  useEffect(() => {
    setData(null);
    setError(null);
    fetchObjects(tenantId, {
      category,
      source_system_id: sourceId,
      q: query,
      issues_only: issuesOnly,
      limit: PAGE,
      offset,
    })
      .then(setData)
      .catch((e) => {
        setError(e);
        setData({ total: 0, rows: [] });
      });
  }, [tenantId, category, sourceId, query, issuesOnly, offset]);

  return (
    <>
      <ErrorBox error={error} prefix="Objekte laden" />
      <div className="card">
        <div className="card-body scroll">
          {data === null ? (
            <Loading>Lade Objekte…</Loading>
          ) : data.rows.length === 0 ? (
            <Empty title={query || issuesOnly ? "Keine Treffer" : "Noch keine Objekte"}>
              {query || issuesOnly
                ? "Filter oder Suche anpassen."
                : "Objekte erscheinen nach dem ersten Lauf einer Firewall (FortiGate)."}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Art</th>
                  <th>{group ? "Mitglieder" : service ? "Protokoll / Ports" : "Werte / Referenz"}</th>
                  {service && <th>Attribute</th>}
                  {!group && <th>In Gruppe</th>}
                  <th className="num">Regeln</th>
                  <th>Quelle</th>
                  <th>Befunde</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((o) => (
                  <tr key={o.id} className="clickable" onClick={() => setSelected(o.id)}>
                    <td>
                      <b className="mono">{o.name}</b>
                      {o.family && !service && (
                        <span className="chip" style={{ marginLeft: 6 }} title="Adressfamilie (FortiGate führt IPv4 und IPv6 getrennt)">
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
                          in NAT / VIPs öffnen
                        </button>
                      )}
                    </td>
                    <td className="nowrap">
                      {CATEGORY_LABEL[o.category] ?? o.category}
                      {o.obj_type && <div className="faint">{o.obj_type}</div>}
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
                          title="Regeln mit diesem Objekt anzeigen (direkt und über Gruppen)"
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
                    <td className="nowrap">{o.source ?? <Dash />}</td>
                    <td>
                      <IssueChips issues={o.issues} />
                    </td>
                  </tr>
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
  protocol: "Protokoll",
  "protocol-number": "IP-Protokoll",
  icmptype: "ICMP-Typ",
  icmpcode: "ICMP-Code",
  "tcp-portrange": "TCP-Ports",
  "udp-portrange": "UDP-Ports",
  "sctp-portrange": "SCTP-Ports",
  category: "Kategorie",
  iprange: "nur Ziel-IP",
  fqdn: "nur Ziel-FQDN",
  "session-ttl": "Session-TTL (s)",
  "tcp-halfclose-timer": "TCP-Halfclose (s)",
  "tcp-halfopen-timer": "TCP-Halfopen (s)",
  "tcp-timewait-timer": "TCP-Timewait (s)",
  "tcp-rst-timer": "TCP-RST (s)",
  "udp-idle-timer": "UDP-Idle (s)",
  "check-reset-range": "Reset-Prüfung",
  helper: "Protokoll-Helper",
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
      return src ? `${dst} (Quellport ${src})` : dst;
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
  if (a["protocol-number"]) bits.push(`IP-Protokoll ${a["protocol-number"]}`);
  if (a.iprange) bits.push(`nur Ziel ${a.iprange}`);
  if (a.fqdn) bits.push(`nur Ziel ${a.fqdn}`);
  if (Array.isArray(a.entries)) bits.push(`${a.entries.length} Einträge`);
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
          <th>Eintrag</th>
          <th>Ether</th>
          <th>Protokoll</th>
          <th>Zielport</th>
          <th>Quellport</th>
          <th>weitere</th>
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
                e.applyToFrag === "yes" ? "Fragmente" : null,
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

// Objekt-Detail wie in der NAT-Sicht: Attribute, Werte, Mitglieder, Befunde und die Regeln
function ObjectDrawer({ id, onClose, onGo }: { id: number; onClose: () => void; onGo?: Go }) {
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
      title={d?.name ?? "Objekt"}
      sub={d ? `${CATEGORY_LABEL[d.category] ?? d.category}${d.obj_type ? ` · ${d.obj_type}` : ""} · ${d.source ?? ""}` : undefined}
      onClose={onClose}
    >
      <ErrorBox error={err} />
      {!d && !err && <Loading />}
      {d && (
        <>
          <dl className="kv">
            {d.comment && (
              <>
                <dt>Kommentar</dt>
                <dd>{d.comment}</dd>
              </>
            )}
            {!d.members.length && (
              <>
                <dt>{service ? "Protokoll / Ports" : "Werte"}</dt>
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
                <dt>Standort</dt>
                <dd>
                  <SiteChip site={d.site} from="name" />
                </dd>
              </>
            )}
            <dt>In Gruppe</dt>
            <dd>{d.member_of.length ? <Members names={d.member_of} /> : <Dash />}</dd>
            <dt>Befunde</dt>
            <dd>
              <IssueChips issues={d.issues} />
            </dd>
          </dl>

          {entries.length > 0 && (
            <>
              <div className="sub-h">Filter-Einträge ({entries.length})</div>
              <EntryTable entries={entries} />
            </>
          )}

          {d.members_detail.length > 0 && (
            <>
              <div className="sub-h">Mitglieder ({d.members_detail.length})</div>
              <table>
                <tbody>
                  {d.members_detail.map((m) => (
                    <tr key={m.name}>
                      <td className="mono">
                        <b>{m.name}</b>
                        {m.comment && <div className="faint">{m.comment}</div>}
                      </td>
                      <td className="faint">
                        {m.category ? (CATEGORY_LABEL[m.category] ?? m.category) : "nicht gelesen"}
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
            Firewall-Regeln ({d.rule_count})
            <div style={{ flex: 1 }} />
            {d.rule_count > 0 && onGo && (
              <button className="btn btn-ghost btn-sm" onClick={() => onGo("regeln", objectLink(d))}>
                In Regeln anzeigen
              </button>
            )}
          </div>
          {d.rules.length === 0 ? (
            <div className="faint">Keine Regel verwendet dieses Objekt (auch nicht über eine Gruppe).</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Regel</th>
                  <th>Aktion</th>
                  <th>Quelle</th>
                  <th>Ziel</th>
                  <th>Dienste</th>
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
      {names.length > shown.length && <span className="faint">+{names.length - shown.length} weitere</span>}
    </div>
  );
}

const list = (v: unknown[] | null | undefined) => (v && v.length ? v.join(", ") : null);
const cell = (v: ReactNode) => (v === null || v === undefined || v === "" ? <Dash /> : v);

function IpamCell({ info, vlan }: { info: PrefixInfo | null; vlan?: number | null }) {
  if (!info) return <span className="faint">keine Quelle</span>;
  if (!info.found) return <span className="chip crit">fehlt in {info.source}</span>;
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
    fetchInventory(kind, tenantId, { source_system_id: sourceId, q: query })
      .then(setRows)
      .catch((e) => {
        setError(e);
        setRows([]);
      });
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
                <th>Subnetz</th>
                <th>Gateway</th>
                <th>Kontext</th>
                <th>VRF</th>
                <th>VLAN</th>
                <th>IPAM (führend)</th>
                <th>Anreicherung</th>
                <th>Quelle</th>
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
                          title="Firewall-Regeln und Contracts, deren Quelle oder Ziel sich mit dem Subnetz überschneidet"
                          onClick={() => onGo("regeln", { net: s.prefix })}
                        >
                          Regeln im Subnetz
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
                  <td className="nowrap">{cell(s.source)}</td>
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
                <th>Gruppe</th>
                <th>EPGs</th>
                <th>Beschreibung</th>
                <th>Quelle</th>
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
                  <td className="mono">{cell(list(v.epgs))}</td>
                  <td className="muted">{cell(v.description)}</td>
                  <td className="nowrap">{cell(v.source)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        );
      case "interfaces":
        return (
          <table>
            <thead>
              <tr>
                <th>Gerät</th>
                <th>Port</th>
                <th>Typ / Modus</th>
                <th>VLANs</th>
                <th>LAG / Parent</th>
                <th>IPs</th>
                <th>Zone / Policy</th>
                <th>Nachbar</th>
                <th>EPGs</th>
                <th>Quelle</th>
              </tr>
            </thead>
            <tbody>
              {(page as InvInterface[]).map((i) => (
                <tr key={i.id}>
                  <td className="mono nowrap">{i.device}</td>
                  <td className="mono nowrap">
                    <b>{i.name}</b>
                    {i.enabled === false && <span className="chip na">aus</span>}
                    {i.description && <div className="faint">{i.description}</div>}
                  </td>
                  <td className="nowrap">
                    {i.kind}
                    {i.mode && <div className="faint">{i.mode}</div>}
                  </td>
                  <td className="mono">
                    {i.untagged_vlan != null && <div>untagged {i.untagged_vlan}</div>}
                    {i.tagged_vlans?.length ? <div>tagged {i.tagged_vlans.join(", ")}</div> : null}
                    {i.untagged_vlan == null && !i.tagged_vlans?.length && <Dash />}
                  </td>
                  <td className="mono">{cell([i.lag, i.parent].filter(Boolean).join(" / "))}</td>
                  <td className="mono">{cell(list(i.ips))}</td>
                  <td>{cell([i.zone, i.policy_group].filter(Boolean).join(" · "))}</td>
                  <td className="mono">{cell([i.neighbor, i.neighbor_port].filter(Boolean).join(" : "))}</td>
                  <td className="mono">{cell(list(i.epgs))}</td>
                  <td className="nowrap">{cell(i.source)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        );
      case "devices":
        return (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Rolle</th>
                <th>Modell</th>
                <th>Seriennummer</th>
                <th>Node</th>
                <th>Mgmt-IP</th>
                <th>Version</th>
                <th>Standort</th>
                <th>Quelle</th>
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
                  <td className="nowrap">{cell(d.source)}</td>
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
                <th>Quelle</th>
              </tr>
            </thead>
            <tbody>
              {(page as InvVrf[]).map((v) => (
                <tr key={v.id}>
                  <td className="mono">
                    <b>{v.name}</b>
                  </td>
                  <td>{cell(v.source)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        );
    }
  })();

  return (
    <>
      <ErrorBox error={error} prefix="Inventar laden" />
      <div className="card">
        <div className="card-body scroll">
          {rows === null ? (
            <Loading>Lade Inventar…</Loading>
          ) : page.length === 0 ? (
            <Empty title={query || issuesOnly ? "Keine Treffer" : "Noch kein Inventar"}>
              {query || issuesOnly
                ? "Filter oder Suche anpassen."
                : "Das Netzinventar erscheint nach dem ersten Lauf einer FortiGate oder eines APIC."}
            </Empty>
          ) : (
            table
          )}
        </div>
        {all.length > 0 && <Pager total={all.length} offset={offset} onOffset={setOffset} />}
      </div>
    </>
  );
}

const POWER: Record<string, [string, string]> = {
  poweredOn: ["ok", "an"],
  poweredOff: ["na", "aus"],
  suspended: ["warn", "pausiert"],
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
    fetchVms(tenantId, { q: query, source_system_id: sourceId, limit: PAGE, offset })
      .then(setData)
      .catch((e) => {
        setError(e);
        setData({ total: 0, rows: [] });
      });
  }, [tenantId, sourceId, query, offset]);
  return (
    <>
      <ErrorBox error={error} prefix="VMs laden" />
      <div className="card">
        <div className="card-body scroll">
          {data === null ? (
            <Loading>Lade VMs…</Loading>
          ) : data.rows.length === 0 ? (
            <Empty title={query ? "Keine Treffer" : "Noch keine VMs"}>
              {query ? "Suche anpassen." : "VMs erscheinen nach dem ersten Lauf eines vCenter-Quellsystems (VMware)."}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>VM</th>
                  <th>Status</th>
                  <th>Netzwerk (Portgruppe → EPG, IPs)</th>
                  <th>Cluster / Host</th>
                  <th>Ressourcen</th>
                  <th>Quelle</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((v) => {
                  const [cls, label] = POWER[v.power_state ?? ""] ?? ["na", v.power_state ?? "–"];
                  return (
                    <tr key={v.id}>
                      <td>
                        <b className="mono">{v.name}</b>
                        {v.template && <span className="chip" style={{ marginLeft: 6 }}>Vorlage</span>}
                        {v.hostname && <div className="faint mono">{v.hostname}</div>}
                        {v.guest_os && <div className="faint">{v.guest_os}</div>}
                        {v.annotation && <div className="faint">{v.annotation}</div>}
                        {onGo && (
                          <button className="linkbtn" title="Regeln, deren Quelle oder Ziel eine IP der VM enthält" onClick={() => onGo("regeln", { q: v.name })}>
                            Regeln der VM
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
                              <span className="chip local" style={{ marginLeft: 6 }} title={n.epg_match === "vlan" ? "über das VLAN zugeordnet" : "über den Portgruppen-Namen zugeordnet"}>
                                EPG {n.epg}
                              </span>
                            )}
                            {n.connected === false && <span className="chip na">getrennt</span>}
                            <div>
                              {n.ips.map((ip) => (
                                <span key={ip.ip} className="refline">
                                  <span className="mono">{ip.ip}</span>
                                  {ip.dns.length > 0 && <span className="faint"> {ip.dns.join(", ")}</span>}
                                  {ip.in_ipam === false && <span className="chip warn">nicht im IPAM</span>}
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

// Portgruppen mit VLAN und zugeordneter EPG (Name Tenant|AP|EPG bzw. eindeutiges VLAN)
function PortgroupTable({ tenantId, q }: { tenantId: number; q: string }) {
  const [rows, setRows] = useState<PortgroupRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const query = useDebounced(q);
  useEffect(() => {
    setRows(null);
    fetchPortgroups(tenantId, query)
      .then(setRows)
      .catch((e) => {
        setError(e);
        setRows([]);
      });
  }, [tenantId, query]);
  return (
    <>
      <ErrorBox error={error} prefix="Portgruppen laden" />
      <div className="card">
        <div className="card-body scroll">
          {rows === null ? (
            <Loading>Lade Portgruppen…</Loading>
          ) : rows.length === 0 ? (
            <Empty title={query ? "Keine Treffer" : "Noch keine Portgruppen"}>
              {query ? "Suche anpassen." : "Portgruppen erscheinen nach dem ersten Lauf eines vCenter-Quellsystems."}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Portgruppe</th>
                  <th>Switch</th>
                  <th>VLAN</th>
                  <th className="num">VMs</th>
                  <th>EPG</th>
                  <th>Quelle</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id}>
                    <td>
                      <b className="mono">{p.name}</b>
                      <div className="faint">{p.kind === "dvs" ? "verteilt" : "Standard (Host)"}</div>
                    </td>
                    <td className="mono">{p.switch ?? <Dash />}</td>
                    <td className="mono">{p.vlan ?? p.vlan_ranges ?? <Dash />}</td>
                    <td className="num">{p.vm_count}</td>
                    <td>
                      {p.epg ? (
                        <>
                          <span className="chip local">{p.epg}</span>
                          <div className="faint">{p.epg_match === "vlan" ? "über das VLAN" : "über den Namen"}</div>
                        </>
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
      </div>
    </>
  );
}
