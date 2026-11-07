// Abgleich (ADR 0010): Befunde je Typ, Referenzquellen des Mandanten und Vergleich des führenden
// IPAM (z. B. Infoblox DDI) mit einer Anreicherung (NetBox, wählbar auch AD-DNS: dann nur die
// DNS-Namen je IP, ADR 0045). Gegenüber den Quellsystemen rein lesend.
// Reiter „Datenqualität“ (ADR 0029): wo Ergebnisse unsicher sind, mit Ursache und Link (#abgleich?tab=datenqualitaet).
import { useCallback, useEffect, useState } from "react";
import { fetchIpamCompare, fetchReconcileSummary, IpamCompare, RefBrief, ReconcileSummary, runReconcile } from "./api";
import type { View, ViewParams } from "./App";
import type { Me } from "./auth";
import { DataQualityPanel } from "./DataQuality";
import { FINDING_HINT, FINDING_LABEL, PLATFORM_LABEL, REF_ROLE_LABEL } from "./refs";
import { Dash, Empty, ErrorBox, Icon, Loading, Note, PageHead } from "./ui";
import { L } from "./i18n";

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


type Tab = "befunde" | "datenqualitaet";
const tabOf = (p: ViewParams): Tab => (p.tab === "datenqualitaet" ? "datenqualitaet" : "befunde");

export default function Reconcile({
  me,
  tenantId,
  tenantLabel,
  params = {},
  onGo,
  onOpenFindings,
  onChanged,
}: {
  me: Me;
  tenantId: number | null;
  tenantLabel: string;
  params?: ViewParams; // tab=datenqualitaet (Kachel der Übersicht)
  onGo?: (v: View, p?: ViewParams) => void; // Links der Datenqualität in die Ansichten
  onOpenFindings: (type: string) => void;
  onChanged: () => void;
}) {
  const [summary, setSummary] = useState<ReconcileSummary | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [stamp, setStamp] = useState(0);
  const [tab, setTab] = useState<Tab>(() => tabOf(params));
  useEffect(() => setTab(params.tab === "datenqualitaet" ? "datenqualitaet" : "befunde"), [params.tab]);
  const choose = (t: Tab) => {
    setTab(t);
    onGo?.("abgleich", t === "befunde" ? {} : { tab: t });
  };

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
        <PageHead title={L("Abgleich", "Reconciliation")} />
        <Note>
          {L(
            "Bitte oben einen Mandanten wählen – der Abgleich wird je Mandant berechnet.",
            "Please select a tenant above – the reconciliation is calculated per tenant.",
          )}
        </Note>
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
        title={L("Abgleich", "Reconciliation")}
        actions={
          me.isOperator && (
            <button className="btn btn-primary" onClick={rerun} disabled={busy}>
              <Icon name="play" size={14} />{" "}
              {busy ? L("Gleiche ab…", "Reconciling…") : L("Jetzt abgleichen", "Reconcile now")}
            </button>
          )
        }
      >
        {L(
          <>
            Firewall- und ACI-Daten von „{tenantLabel}“ gegen das IPAM (Infoblox DDI / NetBox) und die Namenskonvention.
            Läuft automatisch nach jedem Einlesen; die Quellsysteme werden dabei nur gelesen.
          </>,
          <>
            Firewall and ACI data of “{tenantLabel}” against the IPAM (Infoblox DDI / NetBox) and the naming convention.
            Runs automatically after every collection; the source systems are only read.
          </>,
        )}
      </PageHead>
      <ErrorBox error={error} prefix={L("Abgleich", "Reconciliation")} onClose={() => setError(null)} />
      <div className="tabbar">
        <button className={tab === "befunde" ? "on" : ""} onClick={() => choose("befunde")}>
          {L("Befunde & IPAM", "Findings & IPAM")}
        </button>
        <button
          id="data-quality-tab"
          className={tab === "datenqualitaet" ? "on" : ""}
          onClick={() => choose("datenqualitaet")}
        >
          {L("Datenqualität", "Data quality")}
        </button>
      </div>

      {tab === "datenqualitaet" ? (
        <DataQualityPanel tenantId={tenantId} stamp={stamp} onGo={onGo} />
      ) : summary === null ? (
        !error && <Loading />
      ) : (
        <>
          <div className="card">
            <div className="card-head">
              <h2>{L("Referenzquellen", "Reference sources")}</h2>
              <span className="faint" style={{ fontSize: 12 }}>
                {L(
                  "verwaltet unter „Mandanten & Standorte → Quellsysteme“",
                  "managed under “Tenants & sites → Source systems”",
                )}
              </span>
            </div>
            <div className="card-body">
              {sources.length === 0 ? (
                <div style={{ padding: "12px 16px" }}>
                  <Note warn>
                    {L(
                      <>
                        Noch keine Referenzquelle. Unter „Mandanten &amp; Standorte“ ein Quellsystem <b>Infoblox DDI</b>{" "}
                        (führend) und/oder <b>NetBox</b> (Anreicherung) anlegen und einlesen – erst dann können
                        Firewall-Objekte gegen das IPAM geprüft werden.
                      </>,
                      <>
                        No reference source yet. Under “Tenants &amp; sites”, create and collect a source system{" "}
                        <b>Infoblox DDI</b> (primary) and/or <b>NetBox</b> (enrichment) – only then can firewall objects
                        be checked against the IPAM.
                      </>,
                    )}
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
                            <span className="chip ok">{L("eingelesen", "collected")}</span>
                          ) : (
                            <span className="chip warn">{L("noch nicht eingelesen", "not collected yet")}</span>
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
            {L("Befunde", "Findings")}
          </div>
          <div className="kpis wrap">
            {TYPES.map((t) => {
              const f = summary.findings[t];
              return (
                <button
                  key={t}
                  className="kpi"
                  onClick={() => onOpenFindings(t)}
                  title={L("In Findings öffnen", "Open in findings")}
                >
                  <div className="k-label">{FINDING_LABEL[t] ?? t}</div>
                  <div className={`k-val sm ${!f || f.open === 0 ? "ok" : "warn"}`}>{f ? f.open : 0}</div>
                  <div className="k-sub">
                    {f && f.total > f.open
                      ? L(`${f.total - f.open} triagiert · `, `${f.total - f.open} triaged · `)
                      : ""}
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
  if (!r) return <span className="faint">{L("fehlt", "missing")}</span>;
  const names = r.dns_names?.length ? r.dns_names : r.dns_name ? [r.dns_name] : [];
  return (
    <div className="refline">
      {names.length > 0 ? <span className="mono">{names.join(", ")}</span> : null}
      {r.vlan != null && <span className="chip">VLAN {r.vlan}</span>}
      {r.site && <span className="faint">{r.site}</span>}
      {r.description && <span className="faint">{r.description}</span>}
      {r.url && (
        <a href={r.url} target="_blank" rel="noreferrer" title={L("In der Quelle öffnen", "Open in the source")}>
          <Icon name="external" size={12} />
        </a>
      )}
      {names.length === 0 && r.vlan == null && !r.site && !r.description && !r.url && <Dash />}
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
  // Anreicherung wählbar (0.123.0): z. B. NetBox oder AD-DNS; Zahlen der gewählten
  const [enrichmentId, setEnrichmentId] = useState<number | null>(null);
  const [counts, setCounts] = useState<IpamCompare | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setQuery(q), 300);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => setOffset(0), [status, query, enrichmentId]);
  useEffect(() => {
    if (!ipam.available) return;
    setData(null);
    setError(null);
    let current = true; // nur die Antwort zur letzten Anfrage (Filter schnell gewechselt)
    const choice = enrichmentId != null ? { enrichment_id: enrichmentId } : {};
    fetchIpamCompare(tenantId, { status: status || undefined, q: query, limit: PAGE, offset, ...choice })
      .then((d) => {
        if (!current) return;
        setData(d);
        setCounts(d); // Zahlen gelten ohne Filter und Suche
      })
      .catch((e) => current && setError(e));
    return () => {
      current = false;
    };
  }, [tenantId, ipam.available, status, query, offset, enrichmentId]);

  const shown = counts ?? ipam;
  const dnsMode = shown.mode === "dns";
  const enrichments = ipam.enrichments ?? [];
  const p = ipam.primary?.name ?? L("führend", "primary");
  const e = shown.enrichment?.name ?? L("Anreicherung", "enrichment");
  const label: Record<string, string> = {
    missing: L(`fehlt in ${e}`, `missing in ${e}`),
    different: dnsMode ? L("DNS-Name abweichend", "DNS name different") : L("abweichend", "different"),
    only_enrichment: dnsMode ? L(`nur im DNS (${e})`, `only in DNS (${e})`) : L(`nur in ${e}`, `only in ${e}`),
  };
  const cls: Record<string, string> = { missing: "warn", different: "crit", only_enrichment: "info" };
  const kind: Record<string, string> = { prefix: L("Netz", "Network"), range: "Range", ip: "IP" };
  const s = shown.summary ?? {};
  const states = dnsMode ? ["", "different", "only_enrichment"] : ["", "missing", "different", "only_enrichment"];

  return (
    <div className="card" style={{ marginTop: 8 }}>
      <div className="card-head">
        <h2>
          {L(
            <>
              Vergleich {p} ↔ {e}
            </>,
            <>
              Comparison {p} ↔ {e}
            </>,
          )}
        </h2>
        <span className="faint" style={{ fontSize: 12 }}>
          {dnsMode
            ? L(
                <>DNS-Namen je IP aus dem DNS – IPs ohne DNS-Eintrag sind kein Fehler; {p} ist führend</>,
                <>DNS names per IP from the DNS – IPs without a DNS record are not an error; {p} is primary</>,
              )
            : L(
                <>Netze, Ranges und IPs mit DNS-Name – {p} ist führend</>,
                <>Networks, ranges and IPs with DNS name – {p} is primary</>,
              )}
        </span>
        {enrichments.length > 1 && (
          <select
            id="ipam-enrichment"
            className="sel input"
            style={{ marginLeft: "auto", width: "auto" }}
            value={shown.enrichment?.id ?? ""}
            onChange={(ev) => setEnrichmentId(Number(ev.target.value))}
            aria-label={L("Vergleich mit", "Compare with")}
          >
            {enrichments.map((x) => (
              <option key={x.id} value={x.id}>
                {L("Vergleich mit", "Compare with")} {x.name} ({PLATFORM_LABEL[x.platform] ?? x.platform})
              </option>
            ))}
          </select>
        )}
      </div>
      {!ipam.available ? (
        <div className="card-body pad">
          <Note>
            {L(
              "Für den Vergleich braucht der Mandant ein führendes IPAM (z. B. Infoblox DDI bzw. NetBox) und eine Anreicherung (NetBox bzw. AD-DNS), beide eingelesen. Fehlende Einträge übernimmt später der NetBox-Sync (Vorschau + Übernehmen).",
              "The comparison needs a primary IPAM (e.g. Infoblox DDI or NetBox) and an enrichment (NetBox or AD DNS) for the tenant, both collected. Missing entries are applied later by the NetBox sync (Preview + Apply).",
            )}
          </Note>
        </div>
      ) : (
        <>
          <div className="filterbar" style={{ padding: "12px 16px 0" }}>
            <div className="seg">
              {states.map((k) => (
                <button key={k || "all"} className={status === k ? "on" : ""} onClick={() => setStatus(k)}>
                  {k ? label[k] : L("alle Abweichungen", "all deviations")} (
                  {k ? (s[k] ?? 0) : (s.missing ?? 0) + (s.different ?? 0) + (s.only_enrichment ?? 0)})
                </button>
              ))}
            </div>
            <span className="faint" style={{ fontSize: 12.5 }}>
              {L(`${s.equal ?? 0} übereinstimmend`, `${s.equal ?? 0} matching`)}
            </span>
            <div className="search">
              <span className="ico">
                <Icon name="search" />
              </span>
              <input
                placeholder={L("IP, Netz oder DNS-Name…", "IP, network or DNS name…")}
                value={q}
                onChange={(ev) => setQ(ev.target.value)}
              />
            </div>
          </div>
          <ErrorBox error={error} prefix={L("Vergleich", "Comparison")} />
          <div className="card-body scroll">
            {data === null ? (
              !error && <Loading />
            ) : data.rows.length === 0 ? (
              <Empty
                title={
                  query || status ? L("Keine Treffer", "No matches") : L("Keine Abweichungen 🎉", "No deviations 🎉")
                }
              >
                {query || status
                  ? L("Filter oder Suche anpassen.", "Adjust the filter or search.")
                  : L(`${p} und ${e} stimmen überein.`, `${p} and ${e} match.`)}
              </Empty>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>{L("Art", "Type")}</th>
                    <th>{L("Wert", "Value")}</th>
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
              {offset + 1}–{Math.min(offset + PAGE, data.total)} {L("von", "of")} {data.total}
              <button className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>
                {L("Zurück", "Back")}
              </button>
              <button
                className="btn btn-ghost btn-sm"
                disabled={offset + PAGE >= data.total}
                onClick={() => setOffset(offset + PAGE)}
              >
                {L("Weiter", "Next")}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
