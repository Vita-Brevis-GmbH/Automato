import { useEffect, useState } from "react";
import { fetchNat, Nat } from "./api";
import { Dash, Empty, ErrorBox, Loading, PageHead } from "./ui";

const PAGE = 500; // Einträge je Seite

const NAT_TYPE: Record<string, [string, string]> = {
  dnat_vip: ["local", "VIP / DNAT"],
  snat_pool: ["info", "SNAT-Pool"],
};

export default function NatTable({ tenantId, tenantLabel }: { tenantId: number | null; tenantLabel: string }) {
  const [nat, setNat] = useState<Nat[] | null>(null);
  const [total, setTotal] = useState(0);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [type, setType] = useState("");

  // Typ filtert der Orchestrator; Gesamtzahl aus X-Total-Count, Rest per "weitere laden"
  useEffect(() => {
    setNat(null);
    setError(null);
    fetchNat({ tenantId, natType: type, limit: PAGE })
      .then((page) => {
        setNat(page.rows);
        setTotal(page.total);
      })
      .catch((e) => {
        setError(e);
        setNat([]);
        setTotal(0);
      });
  }, [tenantId, type]);

  const loadMore = () => {
    if (!nat) return;
    setMore(true);
    fetchNat({ tenantId, natType: type, limit: PAGE, offset: nat.length })
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
        Virtuelle IPs (externe Adresse → internes Ziel, inkl. Port-Forward) und SNAT-IP-Pools für „
        {tenantLabel}“.
      </PageHead>
      <div className="filterbar">
        <select className="sel" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">Typ: alle</option>
          <option value="dnat_vip">VIP / DNAT</option>
          <option value="snat_pool">SNAT-Pool</option>
        </select>
        <div className="grow" />
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
            <Empty title="Keine NAT-Einträge">Es wurden (noch) keine VIPs oder SNAT-Pools erfasst.</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Typ</th>
                  <th>Extern</th>
                  <th className="num">Port</th>
                  <th>Intern</th>
                  <th className="num">Port</th>
                  <th>Protokoll</th>
                  <th>Port-Forward</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((n) => {
                  const [cls, label] = NAT_TYPE[n.nat_type] ?? ["na", n.nat_type];
                  return (
                    <tr key={n.id}>
                      <td>
                        <b>{n.name}</b>
                      </td>
                      <td>
                        <span className={`chip ${cls}`}>{label}</span>
                      </td>
                      <td className="mono">{n.ext_ip ?? <Dash />}</td>
                      <td className="num">{n.ext_port ?? <Dash />}</td>
                      <td className="mono">{n.mapped_ip ?? <Dash />}</td>
                      <td className="num">{n.mapped_port ?? <Dash />}</td>
                      <td>{n.protocol ? <span className="chip">{n.protocol.toUpperCase()}</span> : <Dash />}</td>
                      <td>{n.portforward ? <span className="chip ok">an</span> : <span className="faint">aus</span>}</td>
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
    </>
  );
}
