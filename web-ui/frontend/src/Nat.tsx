// NAT / VIPs: VIPs (externe IP:Port → internes Ziel) und SNAT-Pools – angereichert wie die
// Firewall-Regeln: DNS-Namen beider IPs aus DDI/NetBox, Server hinter der internen IP und
// die Regeln, die den VIP bzw. Pool nutzen (Klick → Regel-Liste gefiltert).
import { useEffect, useState } from "react";
import { fetchNat, fetchNatDetail, IpInfo, Nat, NatDetail } from "./api";
import type { View, ViewParams } from "./App";
import { ActionBadge, Dash, Drawer, Empty, ErrorBox, Icon, Loading, PageHead } from "./ui";

const PAGE = 500; // Einträge je Seite

const NAT_TYPE: Record<string, [string, string]> = {
  dnat_vip: ["local", "VIP / DNAT"],
  snat_pool: ["info", "SNAT-Pool"],
};

function addr(ip: string | null, port: string | null, proto?: string | null) {
  if (!ip) return null;
  return `${ip}${port ? `:${port}` : ""}${proto ? `/${proto}` : ""}`;
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
          <span className="faint">kein Eintrag im IPAM</span>
        ) : (
          infos.map((i) => (
            <div key={i.source} style={{ marginBottom: 4 }}>
              <span className="chip">{i.source}</span>{" "}
              {i.dns_names.length ? <span className="mono">{i.dns_names.join(", ")}</span> : <span className="faint">ohne DNS-Namen</span>}
              {i.prefix && (
                <div className="faint">
                  Netz {i.prefix.value}
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
}: {
  id: number;
  onClose: () => void;
  onGo?: (v: View, p?: ViewParams) => void;
}) {
  const [d, setD] = useState<NatDetail | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    fetchNatDetail(id).then(setD).catch(setErr);
  }, [id]);
  const pool = d?.nat_type === "snat_pool";
  return (
    <Drawer title={d?.name ?? "NAT"} sub={d ? (NAT_TYPE[d.nat_type]?.[1] ?? d.nat_type) : undefined} onClose={onClose}>
      <ErrorBox error={err} />
      {!d && !err && <Loading />}
      {d && (
        <>
          <dl className="kv">
            <dt>{pool ? "Quell-IP(s)" : "Extern"}</dt>
            <dd className="mono">{addr(d.ext_ip, d.ext_port) ?? <Dash />}</dd>
            {!pool && (
              <>
                <dt>Intern</dt>
                <dd className="mono">{addr(d.mapped_ip, d.mapped_port, d.protocol) ?? <Dash />}</dd>
                <dt>Port-Forward</dt>
                <dd>{d.portforward ? <span className="chip ok">an</span> : "aus"}</dd>
                <dt>Server</dt>
                <dd>
                  {d.server ? (
                    <button className="linkbtn mono objlink" title="Regeln mit diesem Objekt" onClick={() => onGo?.("regeln", { object: d.server! })}>
                      {d.server}
                    </button>
                  ) : (
                    <Dash />
                  )}
                </dd>
              </>
            )}
            {d.external_name && (
              <>
                <dt>Externer Name</dt>
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
                <dt>Kommentar</dt>
                <dd>{d.comment}</dd>
              </>
            )}
            {Object.entries(d.settings).map(([k, v]) => (
              <span key={k} style={{ display: "contents" }}>
                <dt>{k}</dt>
                <dd className="mono">{v}</dd>
              </span>
            ))}
            <IpamBlock title={pool ? "IPAM (Quell-IP)" : "IPAM extern"} infos={d.ext_info} />
            {!pool && <IpamBlock title="IPAM intern" infos={d.mapped_info} />}
          </dl>

          <div className="sub-h" style={{ display: "flex", alignItems: "center" }}>
            Firewall-Regeln ({d.rules.length})
            <div style={{ flex: 1 }} />
            {d.rules.length > 0 && (
              <button className="btn btn-ghost btn-sm" onClick={() => onGo?.("regeln", { nat: d.name })}>
                In Regeln anzeigen
              </button>
            )}
          </div>
          {d.rules.length === 0 ? (
            <div className="faint">Keine Firewall-Regel verwendet diesen Eintrag.</div>
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
  const [selected, setSelected] = useState<number | null>(null);

  // Typ und Suche (Name, IPs, Ports, DNS-Namen, Server, Kommentar) filtert der Orchestrator
  useEffect(() => {
    const timer = setTimeout(() => {
      setNat(null);
      setError(null);
      fetchNat({ tenantId, natType: type, q, limit: PAGE })
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
  }, [tenantId, type, q, params.q]);

  const loadMore = () => {
    if (!nat) return;
    setMore(true);
    fetchNat({ tenantId, natType: type, q, limit: PAGE, offset: nat.length })
      .then((page) => {
        setNat([...nat, ...page.rows]);
        setTotal(page.total);
      })
      .catch(setError)
      .finally(() => setMore(false));
  };

  const shown = nat ?? [];
  const loaded = shown.length;

  return (
    <>
      <PageHead title="NAT / VIPs">
        Virtuelle IPs (externe Adresse → internes Ziel, inkl. Port-Forward) und SNAT-IP-Pools für „{tenantLabel}“ –
        mit DNS-Namen aus dem IPAM und den Firewall-Regeln, die sie nutzen.
      </PageHead>
      <div className="filterbar">
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input
            placeholder="Suche: Name, IP (auch im Pool-Bereich), Port, DNS-Name, Server, Interface, Kommentar…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <select className="sel" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">Typ: alle</option>
          <option value="dnat_vip">VIP / DNAT</option>
          <option value="snat_pool">SNAT-Pool</option>
        </select>
        <span className="count">
          {total} Einträge{total > loaded ? ` · ${loaded} geladen` : ""}
        </span>
      </div>
      <ErrorBox error={error} prefix="NAT laden" />
      <div className="card">
        <div className="card-body scroll">
          {nat === null ? (
            <Loading>Lade VIPs…</Loading>
          ) : shown.length === 0 ? (
            <Empty title={q ? "Keine Treffer" : "Keine NAT-Einträge"}>
              {q ? "Suche anpassen." : "Es wurden (noch) keine VIPs oder SNAT-Pools erfasst."}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Typ</th>
                  <th>Extern</th>
                  <th>→ Intern</th>
                  <th>Server</th>
                  <th>Interface</th>
                  <th className="num">Regeln</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((n) => {
                  const [cls, label] = NAT_TYPE[n.nat_type] ?? ["na", n.nat_type];
                  return (
                    <tr key={n.id} className="clickable" onClick={() => setSelected(n.id)}>
                      <td>
                        <b>{n.name}</b>
                        {n.comment && <div className="faint">{n.comment}</div>}
                      </td>
                      <td>
                        <span className={`chip ${cls}`}>{label}</span>
                        {n.portforward && <span className="chip ok">Port-Forward</span>}
                      </td>
                      <td>
                        <span className="mono">{addr(n.ext_ip, n.ext_port) ?? "–"}</span>
                        <Names items={[n.external_name, ...n.ext_dns]} />
                      </td>
                      <td>
                        {n.mapped_ip ? (
                          <>
                            <span className="mono">{addr(n.mapped_ip, n.mapped_port, n.protocol)}</span>
                            <Names items={n.mapped_dns} />
                          </>
                        ) : (
                          <span className="faint">{n.nat_type === "snat_pool" ? "ausgehende Quell-IP" : "–"}</span>
                        )}
                      </td>
                      <td className="mono">{n.server ?? <Dash />}</td>
                      <td className="mono">{n.interface ?? <Dash />}</td>
                      <td className="num">
                        {n.rule_count > 0 ? (
                          <button
                            className="chip local linkchip"
                            title="Regeln mit diesem Eintrag anzeigen"
                            onClick={(e) => {
                              e.stopPropagation();
                              onGo?.("regeln", { nat: n.name });
                            }}
                          >
                            {n.rule_count}
                          </button>
                        ) : (
                          <span className="chip na" title="von keiner Firewall-Regel verwendet">
                            0
                          </span>
                        )}
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
            {loaded} von {total} geladen –{" "}
            <button className="btn btn-ghost btn-sm" onClick={loadMore} disabled={more}>
              {more ? "Lade…" : `weitere ${Math.min(PAGE, total - loaded)} laden`}
            </button>
          </div>
        )}
      </div>
      {selected !== null && <NatDrawer id={selected} onClose={() => setSelected(null)} onGo={onGo} />}
    </>
  );
}
