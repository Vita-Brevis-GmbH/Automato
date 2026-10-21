// System (Admin): Betriebsstand auf einen Blick - Version, Datenbank-Migration, Agent-Paket
// mit Signatur, Zeitplan, Sicherheits-Schalter, letzte Sicherung. Rein lesend; Updates und
// Sicherungen laufen auf der VM (deploy/update.sh, deploy/backup) - siehe docs/BETRIEB.md.
import { useEffect, useState } from "react";
import { fetchOpsHealth, fetchSystemStatus, OpsHealth, SystemStatus } from "./api";
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
        Betriebsstand der Plattform. Updates, Rückfall und Sicherungen laufen auf der VM
        (<code>deploy/update.sh</code>, <code>deploy/backup/</code>).
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
                  <dt>Umgebung</dt>
                  <dd className="mono">{s.security.environment}</dd>
                </dl>
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
          </div>
        </div>
      )}
    </>
  );
}
