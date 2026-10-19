import { useState } from "react";
import { Agent, AgentCreated, downloadAgentPackage } from "./api";
import { download, ErrorBox, Icon, Note } from "./ui";

// Einrichtung des Collector-Agenten (ADR 0008) Schritt für Schritt - inkl. agent.env-Generator.
// Die agent.env entsteht nur im Browser (Blob-Download); Tokens werden weder gespeichert noch
// an den Server geschickt.

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

function SecretInput({
  id,
  value,
  onChange,
  placeholder,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  const [show, setShow] = useState(false);
  return (
    <div style={{ display: "flex", gap: 6 }}>
      <input
        id={id}
        className="input mono"
        type={show ? "text" : "password"}
        value={value}
        autoComplete="off"
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      <button className="btn btn-ghost btn-sm" type="button" onClick={() => setShow((s) => !s)}>
        {show ? "Verbergen" : "Zeigen"}
      </button>
    </div>
  );
}

export function buildAgentEnv(v: {
  orchestratorUrl: string;
  agentToken: string;
  internalToken: string;
  fortigateUrl: string;
  fortigateToken: string;
  verifyTls: boolean;
}): string {
  return [
    "# Automato Collector-Agent - erzeugt im Web-UI (Collector-Agenten -> Einrichtung).",
    "# Enthaelt Zugangsdaten: nur auf der Agent-Station ablegen, nicht weitergeben.",
    "",
    "# --- Orchestrator (direkt, nicht ueber den Web-UI-Proxy) ---",
    `AUTOMATO_ORCHESTRATOR_URL=${v.orchestratorUrl.trim() || "http://<orchestrator-host>:8000"}`,
    `AUTOMATO_AGENT_TOKEN=${v.agentToken.trim() || "<AGENT_TOKEN>"}`,
    `AUTOMATO_INTERNAL_API_TOKEN=${v.internalToken.trim() || "<INTERNAL_API_TOKEN>"}`,
    "",
    "# --- TLS zum Orchestrator (produktiv) - nur einkommentieren, wenn die Dateien existieren ---",
    "# AUTOMATO_ORCHESTRATOR_CA_BUNDLE=C:\\Automato\\pki\\orch-ca.pem",
    "# AUTOMATO_CLIENT_CERT=C:\\Automato\\pki\\agent.crt",
    "# AUTOMATO_CLIENT_KEY=C:\\Automato\\pki\\agent.key",
    "",
    "# --- Zielsystem FortiGate (read-only) ---",
    `AUTOMATO_FORTIGATE_URL=${v.fortigateUrl.trim() || "https://<fortigate>"}`,
    `AUTOMATO_FORTIGATE_TOKEN=${v.fortigateToken.trim() || "<FORTIGATE_READONLY_TOKEN>"}`,
    `AUTOMATO_FORTIGATE_VERIFY_TLS=${v.verifyTls ? "true" : "false"}`,
    "",
  ].join("\r\n");
}

const mask = (text: string) =>
  text.replace(/^(AUTOMATO_(?:AGENT|INTERNAL_API|FORTIGATE)_TOKEN=)(?!<)(.+)$/gm, "$1••••••••");

export default function AgentGuide({
  agents,
  created,
  onNewAgent,
}: {
  agents: Agent[];
  created: AgentCreated | null;
  onNewAgent: () => void;
}) {
  const [orchestratorUrl, setOrchestratorUrl] = useState(`http://${window.location.hostname}:8000`);
  const [agentToken, setAgentToken] = useState("");
  const [internalToken, setInternalToken] = useState("");
  const [fortigateUrl, setFortigateUrl] = useState("");
  const [fortigateToken, setFortigateToken] = useState("");
  const [verifyTls, setVerifyTls] = useState(true);
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
  const env = buildAgentEnv({
    orchestratorUrl,
    agentToken: token,
    internalToken,
    fortigateUrl,
    fortigateToken,
    verifyTls,
  });
  const missing = [
    !token && "Agent-Token",
    !internalToken && "internes Token",
    !fortigateUrl && "FortiGate-URL",
    !fortigateToken && "FortiGate-Token",
  ].filter(Boolean);

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
              Windows-Rechner im Kundennetz, der die FortiGate per HTTPS erreicht, mit einem{" "}
              <b>read-only REST-API-Token</b> der FortiGate (System → Administratoren → REST API Admin, Profil
              nur Lesen).
            </li>
            <li>
              Der Rechner erreicht den Orchestrator ausgehend (Standard Port 8000) – Test in PowerShell:{" "}
              <code>Test-NetConnection {target}</code>
            </li>
            <li>
              Python 3.11 oder neuer (python.org, beim Setup <b>„Add python.exe to PATH“</b> anhaken) und
              Internetzugang für die einmalige Installation der Python-Pakete.
            </li>
            <li>
              Beim Mandanten ist das <b>Quellsystem</b> angelegt (Mandanten &amp; Standorte → + Quellsystem).
            </li>
          </ul>
        </li>

        <li className={agents.length ? "done" : ""}>
          <h3>Agent anlegen</h3>
          <p>
            Mit <b>+ Agent</b> einen Agenten für den Mandanten anlegen. Das Token erscheint nur einmal und wird
            unten automatisch in die <code>agent.env</code> übernommen.
          </p>
          {created ? (
            <span className="chip ok">Token von „{created.name}“ übernommen</span>
          ) : (
            <button className="btn btn-ghost btn-sm" onClick={onNewAgent}>
              + Agent
            </button>
          )}
        </li>

        <li>
          <h3>Konfiguration erzeugen (agent.env)</h3>
          <p>
            Die Werte bleiben in diesem Browser – nichts wird gespeichert oder an den Server übertragen.
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
            <div className="field">
              <label htmlFor="ag-internal">Internes Token (Ergebnis-Upload)</label>
              <SecretInput id="ag-internal" value={internalToken} onChange={setInternalToken} />
              <div className="hint">
                Auf der Automato-VM:{" "}
                <code>grep ^AUTOMATO_INTERNAL_API_TOKEN /opt/automato/automato/.env</code>
              </div>
            </div>
            <div className="field">
              <label htmlFor="ag-fgt">FortiGate-URL</label>
              <input
                id="ag-fgt"
                className="input mono"
                value={fortigateUrl}
                placeholder="https://fw-01.kunde.lan"
                onChange={(e) => setFortigateUrl(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="ag-fgt-token">FortiGate-Token (read-only)</label>
              <SecretInput id="ag-fgt-token" value={fortigateToken} onChange={setFortigateToken} />
            </div>
            <div className="field" style={{ alignSelf: "end" }}>
              <label className="check">
                <input type="checkbox" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} />
                Zertifikat der FortiGate prüfen (im Lab mit selbstsigniertem Zertifikat ausschalten)
              </label>
            </div>
          </div>
          <div className="cmd">
            <pre>{mask(env)}</pre>
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn btn-primary btn-sm" onClick={() => download("agent.env", env, "text/plain")}>
              <Icon name="download" size={14} /> agent.env herunterladen
            </button>
            {missing.length > 0 && <span className="faint">Noch offen: {missing.join(", ")}</span>}
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
          <h3>Verbindung zur FortiGate testen</h3>
          <Cmd>{`powershell -ExecutionPolicy Bypass -File ${INSTALL_DIR}\\run-agent.ps1 -Check`}</Cmd>
          <p>
            Erwartet: <code>fortigate check: OK - …</code>. Rein lesend, es wird noch nichts übertragen.
          </p>
        </li>

        <li>
          <h3>Ersten Lauf starten</h3>
          <p>
            Übersicht → <b>Jetzt einlesen</b> → Mandant und Quellsystem wählen → Ausführung <b>Extern (Agent)</b>{" "}
            → Lauf starten. Danach auf dem Agent-Rechner sofort abholen (sonst übernimmt das die geplante Aufgabe
            beim nächsten Intervall):
          </p>
          <Cmd>{`powershell -ExecutionPolicy Bypass -File ${INSTALL_DIR}\\run-agent.ps1`}</Cmd>
          <p>
            Erwartet: <code>fortigate run …: completed</code>. In „Letzte Läufe“ steht dann <b>OK</b>, hier ist der
            Agent <b>online</b>, und die Regeln erscheinen unter Firewall-Regeln.
          </p>
        </li>

        <li>
          <h3>Wenn etwas nicht klappt</h3>
          <table className="trouble">
            <tbody>
              <tr>
                <td>keine offenen Laeufe</td>
                <td>Noch kein externer Lauf gestartet (Schritt 7) – oder der Lauf gehört zu einem anderen Mandanten als der Agent.</td>
              </tr>
              <tr>
                <td>401 invalid agent token</td>
                <td>Agent-Token falsch oder Agent deaktiviert – ggf. neuen Agenten anlegen.</td>
              </tr>
              <tr>
                <td>Lauf bleibt „läuft“</td>
                <td>Ergebnis-Upload abgelehnt: internes Token in der agent.env prüfen.</td>
              </tr>
              <tr>
                <td>Verbindung abgelehnt / Timeout</td>
                <td>Port 8000 vom Agent-Rechner zum Orchestrator in der Firewall freigeben; Adresse prüfen.</td>
              </tr>
              <tr>
                <td>check: FEHLER … certificate</td>
                <td>Selbstsigniertes FortiGate-Zertifikat: Haken „Zertifikat prüfen“ entfernen (nur Lab).</td>
              </tr>
              <tr>
                <td>check: FEHLER … 401/403</td>
                <td>FortiGate-Token falsch oder Trusted Hosts des REST-API-Admins schliessen den Agent-Rechner aus.</td>
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
