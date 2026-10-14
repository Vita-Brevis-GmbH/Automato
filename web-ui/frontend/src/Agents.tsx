import { useEffect, useState } from "react";
import { Agent, AgentCreated, createAgent, fetchAgents, setAgentEnabled, Tenant } from "./api";

// Agent-Verwaltung (ADR 0008): externe Collector-Agenten anlegen/deaktivieren, Heartbeat
// (last_seen) sehen. Schreiben erfordert serverseitig Admin (sonst 403 sichtbar).
export default function Agents({ tenants }: { tenants: Tenant[] }) {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [name, setName] = useState("");
  const [tenantId, setTenantId] = useState<number | null>(null);
  const [newToken, setNewToken] = useState<AgentCreated | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () =>
    fetchAgents()
      .then(setAgents)
      .catch((e) => setErr(`Agenten laden fehlgeschlagen: ${e}`));

  useEffect(() => {
    load();
  }, []);

  const tenantName = (id: number | null) => tenants.find((t) => t.id === id)?.name ?? "alle Mandanten";

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      const created = await createAgent(name.trim(), tenantId);
      setNewToken(created);
      setName("");
      await load();
    } catch (e) {
      setErr(`Agent anlegen fehlgeschlagen: ${e} (nur Admin)`);
    }
  };

  const toggle = async (a: Agent) => {
    setErr(null);
    try {
      await setAgentEnabled(a.id, !a.enabled);
      await load();
    } catch (e) {
      setErr(`Statuswechsel fehlgeschlagen: ${e} (nur Admin)`);
    }
  };

  return (
    <div>
      {err && <p className="err">{err}</p>}

      {newToken && (
        <div className="err" style={{ background: "var(--accent-soft)", color: "var(--text)" }}>
          <strong>Agent-Token für „{newToken.name}" — nur jetzt sichtbar, bitte kopieren:</strong>
          <br />
          <code style={{ wordBreak: "break-all" }}>{newToken.token}</code>
          <br />
          <button className="btn" style={{ marginTop: ".4rem" }} onClick={() => setNewToken(null)}>
            Verstanden
          </button>
        </div>
      )}

      <form className="form-row" onSubmit={add}>
        <input
          value={name}
          placeholder="Agent-Name (z. B. agent-win-01)"
          required
          onChange={(e) => setName(e.target.value)}
        />
        <select
          value={tenantId ?? ""}
          onChange={(e) => setTenantId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">alle Mandanten</option>
          {tenants.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <button className="btn primary" type="submit">
          Agent anlegen
        </button>
      </form>

      {agents === null ? (
        <p className="notice">Lade Agenten…</p>
      ) : agents.length === 0 ? (
        <p className="notice">Noch keine Agenten registriert.</p>
      ) : (
        <div className="panel">
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
              {agents.map((a) => (
                <tr key={a.id}>
                  <td>{a.name}</td>
                  <td className="muted">{tenantName(a.tenant_id)}</td>
                  <td>
                    <span className={`badge ${a.enabled ? "b-green" : "b-grey"}`}>
                      {a.enabled ? "aktiv" : "deaktiviert"}
                    </span>
                  </td>
                  <td className="muted">
                    {a.last_seen ? new Date(a.last_seen).toLocaleString() : "nie"}
                  </td>
                  <td>
                    <button className={`btn ${a.enabled ? "danger" : ""}`} onClick={() => toggle(a)}>
                      {a.enabled ? "Deaktivieren" : "Aktivieren"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
