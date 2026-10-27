import { useEffect, useState } from "react";
import { Agent, cancelRun, CollectAllItem, fetchAgents, startCollect, startCollectAll, System, Tenant } from "./api";
import type { View } from "./App";
import { PLATFORM_LABEL, REFERENCE_PLATFORMS } from "./refs";
import { ErrorBox, Modal, Note } from "./ui";
import { L } from "./i18n";

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
  const agentLabel = agent
    ? L(`„${agent.name}“`, `“${agent.name}”`)
    : system?.agent_id != null
      ? `#${system.agent_id}`
      : "";

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
        title={L("Lauf angelegt", "Run created")}
        onClose={onClose}
        footer={
          <button className="btn btn-primary" onClick={onClose}>
            {L("Schliessen", "Close")}
          </button>
        }
      >
        <p>
          {L(
            <>
              Lauf <code>#{done.runId.slice(0, 8)}</code> für <b>{system?.name}</b> wurde angelegt.
            </>,
            <>
              Run <code>#{done.runId.slice(0, 8)}</code> for <b>{system?.name}</b> has been created.
            </>,
          )}
        </p>
        {done.execution === "external" ? (
          <Note>
            {L(
              <>
                Der Lauf wartet, bis der Agent {agentLabel} ihn abholt (geplante Aufgabe auf dem Agent-Rechner bzw.{" "}
                <code>run-agent.ps1</code>). Der Status erscheint unter „Letzte Läufe“.
              </>,
              <>
                The run waits until agent {agentLabel} picks it up (scheduled task on the agent machine or{" "}
                <code>run-agent.ps1</code>). The status appears under “Recent runs”.
              </>,
            )}
          </Note>
        ) : (
          <Note>
            {L(
              "Der zentrale Worker erfasst das System jetzt. Der Status aktualisiert sich automatisch.",
              "The central worker is collecting the system now. The status updates automatically.",
            )}
          </Note>
        )}
        <details>
          <summary className="muted">{L("Vollständige Lauf-ID", "Full run ID")}</summary>
          <code>{done.runId}</code>
        </details>
        <p />
      </Modal>
    );
  }

  return (
    <Modal
      title={L("Jetzt einlesen", "Collect now")}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button
            className="btn btn-primary"
            onClick={submit}
            disabled={busy || tenant === "" || !system}
          >
            {busy ? L("Lege an…", "Creating…") : L("Lauf starten", "Start run")}
          </button>
        </>
      }
    >
      <ErrorBox error={err} prefix={L("Lauf anlegen", "Creating run")} />
      {tenants.length === 0 || active.length === 0 ? (
        <Note warn>
          {tenants.length === 0 ? L("Es gibt noch keinen Mandanten. ", "There is no tenant yet. ") : ""}
          {active.length === 0
            ? L(
                "Für diesen Mandanten ist noch kein aktives Quellsystem erfasst. ",
                "No active source system has been recorded for this tenant yet. ",
              )
            : ""}
          {isAdmin ? (
            L(
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
              </>,
              <>
                Create it under{" "}
                <a
                  href="#tenants"
                  onClick={(e) => {
                    e.preventDefault();
                    onClose();
                    onGo("tenants");
                  }}
                >
                  Tenants &amp; sites
                </a>
                .
              </>,
            )
          ) : (
            L("Bitte einen Administrator, es anzulegen.", "Please ask an administrator to create it.")
          )}
        </Note>
      ) : null}

      <div className="field">
        <label htmlFor="cd-tenant">{L("Mandant", "Tenant")}</label>
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
        <div className="hint">
          {L(
            "Die gelesenen Regeln werden diesem Mandanten zugeordnet.",
            "The collected rules are assigned to this tenant.",
          )}
        </div>
      </div>

      <div className="field">
        <label htmlFor="cd-system">{L("Quellsystem", "Source system")}</label>
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
          <span className="label">{L("Ausführung", "Execution")}</span>
          <div>
            {execution === "external" ? (
              <span className="chip info">Agent {agentLabel}</span>
            ) : (
              <span className="chip na">{L("Zentraler Worker", "Central worker")}</span>
            )}
          </div>
          <div className="hint">
            {execution === "external"
              ? L(
                  "Der zugewiesene Agent holt den Lauf ab, bekommt die Zugangsdaten des Geräts und liefert das Ergebnis zurück.",
                  "The assigned agent picks up the run, receives the device's credentials and returns the result.",
                )
              : L(
                  "Kein Agent zugewiesen: der zentrale Worker liest direkt (braucht Netzzugang zum Gerät).",
                  "No agent assigned: the central worker reads directly (needs network access to the device).",
                )}{" "}
            {L(
              "Ändern: Mandanten & Standorte → Quellsystem → „Verwaltet von“.",
              "Change: Tenants & sites → Source system → “Managed by”.",
            )}
          </div>
          {!system.has_credentials &&
            (REFERENCE_PLATFORMS.includes(system.platform) ? (
              <Note warn>
                {L(
                  <>
                    Für „{system.name}“ fehlt der Lese-Zugang (
                    {system.platform === "netbox" ? "API-Token (lesen)" : "Benutzer/Passwort"}) – ohne ihn kann die
                    Referenzquelle nicht eingelesen werden. Das NetBox-Schreib-Token wird nie zum Lesen verwendet.
                  </>,
                  <>
                    Read access is missing for “{system.name}” (
                    {system.platform === "netbox" ? "API token (read)" : "user/password"}) – without it the reference
                    source cannot be collected. The NetBox write token is never used for reading.
                  </>,
                )}
              </Note>
            ) : (
              <Note warn>
                {L(
                  <>
                    Für „{system.name}“ sind keine Zugangsdaten hinterlegt –{" "}
                    {execution === "external"
                      ? "der Agent bricht den Lauf ab."
                      : "der zentrale Worker nutzt dann seine lokale Konfiguration bzw. Beispieldaten."}
                  </>,
                  <>
                    No credentials are stored for “{system.name}” –{" "}
                    {execution === "external"
                      ? "the agent aborts the run."
                      : "the central worker then uses its local configuration or sample data."}
                  </>,
                )}
              </Note>
            ))}
          {agent && !agent.enabled && (
            <Note warn>
              {L(
                <>Der Agent {agentLabel} ist deaktiviert – der Lauf würde nicht abgeholt.</>,
                <>Agent {agentLabel} is disabled – the run would not be picked up.</>,
              )}
            </Note>
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

  // Hängender Lauf (z. B. Agent neu gestartet): abbrechen und für dieses System neu anlegen
  const restart = async (item: CollectAllItem) => {
    if (!item.blocking_run_id) return;
    setErr(null);
    try {
      await cancelRun(item.blocking_run_id);
      const src = systems.find((s) => s.id === item.source_system_id);
      const res = await startCollect({
        platform: item.platform,
        tenant_id: tenantId,
        source_system_id: item.source_system_id,
        execution: src?.agent_id != null ? "external" : "inprocess",
      });
      setItems((prev) =>
        (prev ?? []).map((i) =>
          i.source_system_id === item.source_system_id
            ? { ...i, run_id: res.run_id, execution: res.execution, skipped: null, blocking_run_id: null }
            : i,
        ),
      );
      onStarted();
    } catch (e) {
      setErr(e);
    }
  };

  const queued = items?.filter((i) => i.run_id).length ?? 0;
  return (
    <Modal
      title={
        items
          ? L(`${queued} Läufe angelegt`, `${queued} runs created`)
          : L("Alle Quellsysteme einlesen", "Collect all source systems")
      }
      onClose={onClose}
      footer={
        items ? (
          <button className="btn btn-primary" onClick={onClose}>
            {L("Schliessen", "Close")}
          </button>
        ) : (
          <>
            <button className="btn btn-ghost" onClick={onClose}>
              {L("Abbrechen", "Cancel")}
            </button>
            <button className="btn btn-primary" onClick={submit} disabled={busy || active.length === 0}>
              {busy ? L("Lege an…", "Creating…") : L(`${active.length} Läufe starten`, `Start ${active.length} runs`)}
            </button>
          </>
        )
      }
    >
      <ErrorBox error={err} prefix={L("Läufe anlegen", "Creating runs")} />
      {!items && (
        <p>
          {L(
            <>
              Für jedes aktive Quellsystem von „{tenantLabel}“ wird ein Lauf angelegt – IPAM-Quellen zuerst. Rein lesend;
              Geräte mit Agent holt der Agent ab, die übrigen liest der zentrale Worker.
            </>,
            <>
              A run is created for every active source system of “{tenantLabel}” – IPAM sources first. Read-only;
              devices with an agent are picked up by the agent, the others are read by the central worker.
            </>,
          )}
        </p>
      )}
      {active.length === 0 && (
        <Note warn>
          {L(
            "Für diesen Mandanten ist kein aktives Quellsystem erfasst.",
            "No active source system is recorded for this tenant.",
          )}
        </Note>
      )}
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
                      <span className="chip na">{L("Zentraler Worker", "Central worker")}</span>
                    )
                  ) : i.run_id ? (
                    <span className="chip ok">
                      {i.execution === "external" ? L("wartet auf Agent", "waiting for agent") : L("läuft", "running")}
                    </span>
                  ) : (
                    <>
                      <span className="chip warn" title={i.skipped ?? ""}>
                        {L("übersprungen:", "skipped:")} {i.skipped}
                      </span>
                      {i.blocking_run_id && (
                        <button
                          className="linkbtn"
                          style={{ marginLeft: 6 }}
                          title={L(
                            "Den offenen Lauf beenden (z. B. hängt er nach einem Neustart des Agenten) und neu einlesen. Liefert der Agent doch noch ab, wird das Ergebnis eingespielt.",
                            "End the open run (e.g. if it hangs after an agent restart) and collect again. If the agent still delivers, the result is ingested.",
                          )}
                          onClick={() => restart(i)}
                        >
                          {L("Abbrechen & neu starten", "Cancel & restart")}
                        </button>
                      )}
                    </>
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
