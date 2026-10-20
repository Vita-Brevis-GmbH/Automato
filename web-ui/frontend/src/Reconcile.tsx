// Abgleich (ADR 0010): Befunde je Typ, Referenzquellen des Mandanten und Vergleich des führenden
// IPAM (z. B. Infoblox DDI) mit der Anreicherung (NetBox). Gegenüber den Quellsystemen rein lesend.
import { useCallback, useEffect, useState } from "react";
import { fetchIpamCompare, fetchReconcileSummary, IpamCompare, RefBrief, ReconcileSummary, runReconcile } from "./api";
import type { Me } from "./auth";
import { FINDING_HINT, FINDING_LABEL, PLATFORM_LABEL, REF_ROLE_LABEL } from "./refs";
import { Dash, Empty, ErrorBox, Icon, Loading, Note, PageHead } from "./ui";

const PAGE = 200;

// Reihenfolge der Kacheln: Abgleich mit dem IPAM zuerst, dann Konvention/Hygiene
const TYPES = [
  "unmatched_object",
  "name_mismatch",
  "subnet_not_in_ipam",
  "vlan_mismatch",
  "invalid_name",
  "name_type_mismatch",
  "duplicate_value",
  "ambiguous_ip",
  "unused_object",
  "epg_no_endpoints",
];


export default function Reconcile({
  me,
  tenantId,
  tenantLabel,
  onOpenFindings,
  onChanged,
}: {
  me: Me;
  tenantId: number | null;
  tenantLabel: string;
  onOpenFindings: (type: string) => void;
  onChanged: () => void;
}) {
  const [summary, setSummary] = useState<ReconcileSummary | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [stamp, setStamp] = useState(0);

  const load = useCallback(() => {
    if (tenantId === null) return;
    setSummary(null);
    setError(null);
    fetchReconcileSummary(tenantId)
      .then(setSummary)
      .catch((e) => setError(e));
  }, [tenantId]);

  useEffect(load, [load]);

  if (tenantId === null) {
    return (
      <>
        <PageHead title="Abgleich" />
        <Note>Bitte oben einen Mandanten wählen – der Abgleich wird je Mandant berechnet.</Note>
      </>
    );
  }

  const rerun = async () => {
    setBusy(true);
    setError(null);
    try {
      await runReconcile(tenantId);
      load();
      setStamp((s) => s + 1);
      onChanged();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const sources = summary?.reference_sources ?? [];
  return (
    <>
      <PageHead
        title="Abgleich"
        actions={
          me.isOperator && (
            <button className="btn btn-primary" onClick={rerun} disabled={busy}>
              <Icon name="play" size={14} /> {busy ? "Gleiche ab…" : "Jetzt abgleichen"}
            </button>
          )
        }
      >
        Firewall- und ACI-Daten von „{tenantLabel}“ gegen das IPAM (Infoblox DDI / NetBox) und die Namenskonvention.
        Läuft automatisch nach jedem Einlesen; die Quellsysteme werden dabei nur gelesen.
      </PageHead>
      <ErrorBox error={error} prefix="Abgleich" onClose={() => setError(null)} />

      {summary === null ? (
        !error && <Loading />
      ) : (
        <>
          <div className="card">
            <div className="card-head">
              <h2>Referenzquellen</h2>
              <span className="faint" style={{ fontSize: 12 }}>
                verwaltet unter „Mandanten &amp; Standorte → Quellsysteme“
              </span>
            </div>
            <div className="card-body">
              {sources.length === 0 ? (
                <div style={{ padding: "12px 16px" }}>
                  <Note warn>
                    Noch keine Referenzquelle. Unter „Mandanten &amp; Standorte“ ein Quellsystem <b>Infoblox DDI</b>{" "}
                    (führend) und/oder <b>NetBox</b> (Anreicherung) anlegen und einlesen – erst dann können
                    Firewall-Objekte gegen das IPAM geprüft werden.
                  </Note>
                </div>
              ) : (
                <table>
                  <tbody>
                    {sources.map((s) => (
                      <tr key={s.id}>
                        <td>
                          <b>{s.name}</b>
                        </td>
                        <td>
                          <span className="chip">{PLATFORM_LABEL[s.platform] ?? s.platform}</span>
                        </td>
                        <td>
                          <span className={`chip ${s.ref_role === "primary" ? "local" : "info"}`}>
                            {REF_ROLE_LABEL[s.ref_role] ?? s.ref_role}
                          </span>
                        </td>
                        <td>
                          {s.has_reference ? (
                            <span className="chip ok">eingelesen</span>
                          ) : (
                            <span className="chip warn">noch nicht eingelesen</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          <div className="sub-h" style={{ marginTop: 22 }}>
            Befunde
          </div>
          <div className="kpis wrap">
            {TYPES.map((t) => {
              const f = summary.findings[t];
              return (
                <button key={t} className="kpi" onClick={() => onOpenFindings(t)} title="In Findings öffnen">
                  <div className="k-label">{FINDING_LABEL[t] ?? t}</div>
                  <div className={`k-val sm ${!f || f.open === 0 ? "ok" : "warn"}`}>{f ? f.open : 0}</div>
                  <div className="k-sub">
                    {f && f.total > f.open ? `${f.total - f.open} triagiert · ` : ""}
                    {FINDING_HINT[t]}
                  </div>
                </button>
              );
            })}
          </div>

          <IpamTable key={`${tenantId}-${stamp}`} tenantId={tenantId} summary={summary} />
        </>
      )}
    </>
  );
}

function Brief({ r }: { r: RefBrief | null }) {
  if (!r) return <span className="faint">fehlt</span>;
  return (
    <div className="refline">
      {r.dns_name ? <span className="mono">{r.dns_name}</span> : null}
      {r.vlan != null && <span className="chip">VLAN {r.vlan}</span>}
      {r.site && <span className="faint">{r.site}</span>}
      {r.description && <span className="faint">{r.description}</span>}
      {r.url && (
        <a href={r.url} target="_blank" rel="noreferrer" title="In der Quelle öffnen">
          <Icon name="external" size={12} />
        </a>
      )}
      {!r.dns_name && r.vlan == null && !r.site && !r.description && !r.url && <Dash />}
    </div>
  );
}

function IpamTable({ tenantId, summary }: { tenantId: number; summary: ReconcileSummary }) {
  const ipam = summary.ipam;
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<IpamCompare | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    const t = setTimeout(() => setQuery(q), 300);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => setOffset(0), [status, query]);
  useEffect(() => {
    if (!ipam.available) return;
    setData(null);
    setError(null);
    fetchIpamCompare(tenantId, { status: status || undefined, q: query, limit: PAGE, offset })
      .then(setData)
      .catch((e) => setError(e));
  }, [tenantId, ipam.available, status, query, offset]);

  const p = ipam.primary?.name ?? "führend";
  const e = ipam.enrichment?.name ?? "Anreicherung";
  const label: Record<string, string> = {
    missing: `fehlt in ${e}`,
    different: "abweichend",
    only_enrichment: `nur in ${e}`,
  };
  const cls: Record<string, string> = { missing: "warn", different: "crit", only_enrichment: "info" };
  const kind: Record<string, string> = { prefix: "Netz", range: "Range", ip: "IP" };
  const s = ipam.summary ?? {};

  return (
    <div className="card" style={{ marginTop: 8 }}>
      <div className="card-head">
        <h2>
          Vergleich {p} ↔ {e}
        </h2>
        <span className="faint" style={{ fontSize: 12 }}>
          Netze, Ranges und IPs mit DNS-Name – {p} ist führend
        </span>
      </div>
      {!ipam.available ? (
        <div className="card-body pad">
          <Note>
            Für den Vergleich braucht der Mandant ein führendes IPAM (z. B. Infoblox DDI) und eine Anreicherung
            (NetBox), beide eingelesen. Fehlende Einträge übernimmt später der NetBox-Sync (Vorschau + Übernehmen).
          </Note>
        </div>
      ) : (
        <>
          <div className="filterbar" style={{ padding: "12px 16px 0" }}>
            <div className="seg">
              {["", "missing", "different", "only_enrichment"].map((k) => (
                <button key={k || "all"} className={status === k ? "on" : ""} onClick={() => setStatus(k)}>
                  {k ? label[k] : "alle Abweichungen"} (
                  {k ? (s[k] ?? 0) : (s.missing ?? 0) + (s.different ?? 0) + (s.only_enrichment ?? 0)})
                </button>
              ))}
            </div>
            <span className="faint" style={{ fontSize: 12.5 }}>
              {s.equal ?? 0} übereinstimmend
            </span>
            <div className="search">
              <span className="ico">
                <Icon name="search" />
              </span>
              <input placeholder="IP, Netz oder DNS-Name…" value={q} onChange={(ev) => setQ(ev.target.value)} />
            </div>
          </div>
          <ErrorBox error={error} prefix="Vergleich" />
          <div className="card-body scroll">
            {data === null ? (
              !error && <Loading />
            ) : data.rows.length === 0 ? (
              <Empty title={query || status ? "Keine Treffer" : "Keine Abweichungen 🎉"}>
                {query || status ? "Filter oder Suche anpassen." : `${p} und ${e} stimmen überein.`}
              </Empty>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Art</th>
                    <th>Wert</th>
                    <th>{p}</th>
                    <th>{e}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr key={`${r.status}-${r.kind}-${r.value}`}>
                      <td>
                        <span className={`chip ${cls[r.status] ?? "na"}`}>{label[r.status] ?? r.status}</span>
                      </td>
                      <td>{kind[r.kind] ?? r.kind}</td>
                      <td className="mono nowrap">
                        <b>{r.value}</b>
                      </td>
                      <td>
                        <Brief r={r.primary} />
                      </td>
                      <td>
                        <Brief r={r.enrichment} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {data && data.total > PAGE && (
            <div className="pager">
              {offset + 1}–{Math.min(offset + PAGE, data.total)} von {data.total}
              <button className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>
                Zurück
              </button>
              <button
                className="btn btn-ghost btn-sm"
                disabled={offset + PAGE >= data.total}
                onClick={() => setOffset(offset + PAGE)}
              >
                Weiter
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
