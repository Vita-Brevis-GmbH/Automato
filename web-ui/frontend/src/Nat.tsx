// NAT / VIPs: VIPs (externe IP:Port → internes Ziel) und SNAT-Pools – angereichert wie die
// Firewall-Regeln: DNS-Namen beider IPs aus DDI/NetBox, Server hinter der internen IP und
// die Regeln, die den VIP bzw. Pool nutzen (Klick → Regel-Liste gefiltert).
import { useEffect, useState } from "react";
import { fetchNat, fetchNatDetail, IpInfo, Nat, NatDetail, NatTarget } from "./api";
import type { View, ViewParams } from "./App";
import { L } from "./i18n";
import { IssueChips, useVdomShown, VdomChip, VmSection } from "./refs";
import { ActionBadge, Dash, Drawer, Empty, ErrorBox, Icon, Loading, PageHead } from "./ui";

const PAGE = 500; // Einträge je Seite

const NAT_TYPE: Record<string, [string, string]> = {
  dnat_vip: ["local", "VIP / DNAT"],
  snat_pool: ["info", L("SNAT-Pool", "SNAT pool")],
};
const SLB = "server-load-balance"; // FortiGate Virtual Server (Lastverteilung)
const typeLabel = (n: Nat): [string, string] =>
  n.vip_type === SLB ? ["local", "Virtual Server"] : (NAT_TYPE[n.nat_type] ?? ["na", n.nat_type]);

// Einstellungen eines Virtual Servers wie auf der FortiGate
const SLB_LABEL: Record<string, string> = {
  "server-type": L("Server-Typ", "Server type"),
  "ldb-method": L("Lastverteilung", "Load balancing"),
  persistence: L("Persistenz", "Persistence"),
  "http-ip-header": L("Client-IP im Header", "Client IP in header"),
  "http-multiplex": L("HTTP-Multiplexing", "HTTP multiplexing"),
  "http-host": L("HTTP-Host", "HTTP host"),
  "max-embryonic-connections": L("Max. halboffene Verbindungen", "Max. half-open connections"),
  "ssl-mode": L("SSL-Modus", "SSL mode"),
  "ssl-min-version": "SSL min.",
  "ssl-max-version": "SSL max.",
  "ssl-certificate": L("Zertifikat", "Certificate"),
};
const STATUS: Record<string, [string, string]> = {
  active: ["ok", L("aktiv", "active")],
  standby: ["info", "Standby"],
  disable: ["na", L("aus", "off")],
};

function attrText(v: unknown): string {
  return Array.isArray(v) ? v.join(", ") : String(v);
}

// Health-Checks: Name und was geprüft wird (Typ, Port, Intervall)
function Monitors({ items }: { items: unknown }) {
  const list = Array.isArray(items) ? (items as Record<string, unknown>[]) : [];
  if (!list.length) return <span className="faint">{L("keiner", "none")}</span>;
  return (
    <>
      {list.map((m) => (
        <div key={String(m.name)}>
          <span className="mono">{String(m.name)}</span>
          <span className="faint">
            {[
              m.type,
              m.port ? `Port ${m.port}` : null,
              m.interval ? L(`alle ${m.interval} s`, `every ${m.interval} s`) : null,
              m["http-get"],
            ]
              .filter(Boolean)
              .map((x) => ` · ${x}`)
              .join("")}
          </span>
        </div>
      ))}
    </>
  );
}

// Real Server mit Status, Gewicht, Firewall-Objekt und DNS-Namen
function Targets({ items, onGo }: { items: NatTarget[]; onGo?: (v: View, p?: ViewParams) => void }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Real Server</th>
          <th>Status</th>
          <th className="num">{L("Gewicht", "Weight")}</th>
          <th>{L("Objekt / DNS", "Object / DNS")}</th>
          <th>{L("Health-Check", "Health check")}</th>
        </tr>
      </thead>
      <tbody>
        {items.map((t, i) => {
          const [cls, label] = STATUS[t.status ?? "active"] ?? ["na", t.status ?? "–"];
          return (
            <tr key={`${t.ip ?? t.address}-${i}`}>
              <td className="mono">
                {t.ip ?? t.address ?? "–"}
                {t.port ? `:${t.port}` : ""}
                {t.http_host && <div className="faint">Host {t.http_host}</div>}
              </td>
              <td>
                <span className={`chip ${cls}`}>{label}</span>
              </td>
              <td className="num">{t.weight ?? <Dash />}</td>
              <td>
                {t.servers.map((name) => (
                  <div key={name}>
                    <button className="linkbtn mono objlink" onClick={() => onGo?.("regeln", { object: name })}>
                      {name}
                    </button>
                  </div>
                ))}
                <Names items={t.dns} />
                {!t.servers.length && !t.dns.length && <Dash />}
              </td>
              <td className="faint">
                {t.monitor.length
                  ? t.monitor.join(", ")
                  : t.healthcheck === "vip"
                    ? L("wie Virtual Server", "as Virtual Server")
                    : (t.healthcheck ?? "–")}
                {t.max_connections ? ` · max. ${t.max_connections}` : ""}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function addr(ip: string | null, port: string | null, proto?: string | null) {
  if (!ip) return null;
  return `${ip}${port ? `:${port}` : ""}${proto ? `/${proto}` : ""}`;
}

// je Eintrag eine Zeile (lange Objektnamen machen die Tabelle sonst zu breit)
function Lines({ items }: { items: string[] }) {
  if (!items.length) return <Dash />;
  return (
    <>
      {items.slice(0, 4).map((x) => (
        <div key={x}>{x}</div>
      ))}
      {items.length > 4 && (
        <div className="faint">{L(`+${items.length - 4} weitere`, `+${items.length - 4} more`)}</div>
      )}
    </>
  );
}

function Names({ items }: { items: (string | null)[] }) {
  const names = items.filter((x, i, a): x is string => !!x && a.indexOf(x) === i);
  return names.length ? <div className="faint">{names.join(", ")}</div> : null;
}

function IpamBlock({ title, infos }: { title: string; infos: IpInfo[] }) {
  return (
    <>
      <dt>{title}</dt>
      <dd>
        {infos.length === 0 ? (
          <span className="faint">{L("kein Eintrag im IPAM", "no entry in the IPAM")}</span>
        ) : (
          infos.map((i) => (
            <div key={i.source} style={{ marginBottom: 4 }}>
              <span className="chip">{i.source}</span>{" "}
              {i.dns_names.length ? (
                <span className="mono">{i.dns_names.join(", ")}</span>
              ) : (
                <span className="faint">{L("ohne DNS-Namen", "no DNS name")}</span>
              )}
              {i.prefix && (
                <div className="faint">
                  {L("Netz", "Network")} {i.prefix.value}
                  {[i.prefix.description, i.prefix.vlan != null ? `VLAN ${i.prefix.vlan}` : null, i.prefix.site, i.prefix.vrf]
                    .filter(Boolean)
                    .map((x) => ` · ${x}`)
                    .join("")}
                </div>
              )}
            </div>
          ))
        )}
      </dd>
    </>
  );
}

// NAT-Detail (auch in der Weg-Suche, ADR 0019 Ergänzung)
export function NatDrawer({
  id,
  onClose,
  onGo,
}: {
  id: number;
  onClose: () => void;
  onGo?: (v: View, p?: ViewParams) => void;
}) {
  const vdomShown = useVdomShown();
  const [d, setD] = useState<NatDetail | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    fetchNatDetail(id).then(setD).catch(setErr);
  }, [id]);
  const pool = d?.nat_type === "snat_pool";
  const slb = d?.vip_type === SLB;
  const attrs = Object.entries(d?.attrs ?? {}).filter(([k]) => k !== "monitor");
  return (
    <Drawer
      title={d?.name ?? "NAT"}
      sub={d ? `${typeLabel(d)[1]}${d.family === "v6" ? " · IPv6" : ""}${vdomShown(d.source_system_id, d.vdom) ? ` · VDOM ${d.vdom}` : ""}` : undefined}
      onClose={onClose}
    >
      <ErrorBox error={err} />
      {!d && !err && <Loading />}
      {d && (
        <>
          <dl className="kv">
            <dt>{L("Prüfung", "Check")}</dt>
            <dd>
              <IssueChips issues={d.issues ?? []} />
              {d.object_id != null && onGo && (
                <button
                  className="linkbtn"
                  style={{ marginLeft: 6 }}
                  title={L(
                    "VIP- bzw. Pool-Objekt in Objekte & Inventar öffnen",
                    "Open VIP or pool object in Objects & inventory",
                  )}
                  onClick={() => onGo("objekte", { object_id: String(d.object_id) })}
                >
                  {L("Objekt öffnen", "Open object")}
                </button>
              )}
            </dd>
            <dt>{pool ? L("Quell-IP(s)", "Source IP(s)") : L("Extern", "External")}</dt>
            <dd className="mono">{addr(d.ext_ip, d.ext_port) ?? <Dash />}</dd>
            {slb && (
              <>
                {attrs.map(([k, v]) => (
                  <span key={k} style={{ display: "contents" }}>
                    <dt>{SLB_LABEL[k] ?? k}</dt>
                    <dd className="mono">{attrText(v)}</dd>
                  </span>
                ))}
                <dt>{L("Health-Check", "Health check")}</dt>
                <dd>
                  <Monitors items={d.attrs?.monitor} />
                </dd>
              </>
            )}
            {!pool && !slb && (
              <>
                <dt>{L("Intern", "Internal")}</dt>
                <dd className="mono">{addr(d.mapped_ip, d.mapped_port, d.protocol) ?? <Dash />}</dd>
                <dt>Port-Forward</dt>
                <dd>{d.portforward ? <span className="chip ok">{L("an", "on")}</span> : L("aus", "off")}</dd>
                <dt>Server</dt>
                <dd>
                  {(d.servers?.length ? d.servers : d.server ? [d.server] : []).map((name) => (
                    <div key={name}>
                      <button
                        className="linkbtn mono objlink"
                        title={L("Regeln mit diesem Objekt", "Rules with this object")}
                        onClick={() => onGo?.("regeln", { object: name })}
                      >
                        {name}
                      </button>
                    </div>
                  ))}
                  {!d.server && <Dash />}
                  {(d.servers?.length ?? 0) > 1 && (
                    <div className="faint">{L("IP in mehreren Objekten definiert", "IP defined in several objects")}</div>
                  )}
                </dd>
              </>
            )}
            {d.external_name && (
              <>
                <dt>{L("Externer Name", "External name")}</dt>
                <dd className="mono">{d.external_name}</dd>
              </>
            )}
            {d.interface && (
              <>
                <dt>Interface</dt>
                <dd className="mono">{d.interface}</dd>
              </>
            )}
            {d.comment && (
              <>
                <dt>{L("Kommentar", "Comment")}</dt>
                <dd>{d.comment}</dd>
              </>
            )}
            {Object.entries(d.settings).map(([k, v]) => (
              <span key={k} style={{ display: "contents" }}>
                <dt>{k}</dt>
                <dd className="mono">{v}</dd>
              </span>
            ))}
            <IpamBlock
              title={pool ? L("IPAM (Quell-IP)", "IPAM (source IP)") : L("IPAM extern", "IPAM external")}
              infos={d.ext_info}
            />
            {!pool && !slb && <IpamBlock title={L("IPAM intern", "IPAM internal")} infos={d.mapped_info} />}
          </dl>

          {slb && (
            <>
              <div className="sub-h">Real Server ({d.targets?.length ?? 0})</div>
              <Targets items={d.targets ?? []} onGo={onGo} />
            </>
          )}

          <VmSection
            items={(d.vms ?? []).map((vm) => ({
              vm,
              role: L(`Ziel ${vm.for_ip ?? ""}`, `Destination ${vm.for_ip ?? ""}`),
            }))}
          />

          <div className="sub-h" style={{ display: "flex", alignItems: "center" }}>
            {L(<>Firewall-Regeln ({d.rules.length})</>, <>Firewall rules ({d.rules.length})</>)}
            <div style={{ flex: 1 }} />
            {d.rules.length > 0 && (
              <button className="btn btn-ghost btn-sm" onClick={() => onGo?.("regeln", { nat: d.name })}>
                {L("In Regeln anzeigen", "Show in rules")}
              </button>
            )}
          </div>
          {d.rules.length === 0 ? (
            <div className="faint">
              {L("Keine Firewall-Regel verwendet diesen Eintrag.", "No firewall rule uses this entry.")}
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
                  <tr key={r.id}>
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

export default function NatTable({
  tenantId,
  tenantLabel,
  params = {},
  onGo,
}: {
  tenantId: number | null;
  tenantLabel: string;
  params?: ViewParams; // q= aus einem Link (z. B. VIP/SNAT-Kennzeichen einer Regel)
  onGo?: (v: View, p?: ViewParams) => void;
}) {
  const [nat, setNat] = useState<Nat[] | null>(null);
  const [total, setTotal] = useState(0);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [type, setType] = useState("");
  const [q, setQ] = useState(params.q ?? "");
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);

  // Typ und Suche (Name, IPs, Ports, DNS-Namen, Server, Kommentar) filtert der Orchestrator
  useEffect(() => {
    const timer = setTimeout(() => {
      setNat(null);
      setError(null);
      fetchNat({ tenantId, natType: type, q, issuesOnly, limit: PAGE })
        .then((page) => {
          setNat(page.rows);
          setTotal(page.total);
          // Link auf genau einen Eintrag (z. B. aus einer Regel): Detail direkt öffnen
          if (params.q && page.rows.length === 1 && page.rows[0].name === params.q) setSelected(page.rows[0].id);
        })
        .catch((e) => {
          setError(e);
          setNat([]);
          setTotal(0);
        });
    }, 250);
    return () => clearTimeout(timer);
  }, [tenantId, type, q, issuesOnly, params.q]);

  const loadMore = () => {
    if (!nat) return;
    setMore(true);
    fetchNat({ tenantId, natType: type, q, issuesOnly, limit: PAGE, offset: nat.length })
      .then((page) => {
        setNat([...nat, ...page.rows]);
        setTotal(page.total);
      })
      .catch(setError)
      .finally(() => setMore(false));
  };

  const shown = nat ?? [];
  const vdomShown = useVdomShown(); // „root“ nur bei Firewalls mit weiteren VDOMs
  const loaded = shown.length;

  return (
    <>
      <PageHead title="NAT / VIPs">
        {L(
          <>
            Virtuelle IPs (externe Adresse → internes Ziel, inkl. Port-Forward), Virtual Server (Lastverteilung auf Real
            Server) und SNAT-IP-Pools für „{tenantLabel}“ –
            mit DNS-Namen aus dem IPAM, den Firewall-Regeln, die sie nutzen, und der Prüfung (Namenskonvention, unbenutzt,
            internes Ziel im IPAM).
          </>,
          <>
            Virtual IPs (external address → internal destination, incl. port forward), Virtual Server (load balancing to
            real servers) and SNAT IP pools for “{tenantLabel}” – with DNS names from the IPAM, the firewall rules that
            use them, and the check (naming convention, unused, internal destination in the IPAM).
          </>,
        )}
      </PageHead>
      <div className="filterbar">
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input
            placeholder={L(
              "Suche: Name, IP (auch im Pool-Bereich), Port, DNS-Name, Server, Interface, Kommentar…",
              "Search: name, IP (also within the pool range), port, DNS name, server, interface, comment…",
            )}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <select className="sel" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">{L("Typ: alle", "Type: all")}</option>
          <option value="dnat_vip">{L("VIP / DNAT (inkl. Virtual Server)", "VIP / DNAT (incl. Virtual Server)")}</option>
          <option value="snat_pool">{L("SNAT-Pool", "SNAT pool")}</option>
        </select>
        <button
          className={`toggle ${issuesOnly ? "on" : ""}`}
          title={L(
            "Nur Einträge mit Befund: Namenskonvention, unbenutzt, Wert bzw. internes Ziel nicht im IPAM",
            "Only entries with a finding: naming convention, unused, value or internal destination not in the IPAM",
          )}
          onClick={() => setIssuesOnly((v) => !v)}
        >
          <span className="dot y" />
          {L("nur Abweichungen", "only deviations")}
        </button>
        <span className="count">
          {L(
            <>
              {total} Einträge{total > loaded ? ` · ${loaded} geladen` : ""}
            </>,
            <>
              {total} entries{total > loaded ? ` · ${loaded} loaded` : ""}
            </>,
          )}
        </span>
      </div>
      <ErrorBox error={error} prefix={L("NAT laden", "Load NAT")} />
      <div className="card">
        <div className="card-body scroll viewport">
          {nat === null ? (
            <Loading>{L("Lade VIPs…", "Loading VIPs…")}</Loading>
          ) : shown.length === 0 ? (
            <Empty
              title={q || issuesOnly ? L("Keine Treffer", "No matches") : L("Keine NAT-Einträge", "No NAT entries")}
            >
              {q || issuesOnly
                ? L("Suche bzw. Filter anpassen.", "Adjust the search or filter.")
                : L(
                    "Es wurden (noch) keine VIPs oder SNAT-Pools erfasst.",
                    "No VIPs or SNAT pools have been collected (yet).",
                  )}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>{L("Typ", "Type")}</th>
                  <th>{L("Extern", "External")}</th>
                  <th>{L("→ Intern", "→ Internal")}</th>
                  <th>Server</th>
                  <th>Interface</th>
                  <th className="num">{L("Regeln", "Rules")}</th>
                  <th>{L("Prüfung", "Check")}</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((n) => {
                  const [cls, label] = typeLabel(n);
                  const targets = n.targets ?? [];
                  return (
                    <tr key={n.id} className="clickable" onClick={() => setSelected(n.id)}>
                      <td>
                        <b>{n.name}</b>
                        {n.comment && <div className="faint">{n.comment}</div>}
                      </td>
                      <td>
                        <span className={`chip ${cls}`}>{label}</span>
                        {n.family === "v6" && <span className="chip">IPv6</span>}
                        {n.portforward && <span className="chip ok">Port-Forward</span>}
                        <VdomChip vdom={n.vdom} always={vdomShown(n.source_system_id, n.vdom)} />
                      </td>
                      <td>
                        <span className="mono">{addr(n.ext_ip, n.ext_port) ?? "–"}</span>
                        <Names items={[n.external_name, ...n.ext_dns]} />
                      </td>
                      <td>
                        {targets.length ? (
                          <>
                            {targets.slice(0, 4).map((t, i) => (
                              <div key={i} className="mono">
                                {t.ip ?? t.address}
                                {t.port ? `:${t.port}` : ""}
                                {t.status && t.status !== "active" ? <span className="faint"> ({STATUS[t.status]?.[1] ?? t.status})</span> : null}
                              </div>
                            ))}
                            {targets.length > 4 && (
                              <span className="faint">
                                {L(`+${targets.length - 4} weitere`, `+${targets.length - 4} more`)}
                              </span>
                            )}
                            <div className="faint">
                              {[n.protocol, n.attrs?.["ldb-method"]].filter(Boolean).join(" · ")}
                            </div>
                          </>
                        ) : n.mapped_ip ? (
                          <>
                            <span className="mono">{addr(n.mapped_ip, n.mapped_port, n.protocol)}</span>
                            <Names items={n.mapped_dns} />
                          </>
                        ) : (
                          <span className="faint">
                            {n.nat_type === "snat_pool" ? L("ausgehende Quell-IP", "outgoing source IP") : "–"}
                          </span>
                        )}
                      </td>
                      <td className="mono">
                        <Lines
                          items={targets.length ? [...new Set(targets.flatMap((t) => t.servers))] : n.servers?.length ? n.servers : n.server ? [n.server] : []}
                        />
                      </td>
                      <td className="mono">{n.interface ?? <Dash />}</td>
                      <td className="num">
                        {n.rule_count > 0 ? (
                          <button
                            className="chip local linkchip"
                            title={L("Regeln mit diesem Eintrag anzeigen", "Show rules with this entry")}
                            onClick={(e) => {
                              e.stopPropagation();
                              onGo?.("regeln", { nat: n.name });
                            }}
                          >
                            {n.rule_count}
                          </button>
                        ) : (
                          <span
                            className="chip na"
                            title={L("von keiner Firewall-Regel verwendet", "not used by any firewall rule")}
                          >
                            0
                          </span>
                        )}
                      </td>
                      <td>
                        <IssueChips issues={n.issues ?? []} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        {nat !== null && total > loaded && (
          <div className="card-foot">
            {L(<>{loaded} von {total} geladen –</>, <>{loaded} of {total} loaded –</>)}{" "}
            <button className="btn btn-ghost btn-sm" onClick={loadMore} disabled={more}>
              {more
                ? L("Lade…", "Loading…")
                : L(`weitere ${Math.min(PAGE, total - loaded)} laden`, `load ${Math.min(PAGE, total - loaded)} more`)}
            </button>
          </div>
        )}
      </div>
      {selected !== null && <NatDrawer id={selected} onClose={() => setSelected(null)} onGo={onGo} />}
    </>
  );
}
