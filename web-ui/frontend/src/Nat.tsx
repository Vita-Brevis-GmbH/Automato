// NAT / VIPs: VIPs (externe IP:Port → internes Ziel) und SNAT-Pools – angereichert wie die
// Firewall-Regeln: DNS-Namen beider IPs aus DDI/NetBox, Server hinter der internen IP und
// die Regeln, die den VIP bzw. Pool nutzen (Klick → Regel-Liste gefiltert).
// 0.95.0: Kette über den Load Balancer (externe IP:Port → VIP des LB → Virtual Service → Pool →
// Server), am Virtual Service die VIPs davor, Ziel-Server für alle VIPs, Export der Liste.
import { useEffect, useState } from "react";
import { fetchAllNat, fetchNat, fetchNatDetail, IpInfo, Nat, NatChain, NatDetail, NatServer, NatTarget } from "./api";
import type { View, ViewParams } from "./App";
import { L } from "./i18n";
import { findingLabel, IssueChips, useVdomShown, VdomChip, VmSection } from "./refs";
import { ActionBadge, Dash, download, Drawer, Empty, ErrorBox, Icon, Loading, PageHead } from "./ui";

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

function Names({ items }: { items: (string | null)[] }) {
  const names = items.filter((x, i, a): x is string => !!x && a.indexOf(x) === i);
  return names.length ? <div className="faint">{names.join(", ")}</div> : null;
}

const hostPort = (ip: string | null, port: string | null) => (ip ? `${ip}${port ? `:${port}` : ""}` : "–");
const uniq = (items: (string | null | undefined)[]) =>
  items.filter((x, i, a): x is string => !!x && a.indexOf(x) === i);

// Ziel-Server (0.95.0): IP:Port mit Firewall-Objekt bzw. DNS-Namen, Status ausser „aktiv“
function ServerLines({ items, max = 4 }: { items: NatServer[]; max?: number }) {
  if (!items.length) return <Dash />;
  return (
    <>
      {items.slice(0, max).map((s, i) => (
        <div key={`${s.ip}-${s.port}-${s.via}-${i}`}>
          <span className="mono">{hostPort(s.ip, s.port)}</span>
          {s.status && s.status !== "active" && (
            <span className="faint"> ({STATUS[s.status]?.[1] ?? s.status})</span>
          )}
          <Names items={[...s.names, ...s.dns]} />
        </div>
      ))}
      {items.length > max && (
        <div className="faint">{L(`+${items.length - max} weitere`, `+${items.length - max} more`)}</div>
      )}
    </>
  );
}

// Ziel-Server einer Zeile, darunter „über vs-…“, wenn sie hinter einem Load Balancer stehen
function DestServers({ n }: { n: Nat }) {
  const items = n.dest_servers ?? [];
  const via = uniq(items.map((s) => s.via));
  return (
    <div className="nat-dest">
      <ServerLines items={items} />
      {via.length > 0 && (
        <div className="faint nat-via">
          {L("über", "via")} {via.join(", ")}
        </div>
      )}
    </div>
  );
}

// Glied „→ vs-shop (avi-01)“ unter dem internen Ziel einer VIP der Firewall
function ChainBrief({ chain }: { chain: NatChain[] }) {
  if (!chain.length) return null;
  return (
    <div className="faint nat-chain">
      {chain.slice(0, 3).map((c, i) => (
        <div key={`${c.balancer_id}-${i}`}>
          → {c.balancer}
          {c.vip_port ? ` :${c.vip_port}` : ""}
          {c.source ? ` (${c.source})` : ""}
        </div>
      ))}
      {chain.length > 3 && <div>{L(`+${chain.length - 3} weitere`, `+${chain.length - 3} more`)}</div>}
    </div>
  );
}

// Kette im Detail: je Virtual Service eine Zeile externe IP:Port → VIP des LB → Virtual Service → Pool → Server
function ChainTable({ chain, onOpen }: { chain: NatChain[]; onOpen?: (id: number) => void }) {
  return (
    <table id="nat-chain">
      <thead>
        <tr>
          <th>{L("Extern", "External")}</th>
          <th>{L("→ Intern (VIP des Load Balancers)", "→ Internal (VIP of the load balancer)")}</th>
          <th>Virtual Service</th>
          <th>Pool</th>
          <th>Server</th>
        </tr>
      </thead>
      <tbody>
        {chain.map((c, i) => (
          <tr key={`${c.balancer_id}-${i}`}>
            <td className="mono">{hostPort(c.ext_ip, c.ext_port)}</td>
            <td className="mono">
              {hostPort(c.vip_ip, c.vip_port)}
              {c.via === "fqdn" && <div className="faint">{L("über den FQDN", "via the FQDN")}</div>}
              {c.floating && <span className="chip">Floating IP</span>}
            </td>
            <td>
              {onOpen ? (
                <button className="linkbtn mono" onClick={() => onOpen(c.balancer_id)}>
                  {c.balancer}
                </button>
              ) : (
                <span className="mono">{c.balancer}</span>
              )}
              {c.down && <span className="chip warn">{L("aus", "down")}</span>}
              <div className="faint">{uniq([c.source, c.tenant && `Tenant ${c.tenant}`, c.vrf && `VRF ${c.vrf}`]).join(" · ")}</div>
            </td>
            <td className="mono">{c.pool ?? <Dash />}</td>
            <td>
              <ServerLines items={c.servers} max={20} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const TYPE_TEXT = (n: Nat) => (n.attrs?.platform === "avi" ? "NSX ALB" : typeLabel(n)[1]);

// Export der Liste (0.95.0): je Weg externe IP → Ziel-Server eine Zeile, auch über den Load Balancer
function natCsv(rows: Nat[]): string {
  const head = [
    "Name",
    L("Typ", "Type"),
    L("Quellsystem", "Source system"),
    "VDOM",
    L("Extern", "External"),
    L("Intern", "Internal"),
    "Load Balancer",
    "Pool",
    L("Ziel-Server", "Destination server"),
    L("Server-Name", "Server name"),
    L("Davor", "In front"),
    L("VIP-Gruppe", "VIP group"),
    L("Regeln", "Rules"),
    L("Prüfung", "Check"),
  ];
  const lines: string[][] = [head];
  for (const n of rows) {
    const base = [n.name, TYPE_TEXT(n), n.source ?? "", n.vdom ?? ""];
    const tail = (s: NatServer | null) => [
      s ? hostPort(s.ip, s.port) : "",
      s ? uniq([...s.names, ...s.dns]).join(", ") : "",
      (n.fronted_by ?? []).map((f) => `${hostPort(f.ext_ip, f.ext_port)} ${f.name}${f.source ? ` (${f.source})` : ""}`).join(", "),
      (n.groups ?? []).join(", "),
      String(n.rule_count),
      (n.issues ?? []).map(findingLabel).join(", "),
    ];
    const chain = n.chain ?? [];
    if (chain.length) {
      for (const c of chain) {
        const mid = [hostPort(c.ext_ip, c.ext_port), hostPort(c.vip_ip, c.vip_port), `${c.balancer}${c.source ? ` (${c.source})` : ""}`, c.pool ?? ""];
        for (const s of c.servers.length ? c.servers : [null]) lines.push([...base, ...mid, ...tail(s)]);
      }
      continue;
    }
    const lb = n.attrs?.platform === "avi"; // Virtual Service: Server im Pool, kein internes Ziel
    const ext = n.nat_type === "snat_pool" ? n.ext_ip ?? "" : hostPort(n.ext_ip, n.ext_port);
    const mapped = n.mapped_ip && !lb ? hostPort(n.mapped_ip, n.mapped_port) : "";
    const balancer = lb ? `${n.name}${n.source ? ` (${n.source})` : ""}` : "";
    const pool = String(n.attrs?.pool ?? n.attrs?.pool_group ?? "");
    const servers = n.dest_servers ?? [];
    for (const s of servers.length ? servers : [null]) lines.push([...base, ext, mapped, balancer, pool, ...tail(s)]);
  }
  const cell = (v: string) => (/[;"\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  return "﻿" + lines.map((l) => l.map(cell).join(";")).join("\r\n") + "\r\n";
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

function NatDrawer({
  id,
  onClose,
  onGo,
  onOpen,
}: {
  id: number;
  onClose: () => void;
  onGo?: (v: View, p?: ViewParams) => void;
  onOpen?: (id: number) => void; // anderen Eintrag der Kette öffnen (VIP davor bzw. Virtual Service)
}) {
  const vdomShown = useVdomShown();
  const [d, setD] = useState<NatDetail | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    setD(null);
    setErr(null);
    fetchNatDetail(id).then(setD).catch(setErr);
  }, [id]);
  const chain = d?.chain ?? [];
  const fronts = d?.fronted_by ?? [];
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
            {d.nat_type === "dnat_vip" && (
              <>
                <dt>{L("Ziel-Server", "Destination servers")}</dt>
                <dd id="nat-dest">
                  <DestServers n={d} />
                </dd>
              </>
            )}
            {(d.groups ?? []).length > 0 && (
              <>
                <dt>{L("VIP-Gruppe", "VIP group")}</dt>
                <dd className="mono">{(d.groups ?? []).join(", ")}</dd>
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

          {chain.length > 0 && (
            <>
              <div className="sub-h">
                {L(
                  <>Kette über den Load Balancer ({chain.length})</>,
                  <>Chain via the load balancer ({chain.length})</>,
                )}
              </div>
              <ChainTable chain={chain} onOpen={onOpen} />
            </>
          )}
          {fronts.length > 0 && (
            <>
              <div className="sub-h">
                {L(<>Davor: VIPs der Firewall ({fronts.length})</>, <>In front: VIPs of the firewall ({fronts.length})</>)}
              </div>
              <table id="nat-fronted">
                <thead>
                  <tr>
                    <th>VIP</th>
                    <th>Firewall</th>
                    <th>{L("Extern", "External")}</th>
                  </tr>
                </thead>
                <tbody>
                  {fronts.map((f) => (
                    <tr key={`${f.id}-${f.ext_port}`}>
                      <td>
                        {onOpen ? (
                          <button className="linkbtn mono" onClick={() => onOpen(f.id)}>
                            {f.name}
                          </button>
                        ) : (
                          <span className="mono">{f.name}</span>
                        )}
                        {f.via === "fqdn" && <div className="faint">{L("über den FQDN", "via the FQDN")}</div>}
                      </td>
                      <td>
                        {f.source ?? <Dash />}
                        <VdomChip vdom={f.vdom} always={vdomShown(f.source_system_id, f.vdom)} />
                      </td>
                      <td className="mono">{hostPort(f.ext_ip, f.ext_port)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

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
  const [exporting, setExporting] = useState(false);

  // Export (0.95.0): alle Einträge mit Suche/Filter der Ansicht, je Weg eine Zeile
  const exportCsv = () => {
    setExporting(true);
    fetchAllNat({ tenantId, natType: type, q, issuesOnly })
      .then((rows) => download("nat-vips.csv", natCsv(rows), "text/csv;charset=utf-8"))
      .catch(setError)
      .finally(() => setExporting(false));
  };

  // Typ und Suche (Name, IPs, Ports, DNS-Namen, Server, Kommentar) filtert der Orchestrator
  useEffect(() => {
    const timer = setTimeout(() => {
      setNat(null);
      setError(null);
      fetchNat({ tenantId, natType: type, q, issuesOnly, limit: PAGE })
        .then((page) => {
          setNat(page.rows);
          setTotal(page.total);
          // Link auf genau einen Eintrag (z. B. aus einer Regel bzw. einem Befund): Detail direkt
          // öffnen - seit 0.95.0 findet die Suche auch die Kette, entscheidend ist der Name
          const exact = page.rows.filter((r) => r.name === params.q);
          if (params.q && q === params.q && exact.length === 1) setSelected(exact[0].id);
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
            internes Ziel im IPAM). Zeigt eine VIP auf einen Load Balancer (NSX ALB), steht die ganze Kette bis zu den
            Ziel-Servern da.
          </>,
          <>
            Virtual IPs (external address → internal destination, incl. port forward), Virtual Server (load balancing to
            real servers) and SNAT IP pools for “{tenantLabel}” – with DNS names from the IPAM, the firewall rules that
            use them, and the check (naming convention, unused, internal destination in the IPAM). If a VIP points to a
            load balancer (NSX ALB), the whole chain up to the destination servers is shown.
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
              "Suche: Name, IP (auch im Pool-Bereich), Port, DNS-Name, Server (auch hinter dem Load Balancer), Virtual Service, Interface, Kommentar…",
              "Search: name, IP (also within the pool range), port, DNS name, server (also behind the load balancer), virtual service, interface, comment…",
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
        <button
          id="nat-export"
          className="btn btn-ghost btn-sm"
          disabled={exporting || !total}
          title={L(
            "Liste mit Suche und Filter als CSV – je Weg externe IP → Ziel-Server eine Zeile, auch über den Load Balancer",
            "List with search and filter as CSV – one line per path external IP → destination server, also via the load balancer",
          )}
          onClick={exportCsv}
        >
          <Icon name="download" size={14} /> {exporting ? L("Exportiere…", "Exporting…") : "Export (CSV)"}
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
                  <th>{L("Ziel-Server", "Destination servers")}</th>
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
                    <tr key={n.id} className="clickable" data-nat={n.name} onClick={() => setSelected(n.id)}>
                      <td>
                        <b>{n.name}</b>
                        {n.comment && <div className="faint">{n.comment}</div>}
                        {(n.groups ?? []).length > 0 && (
                          <div className="faint" title={L("VIP-Gruppe der Firewall", "VIP group of the firewall")}>
                            {L("Gruppe", "Group")} {(n.groups ?? []).join(", ")}
                          </div>
                        )}
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
                        {(n.fronted_by ?? []).length > 0 && (
                          <div className="faint nat-front">
                            {(n.fronted_by ?? []).slice(0, 3).map((f) => (
                              <div key={`${f.id}-${f.ext_port}`}>
                                {L("davor", "in front")} {hostPort(f.ext_ip, f.ext_port)} · {f.name}
                                {f.source ? ` (${f.source})` : ""}
                              </div>
                            ))}
                          </div>
                        )}
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
                        <ChainBrief chain={n.chain ?? []} />
                      </td>
                      <td>
                        {n.nat_type === "dnat_vip" ? <DestServers n={n} /> : <Dash />}
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
      {selected !== null && (
        <NatDrawer id={selected} onClose={() => setSelected(null)} onGo={onGo} onOpen={setSelected} />
      )}
    </>
  );
}
