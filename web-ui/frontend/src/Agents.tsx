import { useEffect, useState } from "react";
import { Agent, AgentCreated, createAgent, fetchAgents, setAgentEnabled, Tenant } from "./api";
import AgentGuide from "./AgentGuide";
import { Empty, ErrorBox, fmtTime, Icon, Loading, Modal, Note, PageHead } from "./ui";

// Agent-Verwaltung (ADR 0008): externe Collector-Agenten anlegen/deaktivieren, Heartbeat
// (last_seen) sehen. Nur Admin (serverseitig erzwungen).
const ONLINE_MS = 15 * 60 * 1000;

function onlineState(a: Agent): [string, string, string] {
  if (!a.enabled) return ["na", "n", "deaktiviert"];
  if (!a.last_seen) return ["warn", "y", "noch nie verbunden"];
  return Date.now() - new Date(a.last_seen).getTime() < ONLINE_MS
    ? ["ok", "g", "online"]
    : ["warn", "y", "keine Meldung"];
}

export default function Agents({ tenants, tenantId }: { tenants: Tenant[]; tenantId: number | null }) {
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
        Agenten laufen nahe am Zielsystem im Kundennetz (z. B. Windows) und sprechen nur ausgehend HTTPS mit
        dem Orchestrator. Zugangsdaten der Zielsysteme bleiben beim Agenten.
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
                  <th>Status</th>
                  <th>Zuletzt gesehen</th>
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
                        <span className={`chip ${cls}`}>
                          <span className={`dot ${dot}`} />
                          {label}
                        </span>
                      </td>
                      <td className="mono">{fmtTime(a.last_seen)}</td>
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

      <AgentGuide agents={agents ?? []} created={created} onNewAgent={() => setNewOpen(true)} />

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
        <div className="hint">Der Agent holt nur externe Läufe dieses Mandanten ab.</div>
      </div>
      <Note>
        <span>
          <Icon name="lock" size={13} /> Das Token wird nur einmal angezeigt; gespeichert wird nur ein Hash.
        </span>
      </Note>
    </Modal>
  );
}
