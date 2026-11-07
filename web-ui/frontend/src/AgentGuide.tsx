import { useEffect, useState } from "react";
import { Agent, AgentCreated, AgentPackageInfo, downloadAgentCa, downloadAgentPackage, fetchAgentPackageInfo } from "./api";
import { download, ErrorBox, Icon, Note, SecretInput } from "./ui";
import { L } from "./i18n";

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
        {copied ? L("Kopiert ✓", "Copied ✓") : L("Kopieren", "Copy")}
      </button>
    </div>
  );
}

// Direkt auf Port 8000 mit TLS intern (0.78.2): https:// und die CA aus dem Agent-Paket
const CA_PATH = `${INSTALL_DIR}\\automato-ca.crt`;
const directTls = (url: string) => /^https:\/\/[^/\s]+:8000\/?$/.test(url.trim());

export function buildAgentEnv(v: { orchestratorUrl: string; agentToken: string; ca?: boolean }): string {
  const withCa = !!v.ca && directTls(v.orchestratorUrl);
  return [
    L(
      "# Automato Collector-Agent - erzeugt im Web-UI (Collector-Agenten -> Einrichtung).",
      "# Automato collector agent - generated in the web UI (Collector agents -> Setup).",
    ),
    L(
      "# Enthaelt das Agent-Token: nur auf der Agent-Station ablegen, nicht weitergeben.",
      "# Contains the agent token: store only on the agent station, do not pass on.",
    ),
    L(
      "# Geraete + Zugangsdaten kommen vom Orchestrator (Web-UI: Mandanten -> Quellsysteme).",
      "# Devices + credentials come from the orchestrator (web UI: Tenants -> Source systems).",
    ),
    "",
    L(
      "# --- Orchestrator (direkt, nicht ueber den Web-UI-Proxy) ---",
      "# --- Orchestrator (direct, not via the web UI proxy) ---",
    ),
    `AUTOMATO_ORCHESTRATOR_URL=${v.orchestratorUrl.trim() || "http://<orchestrator-host>:8000"}`,
    `AUTOMATO_AGENT_TOKEN=${v.agentToken.trim() || "<AGENT_TOKEN>"}`,
    "",
    L(
      "# --- TLS zum Orchestrator (produktiv) - nur einkommentieren, wenn die Dateien existieren ---",
      "# --- TLS to the orchestrator (production) - only uncomment if the files exist ---",
    ),
    withCa
      ? `AUTOMATO_ORCHESTRATOR_CA_BUNDLE=${CA_PATH}`
      : "# AUTOMATO_ORCHESTRATOR_CA_BUNDLE=C:\\Automato\\pki\\orch-ca.pem",
    "# AUTOMATO_CLIENT_CERT=C:\\Automato\\pki\\agent.crt",
    "# AUTOMATO_CLIENT_KEY=C:\\Automato\\pki\\agent.key",
    "",
    // Sprache der Konsole (0.90.0, ADR 0028 Schritt 4) - Vorgabe = Sprache der Oberflaeche
    L(
      "# --- Sprache der Agent-Konsole (de | en); Meldungen an den Orchestrator bleiben deutsch ---",
      "# --- Language of the agent console (de | en); messages to the orchestrator stay German ---",
    ),
    L("# AUTOMATO_LANG=en", "AUTOMATO_LANG=en"),
    "",
  ].join("\r\n");
}

// Meldung des Agenten in beiden Sprachen: seine Konsole folgt AUTOMATO_LANG in der agent.env
// (0.90.0), unabhängig von der Sprache der Oberfläche
function AgentMsg({ de, en }: { de: string; en: string }) {
  return (
    <td>
      {de}
      {en !== de && (
        <>
          <br />
          <span className="faint">{en}</span>
        </>
      )}
    </td>
  );
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
  const [info, setInfo] = useState<AgentPackageInfo | null>(null);
  const [agentToken, setAgentToken] = useState("");
  // TLS intern (0.78.2): Port 8000 spricht nur HTTPS - die Vorgabe der Adresse folgt
  useEffect(() => {
    fetchAgentPackageInfo()
      .then((i) => {
        setInfo(i);
        if (i.internal_tls) setOrchestratorUrl((u) => u.replace(/^http:\/\//, "https://"));
      })
      .catch(() => setInfo(null));
  }, []);
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
  const env = buildAgentEnv({ orchestratorUrl, agentToken: token, ca: !!info?.ca });
  const plainToTls = !!info?.internal_tls && /^http:\/\//.test(orchestratorUrl.trim());
  const getCa = async () => {
    try {
      download(info?.ca_name ?? "automato-ca.crt", await downloadAgentCa());
    } catch (e) {
      setPkgErr(e);
    }
  };

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
        <h2>{L("Einrichtung eines Agenten (Windows)", "Setting up an agent (Windows)")}</h2>
        <span className="faint" style={{ fontSize: 12 }}>
          {L(
            "ca. 10 Minuten · Admin-PowerShell auf dem Agent-Rechner nötig",
            "approx. 10 minutes · admin PowerShell on the agent machine required",
          )}
        </span>
      </div>
      <ol className="steps">
        <li>
          <h3>{L("Voraussetzungen prüfen", "Check prerequisites")}</h3>
          <ul>
            <li>
              {L(
                "Windows-Rechner im Kundennetz, der die Geräte (FortiGate, Cisco APIC) per HTTPS erreicht.",
                "Windows machine in the customer network that reaches the devices (FortiGate, Cisco APIC) via HTTPS.",
              )}
            </li>
            <li>
              {L(
                <>
                  Pro Gerät <b>read-only Zugangsdaten</b>: FortiGate – REST-API-Token (System → Administratoren → REST API
                  Admin, Profil nur Lesen, Trusted Hosts = Agent-Rechner); APIC – Benutzer mit Nur-Lese-Rolle (z. B.
                  read-all).
                </>,
                <>
                  Per device <b>read-only credentials</b>: FortiGate – REST API token (System → Administrators → REST API
                  Admin, read-only profile, Trusted Hosts = agent machine); APIC – user with a read-only role (e.g.
                  read-all).
                </>,
              )}
            </li>
            <li>
              {L(
                <>
                  Der Rechner erreicht den Orchestrator ausgehend (Standard Port 8000) – Test in PowerShell:{" "}
                  <code>Test-NetConnection {target}</code>
                </>,
                <>
                  The machine reaches the orchestrator outbound (default port 8000) – test in PowerShell:{" "}
                  <code>Test-NetConnection {target}</code>
                </>,
              )}
            </li>
            <li>
              {L(
                <>
                  Python 3.11 oder neuer (python.org, beim Setup <b>„Add python.exe to PATH“</b> anhaken) und
                  Internetzugang für die einmalige Installation der Python-Pakete.
                </>,
                <>
                  Python 3.11 or newer (python.org, tick <b>“Add python.exe to PATH”</b> during setup) and
                  internet access for the one-time installation of the Python packages.
                </>,
              )}
            </li>
          </ul>
        </li>

        <li className={agents.length ? "done" : ""}>
          <h3>{L("Agent anlegen", "Create agent")}</h3>
          <p>
            {L(
              <>
                Mit <b>+ Agent</b> einen Agenten für den Mandanten anlegen. Das Token erscheint nur einmal und wird
                in Schritt 4 automatisch in die <code>agent.env</code> übernommen.
              </>,
              <>
                Use <b>+ Agent</b> to create an agent for the tenant. The token is shown only once and is
                taken over automatically into the <code>agent.env</code> in step 4.
              </>,
            )}
          </p>
          {created ? (
            <span className="chip ok">
              {L(<>Token von „{created.name}“ übernommen</>, <>Token of “{created.name}” taken over</>)}
            </span>
          ) : (
            <button className="btn btn-ghost btn-sm" onClick={onNewAgent}>
              + Agent
            </button>
          )}
        </li>

        <li className={devices.length && !withoutToken.length ? "done" : ""}>
          <h3>{L("Geräte erfassen und dem Agenten zuweisen", "Add devices and assign them to the agent")}</h3>
          <p>
            {L(
              <>
                Unter <b>Mandanten &amp; Standorte → Quellsysteme</b> das Gerät anlegen bzw. bearbeiten: Adresse,
                read-only Zugangsdaten (FortiGate: API-Token, APIC: Benutzer/Passwort und ACI-Tenants), TLS-Prüfung und{" "}
                <b>Verwaltet von</b> = dieser Agent. Die Zugangsdaten werden verschlüsselt im Orchestrator gespeichert und
                nur diesem Agenten für seine Läufe übergeben – auf dem Agent-Rechner liegen keine Geräte-Zugangsdaten. Geräte lassen sich später jederzeit einem anderen Agenten zuweisen.
              </>,
              <>
                Under <b>Tenants &amp; sites → Source systems</b> create or edit the device: address,
                read-only credentials (FortiGate: API token, APIC: user/password and ACI tenants), TLS verification and{" "}
                <b>Managed by</b> = this agent. The credentials are stored encrypted in the orchestrator and
                handed only to this agent for its runs – no device credentials are kept on the agent machine. Devices
                can be assigned to another agent at any time later.
              </>,
            )}
          </p>
          {devices.length > 0 && (
            <p>
              {L("Zugewiesen:", "Assigned:")}{" "}
              {devices.map((d) => (
                <span key={d.id} className={`chip ${d.has_credentials ? "ok" : "warn"}`}>
                  {d.name} → {d.agent}
                  {d.has_credentials ? "" : L(" · ohne Token", " · no token")}
                </span>
              ))}
            </p>
          )}
          <button className="btn btn-ghost btn-sm" onClick={onGoTenants}>
            {L("Zu Mandanten & Standorte", "Go to Tenants & sites")}
          </button>
        </li>

        <li>
          <h3>{L("Konfiguration erzeugen (agent.env)", "Generate configuration (agent.env)")}</h3>
          <p>
            {L(
              <>
                Nur Orchestrator-Adresse und Agent-Token. Die Werte bleiben in diesem Browser – nichts wird gespeichert
                oder an den Server übertragen.
              </>,
              <>
                Only the orchestrator address and agent token. The values stay in this browser – nothing is stored
                or sent to the server.
              </>,
            )}
          </p>
          <p className="faint" id="ag-lang">
            {L(
              <>
                Sprache der Agent-Konsole und des Logs: <code>AUTOMATO_LANG=de</code> (Standard) bzw.{" "}
                <code>AUTOMATO_LANG=en</code>. Was der Agent an Automato meldet, bleibt deutsch gespeichert und
                erscheint in der Oberfläche in der gewählten Sprache.
              </>,
              <>
                Language of the agent console and log: <code>AUTOMATO_LANG=de</code> (default) or{" "}
                <code>AUTOMATO_LANG=en</code>. What the agent reports to Automato is stored in German and shown in
                the user interface in the chosen language.
              </>,
            )}
          </p>
          <div className="envgen">
            <div className="field">
              <label htmlFor="ag-orch">
                {L("Orchestrator-Adresse (vom Agent-Rechner aus)", "Orchestrator address (as seen from the agent machine)")}
              </label>
              <input
                id="ag-orch"
                className="input mono"
                value={orchestratorUrl}
                onChange={(e) => setOrchestratorUrl(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="ag-token">{L("Agent-Token", "Agent token")}</label>
              <SecretInput
                id="ag-token"
                value={token}
                onChange={setAgentToken}
                placeholder={L("aus Schritt 2 (oder einfügen)", "from step 2 (or paste)")}
              />
            </div>
          </div>
          {info?.internal_tls && (
            <Note>
              <span id="ag-tls">
                {L(
                  <>
                    TLS intern ist eingeschaltet: direkt auf Port 8000 nur mit <code>https://</code> und der CA{" "}
                    <code>{info.ca_name}</code> (liegt im Agent-Paket, <code>install.ps1</code> legt sie nach{" "}
                    <code>{CA_PATH}</code>). Bestehende Agenten: in <code>{INSTALL_DIR}\agent.env</code> die Adresse auf{" "}
                    <code>https://</code> ändern, <code>AUTOMATO_ORCHESTRATOR_CA_BUNDLE={CA_PATH}</code> ergänzen und die CA dorthin kopieren.{" "}
                  </>,
                  <>
                    Internal TLS is switched on: directly on port 8000 only with <code>https://</code> and the CA{" "}
                    <code>{info.ca_name}</code> (included in the agent package, <code>install.ps1</code> puts it at{" "}
                    <code>{CA_PATH}</code>). Existing agents: in <code>{INSTALL_DIR}\agent.env</code> change the address
                    to <code>https://</code>, add <code>AUTOMATO_ORCHESTRATOR_CA_BUNDLE={CA_PATH}</code> and copy the CA
                    there.{" "}
                  </>,
                )}
                {info.ca && (
                  <button className="btn btn-ghost btn-sm" id="ag-ca" onClick={getCa}>
                    <Icon name="download" size={14} /> {L("CA-Zertifikat", "CA certificate")}
                  </button>
                )}
              </span>
            </Note>
          )}
          {plainToTls && (
            <Note warn>
              {L(
                <>
                  Mit <code>http://</code> beendet der Orchestrator die Verbindung ohne Antwort („Server disconnected without sending a
                  response“).
                </>,
                <>
                  With <code>http://</code> the orchestrator closes the connection without a reply (“Server disconnected without
                  sending a response”).
                </>,
              )}
            </Note>
          )}
          <div className="cmd">
            <pre>{mask(env)}</pre>
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn btn-primary btn-sm" onClick={() => download("agent.env", env, "text/plain")}>
              <Icon name="download" size={14} /> {L("agent.env herunterladen", "Download agent.env")}
            </button>
            {!token && <span className="faint">{L("Noch offen: Agent-Token", "Still missing: agent token")}</span>}
          </div>
        </li>

        <li>
          <h3>{L("Agent-Paket herunterladen", "Download agent package")}</h3>
          <p>
            {L(
              <>
                ZIP mit dem Collector (Python-Paket), <code>install.ps1</code> und <code>run-agent.ps1</code> – ohne
                Zugangsdaten.
              </>,
              <>
                ZIP with the collector (Python package), <code>install.ps1</code> and <code>run-agent.ps1</code> – without
                credentials.
              </>,
            )}
          </p>
          <ErrorBox error={pkgErr} prefix={L("Paket", "Package")} />
          <button className="btn btn-ghost btn-sm" onClick={getPackage} disabled={pkgBusy}>
            <Icon name="download" size={14} />{" "}
            {pkgBusy ? L("Lade…", "Loading…") : L("Agent-Paket (ZIP) herunterladen", "Download agent package (ZIP)")}
          </button>
        </li>

        <li>
          <h3>{L("Auf dem Agent-Rechner installieren", "Install on the agent machine")}</h3>
          <p>
            {L(
              <>
                ZIP entpacken, die <code>agent.env</code> in den entpackten Ordner{" "}
                <code>automato-collector-agent</code> legen, dann eine <b>PowerShell als Administrator</b> öffnen:
              </>,
              <>
                Unpack the ZIP, put the <code>agent.env</code> into the unpacked folder{" "}
                <code>automato-collector-agent</code>, then open a <b>PowerShell as administrator</b>:
              </>,
            )}
          </p>
          <Cmd>{`cd $HOME\\Downloads\\automato-collector-agent
Get-ChildItem | Unblock-File
powershell -ExecutionPolicy Bypass -File .\\install.ps1 -IntervalMinutes 15`}</Cmd>
          <p>
            {L(
              <>
                Installiert nach <code>{INSTALL_DIR}</code> und legt die geplante Aufgabe <b>„Automato Collector
                Agent“</b> an: Start alle 15 Minuten (inkl. Prüfung auf ein Selbst-Update), dazwischen fragt der Agent
                alle 20 Sekunden nach neuen Läufen (<code>-WatchSeconds</code>). Ändern der Konfiguration später:{" "}
                <code>{INSTALL_DIR}\agent.env</code>.
              </>,
              <>
                Installs to <code>{INSTALL_DIR}</code> and creates the scheduled task <b>“Automato Collector
                Agent”</b>: start every 15 minutes (including a check for a self-update), in between the agent asks
                for new runs every 20 seconds (<code>-WatchSeconds</code>). To change the configuration later:{" "}
                <code>{INSTALL_DIR}\agent.env</code>.
              </>,
            )}
          </p>
        </li>

        <li>
          <h3>{L("Verbindung zu den Geräten testen", "Test the connection to the devices")}</h3>
          <Cmd>{`powershell -ExecutionPolicy Bypass -File ${INSTALL_DIR}\\run-agent.ps1 -Check`}</Cmd>
          <p>
            {L(
              <>
                Prüft alle zugewiesenen Geräte, eine Zeile je Gerät – erwartet z. B.{" "}
                <code>fw-01 (fortigate) check: OK - erreichbar: https://…</code> bzw.{" "}
                <code>apic-01 (cisco_aci) check: OK - erreichbar: https://… (5 Tenants sichtbar)</code>. Rein lesend, es
                wird nichts übertragen.
              </>,
              <>
                Checks all assigned devices, one line per device – expected e.g.{" "}
                <code>fw-01 (fortigate) check: OK - reachable: https://…</code> or{" "}
                <code>apic-01 (cisco_aci) check: OK - reachable: https://… (5 tenants visible)</code> (with{" "}
                <code>AUTOMATO_LANG=en</code>, otherwise German). Read-only, nothing is transferred.
              </>,
            )}
          </p>
        </li>

        <li>
          <h3>{L("Ersten Lauf starten", "Start the first run")}</h3>
          <p>
            {L(
              <>
                Übersicht → <b>Jetzt einlesen</b> → Mandant und Quellsystem wählen → Lauf starten (die Ausführung über
                den Agenten ergibt sich aus der Zuweisung). Der laufende Agent holt den Lauf innert rund 20 Sekunden ab.
                Von Hand (z. B. direkt nach der Installation) einmal abholen und beenden:
              </>,
              <>
                Overview → <b>Collect now</b> → choose tenant and source system → start run (execution via the
                agent follows from the assignment). The running agent picks up the run within about 20 seconds.
                By hand (e.g. right after the installation) fetch once and exit:
              </>,
            )}
          </p>
          <Cmd>{`powershell -ExecutionPolicy Bypass -File ${INSTALL_DIR}\\run-agent.ps1 -Once`}</Cmd>
          <p>
            {L(
              <>
                Erwartet: <code>fortigate run … (fw-01): completed</code>. In „Letzte Läufe“ steht dann <b>OK</b>, hier ist der
                Agent <b>online</b>, und die Regeln erscheinen unter Firewall-Regeln.
              </>,
              <>
                Expected: <code>fortigate run … (fw-01): completed</code>. “Recent runs” then shows <b>OK</b>, here the
                agent is <b>online</b>, and the rules appear under firewall rules.
              </>,
            )}
          </p>
        </li>

        <li>
          <h3>{L("Wenn etwas nicht klappt", "If something does not work")}</h3>
          <table className="trouble">
            <tbody>
              <tr>
                <AgentMsg de="keine offenen Laeufe" en="no open runs" />
                <td>
                  {L(
                    "Noch kein Lauf gestartet (Schritt 8) – oder das Gerät ist einem anderen Agenten zugewiesen.",
                    "No run started yet (step 8) – or the device is assigned to another agent.",
                  )}
                </td>
              </tr>
              <tr>
                <AgentMsg de="keine Geraete zugewiesen" en="no devices are assigned" />
                <td>
                  {L(
                    "Beim Quellsystem „Verwaltet von“ auf diesen Agenten setzen (Schritt 3).",
                    "Set “Managed by” of the source system to this agent (step 3).",
                  )}
                </td>
              </tr>
              <tr>
                <AgentMsg de="keine Zugangsdaten fuer „fw-01“" en="no credentials for “fw-01”" />
                <td>
                  {L(
                    "Beim Quellsystem die Zugangsdaten hinterlegen (Bearbeiten → API-Token bzw. APIC-Benutzer/Passwort).",
                    "Store the credentials at the source system (Edit → API token or APIC user/password).",
                  )}
                </td>
              </tr>
              <tr>
                <AgentMsg de="401 invalid agent token" en="401 invalid agent token" />
                <td>
                  {L(
                    "Agent-Token falsch oder Agent deaktiviert – ggf. neuen Agenten anlegen.",
                    "Agent token wrong or agent deactivated – create a new agent if needed.",
                  )}
                </td>
              </tr>
              <tr>
                <AgentMsg de="Zugangsdaten nicht lesbar" en="Credentials not readable" />
                <td>
                  {L(
                    <>
                      Der Schlüssel des Orchestrators hat sich geändert (Schlüsseldatei/AUTOMATO_CREDENTIALS_KEY) – Token
                      beim Quellsystem neu hinterlegen.
                    </>,
                    <>
                      The orchestrator's key has changed (key file/AUTOMATO_CREDENTIALS_KEY) – store the token at the
                      source system again.
                    </>,
                  )}
                </td>
              </tr>
              <tr>
                <AgentMsg de="Verbindung abgelehnt / Timeout" en="connection refused / timeout" />
                <td>
                  {L(
                    "Port 8000 vom Agent-Rechner zum Orchestrator in der Firewall freigeben; Adresse prüfen.",
                    "Allow port 8000 from the agent machine to the orchestrator in the firewall; check the address.",
                  )}
                </td>
              </tr>
              <tr>
                <AgentMsg de="check: FEHLER … certificate" en="check: ERROR … certificate" />
                <td>
                  {L(
                    "Selbstsigniertes Geräte-Zertifikat: beim Quellsystem „TLS-Zertifikat prüfen“ ausschalten (nur Lab).",
                    "Self-signed device certificate: switch off “Verify the device's TLS certificate” at the source system (lab only).",
                  )}
                </td>
              </tr>
              <tr>
                <AgentMsg de="check: FEHLER … 401/403" en="check: ERROR … 401/403" />
                <td>
                  {L(
                    <>
                      Zugangsdaten beim Quellsystem falsch – FortiGate: Token oder Trusted Hosts des REST-API-Admins; APIC:
                      Benutzer/Passwort oder Login-Domäne (<code>apic#Domäne\Benutzer</code>).
                    </>,
                    <>
                      Credentials at the source system wrong – FortiGate: token or Trusted Hosts of the REST API admin; APIC:
                      user/password or login domain (<code>apic#Domain\User</code>).
                    </>,
                  )}
                </td>
              </tr>
              <tr>
                <AgentMsg de="ACI-Tenant nicht gefunden" en="ACI tenant not found" />
                <td>
                  {L(
                    "Name unter „ACI-Tenants“ beim Quellsystem prüfen (Gross-/Kleinschreibung) oder das Feld leeren.",
                    "Check the name under “ACI tenants” at the source system (upper/lower case) or empty the field.",
                  )}
                </td>
              </tr>
            </tbody>
          </table>
          <Note>
            {L(
              <>
                Produktiv den Orchestrator per HTTPS/mTLS ansprechen (TLS-Zeilen in der agent.env, siehe{" "}
                <code>collectors/EXTERNAL.md</code>).
              </>,
              <>
                In production, address the orchestrator via HTTPS/mTLS (TLS lines in the agent.env, see{" "}
                <code>collectors/EXTERNAL.md</code>).
              </>,
            )}
          </Note>
        </li>
      </ol>
    </div>
  );
}
