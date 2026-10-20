import { useEffect, useState } from "react";
import { Agent, AgentCreated, createAgent, fetchAgents, setAgentAutoUpdate, setAgentEnabled, Tenant } from "./api";
import AgentGuide from "./AgentGuide";
import type { View } from "./App";
import { Empty, ErrorBox, fmtTime, Icon, Loading, Modal, Note, PageHead } from "./ui";

// Agent-Verwaltung (ADR 0008/0009): externe Collector-Agenten anlegen/deaktivieren, Heartbeat
// (last_seen) und zugewiesene Geräte sehen. Geräte + Zugangsdaten werden beim Mandanten
// (Quellsysteme) erfasst und dort einem Agenten zugewiesen. Nur Admin (serverseitig erzwungen).
const ONLINE_MS = 15 * 60 * 1000;

function onlineState(a: Agent): [string, string, string] {
  if (!a.enabled) return ["na", "n", "deaktiviert"];
  if (!a.last_seen) return ["warn", "y", "noch nie verbunden"];
  return Date.now() - new Date(a.last_seen).getTime() < ONLINE_MS
    ? ["ok", "g", "online"]
    : ["warn", "y", "keine Meldung"];
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
  const [copied, setCopied] = useState(false);
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
    id == null ? "alle Mandanten" : (tenants.find((t) => t.id === id)?.name ?? `#${id}`);

  const toggle = async (a: Agent) => {
    if (a.enabled && !window.confirm(`Agent „${a.name}“ deaktivieren? Sein Token wird sofort abgelehnt.`)) return;
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

  const copy = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.token);
      setCopied(true);
    } catch {
      setCopied(false); // Zwischenablage nicht verfügbar (z. B. ohne HTTPS) -> manuell kopieren
    }
  };

  return (
    <>
      <PageHead
        title="Collector-Agenten"
        actions={
          <button className="btn btn-primary btn-sm" onClick={() => setNewOpen(true)}>
            + Agent
          </button>
        }
      >
        Agenten laufen nahe an den Geräten im Kundennetz (z. B. Windows) und sprechen nur ausgehend HTTPS mit
        dem Orchestrator. Welche Geräte ein Agent liest, legst du beim Mandanten unter Quellsysteme fest
        („Verwaltet von“) – die Zugangsdaten bekommt er von dort pro Lauf.
      </PageHead>

      <ErrorBox error={err} onClose={() => setErr(null)} />

      {created && (
        <div className="secret">
          <b>Agent-Token für „{created.name}“ – wird nur jetzt angezeigt.</b> Ist unten in der Einrichtung
          bereits in die <code>agent.env</code> übernommen (Schritt 3) – oder hier kopieren.
          <code className="token">{created.token}</code>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn-ghost btn-sm" onClick={copy}>
              {copied ? "Kopiert ✓" : "In Zwischenablage kopieren"}
            </button>
            <button
              className="btn btn-primary btn-sm"
              onClick={() => {
                setCreated(null);
                setCopied(false);
              }}
            >
              Gespeichert – ausblenden
            </button>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <h2>Registrierte Agenten</h2>
          <div className="spacer" />
          <button className="btn btn-ghost btn-sm" onClick={load}>
            Aktualisieren
          </button>
        </div>
        <div className="card-body scroll">
          {agents === null ? (
            <Loading />
          ) : agents.length === 0 ? (
            <Empty
              title="Noch keine Agenten"
              action={
                <button className="btn btn-primary btn-sm" onClick={() => setNewOpen(true)}>
                  + Agent anlegen
                </button>
              }
            >
              Die Einrichtung ist unten Schritt für Schritt beschrieben.
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Mandant</th>
                  <th>Geräte</th>
                  <th>Status</th>
                  <th>Zuletzt gesehen</th>
                  <th title="Paket-Version des Agenten; mit Auto-Update aktualisiert er sich selbst">Version</th>
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
                          <span className="faint">keine zugewiesen</span>
                        ) : (
                          a.systems.map((d) => (
                            <span
                              key={d.id}
                              className={`chip ${d.has_credentials ? "" : "warn"}`}
                              title={d.has_credentials ? "Zugangsdaten hinterlegt" : "keine Zugangsdaten hinterlegt"}
                            >
                              {d.name}
                              {d.has_credentials ? "" : " · ohne Token"}
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
                        {a.version ? <span className="mono">{a.version.slice(0, 8)}</span> : <span className="faint">–</span>}{" "}
                        {a.update_available === true && (
                          <span
                            className={`chip ${a.auto_update ? "info" : "warn"}`}
                            title={
                              a.auto_update
                                ? "Neue Version verfügbar – der Agent aktualisiert sich beim nächsten Aufruf selbst"
                                : "Neue Version verfügbar – Auto-Update aus: Paket manuell installieren"
                            }
                          >
                            {a.auto_update ? "Update beim nächsten Lauf" : "veraltet"}
                          </span>
                        )}
                        {a.update_available === false && <span className="chip ok">aktuell</span>}
                        <label className="check" style={{ marginTop: 4, fontSize: 12 }}>
                          <input type="checkbox" checked={a.auto_update} onChange={() => toggleUpdate(a)} />
                          Auto-Update
                        </label>
                      </td>
                      <td className="actions">
                        <button className={`btn btn-sm ${a.enabled ? "btn-danger" : "btn-ghost"}`} onClick={() => toggle(a)}>
                          {a.enabled ? "Deaktivieren" : "Aktivieren"}
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
            setCopied(false);
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
      title="Neuer Collector-Agent"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={busy || !name.trim()}>
            {busy ? "Lege an…" : "Anlegen & Token erzeugen"}
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
          placeholder="z. B. agent-win-01"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
      </div>
      <div className="field">
        <label htmlFor="na-tenant">Mandant</label>
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
          <option value="">alle Mandanten</option>
        </select>
        <div className="hint">
          Der Agent kann Geräte dieses Mandanten verwalten. Zuweisung danach beim Mandanten unter Quellsysteme.
        </div>
      </div>
      <Note>
        <span>
          <Icon name="lock" size={13} /> Das Token wird nur einmal angezeigt; gespeichert wird nur ein Hash.
        </span>
      </Note>
    </Modal>
  );
}
