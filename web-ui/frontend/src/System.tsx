// System (Admin): Betriebsstand auf einen Blick - Version, Datenbank-Migration, Agent-Paket
// mit Signatur, Zeitplan, Sicherheits-Schalter, letzte Sicherung, seit 0.40.0 die Verbindungen
// (vom Orchestrator-Container aus), seit 0.48.0 DB-Rolle und Aufbewahrung ("Jetzt aufräumen"
// löscht, was über der Frist ist). Updates und Sicherungen laufen auf der VM
// (deploy/update.sh, deploy/aufbau.sh, deploy/backup) - siehe docs/BETRIEB.md.
import { useEffect, useState } from "react";
import {
  checkConnections,
  ConnectionItem,
  ConnectionReport,
  fetchOpsHealth,
  fetchRetention,
  fetchSystemStatus,
  OpsHealth,
  purgeRetention,
  RetentionCounts,
  RetentionStatus,
  SystemStatus,
} from "./api";
import { HealthChip, HealthTable } from "./Health";
import { Dash, ErrorBox, fmtTime, Loading, Note, PageHead } from "./ui";

const SIG: Record<string, [string, string]> = {
  signed: ["ok", "signiert, Signatur gültig"],
  unchecked: ["warn", "signiert, aber kein Schlüssel zum Prüfen hinterlegt"],
  unsigned: ["warn", "nicht signiert"],
  invalid: ["crit", "Signatur ungültig"],
  no_package: ["na", "kein Agent-Paket im Image"],
};
const MTLS: Record<string, [string, string]> = {
  off: ["warn", "aus – Agent-Token genügt"],
  require: ["ok", "nur über das mTLS-Gateway"],
  bind: ["ok", "nur über das mTLS-Gateway, Zertifikat = Agent"],
};
const KEY: Record<string, [string, string]> = {
  setting: ["ok", "aus der Konfiguration / Secret-Provider"],
  file: ["info", "Schlüsseldatei im Daten-Volume (mitsichern!)"],
  missing: ["warn", "noch nicht erzeugt"],
};

function Chip({ map, value }: { map: Record<string, [string, string]>; value: string }) {
  const [cls, label] = map[value] ?? ["na", value];
  return <span className={`chip ${cls}`}>{label}</span>;
}

const CONN: Record<ConnectionItem["status"], [string, string]> = {
  ok: ["ok", "ok"],
  info: ["na", "Hinweis"],
  warn: ["warn", "Warnung"],
  error: ["crit", "Fehler"],
};

// Verbindungen (0.40.0): auf Knopfdruck vom Orchestrator-Container aus - derselbe Weg wie
// Einlesen und NetBox-Sync; je Ziel die Schritte (Name, TCP, HTTP) und die Abhilfe
function Connections() {
  const [report, setReport] = useState<ConnectionReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const run = () => {
    setBusy(true);
    setErr(null);
    checkConnections()
      .then(setReport)
      .catch(setErr)
      .finally(() => setBusy(false));
  };
  const status = report ? CONN[report.status] : null;
  return (
    <div className="card" id="connections" style={{ marginBottom: 16 }}>
      <div className="card-head">
        <h2>Verbindungen</h2>
        {status && report && (
          <span className={`chip ${status[0]}`}>
            {report.problems ? `${report.problems} Problem${report.problems === 1 ? "" : "e"}` : "alles erreichbar"}
          </span>
        )}
        <span className="faint" style={{ fontSize: 12 }}>
          vom Orchestrator-Container aus – derselbe Weg wie Einlesen und NetBox-Sync
          {report?.container.network ? ` · Container-Netz ${report.container.network}` : ""}
        </span>
        <div className="spacer" />
        <button className="btn btn-primary btn-sm" onClick={run} disabled={busy}>
          {busy ? "Prüfe…" : report ? "Erneut prüfen" : "Jetzt prüfen"}
        </button>
      </div>
      <ErrorBox error={err} prefix="Verbindungen prüfen" />
      {report ? (
        <div className="card-body scroll">
          <table>
            <thead>
              <tr>
                <th>Ziel</th>
                <th>Ergebnis</th>
                <th>Schritte</th>
                <th>Abhilfe</th>
              </tr>
            </thead>
            <tbody>
              {report.items.map((i) => (
                <tr key={i.key} className={`conn-${i.status}`}>
                  <td>
                    <b>{i.name}</b>
                    <div className="faint">{i.area}</div>
                    {i.target && <div className="faint mono">{i.target}</div>}
                  </td>
                  <td className="nowrap">
                    <span className={`chip ${CONN[i.status][0]}`}>{CONN[i.status][1]}</span>
                  </td>
                  <td>
                    {i.steps.map((st) => (
                      <div key={st.step + st.detail} className={st.ok ? "" : "conn-fail"}>
                        {st.ok ? "✓" : "✗"} <span className="mono">{st.step}</span> {st.detail}
                      </div>
                    ))}
                  </td>
                  <td>
                    {i.status !== "ok" && i.hint}
                    {i.ufw && (
                      <div className="conn-cmd">
                        <code>{i.ufw.command}</code>
                        <button
                          className="btn btn-ghost btn-sm"
                          title="Befehl kopieren – auf dem Docker-Host als root ausführen"
                          onClick={() => void navigator.clipboard?.writeText(i.ufw!.command)}
                        >
                          Kopieren
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="faint" style={{ padding: "8px 12px" }}>
            geprüft {fmtTime(report.checked_at)} · auf der VM dasselbe mit <code>deploy/aufbau.sh pruefen</code>; Firewall-Regeln
            setzt <code>deploy/aufbau.sh firewall --anwenden</code>
          </div>
        </div>
      ) : (
        <div className="card-body pad faint">
          Prüft Datenbank, Redis (Warteschlange zum zentralen Worker), Keycloak bei OIDC und jedes zentral gelesene Quellsystem
          (Name, TCP, bei NetBox die API mit dem Lese-Token – nur lesend). Systeme, die ein Agent liest, prüft der Agent selbst.
        </div>
      )}
    </div>
  );
}

const days = (n: number) => (n > 0 ? `${n} Tage` : "nie löschen");
const counts = (c: RetentionCounts) =>
  `${c.runs} Läufe · ${c.sync_plans} NetBox-Sync-Vorschauen · ${c.audit} Audit-Einträge`;

// Aufbewahrung (0.48.0, R14): beendete Läufe, alte Sync-Vorschauen und Audit-Einträge nach Frist
// löschen; der aktuelle Stand bleibt. Der Zeitplan räumt täglich auf, hier auch von Hand.
function Retention() {
  const [r, setR] = useState<RetentionStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    let current = true;
    fetchRetention()
      .then((d) => current && setR(d))
      .catch((e) => current && setErr(e));
    return () => {
      current = false;
    };
  }, []);
  const purge = () => {
    if (!r || !window.confirm(`Jetzt löschen: ${counts(r.due)}?`)) return;
    setBusy(true);
    setErr(null);
    purgeRetention()
      .then(setR)
      .catch(setErr)
      .finally(() => setBusy(false));
  };
  const due = r ? r.due.runs + r.due.sync_plans + r.due.audit : 0;
  return (
    <div className="card" id="retention">
      <div className="card-head">
        <h2>Aufbewahrung</h2>
        {r && (due ? <span className="chip warn">{due} fällig</span> : <span className="chip ok">nichts fällig</span>)}
        <div className="spacer" />
        {r?.active && (
          <button className="btn btn-sm" onClick={purge} disabled={busy || !due}>
            {busy ? "Räume auf…" : "Jetzt aufräumen"}
          </button>
        )}
      </div>
      <ErrorBox error={err} prefix="Aufbewahrung" />
      {r && (
        <div className="card-body pad">
          <dl className="kv">
            <dt>Läufe</dt>
            <dd>{days(r.policy.runs_days)}</dd>
            <dt>NetBox-Sync</dt>
            <dd>{days(r.policy.sync_days)}</dd>
            <dt>Audit-Log</dt>
            <dd>{days(r.policy.audit_days)}</dd>
            <dt>Fällig</dt>
            <dd>{counts(r.due)}</dd>
            <dt>Zuletzt</dt>
            <dd>
              {r.last ? (
                <>
                  {fmtTime(r.last.at)} <span className="faint">({r.last.actor})</span>
                  <div className="faint">gelöscht: {counts(r.last.counts)}</div>
                </>
              ) : (
                <Dash />
              )}
            </dd>
          </dl>
          {r.deleted && <Note>Gelöscht: {counts(r.deleted)}</Note>}
          <div className="faint" style={{ marginTop: 8 }}>
            Es bleiben immer: der aktuelle Stand (Läufe, auf die Regeln, Objekte, NAT, VMs bzw. der Referenzstand zeigen), je
            Quellsystem der neuste und der neuste erfolgreiche Lauf, offene Läufe und die neuste Vorschau je NetBox.{" "}
            {r.scheduled ? "Der Zeitplan räumt täglich auf." : "Zeitplan aus – nur von Hand bzw. automato purge."} Fristen
            in <code>.env</code>: <code>AUTOMATO_RETENTION_RUNS_DAYS</code>, <code>…_SYNC_DAYS</code>, <code>…_AUDIT_DAYS</code>.
          </div>
        </div>
      )}
    </div>
  );
}

export default function System() {
  const [s, setS] = useState<SystemStatus | null>(null);
  const [health, setHealth] = useState<OpsHealth | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    fetchSystemStatus().then(setS).catch(setErr);
    fetchOpsHealth().then(setHealth).catch(setErr);
  }, []);

  return (
    <>
      <PageHead title="System">
        Betriebsstand der Plattform. Updates, Rückfall, Prüfung des Aufbaus und Sicherungen laufen auf der VM
        (<code>deploy/update.sh</code>, <code>deploy/aufbau.sh</code>, <code>deploy/backup/</code>).
      </PageHead>
      <ErrorBox error={err} prefix="Status laden" />
      {health && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-head">
            <h2>Betriebszustand</h2>
            <HealthChip health={health} />
          </div>
          <div className="card-body scroll">
            <HealthTable health={health} />
          </div>
        </div>
      )}
      <Connections />
      {!s && !err && <Loading />}
      {s && (
        <div className="two-col">
          <div className="stack">
            <div className="card">
              <div className="card-head">
                <h2>Version &amp; Datenbank</h2>
              </div>
              <div className="card-body pad">
                <dl className="kv">
                  <dt>Plattform</dt>
                  <dd className="mono">{s.version}</dd>
                  <dt>Migration</dt>
                  <dd>
                    <span className="mono">{s.database.revision ?? "–"}</span>
                    {" / Code "}
                    <span className="mono">{s.database.head ?? "–"}</span>{" "}
                    {s.database.up_to_date ? (
                      <span className="chip ok">aktuell</span>
                    ) : (
                      <span className="chip warn">abweichend</span>
                    )}
                  </dd>
                  <dt>DB-Rolle</dt>
                  <dd>
                    {s.database.role ? (
                      <>
                        <span className="mono">{s.database.role.role}</span>{" "}
                        {s.database.role.separated ? (
                          <span className="chip ok">ohne DDL</span>
                        ) : (
                          <span className="chip warn">
                            {s.database.role.superuser ? "Superuser" : "Eigentümer"} – darf Tabellen ändern
                          </span>
                        )}
                      </>
                    ) : (
                      <Dash />
                    )}
                  </dd>
                  <dt>Umgebung</dt>
                  <dd className="mono">{s.security.environment}</dd>
                </dl>
                {s.database.role && !s.database.role.separated && (
                  <Note>
                    Getrennte Rollen einrichten: <code>deploy/aufbau.sh db-rollen</code> – die Migration läuft als Eigentümer
                    (Dienst <code>migrate</code>), der Orchestrator mit einer Rolle ohne DDL.
                  </Note>
                )}
              </div>
            </div>
            <div className="card">
              <div className="card-head">
                <h2>Collector-Agenten</h2>
              </div>
              <div className="card-body pad">
                <dl className="kv">
                  <dt>Agent-Paket</dt>
                  <dd className="mono">{s.agent_package.version ?? <Dash />}</dd>
                  <dt>Signatur</dt>
                  <dd>
                    <Chip map={SIG} value={s.agent_package.signature} />
                  </dd>
                  <dt>Aktuell</dt>
                  <dd>
                    {s.agent_package.agents_current} von {s.agent_package.agents} Agenten melden diese Version
                  </dd>
                </dl>
                {s.agent_package.signature !== "signed" && s.agent_package.version && (
                  <Note>
                    Paket offline signieren: <code>automato agent-package manifest</code> →{" "}
                    <code>scripts/agent_signing.py sign</code> → <code>automato agent-package install-signature</code>.
                    Agenten mit <code>AUTOMATO_AGENT_SIGNING_PUBKEY</code> installieren nur signierte Updates.
                  </Note>
                )}
              </div>
            </div>
          </div>
          <div className="stack">
            <div className="card">
              <div className="card-head">
                <h2>Sicherheit</h2>
              </div>
              <div className="card-body pad">
                <dl className="kv">
                  <dt>Anmeldung</dt>
                  <dd>
                    {s.security.auth_mode === "oidc" ? (
                      <span className="chip ok">OIDC (Keycloak)</span>
                    ) : (
                      <span className="chip warn">dev – ohne Login</span>
                    )}
                  </dd>
                  <dt>Agent-Kanal</dt>
                  <dd>
                    <Chip map={MTLS} value={s.security.internal_mtls_mode} />
                  </dd>
                  <dt>Audit-Log</dt>
                  <dd>
                    {s.security.audit_encryption ? (
                      <span className="chip ok">verschlüsselt</span>
                    ) : (
                      <span className="chip warn">Klartext</span>
                    )}
                  </dd>
                  <dt>Zugangsdaten</dt>
                  <dd>
                    verschlüsselt, Schlüssel: <Chip map={KEY} value={s.security.credentials_key} />
                  </dd>
                </dl>
              </div>
            </div>
            <div className="card">
              <div className="card-head">
                <h2>Zeitplan &amp; Sicherung</h2>
              </div>
              <div className="card-body pad">
                <dl className="kv">
                  <dt>Zeitplan</dt>
                  <dd>
                    {s.scheduler.enabled ? (
                      <span className="chip ok">aktiv</span>
                    ) : (
                      <span className="chip warn">aus</span>
                    )}{" "}
                    {s.scheduler.scheduled_sources} Quellsystem{s.scheduler.scheduled_sources === 1 ? "" : "e"} ·{" "}
                    {s.scheduler.timezone}
                  </dd>
                  <dt>Nächster Lauf</dt>
                  <dd>{s.scheduler.next_run_at ? fmtTime(s.scheduler.next_run_at) : <Dash />}</dd>
                  <dt>Letzte Sicherung</dt>
                  <dd>
                    {s.backup?.at ? (
                      <>
                        {fmtTime(s.backup.at)}{" "}
                        {s.backup.encrypted ? (
                          <span className="chip ok">verschlüsselt</span>
                        ) : (
                          <span className="chip warn">unverschlüsselt</span>
                        )}
                        {s.backup.file && <div className="faint mono">{s.backup.file}</div>}
                      </>
                    ) : (
                      <span className="chip warn">keine bekannt</span>
                    )}
                  </dd>
                </dl>
              </div>
            </div>
            <Retention />
          </div>
        </div>
      )}
    </>
  );
}
