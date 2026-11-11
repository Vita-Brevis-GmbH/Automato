import { ReactNode, useEffect, useState } from "react";
import {
  Agent,
  AgentCaState,
  AgentCreated,
  AgentPackageInfo,
  ApiError,
  downloadAgentAccess,
  downloadAgentPackage,
  fetchAgentPackageInfo,
} from "./api";
import { download, ErrorBox, Icon, Note } from "./ui";
import { L } from "./i18n";

// Einrichtung des Collector-Agenten (ADR 0008/0009) Schritt für Schritt. agent.env, Zertifikat,
// Schlüssel und CA kommen je Agent als „Zugang“ vom Orchestrator (1.6.0, ADR 0052): ein ZIP mit
// neuem Token; Geräte und ihre Zugangsdaten bleiben beim Mandanten (Quellsysteme).

const INSTALL_DIR = "C:\\Automato\\collector";
// Namen, die im Subject des Zertifikats unverändert ankommen (orchestrator agent_access.CERT_NAME)
export const CERT_NAME = /^[A-Za-z0-9][A-Za-z0-9 ._()-]*$/;

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

// Warum kein Zugang möglich ist - aus /agents/package/info (agent_ca) bzw. der Ablehnung (409/422)
export function accessProblem(reason: AgentCaState | string | null | undefined): ReactNode | null {
  switch (reason) {
    case "ca_missing":
      return L(
        <>
          Die Agent-CA fehlt noch. Auf der Automato-VM einmal:{" "}
          <code>cd deploy/agent-gateway &amp;&amp; sudo ./make-certs.sh ca</code> und{" "}
          <code>sudo ./make-certs.sh server &lt;Name des Gateways&gt;</code>, danach <code>sudo docker compose up -d</code>.
        </>,
        <>
          The agent CA is still missing. On the Automato VM once:{" "}
          <code>cd deploy/agent-gateway &amp;&amp; sudo ./make-certs.sh ca</code> and{" "}
          <code>sudo ./make-certs.sh server &lt;gateway name&gt;</code>, then <code>sudo docker compose up -d</code>.
        </>,
      );
    case "ca_denied":
      return L(
        <>
          Die Agent-CA ist für die Oberfläche noch nicht freigegeben. Auf der Automato-VM einmal:{" "}
          <code>sudo deploy/agent-gateway/make-certs.sh oberflaeche</code> und <code>sudo docker compose up -d</code>.
        </>,
        <>
          The agent CA is not yet released for the web UI. On the Automato VM once:{" "}
          <code>sudo deploy/agent-gateway/make-certs.sh oberflaeche</code> and <code>sudo docker compose up -d</code>.
        </>,
      );
    case "ca_invalid":
      return L(
        <>
          Die Agent-CA ist nicht lesbar, abgelaufen oder ihr Schlüssel passt nicht zum Zertifikat (
          <code>deploy/agent-gateway/pki</code> auf der VM prüfen).
        </>,
        <>
          The agent CA cannot be read, has expired or its key does not match the certificate (check{" "}
          <code>deploy/agent-gateway/pki</code> on the VM).
        </>,
      );
    case "name":
      return L(
        "Der Name des Agenten eignet sich nicht für ein Zertifikat (erlaubt: A–Z, a–z, 0–9, Leerzeichen, . _ - ( ) ) – Agent unter neuem Namen anlegen.",
        "The agent's name is not usable in a certificate (allowed: A–Z, a–z, 0–9, space, . _ - ( ) ) – create the agent under a new name.",
      );
    case "gateway_host":
      return L(
        <>
          In der <code>.env</code> der VM fehlt <code>AUTOMATO_AGENT_GATEWAY_HOST</code> (Name im Zertifikat des
          Gateways), danach <code>sudo docker compose up -d</code>.
        </>,
        <>
          <code>AUTOMATO_AGENT_GATEWAY_HOST</code> (name in the gateway's certificate) is missing in the VM's{" "}
          <code>.env</code>, then <code>sudo docker compose up -d</code>.
        </>,
      );
    case "url":
      return L(
        "Die Adresse muss http(s)://Name[:Port] sein.",
        "The address must be http(s)://name[:port].",
      );
    default:
      return null;
  }
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

export default function AgentGuide({
  agents,
  created,
  accessFor,
  onNewAgent,
  onGoTenants,
}: {
  agents: Agent[];
  created: AgentCreated | null;
  accessFor: number | null; // „Zugang“ in der Liste gewählt
  onNewAgent: () => void;
  onGoTenants: () => void;
}) {
  const [orchestratorUrl, setOrchestratorUrl] = useState(`http://${window.location.hostname}:8000`);
  const [info, setInfo] = useState<AgentPackageInfo | null>(null);
  const [chosen, setChosen] = useState<number | null>(null);
  // TLS intern (0.78.2): Port 8000 spricht nur HTTPS - die Vorgabe der Adresse folgt
  useEffect(() => {
    fetchAgentPackageInfo()
      .then((i) => {
        setInfo(i);
        if (i.internal_tls) setOrchestratorUrl((u) => u.replace(/^http:\/\//, "https://"));
      })
      .catch(() => setInfo(null));
  }, []);
  useEffect(() => {
    if (accessFor != null) setChosen(accessFor);
  }, [accessFor]);
  const [pkgBusy, setPkgBusy] = useState(false);
  const [pkgErr, setPkgErr] = useState<unknown>(null);
  const [accBusy, setAccBusy] = useState(false);
  const [accErr, setAccErr] = useState<unknown>(null);
  const [accDone, setAccDone] = useState<string | null>(null);

  // mTLS-Agent-Gateway (1.5.1): Agenten nur darüber - die Adresse kommt vom Orchestrator
  const gateway = info?.gateway_url ?? "";
  const url = gateway || orchestratorUrl;
  let target = `${window.location.hostname} -Port 8000`;
  try {
    const u = new URL(url);
    target = `${u.hostname} -Port ${u.port || (u.protocol === "https:" ? "443" : "80")}`;
  } catch {
    /* noch keine gültige URL */
  }
  const agentId = chosen ?? created?.id ?? (agents.length === 1 ? agents[0].id : null);
  const agent = agents.find((a) => a.id === agentId) ?? null;
  const caState = info?.agent_ca ?? null;
  const nameBad = !!gateway && !!agent && !CERT_NAME.test(agent.name);
  const blocked = (!!gateway && caState !== "ok") || nameBad;
  const plainToTls = !gateway && !!info?.internal_tls && /^http:\/\//.test(orchestratorUrl.trim());

  const getAccess = async () => {
    if (!agent) return;
    if (
      !window.confirm(
        L(
          `Zugang für „${agent.name}“ erzeugen? Das bisherige Token dieses Agenten gilt danach nicht mehr – ein laufender Agent verbindet sich erst wieder mit den neuen Dateien.`,
          `Generate access for “${agent.name}”? The agent's previous token stops working – a running agent connects again only with the new files.`,
        ),
      )
    )
      return;
    setAccBusy(true);
    setAccErr(null);
    setAccDone(null);
    try {
      const { blob, filename } = await downloadAgentAccess(agent.id, gateway ? "" : orchestratorUrl);
      download(filename, blob);
      setAccDone(agent.name);
    } catch (e) {
      setAccErr(e);
    } finally {
      setAccBusy(false);
    }
  };
  const accReason = accErr instanceof ApiError ? accessProblem(accErr.reason) : null;

  // Zugewiesene Geräte: des gewählten Agenten, sonst aller Agenten
  const relevant = agent ? [agent] : agents;
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
                  Der Rechner erreicht den Orchestrator ausgehend ({gateway ? "Port des Agent-Gateways" : "Standard Port 8000"})
                  – Test in PowerShell: <code>Test-NetConnection {target}</code>
                </>,
                <>
                  The machine reaches the orchestrator outbound ({gateway ? "port of the agent gateway" : "default port 8000"})
                  – test in PowerShell: <code>Test-NetConnection {target}</code>
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
                Mit <b>+ Agent</b> einen Agenten für den Mandanten anlegen. Token und Zertifikat kommen in Schritt 4 mit
                dem Zugang.
              </>,
              <>
                Use <b>+ Agent</b> to create an agent for the tenant. Token and certificate come with the access in
                step 4.
              </>,
            )}
          </p>
          {created ? (
            <span className="chip ok">
              {L(<>„{created.name}“ angelegt</>, <>“{created.name}” created</>)}
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

        <li id="ag-access" className={accDone ? "done" : ""}>
          <h3>{L("Zugang herunterladen (agent.env und Zertifikat)", "Download access (agent.env and certificate)")}</h3>
          <p>
            {L(
              <>
                Ein ZIP je Agent: <code>agent.env</code> mit neuem Token
                {gateway && (
                  <>
                    {" "}
                    sowie Zertifikat <code>agent.crt</code>, Schlüssel <code>agent.key</code> und <code>agents-ca.crt</code>{" "}
                    für das Agent-Gateway
                  </>
                )}
                . Das bisherige Token des Agenten gilt danach nicht mehr; gespeichert wird davon nichts.
              </>,
              <>
                One ZIP per agent: <code>agent.env</code> with a new token
                {gateway && (
                  <>
                    {" "}
                    plus certificate <code>agent.crt</code>, key <code>agent.key</code> and <code>agents-ca.crt</code> for
                    the agent gateway
                  </>
                )}
                . The agent's previous token stops working; nothing of it is stored.
              </>,
            )}
          </p>
          <div className="envgen">
            <div className="field">
              <label htmlFor="ag-agent">Agent</label>
              <select
                id="ag-agent"
                className="sel input"
                value={agentId ?? ""}
                onChange={(e) => setChosen(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">{L("– Agent wählen –", "– choose agent –")}</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>
            {gateway ? (
              <div className="field">
                <label>{L("Adresse (mTLS-Agent-Gateway)", "Address (mTLS agent gateway)")}</label>
                <code id="ag-gateway-url" className="mono">
                  {gateway}
                </code>
              </div>
            ) : (
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
            )}
          </div>
          {gateway && caState && caState !== "ok" && (
            <Note warn>
              <span id="ag-ca-state">{accessProblem(caState)}</span>
            </Note>
          )}
          {nameBad && (
            <Note warn>
              <span id="ag-name-bad">{accessProblem("name")}</span>
            </Note>
          )}
          {plainToTls && (
            <Note warn>
              {L(
                <>
                  TLS intern ist eingeschaltet: mit <code>http://</code> beendet der Orchestrator die Verbindung ohne Antwort
                  („Server disconnected without sending a response“) – Adresse mit <code>https://</code> angeben.
                </>,
                <>
                  Internal TLS is switched on: with <code>http://</code> the orchestrator closes the connection without a
                  reply (“Server disconnected without sending a response”) – use <code>https://</code>.
                </>,
              )}
            </Note>
          )}
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button
              id="ag-access-btn"
              className="btn btn-primary btn-sm"
              onClick={getAccess}
              disabled={!agent || accBusy || blocked}
            >
              {accBusy ? L("Erzeuge…", "Generating…") : L("Zugang herunterladen", "Download access")}
            </button>
            {!agent && <span className="faint">{L("Noch offen: Agent wählen", "Still missing: choose agent")}</span>}
            {accDone && (
              <span className="chip ok" id="ag-access-done">
                {L(<>Zugang für „{accDone}“ heruntergeladen</>, <>Access for “{accDone}” downloaded</>)}
              </span>
            )}
          </div>
          {accErr != null && (accReason ? <Note warn>{accReason}</Note> : <ErrorBox error={accErr} prefix={L("Zugang", "Access")} />)}
          <p>
            {L(
              <>
                <b>Bestehender Agent:</b> alle Dateien aus dem ZIP nach <code>{INSTALL_DIR}</code> kopieren (ersetzt{" "}
                <code>agent.env</code>), dann prüfen (Schritt 7). <b>Neue Installation:</b> die Dateien in den Ordner des
                Agent-Pakets legen (Schritt 6). Die Sprache der Agent-Konsole folgt der Sprache dieser Oberfläche (
                <code>AUTOMATO_LANG</code> in der <code>agent.env</code>).
              </>,
              <>
                <b>Existing agent:</b> copy all files from the ZIP to <code>{INSTALL_DIR}</code> (replaces{" "}
                <code>agent.env</code>), then check (step 7). <b>New installation:</b> put the files into the folder of the
                agent package (step 6). The language of the agent console follows the language of this interface (
                <code>AUTOMATO_LANG</code> in the <code>agent.env</code>).
              </>,
            )}
          </p>
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
                ZIP entpacken, die Dateien aus dem Zugang (Schritt 4) in den entpackten Ordner{" "}
                <code>automato-collector-agent</code> legen, dann eine <b>PowerShell als Administrator</b> öffnen:
              </>,
              <>
                Unpack the ZIP, put the files from the access (step 4) into the unpacked folder{" "}
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
                Installiert nach <code>{INSTALL_DIR}</code> (samt <code>agent.env</code> und Zertifikat) und legt die
                geplante Aufgabe <b>„Automato Collector Agent“</b> an: Start alle 15 Minuten (inkl. Prüfung auf ein Selbst-Update), dazwischen fragt der Agent
                alle 20 Sekunden nach neuen Läufen (<code>-WatchSeconds</code>). Ändern der Konfiguration später:{" "}
                <code>{INSTALL_DIR}\agent.env</code>.
              </>,
              <>
                Installs to <code>{INSTALL_DIR}</code> (including <code>agent.env</code> and certificate) and creates the
                scheduled task <b>“Automato Collector Agent”</b>: start every 15 minutes (including a check for a self-update), in between the agent asks
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
                    "Token nicht mehr gültig (neuer Zugang heruntergeladen) oder Agent deaktiviert – Zugang neu herunterladen (Schritt 4).",
                    "Token no longer valid (new access downloaded) or agent deactivated – download the access again (step 4).",
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
                  {gateway
                    ? L(
                        "Port des Agent-Gateways vom Agent-Rechner zur VM freigeben (Firewall davor und DOCKER-USER); der Name der Adresse muss auf die VM zeigen.",
                        "Allow the agent gateway's port from the agent machine to the VM (firewall in front and DOCKER-USER); the address name must point to the VM.",
                      )
                    : L(
                        "Port 8000 vom Agent-Rechner zum Orchestrator in der Firewall freigeben; Adresse prüfen.",
                        "Allow port 8000 from the agent machine to the orchestrator in the firewall; check the address.",
                      )}
                </td>
              </tr>
              <tr>
                <AgentMsg de="403 agent channel requires the mTLS gateway" en="403 agent channel requires the mTLS gateway" />
                <td>
                  {L(
                    "Der Agent geht direkt auf Port 8000 – Zugang neu herunterladen: die agent.env zeigt dann auf das Gateway.",
                    "The agent goes directly to port 8000 – download the access again: the agent.env then points to the gateway.",
                  )}
                </td>
              </tr>
              <tr>
                <AgentMsg de="certificate required / unknown ca" en="certificate required / unknown ca" />
                <td>
                  {L(
                    <>
                      Zertifikat fehlt oder stammt nicht von der Agent-CA: <code>agent.crt</code>, <code>agent.key</code> und{" "}
                      <code>agents-ca.crt</code> aus demselben Zugang nach <code>{INSTALL_DIR}</code> kopieren.
                    </>,
                    <>
                      Certificate missing or not from the agent CA: copy <code>agent.crt</code>, <code>agent.key</code> and{" "}
                      <code>agents-ca.crt</code> from the same access to <code>{INSTALL_DIR}</code>.
                    </>,
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
        </li>
      </ol>
    </div>
  );
}
