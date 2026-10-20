import { useState } from "react";
import { Agent, AgentCreated, downloadAgentPackage } from "./api";
import { download, ErrorBox, Icon, Note, SecretInput } from "./ui";

// Einrichtung des Collector-Agenten (ADR 0008/0009) Schritt für Schritt - inkl. agent.env-Generator.
// Die agent.env enthält nur Orchestrator-Adresse + Agent-Token; Geräte und ihre Zugangsdaten
// werden beim Mandanten (Quellsysteme) erfasst und dem Agenten pro Lauf übergeben. Die
// agent.env entsteht nur im Browser (Blob-Download); das Token wird weder gespeichert noch an
// den Server geschickt.

const INSTALL_DIR = "C:\\Automato\\collector";

function Cmd({ children }: { children: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(children);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* Zwischenablage nicht verfügbar (ohne HTTPS) -> manuell markieren */
    }
  };
  return (
    <div className="cmd">
      <pre>{children}</pre>
      <button className="btn btn-ghost btn-sm copy" onClick={copy}>
        {copied ? "Kopiert ✓" : "Kopieren"}
      </button>
    </div>
  );
}

export function buildAgentEnv(v: { orchestratorUrl: string; agentToken: string }): string {
  return [
    "# Automato Collector-Agent - erzeugt im Web-UI (Collector-Agenten -> Einrichtung).",
    "# Enthaelt das Agent-Token: nur auf der Agent-Station ablegen, nicht weitergeben.",
    "# Geraete + Zugangsdaten kommen vom Orchestrator (Web-UI: Mandanten -> Quellsysteme).",
    "",
    "# --- Orchestrator (direkt, nicht ueber den Web-UI-Proxy) ---",
    `AUTOMATO_ORCHESTRATOR_URL=${v.orchestratorUrl.trim() || "http://<orchestrator-host>:8000"}`,
    `AUTOMATO_AGENT_TOKEN=${v.agentToken.trim() || "<AGENT_TOKEN>"}`,
    "",
    "# --- TLS zum Orchestrator (produktiv) - nur einkommentieren, wenn die Dateien existieren ---",
    "# AUTOMATO_ORCHESTRATOR_CA_BUNDLE=C:\\Automato\\pki\\orch-ca.pem",
    "# AUTOMATO_CLIENT_CERT=C:\\Automato\\pki\\agent.crt",
    "# AUTOMATO_CLIENT_KEY=C:\\Automato\\pki\\agent.key",
    "",
  ].join("\r\n");
}

const mask = (text: string) => text.replace(/^(AUTOMATO_AGENT_TOKEN=)(?!<)(.+)$/gm, "$1••••••••");

export default function AgentGuide({
  agents,
  created,
  onNewAgent,
  onGoTenants,
}: {
  agents: Agent[];
  created: AgentCreated | null;
  onNewAgent: () => void;
  onGoTenants: () => void;
}) {
  const [orchestratorUrl, setOrchestratorUrl] = useState(`http://${window.location.hostname}:8000`);
  const [agentToken, setAgentToken] = useState("");
  const [pkgBusy, setPkgBusy] = useState(false);
  const [pkgErr, setPkgErr] = useState<unknown>(null);
  let target = `${window.location.hostname} -Port 8000`;
  try {
    const u = new URL(orchestratorUrl);
    target = `${u.hostname} -Port ${u.port || (u.protocol === "https:" ? "443" : "80")}`;
  } catch {
    /* noch keine gültige URL */
  }

  // Token des soeben angelegten Agenten übernehmen (nur solange es angezeigt wird)
  const token = agentToken || created?.token || "";
  const env = buildAgentEnv({ orchestratorUrl, agentToken: token });

  // Zugewiesene Geräte: des soeben angelegten Agenten, sonst aller Agenten
  const relevant = created ? agents.filter((a) => a.id === created.id) : agents;
  const devices = relevant.flatMap((a) => a.systems.map((d) => ({ ...d, agent: a.name })));
  const withoutToken = devices.filter((d) => !d.has_credentials);

  const getPackage = async () => {
    setPkgBusy(true);
    setPkgErr(null);
    try {
      download("automato-collector-agent.zip", await downloadAgentPackage());
    } catch (e) {
      setPkgErr(e);
    } finally {
      setPkgBusy(false);
    }
  };

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-head">
        <h2>Einrichtung eines Agenten (Windows)</h2>
        <span className="faint" style={{ fontSize: 12 }}>
          ca. 10 Minuten · Admin-PowerShell auf dem Agent-Rechner nötig
        </span>
      </div>
      <ol className="steps">
        <li>
          <h3>Voraussetzungen prüfen</h3>
          <ul>
            <li>
              Windows-Rechner im Kundennetz, der die Geräte (z. B. FortiGate) per HTTPS erreicht.
            </li>
            <li>
              Pro Gerät ein <b>read-only REST-API-Token</b> (FortiGate: System → Administratoren → REST API Admin,
              Profil nur Lesen, Trusted Hosts = Agent-Rechner).
            </li>
            <li>
              Der Rechner erreicht den Orchestrator ausgehend (Standard Port 8000) – Test in PowerShell:{" "}
              <code>Test-NetConnection {target}</code>
            </li>
            <li>
              Python 3.11 oder neuer (python.org, beim Setup <b>„Add python.exe to PATH“</b> anhaken) und
              Internetzugang für die einmalige Installation der Python-Pakete.
            </li>
          </ul>
        </li>

        <li className={agents.length ? "done" : ""}>
          <h3>Agent anlegen</h3>
          <p>
            Mit <b>+ Agent</b> einen Agenten für den Mandanten anlegen. Das Token erscheint nur einmal und wird
            in Schritt 4 automatisch in die <code>agent.env</code> übernommen.
          </p>
          {created ? (
            <span className="chip ok">Token von „{created.name}“ übernommen</span>
          ) : (
            <button className="btn btn-ghost btn-sm" onClick={onNewAgent}>
              + Agent
            </button>
          )}
        </li>

        <li className={devices.length && !withoutToken.length ? "done" : ""}>
          <h3>Geräte erfassen und dem Agenten zuweisen</h3>
          <p>
            Unter <b>Mandanten &amp; Standorte → Quellsysteme</b> das Gerät anlegen bzw. bearbeiten: Adresse,
            read-only API-Token, TLS-Prüfung und <b>Verwaltet von</b> = dieser Agent. Das Token wird verschlüsselt
            im Orchestrator gespeichert und nur diesem Agenten für seine Läufe übergeben – auf dem Agent-Rechner
            liegen keine Geräte-Zugangsdaten. Geräte lassen sich später jederzeit einem anderen Agenten zuweisen.
          </p>
          {devices.length > 0 && (
            <p>
              Zugewiesen:{" "}
              {devices.map((d) => (
                <span key={d.id} className={`chip ${d.has_credentials ? "ok" : "warn"}`}>
                  {d.name} → {d.agent}
                  {d.has_credentials ? "" : " · ohne Token"}
                </span>
              ))}
            </p>
          )}
          <button className="btn btn-ghost btn-sm" onClick={onGoTenants}>
            Zu Mandanten &amp; Standorte
          </button>
        </li>

        <li>
          <h3>Konfiguration erzeugen (agent.env)</h3>
          <p>
            Nur Orchestrator-Adresse und Agent-Token. Die Werte bleiben in diesem Browser – nichts wird gespeichert
            oder an den Server übertragen.
          </p>
          <div className="envgen">
            <div className="field">
              <label htmlFor="ag-orch">Orchestrator-Adresse (vom Agent-Rechner aus)</label>
              <input
                id="ag-orch"
                className="input mono"
                value={orchestratorUrl}
                onChange={(e) => setOrchestratorUrl(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="ag-token">Agent-Token</label>
              <SecretInput
                id="ag-token"
                value={token}
                onChange={setAgentToken}
                placeholder="aus Schritt 2 (oder einfügen)"
              />
            </div>
          </div>
          <div className="cmd">
            <pre>{mask(env)}</pre>
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn btn-primary btn-sm" onClick={() => download("agent.env", env, "text/plain")}>
              <Icon name="download" size={14} /> agent.env herunterladen
            </button>
            {!token && <span className="faint">Noch offen: Agent-Token</span>}
          </div>
        </li>

        <li>
          <h3>Agent-Paket herunterladen</h3>
          <p>
            ZIP mit dem Collector (Python-Paket), <code>install.ps1</code> und <code>run-agent.ps1</code> – ohne
            Zugangsdaten.
          </p>
          <ErrorBox error={pkgErr} prefix="Paket" />
          <button className="btn btn-ghost btn-sm" onClick={getPackage} disabled={pkgBusy}>
            <Icon name="download" size={14} /> {pkgBusy ? "Lade…" : "Agent-Paket (ZIP) herunterladen"}
          </button>
        </li>

        <li>
          <h3>Auf dem Agent-Rechner installieren</h3>
          <p>
            ZIP entpacken, die <code>agent.env</code> in den entpackten Ordner{" "}
            <code>automato-collector-agent</code> legen, dann eine <b>PowerShell als Administrator</b> öffnen:
          </p>
          <Cmd>{`cd $HOME\\Downloads\\automato-collector-agent
Get-ChildItem | Unblock-File
powershell -ExecutionPolicy Bypass -File .\\install.ps1 -IntervalMinutes 15`}</Cmd>
          <p>
            Installiert nach <code>{INSTALL_DIR}</code> und legt die geplante Aufgabe <b>„Automato Collector
            Agent“</b> an (alle 15 Minuten). Ändern der Konfiguration später: <code>{INSTALL_DIR}\agent.env</code>.
          </p>
        </li>

        <li>
          <h3>Verbindung zu den Geräten testen</h3>
          <Cmd>{`powershell -ExecutionPolicy Bypass -File ${INSTALL_DIR}\\run-agent.ps1 -Check`}</Cmd>
          <p>
            Prüft alle zugewiesenen Geräte, eine Zeile je Gerät – erwartet z. B.{" "}
            <code>fw-01 (fortigate) check: OK - erreichbar: https://…</code>. Rein lesend, es wird nichts
            übertragen.
          </p>
        </li>

        <li>
          <h3>Ersten Lauf starten</h3>
          <p>
            Übersicht → <b>Jetzt einlesen</b> → Mandant und Quellsystem wählen → Lauf starten (die Ausführung über
            den Agenten ergibt sich aus der Zuweisung). Danach auf dem Agent-Rechner sofort abholen (sonst übernimmt
            das die geplante Aufgabe beim nächsten Intervall):
          </p>
          <Cmd>{`powershell -ExecutionPolicy Bypass -File ${INSTALL_DIR}\\run-agent.ps1`}</Cmd>
          <p>
            Erwartet: <code>fortigate run … (fw-01): completed</code>. In „Letzte Läufe“ steht dann <b>OK</b>, hier ist der
            Agent <b>online</b>, und die Regeln erscheinen unter Firewall-Regeln.
          </p>
        </li>

        <li>
          <h3>Wenn etwas nicht klappt</h3>
          <table className="trouble">
            <tbody>
              <tr>
                <td>keine offenen Laeufe</td>
                <td>Noch kein Lauf gestartet (Schritt 8) – oder das Gerät ist einem anderen Agenten zugewiesen.</td>
              </tr>
              <tr>
                <td>keine Geraete zugewiesen</td>
                <td>Beim Quellsystem „Verwaltet von“ auf diesen Agenten setzen (Schritt 3).</td>
              </tr>
              <tr>
                <td>keine Zugangsdaten fuer „fw-01“</td>
                <td>Beim Quellsystem das API-Token hinterlegen (Bearbeiten → API-Token).</td>
              </tr>
              <tr>
                <td>401 invalid agent token</td>
                <td>Agent-Token falsch oder Agent deaktiviert – ggf. neuen Agenten anlegen.</td>
              </tr>
              <tr>
                <td>Zugangsdaten nicht lesbar</td>
                <td>
                  Der Schlüssel des Orchestrators hat sich geändert (Schlüsseldatei/AUTOMATO_CREDENTIALS_KEY) – Token
                  beim Quellsystem neu hinterlegen.
                </td>
              </tr>
              <tr>
                <td>Verbindung abgelehnt / Timeout</td>
                <td>Port 8000 vom Agent-Rechner zum Orchestrator in der Firewall freigeben; Adresse prüfen.</td>
              </tr>
              <tr>
                <td>check: FEHLER … certificate</td>
                <td>Selbstsigniertes Geräte-Zertifikat: beim Quellsystem „TLS-Zertifikat prüfen“ ausschalten (nur Lab).</td>
              </tr>
              <tr>
                <td>check: FEHLER … 401/403</td>
                <td>API-Token beim Quellsystem falsch oder Trusted Hosts des REST-API-Admins schliessen den Agent-Rechner aus.</td>
              </tr>
            </tbody>
          </table>
          <Note>
            Produktiv den Orchestrator per HTTPS/mTLS ansprechen (TLS-Zeilen in der agent.env, siehe{" "}
            <code>collectors/EXTERNAL.md</code>).
          </Note>
        </li>
      </ol>
    </div>
  );
}
