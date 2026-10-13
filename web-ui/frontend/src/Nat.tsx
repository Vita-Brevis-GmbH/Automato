import { useEffect, useState } from "react";
import { fetchNat, Nat } from "./api";

function target(n: Nat): string {
  let t = n.mapped_ip ?? "?";
  if (n.mapped_port) t += `:${n.mapped_port}`;
  if (n.protocol) t += `/${n.protocol}`;
  return t;
}

function ext(n: Nat): string {
  let e = n.ext_ip ?? "?";
  if (n.ext_port) e += `:${n.ext_port}`;
  return e;
}

export default function NatTable() {
  const [nat, setNat] = useState<Nat[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchNat()
      .then(setNat)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <p className="notice">NAT/VIPs konnten nicht geladen werden: {error}</p>;
  if (nat === null) return <p className="notice">Lade VIPs…</p>;
  if (nat.length === 0) return <p className="notice">Keine VIPs/DNAT erfasst.</p>;

  return (
    <div className="panel">
      <table>
        <thead>
          <tr>
            <th>VIP</th>
            <th>Extern</th>
            <th>→ Intern</th>
            <th>Port-Forward</th>
          </tr>
        </thead>
        <tbody>
          {nat.map((n) => (
            <tr key={n.id} style={{ cursor: "default" }}>
              <td>{n.name}</td>
              <td>
                <code>{ext(n)}</code>
              </td>
              <td>
                <code>{target(n)}</code>
              </td>
              <td>
                {n.portforward ? (
                  <span className="badge b-blue">an</span>
                ) : (
                  <span className="muted">aus</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
