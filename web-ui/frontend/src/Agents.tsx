import { useEffect, useState } from "react";
import { Agent, AgentCreated, createAgent, fetchAgents, setAgentAutoUpdate, setAgentEnabled, Tenant } from "./api";
import AgentGuide from "./AgentGuide";
import type { View } from "./App";
import { Empty, ErrorBox, fmtTime, Icon, Loading, Modal, Note, PageHead } from "./ui";
import { L } from "./i18n";

// Agent-Verwaltung (ADR 0008/0009): externe Collector-Agenten anlegen/deaktivieren, Heartbeat
// (last_seen) und zugewiesene Geräte sehen. Geräte + Zugangsdaten werden beim Mandanten
// (Quellsysteme) erfasst und dort einem Agenten zugewiesen. Nur Admin (serverseitig erzwungen).
const ONLINE_MS = 15 * 60 * 1000;

function onlineState(a: Agent): [string, string, string] {
  if (!a.enabled) return ["na", "n", L("deaktiviert", "disabled")];
  if (!a.last_seen) return ["warn", "y", L("noch nie verbunden", "never connected")];
  return Date.now() - new Date(a.last_seen).getTime() < ONLINE_MS
    ? ["ok", "g", "online"]
    : ["warn", "y", L("keine Meldung", "no report")];
}

export default function Agents({
  tenants,
  tenantId,
  onGo,
}: {
  tenants: Tenant[];
  tenantId: number | null;
  onGo: (v: View) => void;
}) {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [created, setCreated] = useState<AgentCreated | null>(null);
  // „Zugang“ in der Liste (1.6.0): Agent in der Einrichtung wählen und hinscrollen
  const [accessFor, setAccessFor] = useState<number | null>(null);
  const [err, setErr] = useState<unknown>(null);

  const load = () =>
    fetchAgents()
      .then(setAgents)
      .catch((e) => {
        setErr(e);
        setAgents([]);
      });

  useEffect(() => {
    load();
  }, []);

  const tenantName = (id: number | null) =>
    id == null ? L("alle Mandanten", "all tenants") : (tenants.find((t) => t.id === id)?.name ?? `#${id}`);

  const toggle = async (a: Agent) => {
    if (
      a.enabled &&
      !window.confirm(
        L(
          `Agent „${a.name}“ deaktivieren? Sein Token wird sofort abgelehnt.`,
          `Disable agent “${a.name}”? Its token will be rejected immediately.`,
        ),
      )
    )
      return;
    setErr(null);
    try {
      await setAgentEnabled(a.id, !a.enabled);
      await load();
    } catch (e) {
      setErr(e);
    }
  };

  const toggleUpdate = async (a: Agent) => {
    setErr(null);
    try {
      await setAgentAutoUpdate(a.id, !a.auto_update);
      await load();
    } catch (e) {
      setErr(e);
    }
  };

  const access = (a: Agent) => {
    setAccessFor(a.id);
    document.getElementById("ag-access")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <>
      <PageHead
        title={L("Collector-Agenten", "Collector agents")}
        actions={
          <button className="btn btn-primary btn-sm" onClick={() => setNewOpen(true)}>
            + Agent
          </button>
        }
      >
        {L(
          "Agenten laufen nahe an den Geräten im Kundennetz (z. B. Windows) und sprechen nur ausgehend HTTPS mit dem Orchestrator. Welche Geräte ein Agent liest, legst du beim Mandanten unter Quellsysteme fest („Verwaltet von“) – die Zugangsdaten bekommt er von dort pro Lauf.",
          "Agents run close to the devices in the customer network (e.g. Windows) and talk to the orchestrator only via outbound HTTPS. Which devices an agent reads is set on the tenant under Source systems (“Managed by”) – it receives the credentials from there for each run.",
        )}
      </PageHead>

      <ErrorBox error={err} onClose={() => setErr(null)} />

      {created && (
        <Note>
          <span id="agent-created">
            {L(
              <>
                Agent „{created.name}“ angelegt. Weiter unten in der Einrichtung die Geräte zuweisen und den{" "}
                <b>Zugang</b> herunterladen (agent.env mit Token und – mit Agent-Gateway – Zertifikat).
              </>,
              <>
                Agent “{created.name}” created. Further down in the setup assign the devices and download the{" "}
                <b>access</b> (agent.env with token and – with agent gateway – certificate).
              </>,
            )}{" "}
            <button className="linkbtn" onClick={() => access(created)}>
              {L("Zum Zugang", "Go to access")}
            </button>
          </span>
        </Note>
      )}

      <div className="card">
        <div className="card-head">
          <h2>{L("Registrierte Agenten", "Registered agents")}</h2>
          <div className="spacer" />
          <button className="btn btn-ghost btn-sm" onClick={load}>
            {L("Aktualisieren", "Refresh")}
          </button>
        </div>
        <div className="card-body scroll">
          {agents === null ? (
            <Loading />
          ) : agents.length === 0 ? (
            <Empty
              title={L("Noch keine Agenten", "No agents yet")}
              action={
                <button className="btn btn-primary btn-sm" onClick={() => setNewOpen(true)}>
                  {L("+ Agent anlegen", "+ Create agent")}
                </button>
              }
            >
              {L(
                "Die Einrichtung ist unten Schritt für Schritt beschrieben.",
                "The setup is described step by step below.",
              )}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>{L("Mandant", "Tenant")}</th>
                  <th>{L("Geräte", "Devices")}</th>
                  <th>Status</th>
                  <th>{L("Zuletzt gesehen", "Last seen")}</th>
                  <th
                    title={L(
                      "Paket-Version des Agenten; mit Auto-Update aktualisiert er sich selbst",
                      "Package version of the agent; with auto-update it updates itself",
                    )}
                  >
                    Version
                  </th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {agents.map((a) => {
                  const [cls, dot, label] = onlineState(a);
                  return (
                    <tr key={a.id}>
                      <td>
                        <b>{a.name}</b>
                      </td>
                      <td>{tenantName(a.tenant_id)}</td>
                      <td>
                        {a.systems.length === 0 ? (
                          <span className="faint">{L("keine zugewiesen", "none assigned")}</span>
                        ) : (
                          a.systems.map((d) => (
                            <span
                              key={d.id}
                              className={`chip ${d.has_credentials ? "" : "warn"}`}
                              title={
                                d.has_credentials
                                  ? L("Zugangsdaten hinterlegt", "Credentials stored")
                                  : L("keine Zugangsdaten hinterlegt", "no credentials stored")
                              }
                            >
                              {d.name}
                              {d.has_credentials ? "" : L(" · ohne Token", " · without token")}
                            </span>
                          ))
                        )}
                      </td>
                      <td>
                        <span className={`chip ${cls}`}>
                          <span className={`dot ${dot}`} />
                          {label}
                        </span>
                      </td>
                      <td className="mono">{fmtTime(a.last_seen)}</td>
                      <td className="nowrap">
                        {a.version ? (
                          <span className="mono" title={a.version}>
                            {a.version.includes("+")
                              ? `${a.version.split("+")[0]} · Build ${a.version.split("+")[1]}`
                              : a.version.slice(0, 8)}
                          </span>
                        ) : (
                          <span className="faint">–</span>
                        )}{" "}
                        {a.update_available === true && (
                          <span
                            className={`chip ${a.auto_update ? "info" : "warn"}`}
                            title={
                              a.auto_update
                                ? L(
                                    "Neue Version verfügbar – der Agent aktualisiert sich beim nächsten Aufruf selbst",
                                    "New version available – the agent updates itself on its next call",
                                  )
                                : L(
                                    "Neue Version verfügbar – Auto-Update aus: Paket manuell installieren",
                                    "New version available – auto-update off: install the package manually",
                                  )
                            }
                          >
                            {a.auto_update
                              ? L("Update beim nächsten Lauf", "Update on next run")
                              : L("veraltet", "outdated")}
                          </span>
                        )}
                        {a.update_available === false && <span className="chip ok">{L("aktuell", "up to date")}</span>}
                        {a.update_note && (
                          <div className="run-msg err" title={a.update_note}>
                            {a.update_note}
                          </div>
                        )}
                        <label className="check" style={{ marginTop: 4, fontSize: 12 }}>
                          <input type="checkbox" checked={a.auto_update} onChange={() => toggleUpdate(a)} />
                          Auto-Update
                        </label>
                      </td>
                      <td className="actions">
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => access(a)}
                          title={L(
                            "agent.env und Zertifikat für diesen Agenten herunterladen (neues Token)",
                            "Download agent.env and certificate for this agent (new token)",
                          )}
                        >
                          {L("Zugang", "Access")}
                        </button>{" "}
                        <button className={`btn btn-sm ${a.enabled ? "btn-danger" : "btn-ghost"}`} onClick={() => toggle(a)}>
                          {a.enabled ? L("Deaktivieren", "Disable") : L("Aktivieren", "Enable")}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <AgentGuide
        agents={agents ?? []}
        created={created}
        accessFor={accessFor}
        onNewAgent={() => setNewOpen(true)}
        onGoTenants={() => onGo("tenants")}
      />

      {newOpen && (
        <NewAgentDialog
          tenants={tenants}
          defaultTenantId={tenantId ?? tenants[0]?.id ?? null}
          onClose={() => setNewOpen(false)}
          onCreated={async (a) => {
            setNewOpen(false);
            setCreated(a);
            await load();
          }}
        />
      )}
    </>
  );
}

function NewAgentDialog({
  tenants,
  defaultTenantId,
  onClose,
  onCreated,
}: {
  tenants: Tenant[];
  defaultTenantId: number | null;
  onClose: () => void;
  onCreated: (a: AgentCreated) => void;
}) {
  const [name, setName] = useState("");
  const [tenantId, setTenantId] = useState<number | null>(defaultTenantId);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const submit = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      onCreated(await createAgent(name.trim(), tenantId));
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  };

  return (
    <Modal
      title={L("Neuer Collector-Agent", "New collector agent")}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={busy || !name.trim()}>
            {busy ? L("Lege an…", "Creating…") : L("Anlegen", "Create")}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="field">
        <label htmlFor="na-name">Name</label>
        <input
          id="na-name"
          className="input"
          value={name}
          autoFocus
          placeholder={L("z. B. agent-win-01", "e.g. agent-win-01")}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
      </div>
      <div className="field">
        <label htmlFor="na-tenant">{L("Mandant", "Tenant")}</label>
        <select
          id="na-tenant"
          className="sel input"
          value={tenantId ?? ""}
          onChange={(e) => setTenantId(e.target.value ? Number(e.target.value) : null)}
        >
          {tenants.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
          <option value="">{L("alle Mandanten", "all tenants")}</option>
        </select>
        <div className="hint">
          {L(
            "Der Agent kann Geräte dieses Mandanten verwalten. Zuweisung danach beim Mandanten unter Quellsysteme.",
            "The agent can manage devices of this tenant. Assign them afterwards on the tenant under Source systems.",
          )}
        </div>
      </div>
      <Note>
        <span>
          <Icon name="lock" size={13} />{" "}
          {L(
            "Token und Zertifikat kommen mit dem Zugang (Einrichtung, Schritt 4); gespeichert wird nur ein Hash des Tokens.",
            "Token and certificate come with the access (setup, step 4); only a hash of the token is stored.",
          )}
        </span>
      </Note>
    </Modal>
  );
}
