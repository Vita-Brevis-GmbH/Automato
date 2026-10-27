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
import { L } from "./i18n";

const SIG: Record<string, [string, string]> = {
  signed: ["ok", L("signiert, Signatur gültig", "signed, signature valid")],
  unchecked: ["warn", L("signiert, aber kein Schlüssel zum Prüfen hinterlegt", "signed, but no key stored for verification")],
  unsigned: ["warn", L("nicht signiert", "not signed")],
  invalid: ["crit", L("Signatur ungültig", "signature invalid")],
  no_package: ["na", L("kein Agent-Paket im Image", "no agent package in the image")],
};
const MTLS: Record<string, [string, string]> = {
  off: ["warn", L("aus – Agent-Token genügt", "off – agent token is sufficient")],
  require: ["ok", L("nur über das mTLS-Gateway", "only via the mTLS gateway")],
  bind: ["ok", L("nur über das mTLS-Gateway, Zertifikat = Agent", "only via the mTLS gateway, certificate = agent")],
};
const KEY: Record<string, [string, string]> = {
  setting: ["ok", L("aus der Konfiguration / Secret-Provider", "from the configuration / secrets provider")],
  file: ["info", L("Schlüsseldatei im Daten-Volume (mitsichern!)", "key file in the data volume (include in backup!)")],
  missing: ["warn", L("noch nicht erzeugt", "not generated yet")],
};

function Chip({ map, value }: { map: Record<string, [string, string]>; value: string }) {
  const [cls, label] = map[value] ?? ["na", value];
  return <span className={`chip ${cls}`}>{label}</span>;
}

const CONN: Record<ConnectionItem["status"], [string, string]> = {
  ok: ["ok", "ok"],
  info: ["na", L("Hinweis", "Note")],
  warn: ["warn", L("Warnung", "Warning")],
  error: ["crit", L("Fehler", "Error")],
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
        <h2>{L("Verbindungen", "Connections")}</h2>
        {status && report && (
          <span className={`chip ${status[0]}`}>
            {report.problems
              ? L(
                  `${report.problems} Problem${report.problems === 1 ? "" : "e"}`,
                  `${report.problems} problem${report.problems === 1 ? "" : "s"}`,
                )
              : L("alles erreichbar", "all reachable")}
          </span>
        )}
        <span className="faint" style={{ fontSize: 12 }}>
          {L(
            "vom Orchestrator-Container aus – derselbe Weg wie Einlesen und NetBox-Sync",
            "from the orchestrator container – the same path as collecting and NetBox sync",
          )}
          {report?.container.network
            ? L(` · Container-Netz ${report.container.network}`, ` · container network ${report.container.network}`)
            : ""}
        </span>
        <div className="spacer" />
        <button className="btn btn-primary btn-sm" onClick={run} disabled={busy}>
          {busy ? L("Prüfe…", "Checking…") : report ? L("Erneut prüfen", "Check again") : L("Jetzt prüfen", "Check now")}
        </button>
      </div>
      <ErrorBox error={err} prefix={L("Verbindungen prüfen", "Check connections")} />
      {report ? (
        <div className="card-body scroll">
          <table>
            <thead>
              <tr>
                <th>{L("Ziel", "Target")}</th>
                <th>{L("Ergebnis", "Result")}</th>
                <th>{L("Schritte", "Steps")}</th>
                <th>{L("Abhilfe", "Remedy")}</th>
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
                          title={L(
                            "Befehl kopieren – auf dem Docker-Host als root ausführen",
                            "Copy command – run it as root on the Docker host",
                          )}
                          onClick={() => void navigator.clipboard?.writeText(i.ufw!.command)}
                        >
                          {L("Kopieren", "Copy")}
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="faint" style={{ padding: "8px 12px" }}>
            {L(
              <>
                geprüft {fmtTime(report.checked_at)} · auf der VM dasselbe mit <code>deploy/aufbau.sh pruefen</code>; Firewall-Regeln
                setzt <code>deploy/aufbau.sh firewall --anwenden</code>
              </>,
              <>
                checked {fmtTime(report.checked_at)} · the same on the VM with <code>deploy/aufbau.sh pruefen</code>; firewall
                rules are set by <code>deploy/aufbau.sh firewall --anwenden</code>
              </>,
            )}
          </div>
        </div>
      ) : (
        <div className="card-body pad faint">
          {L(
            <>
              Prüft Datenbank, Redis (Warteschlange zum zentralen Worker), Keycloak bei OIDC und jedes zentral gelesene Quellsystem
              (Name, TCP, bei NetBox die API mit dem Lese-Token – nur lesend). Systeme, die ein Agent liest, prüft der Agent selbst.
            </>,
            <>
              Checks the database, Redis (queue to the central worker), Keycloak with OIDC and every centrally collected source
              system (name, TCP, for NetBox the API with the read token – read-only). Systems collected by an agent are checked by
              the agent itself.
            </>,
          )}
        </div>
      )}
    </div>
  );
}

const days = (n: number) => (n > 0 ? L(`${n} Tage`, `${n} days`) : L("nie löschen", "never delete"));
const counts = (c: RetentionCounts) =>
  L(
    `${c.runs} Läufe · ${c.sync_plans} NetBox-Sync-Vorschauen · ${c.audit} Audit-Einträge · ${c.l2 ?? 0} MAC-/ARP-Einträge`,
    `${c.runs} runs · ${c.sync_plans} NetBox sync previews · ${c.audit} audit entries · ${c.l2 ?? 0} MAC/ARP entries`,
  );

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
    if (!r || !window.confirm(L(`Jetzt löschen: ${counts(r.due)}?`, `Delete now: ${counts(r.due)}?`))) return;
    setBusy(true);
    setErr(null);
    purgeRetention()
      .then(setR)
      .catch(setErr)
      .finally(() => setBusy(false));
  };
  const due = r ? r.due.runs + r.due.sync_plans + r.due.audit + (r.due.l2 ?? 0) : 0;
  return (
    <div className="card" id="retention">
      <div className="card-head">
        <h2>{L("Aufbewahrung", "Retention")}</h2>
        {r &&
          (due ? (
            <span className="chip warn">{L(<>{due} fällig</>, <>{due} due</>)}</span>
          ) : (
            <span className="chip ok">{L("nichts fällig", "nothing due")}</span>
          ))}
        <div className="spacer" />
        {r?.active && (
          <button className="btn btn-sm" onClick={purge} disabled={busy || !due}>
            {busy ? L("Räume auf…", "Cleaning up…") : L("Jetzt aufräumen", "Clean up now")}
          </button>
        )}
      </div>
      <ErrorBox error={err} prefix={L("Aufbewahrung", "Retention")} />
      {r && (
        <div className="card-body pad">
          <dl className="kv">
            <dt>{L("Läufe", "Runs")}</dt>
            <dd>{days(r.policy.runs_days)}</dd>
            <dt>NetBox-Sync</dt>
            <dd>{days(r.policy.sync_days)}</dd>
            <dt>Audit-Log</dt>
            <dd>{days(r.policy.audit_days)}</dd>
            <dt>{L("MAC/ARP der Switches", "MAC/ARP of the switches")}</dt>
            <dd>{days(r.policy.l2_days ?? 30)}</dd>
            <dt>{L("Fällig", "Due")}</dt>
            <dd>{counts(r.due)}</dd>
            <dt>{L("Zuletzt", "Last")}</dt>
            <dd>
              {r.last ? (
                <>
                  {fmtTime(r.last.at)} <span className="faint">({r.last.actor})</span>
                  <div className="faint">
                    {L("gelöscht:", "deleted:")} {counts(r.last.counts)}
                  </div>
                </>
              ) : (
                <Dash />
              )}
            </dd>
          </dl>
          {r.deleted && (
            <Note>
              {L("Gelöscht:", "Deleted:")} {counts(r.deleted)}
            </Note>
          )}
          <div className="faint" style={{ marginTop: 8 }}>
            {L(
              <>
                Es bleiben immer: der aktuelle Stand (Läufe, auf die Regeln, Objekte, NAT, VMs bzw. der Referenzstand zeigen), je
                Quellsystem der neuste und der neuste erfolgreiche Lauf, offene Läufe und die neuste Vorschau je NetBox.{" "}
                {r.scheduled ? "Der Zeitplan räumt täglich auf." : "Zeitplan aus – nur von Hand bzw. automato purge."} Fristen
                in <code>.env</code>: <code>AUTOMATO_RETENTION_RUNS_DAYS</code>, <code>…_SYNC_DAYS</code>, <code>…_AUDIT_DAYS</code>.
              </>,
              <>
                Always kept: the current state (runs referenced by rules, objects, NAT, VMs or the reference data), per
                source system the newest and the newest successful run, open runs and the newest preview per NetBox.{" "}
                {r.scheduled ? "The schedule cleans up daily." : "Schedule off – only by hand or automato purge."} Retention
                periods in <code>.env</code>: <code>AUTOMATO_RETENTION_RUNS_DAYS</code>, <code>…_SYNC_DAYS</code>,{" "}
                <code>…_AUDIT_DAYS</code>.
              </>,
            )}
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
        {L(
          <>
            Betriebsstand der Plattform. Updates, Rückfall, Prüfung des Aufbaus und Sicherungen laufen auf der VM
            (<code>deploy/update.sh</code>, <code>deploy/aufbau.sh</code>, <code>deploy/backup/</code>).
          </>,
          <>
            Operating status of the platform. Updates, rollback, setup checks and backups run on the VM
            (<code>deploy/update.sh</code>, <code>deploy/aufbau.sh</code>, <code>deploy/backup/</code>).
          </>,
        )}
      </PageHead>
      <ErrorBox error={err} prefix={L("Status laden", "Load status")} />
      {health && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-head">
            <h2>{L("Betriebszustand", "Health")}</h2>
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
                <h2>{L("Version & Datenbank", "Version & database")}</h2>
              </div>
              <div className="card-body pad">
                <dl className="kv">
                  <dt>{L("Plattform", "Platform")}</dt>
                  <dd className="mono">{s.version}</dd>
                  <dt>Migration</dt>
                  <dd>
                    <span className="mono">{s.database.revision ?? "–"}</span>
                    {" / Code "}
                    <span className="mono">{s.database.head ?? "–"}</span>{" "}
                    {s.database.up_to_date ? (
                      <span className="chip ok">{L("aktuell", "up to date")}</span>
                    ) : (
                      <span className="chip warn">{L("abweichend", "different")}</span>
                    )}
                  </dd>
                  <dt>{L("DB-Rolle", "DB role")}</dt>
                  <dd>
                    {s.database.role ? (
                      <>
                        <span className="mono">{s.database.role.role}</span>{" "}
                        {s.database.role.separated ? (
                          <span className="chip ok">{L("ohne DDL", "without DDL")}</span>
                        ) : (
                          <span className="chip warn">
                            {s.database.role.superuser ? "Superuser" : L("Eigentümer", "Owner")}
                            {L(" – darf Tabellen ändern", " – may change tables")}
                          </span>
                        )}
                      </>
                    ) : (
                      <Dash />
                    )}
                  </dd>
                  <dt>{L("Umgebung", "Environment")}</dt>
                  <dd className="mono">{s.security.environment}</dd>
                </dl>
                {s.database.role && !s.database.role.separated && (
                  <Note>
                    {L(
                      <>
                        Getrennte Rollen einrichten: <code>deploy/aufbau.sh db-rollen</code> – die Migration läuft als Eigentümer
                        (Dienst <code>migrate</code>), der Orchestrator mit einer Rolle ohne DDL.
                      </>,
                      <>
                        Set up separate roles: <code>deploy/aufbau.sh db-rollen</code> – the migration runs as owner
                        (service <code>migrate</code>), the orchestrator with a role without DDL.
                      </>,
                    )}
                  </Note>
                )}
              </div>
            </div>
            <div className="card">
              <div className="card-head">
                <h2>{L("Collector-Agenten", "Collector agents")}</h2>
              </div>
              <div className="card-body pad">
                <dl className="kv">
                  <dt>{L("Agent-Paket", "Agent package")}</dt>
                  <dd className="mono">{s.agent_package.version ?? <Dash />}</dd>
                  <dt>{L("Signatur", "Signature")}</dt>
                  <dd>
                    <Chip map={SIG} value={s.agent_package.signature} />
                  </dd>
                  <dt>{L("Aktuell", "Current")}</dt>
                  <dd>
                    {L(
                      <>
                        {s.agent_package.agents_current} von {s.agent_package.agents} Agenten melden diese Version
                      </>,
                      <>
                        {s.agent_package.agents_current} of {s.agent_package.agents} agents report this version
                      </>,
                    )}
                  </dd>
                </dl>
                {s.agent_package.signature !== "signed" && s.agent_package.version && (
                  <Note>
                    {L(
                      <>
                        Paket offline signieren: <code>automato agent-package manifest</code> →{" "}
                        <code>scripts/agent_signing.py sign</code> → <code>automato agent-package install-signature</code>.
                        Agenten mit <code>AUTOMATO_AGENT_SIGNING_PUBKEY</code> installieren nur signierte Updates.
                      </>,
                      <>
                        Sign the package offline: <code>automato agent-package manifest</code> →{" "}
                        <code>scripts/agent_signing.py sign</code> → <code>automato agent-package install-signature</code>.
                        Agents with <code>AUTOMATO_AGENT_SIGNING_PUBKEY</code> only install signed updates.
                      </>,
                    )}
                  </Note>
                )}
              </div>
            </div>
          </div>
          <div className="stack">
            <div className="card">
              <div className="card-head">
                <h2>{L("Sicherheit", "Security")}</h2>
              </div>
              <div className="card-body pad">
                <dl className="kv">
                  <dt>{L("Anmeldung", "Login")}</dt>
                  <dd>
                    {s.security.auth_mode === "oidc" ? (
                      <span className="chip ok">OIDC (Keycloak)</span>
                    ) : (
                      <span className="chip warn">{L("dev – ohne Login", "dev – no login")}</span>
                    )}
                  </dd>
                  <dt>{L("Intern", "Internal")}</dt>
                  <dd>
                    {s.security.internal_tls?.enabled ? (
                      <>
                        <span className={`chip ${(s.security.internal_tls.days_left ?? 0) < 30 ? "warn" : "ok"}`}>
                          {L("TLS mit eigener CA", "TLS with own CA")}
                        </span>{" "}
                        {s.security.internal_tls.not_after && (
                          <span className="faint">
                            {L("Zertifikat bis", "Certificate until")} {fmtTime(s.security.internal_tls.not_after)}
                          </span>
                        )}
                        {s.security.internal_tls.database && (
                          <div className="faint">
                            {L("Datenbank", "Database")} {s.security.internal_tls.database}
                          </div>
                        )}
                      </>
                    ) : (
                      <span className="chip warn">
                        {L("unverschlüsselt – deploy/aufbau.sh tls-intern", "unencrypted – deploy/aufbau.sh tls-intern")}
                      </span>
                    )}
                  </dd>
                  <dt>{L("Agent-Kanal", "Agent channel")}</dt>
                  <dd>
                    <Chip map={MTLS} value={s.security.internal_mtls_mode} />
                  </dd>
                  <dt>Audit-Log</dt>
                  <dd>
                    {s.security.audit_encryption ? (
                      <span className="chip ok">{L("verschlüsselt", "encrypted")}</span>
                    ) : (
                      <span className="chip warn">{L("Klartext", "plain text")}</span>
                    )}
                  </dd>
                  <dt>{L("Zugangsdaten", "Credentials")}</dt>
                  <dd>
                    {L("verschlüsselt, Schlüssel:", "encrypted, key:")} <Chip map={KEY} value={s.security.credentials_key} />
                  </dd>
                </dl>
              </div>
            </div>
            <div className="card">
              <div className="card-head">
                <h2>{L("Zeitplan & Sicherung", "Schedule & backup")}</h2>
              </div>
              <div className="card-body pad">
                <dl className="kv">
                  <dt>{L("Zeitplan", "Schedule")}</dt>
                  <dd>
                    {s.scheduler.enabled ? (
                      <span className="chip ok">{L("aktiv", "active")}</span>
                    ) : (
                      <span className="chip warn">{L("aus", "off")}</span>
                    )}{" "}
                    {L(
                      <>
                        {s.scheduler.scheduled_sources} Quellsystem{s.scheduler.scheduled_sources === 1 ? "" : "e"} ·{" "}
                      </>,
                      <>
                        {s.scheduler.scheduled_sources} source system{s.scheduler.scheduled_sources === 1 ? "" : "s"} ·{" "}
                      </>,
                    )}
                    {s.scheduler.timezone}
                  </dd>
                  <dt>{L("Nächster Lauf", "Next run")}</dt>
                  <dd>{s.scheduler.next_run_at ? fmtTime(s.scheduler.next_run_at) : <Dash />}</dd>
                  <dt>{L("Letzte Sicherung", "Last backup")}</dt>
                  <dd>
                    {s.backup?.at ? (
                      <>
                        {fmtTime(s.backup.at)}{" "}
                        {s.backup.encrypted ? (
                          <span className="chip ok">{L("verschlüsselt", "encrypted")}</span>
                        ) : (
                          <span className="chip warn">{L("unverschlüsselt", "unencrypted")}</span>
                        )}
                        {s.backup.file && <div className="faint mono">{s.backup.file}</div>}
                      </>
                    ) : (
                      <span className="chip warn">{L("keine bekannt", "none known")}</span>
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
