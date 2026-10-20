import { useEffect, useState } from "react";
import { Agent, CollectAllItem, fetchAgents, startCollect, startCollectAll, System, Tenant } from "./api";
import type { View } from "./App";
import { PLATFORM_LABEL, REFERENCE_PLATFORMS } from "./refs";
import { ErrorBox, Modal, Note } from "./ui";

// "Jetzt einlesen": Collector-Lauf für Mandant + Quellsystem anlegen (operator).
// Die Ausführung ergibt sich aus dem Gerät (ADR 0009): mit zugewiesenem Agenten holt dieser
// den Lauf per --poll ab (extern), sonst liest der zentrale Celery-Worker (ADR 0008).
export default function CollectDialog({
  tenants,
  tenantId,
  systems,
  isAdmin,
  onClose,
  onStarted,
  onGo,
}: {
  tenants: Tenant[];
  tenantId: number | null;
  systems: System[];
  isAdmin: boolean;
  onClose: () => void;
  onStarted: () => void;
  onGo: (v: View) => void;
}) {
  const [tenant, setTenant] = useState<number | "">(tenantId ?? tenants[0]?.id ?? "");
  // Nur aktive Quellsysteme des gewählten Mandanten
  const active = systems.filter((s) => s.enabled && s.tenant_id === tenant);
  const [systemId, setSystemId] = useState<number | "">(active[0]?.id ?? "");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [done, setDone] = useState<{ runId: string; execution: string } | null>(null);

  useEffect(() => {
    if (isAdmin) fetchAgents().then(setAgents).catch(() => setAgents([]));
  }, [isAdmin]);

  const system = active.find((s) => s.id === systemId);
  const execution: "inprocess" | "external" = system?.agent_id != null ? "external" : "inprocess";
  const agent = agents.find((a) => a.id === system?.agent_id);
  const agentLabel = agent ? `„${agent.name}“` : system?.agent_id != null ? `#${system.agent_id}` : "";

  const submit = async () => {
    if (tenant === "" || !system) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await startCollect({
        platform: system.platform,
        tenant_id: tenant,
        source_system_id: system.id,
        execution,
      });
      setDone({ runId: res.run_id, execution: res.execution ?? execution });
      onStarted();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Modal
        title="Lauf angelegt"
        onClose={onClose}
        footer={
          <button className="btn btn-primary" onClick={onClose}>
            Schliessen
          </button>
        }
      >
        <p>
          Lauf <code>#{done.runId.slice(0, 8)}</code> für <b>{system?.name}</b> wurde angelegt.
        </p>
        {done.execution === "external" ? (
          <Note>
            Der Lauf wartet, bis der Agent {agentLabel} ihn abholt (geplante Aufgabe auf dem Agent-Rechner bzw.{" "}
            <code>run-agent.ps1</code>). Der Status erscheint unter „Letzte Läufe“.
          </Note>
        ) : (
          <Note>Der zentrale Worker erfasst das System jetzt. Der Status aktualisiert sich automatisch.</Note>
        )}
        <details>
          <summary className="muted">Vollständige Lauf-ID</summary>
          <code>{done.runId}</code>
        </details>
        <p />
      </Modal>
    );
  }

  return (
    <Modal
      title="Jetzt einlesen"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button
            className="btn btn-primary"
            onClick={submit}
            disabled={busy || tenant === "" || !system}
          >
            {busy ? "Lege an…" : "Lauf starten"}
          </button>
        </>
      }
    >
      <ErrorBox error={err} prefix="Lauf anlegen" />
      {tenants.length === 0 || active.length === 0 ? (
        <Note warn>
          {tenants.length === 0 ? "Es gibt noch keinen Mandanten. " : ""}
          {active.length === 0 ? "Für diesen Mandanten ist noch kein aktives Quellsystem erfasst. " : ""}
          {isAdmin ? (
            <>
              Unter{" "}
              <a
                href="#tenants"
                onClick={(e) => {
                  e.preventDefault();
                  onClose();
                  onGo("tenants");
                }}
              >
                Mandanten &amp; Standorte
              </a>{" "}
              anlegen.
            </>
          ) : (
            "Bitte einen Administrator, es anzulegen."
          )}
        </Note>
      ) : null}

      <div className="field">
        <label htmlFor="cd-tenant">Mandant</label>
        <select
          id="cd-tenant"
          className="sel input"
          value={tenant}
          onChange={(e) => {
            const next = e.target.value ? Number(e.target.value) : "";
            setTenant(next);
            setSystemId(systems.find((s) => s.enabled && s.tenant_id === next)?.id ?? "");
          }}
        >
          {tenants.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
              {t.code ? ` · ${t.code}` : ""}
            </option>
          ))}
        </select>
        <div className="hint">Die gelesenen Regeln werden diesem Mandanten zugeordnet.</div>
      </div>

      <div className="field">
        <label htmlFor="cd-system">Quellsystem</label>
        <select
          id="cd-system"
          className="sel input"
          value={systemId}
          onChange={(e) => setSystemId(e.target.value ? Number(e.target.value) : "")}
        >
          {active.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} ({PLATFORM_LABEL[s.platform] ?? s.platform})
            </option>
          ))}
        </select>
      </div>

      {system && (
        <div className="field">
          <span className="label">Ausführung</span>
          <div>
            {execution === "external" ? (
              <span className="chip info">Agent {agentLabel}</span>
            ) : (
              <span className="chip na">Zentraler Worker</span>
            )}
          </div>
          <div className="hint">
            {execution === "external"
              ? "Der zugewiesene Agent holt den Lauf ab, bekommt die Zugangsdaten des Geräts und liefert das Ergebnis zurück."
              : "Kein Agent zugewiesen: der zentrale Worker liest direkt (braucht Netzzugang zum Gerät)."}{" "}
            Ändern: Mandanten &amp; Standorte → Quellsystem → „Verwaltet von“.
          </div>
          {!system.has_credentials &&
            (REFERENCE_PLATFORMS.includes(system.platform) ? (
              <Note warn>
                Für „{system.name}“ fehlt der Lese-Zugang (
                {system.platform === "netbox" ? "API-Token (lesen)" : "Benutzer/Passwort"}) – ohne ihn kann die
                Referenzquelle nicht eingelesen werden. Das NetBox-Schreib-Token wird nie zum Lesen verwendet.
              </Note>
            ) : (
              <Note warn>
                Für „{system.name}“ sind keine Zugangsdaten hinterlegt –{" "}
                {execution === "external"
                  ? "der Agent bricht den Lauf ab."
                  : "der zentrale Worker nutzt dann seine lokale Konfiguration bzw. Beispieldaten."}
              </Note>
            ))}
          {agent && !agent.enabled && (
            <Note warn>Der Agent {agentLabel} ist deaktiviert – der Lauf würde nicht abgeholt.</Note>
          )}
        </div>
      )}
    </Modal>
  );
}

// "Alle einlesen": für jedes aktive Quellsystem des Mandanten einen Lauf anlegen (operator).
// Läuft/wartet schon einer oder fehlt der Lese-Zugang, überspringt der Orchestrator das System.
export function CollectAllDialog({
  tenantId,
  tenantLabel,
  systems,
  onClose,
  onStarted,
}: {
  tenantId: number;
  tenantLabel: string;
  systems: System[];
  onClose: () => void;
  onStarted: () => void;
}) {
  const active = systems
    .filter((s) => s.enabled && s.tenant_id === tenantId)
    .sort(
      (a, b) =>
        Number(!REFERENCE_PLATFORMS.includes(a.platform)) - Number(!REFERENCE_PLATFORMS.includes(b.platform)) ||
        a.name.localeCompare(b.name),
    );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [items, setItems] = useState<CollectAllItem[] | null>(null);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const res = await startCollectAll(tenantId);
      setItems(res.items);
      onStarted();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  const queued = items?.filter((i) => i.run_id).length ?? 0;
  return (
    <Modal
      title={items ? `${queued} Läufe angelegt` : "Alle Quellsysteme einlesen"}
      onClose={onClose}
      footer={
        items ? (
          <button className="btn btn-primary" onClick={onClose}>
            Schliessen
          </button>
        ) : (
          <>
            <button className="btn btn-ghost" onClick={onClose}>
              Abbrechen
            </button>
            <button className="btn btn-primary" onClick={submit} disabled={busy || active.length === 0}>
              {busy ? "Lege an…" : `${active.length} Läufe starten`}
            </button>
          </>
        )
      }
    >
      <ErrorBox error={err} prefix="Läufe anlegen" />
      {!items && (
        <p>
          Für jedes aktive Quellsystem von „{tenantLabel}“ wird ein Lauf angelegt – IPAM-Quellen zuerst. Rein lesend;
          Geräte mit Agent holt der Agent ab, die übrigen liest der zentrale Worker.
        </p>
      )}
      {active.length === 0 && <Note warn>Für diesen Mandanten ist kein aktives Quellsystem erfasst.</Note>}
      <table>
        <tbody>
          {(items ?? active.map((s) => ({ source_system_id: s.id, name: s.name, platform: s.platform }) as CollectAllItem)).map(
            (i) => (
              <tr key={i.source_system_id}>
                <td>
                  <b>{i.name}</b>
                </td>
                <td className="faint">{PLATFORM_LABEL[i.platform] ?? i.platform}</td>
                <td>
                  {!items ? (
                    systems.find((s) => s.id === i.source_system_id)?.agent_id != null ? (
                      <span className="chip info">Agent</span>
                    ) : (
                      <span className="chip na">Zentraler Worker</span>
                    )
                  ) : i.run_id ? (
                    <span className="chip ok">{i.execution === "external" ? "wartet auf Agent" : "läuft"}</span>
                  ) : (
                    <span className="chip warn" title={i.skipped ?? ""}>
                      übersprungen: {i.skipped}
                    </span>
                  )}
                </td>
              </tr>
            ),
          )}
        </tbody>
      </table>
    </Modal>
  );
}
