import { useState } from "react";
import { startCollect, System, Tenant } from "./api";
import type { View } from "./App";
import { ErrorBox, Modal, Note } from "./ui";

const PLATFORM_LABEL: Record<string, string> = { fortigate: "FortiGate", cisco_aci: "Cisco ACI" };

// "Jetzt einlesen": Collector-Lauf für Mandant + Quellsystem anlegen (operator).
// zentral = Celery-Worker der Plattform; extern = ein Agent im Kundennetz holt den Lauf
// per --poll ab (ADR 0008).
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
  const active = systems.filter((s) => s.enabled);
  const [tenant, setTenant] = useState<number | "">(tenantId ?? tenants[0]?.id ?? "");
  const [systemId, setSystemId] = useState<number | "">(active[0]?.id ?? "");
  const [execution, setExecution] = useState<"inprocess" | "external">("external");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);

  const system = active.find((s) => s.id === systemId);

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
      setDone(res.run_id);
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
          Lauf <code>#{done.slice(0, 8)}</code> für <b>{system?.name}</b> wurde angelegt.
        </p>
        {execution === "external" ? (
          <Note>
            Der Lauf wartet, bis ein Collector-Agent ihn abholt (<code>automato-collector --poll</code>{" "}
            bzw. die geplante Aufgabe auf dem Windows-Agent). Der Status erscheint unter „Letzte
            Läufe“.
          </Note>
        ) : (
          <Note>Der zentrale Worker erfasst das System jetzt. Der Status aktualisiert sich automatisch.</Note>
        )}
        <details>
          <summary className="muted">Vollständige Lauf-ID</summary>
          <code>{done}</code>
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
          {active.length === 0 ? "Es ist noch kein Quellsystem erfasst. " : ""}
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
          onChange={(e) => setTenant(e.target.value ? Number(e.target.value) : "")}
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

      <div className="field">
        <span className="label">Ausführung</span>
        <div className="seg">
          <button className={execution === "external" ? "on" : ""} onClick={() => setExecution("external")}>
            Extern (Agent)
          </button>
          <button className={execution === "inprocess" ? "on" : ""} onClick={() => setExecution("inprocess")}>
            Zentral (Worker)
          </button>
        </div>
        <div className="hint">
          {execution === "external"
            ? "Für Zielsysteme im Kundennetz: der registrierte Agent holt den Lauf ab und liefert das Ergebnis zurück."
            : "Der zentrale Worker liest direkt (braucht Netzzugang und Zugangsdaten auf der Plattform; ohne Zugangsdaten Beispieldaten)."}
        </div>
      </div>
    </Modal>
  );
}
