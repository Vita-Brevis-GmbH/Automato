// Datenqualität (ADR 0029, Roadmap Schritt 6 „Daten verschärfen“): wo Doku, Weg-Suche, Zonen und Kunden
// unsicher sind, weil Daten fehlen, veraltet oder widersprüchlich sind. Gesamtnote, je Bereich Ampel und
// Kennzahl, je Eintrag Ursache, Hinweis und Link in die Ansicht zum Nachbessern. Daten aus GET /data-quality
// (Kunde oben gewählt: sein Ausschnitt). Reiter in „Abgleich“, Kachel in der Übersicht.
import { useEffect, useState } from "react";
import type { View, ViewParams } from "./App";
import { DataQuality, DqArea, DqEntry, fetchDataQuality } from "./api";
import { ErrorBox, fmtTime, Loading } from "./ui";
import { L } from "./i18n";

type Go = (v: View, p?: ViewParams) => void;

const LIGHT: Record<string, [string, string]> = {
  ok: ["ok", "OK"],
  info: ["info", L("Hinweis", "Note")],
  warn: ["warn", L("Warnung", "Warning")],
  crit: ["crit", L("Kritisch", "Critical")],
};

// Ziel-Ansichten der Links (Schlüssel vom Server, nur die Beschriftung ist übersetzt)
const VIEW_LABEL: Record<string, string> = {
  laeufe: L("Läufe", "Runs"),
  routing: "Routing",
  regeln: L("Regeln", "Rules"),
  objekte: L("Objekte", "Objects"),
  findings: "Findings",
  abgleich: L("Abgleich", "Reconciliation"),
  design: L("Netzwerkdesign", "Network design"),
  kunden: L("Kunden", "Customers"),
  tenants: L("Quellsysteme", "Source systems"),
};

export function DqChip({ status }: { status: string }) {
  const [cls, label] = LIGHT[status] ?? ["na", status];
  return <span className={`chip ${cls}`}>{label}</span>;
}

function EntryRow({ e, onGo }: { e: DqEntry; onGo?: Go }) {
  const fine = e.status === "ok";
  return (
    <tr className={fine ? "dq-ok" : undefined} data-key={e.key}>
      <td className="nowrap">
        <DqChip status={e.status} />
      </td>
      <td>
        <b>{e.title}</b>
        {e.cause && <div className="dq-cause">{e.cause}</div>}
        {e.samples.length > 0 && (
          <div className="faint dq-samples">
            {L("z. B. ", "e.g. ")}
            <span className="mono">{e.samples.join(", ")}</span>
          </div>
        )}
        {e.hint && !fine && <div className="faint dq-hint">{e.hint}</div>}
      </td>
      <td className="num mono">{e.count ?? ""}</td>
      <td className="nowrap">
        {e.link && onGo && (
          <button
            className="linkbtn dq-link"
            onClick={() => onGo(e.link!.view as View, e.link!.params)}
            title={L("Ansicht öffnen", "Open view")}
          >
            {VIEW_LABEL[e.link.view] ?? e.link.view} →
          </button>
        )}
      </td>
    </tr>
  );
}

function AreaCard({ area, onGo, showOk }: { area: DqArea; onGo?: Go; showOk: boolean }) {
  const problems = area.entries.filter((e) => e.status !== "ok");
  const fine = area.entries.filter((e) => e.status === "ok");
  const shown = showOk ? [...problems, ...fine] : problems;
  return (
    <div className={`card dq-area${area.applicable ? "" : " dq-na"}`} data-area={area.key}>
      <div className="card-head">
        <h2>{area.title}</h2>
        {area.applicable ? (
          <DqChip status={area.status} />
        ) : (
          <span className="chip na">{L("ohne Daten", "no data")}</span>
        )}
        <span className="faint dq-metric">{area.metric}</span>
        {area.note && <span className="chip info">{area.note}</span>}
      </div>
      {shown.length > 0 ? (
        <div className="card-body scroll">
          <table className="dq-table">
            <tbody>
              {shown.map((e) => (
                <EntryRow key={e.key} e={e} onGo={onGo} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="card-body pad muted">{L("Keine Auffälligkeiten.", "No issues.")}</div>
      )}
      {!showOk && fine.length > 0 && (
        <div className="card-foot">
          {L(
            `${fine.length} ${fine.length === 1 ? "Prüfung" : "Prüfungen"} ohne Befund`,
            `${fine.length} ${fine.length === 1 ? "check" : "checks"} without issue`,
          )}
        </div>
      )}
    </div>
  );
}

function scoreClass(status: string): string {
  return status === "ok" ? "ok" : status === "crit" ? "crit" : "warn";
}

export function DataQualityPanel({ tenantId, stamp = 0, onGo }: { tenantId: number; stamp?: number; onGo?: Go }) {
  const [data, setData] = useState<DataQuality | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [showOk, setShowOk] = useState(false);

  useEffect(() => {
    let current = true;
    setData(null);
    setError(null);
    fetchDataQuality(tenantId)
      .then((d) => current && setData(d))
      .catch((e) => current && setError(e));
    return () => {
      current = false;
    };
  }, [tenantId, stamp]);

  if (error) return <ErrorBox error={error} prefix={L("Datenqualität", "Data quality")} />;
  if (data === null) return <Loading />;
  const s = data.score;
  return (
    <div id="data-quality">
      <div className="card dq-score">
        <div className="card-body pad">
          <div>
            <div className="faint dq-label">{L("Gesamtnote", "Overall score")}</div>
            <div className={`dq-percent ${scoreClass(s.status)}`}>{s.percent === null ? "–" : `${s.percent} %`}</div>
          </div>
          <div className="dq-score-text">
            <b>
              {L(
                `${s.ok} von ${s.total} Bereichen in Ordnung`,
                `${s.ok} of ${s.total} areas in order`,
              )}
            </b>
            <div className="faint">
              {L(
                <>
                  Wo Doku, Weg-Suche, Zonen und Kunden unsicher sind, weil Daten fehlen, veraltet oder widersprüchlich
                  sind – mit Ursache und Link zum Nachbessern. Stand veraltet nach {data.stale_days} Tagen ohne
                  erfolgreichen Lauf (mit Zeitplan mindestens ein Abstand plus ein Tag).
                </>,
                <>
                  Where documentation, path search, zones and customers are uncertain because data is missing, outdated
                  or contradictory – with cause and link to improve it. Data counts as outdated after {data.stale_days}{" "}
                  days without a successful run (with a schedule at least one interval plus one day).
                </>,
              )}
            </div>
            <div className="faint">
              {L("berechnet", "calculated")} {fmtTime(data.generated_at)}
              {data.sample && data.sample.rules > 0 &&
                L(
                  ` · Relevanz als Stichprobe: ${data.sample.rules} von ${data.sample.of} prüfbaren Firewall-Regeln`,
                  ` · relevance as a sample: ${data.sample.rules} of ${data.sample.of} verifiable firewall rules`,
                )}
            </div>
          </div>
          <label className="check dq-toggle">
            <input type="checkbox" checked={showOk} onChange={(e) => setShowOk(e.target.checked)} />{" "}
            {L("Prüfungen ohne Befund zeigen", "Show checks without issue")}
          </label>
        </div>
      </div>
      {data.areas.map((a) => (
        <AreaCard key={a.key} area={a} onGo={onGo} showOk={showOk} />
      ))}
    </div>
  );
}

// Kachel der Übersicht: Gesamtnote mit Link auf den Reiter „Datenqualität“
export function DataQualityTile({ tenantId, onGo }: { tenantId: number; onGo: Go }) {
  const [data, setData] = useState<DataQuality | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let current = true;
    setData(null);
    setFailed(false);
    fetchDataQuality(tenantId)
      .then((d) => current && setData(d))
      .catch(() => current && setFailed(true));
    return () => {
      current = false;
    };
  }, [tenantId]);
  const s = data?.score;
  const worst = data?.areas.filter((a) => a.applicable && a.status !== "ok").map((a) => a.title) ?? [];
  return (
    <button
      className="kpi"
      id="data-quality-tile"
      onClick={() => onGo("abgleich", { tab: "datenqualitaet" })}
      title={L("Abgleich → Datenqualität öffnen", "Open Reconciliation → Data quality")}
    >
      <div className="k-label">{L("Datenqualität", "Data quality")}</div>
      <div className={`k-val ${s ? scoreClass(s.status) : ""}`}>
        {failed ? "–" : !s ? "…" : s.percent === null ? "–" : `${s.percent}%`}
      </div>
      <div className="k-sub">
        {s
          ? worst.length
            ? L(`prüfen: ${worst.join(", ")}`, `check: ${worst.join(", ")}`)
            : L(`${s.ok} von ${s.total} Bereichen in Ordnung`, `${s.ok} of ${s.total} areas in order`)
          : L("Bereiche in Ordnung", "areas in order")}
      </div>
    </button>
  );
}
