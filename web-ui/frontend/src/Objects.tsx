// Objekte & Inventar (ADR 0010): Firewall-Objekte wie auf dem Gerät mit Abgleich gegen das IPAM,
// dazu das Netzinventar aus FortiGate/APIC (Subnetze, VLANs, Ports, Geräte, VRFs). Rein lesend.
import { ReactNode, useEffect, useState } from "react";
import {
  DeviceObject,
  fetchInventory,
  fetchObjects,
  fetchSystems,
  InvDevice,
  InvInterface,
  InvSubnet,
  InvVlan,
  InvVrf,
  PrefixInfo,
  System,
} from "./api";
import { CATEGORY_LABEL, IssueChips, REFERENCE_PLATFORMS, ValueList } from "./refs";
import { Dash, Empty, ErrorBox, Icon, Loading, Note, PageHead } from "./ui";

type Tab = "addr" | "groups" | "services" | "subnets" | "vlans" | "ports" | "devices" | "vrfs";

const TABS: { key: Tab; label: string; category?: string }[] = [
  { key: "addr", label: "Adressobjekte", category: "address,vip,ippool" },
  { key: "groups", label: "Gruppen", category: "address_group,service_group" },
  { key: "services", label: "Services", category: "service" },
  { key: "subnets", label: "Subnetze" },
  { key: "vlans", label: "VLANs" },
  { key: "ports", label: "Ports" },
  { key: "devices", label: "Geräte" },
  { key: "vrfs", label: "VRFs" },
];

const PAGE = 200;

export default function Objects({ tenantId, tenantLabel }: { tenantId: number | null; tenantLabel: string }) {
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

      {current.category ? (
        <ObjectTable
          key={tab}
          tenantId={tenantId}
          category={current.category}
          group={tab === "groups"}
          service={tab === "services"}
          sourceId={sourceId}
          q={q}
          issuesOnly={issuesOnly}
        />
      ) : (
        <InventoryTable key={tab} tab={tab} tenantId={tenantId} sourceId={sourceId} q={q} issuesOnly={issuesOnly} />
      )}
    </>
  );
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
}: {
  tenantId: number;
  category: string;
  group: boolean;
  service: boolean;
  sourceId: number | null;
  q: string;
  issuesOnly: boolean;
}) {
  const [data, setData] = useState<{ total: number; rows: DeviceObject[] } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [offset, setOffset] = useState(0);
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
                  <th>{group ? "Mitglieder" : "Werte / Referenz"}</th>
                  {!group && <th>In Gruppe</th>}
                  <th className="num">Regeln</th>
                  <th>Quelle</th>
                  <th>Befunde</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((o) => (
                  <tr key={o.id}>
                    <td>
                      <b className="mono">{o.name}</b>
                      {o.comment && <div className="faint">{o.comment}</div>}
                      {o.interface && <div className="faint">Interface {o.interface}</div>}
                    </td>
                    <td className="nowrap">
                      {CATEGORY_LABEL[o.category] ?? o.category}
                      {o.obj_type && <div className="faint">{o.obj_type}</div>}
                    </td>
                    <td style={{ minWidth: 280 }}>
                      {group ? (
                        <Members names={o.members} />
                      ) : (
                        <ValueList values={o.values} service={service} />
                      )}
                    </td>
                    {!group && <td>{o.member_of.length ? <Members names={o.member_of} /> : <Dash />}</td>}
                    <td className="num">{o.used_in_rules || <span className="faint">0</span>}</td>
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
    </>
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
}: {
  tab: Tab;
  tenantId: number;
  sourceId: number | null;
  q: string;
  issuesOnly: boolean;
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
                  </td>
                  <td className="mono">{cell(s.gateway)}</td>
                  <td>
                    <span className="faint">{s.context === "bd" ? "BD" : s.context === "epg" ? "EPG" : s.context}</span>{" "}
                    <span className="mono">{s.context_name}</span>
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
