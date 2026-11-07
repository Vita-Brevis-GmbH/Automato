// Module je Mandant (0.107.0, ADR 0036, Anwender: „damit man die auch modular deaktivieren kann“, „je Mandant wählbar“).
// Kern und Einstellungen sind immer an; ein Modul, dessen Abhängigkeit aus ist, ist auch aus. Schalten = Admin. Erste Stufe
// der Basis-Settings für das Cockpit.
import { useEffect, useState } from "react";
import { fetchModules, ModuleInfo, switchModule, Tenant } from "./api";
import type { Me } from "./auth";
import { L } from "./i18n";
import { MODULES_CHANGED } from "./moduleState";
import { ErrorBox, Loading } from "./ui";

const ROLE: Record<ModuleInfo["role"], string> = {
  basis: L("Basis, immer an", "base, always on"),
  lesen: L("lesend", "read-only"),
  schreiben: L("schreibend", "writing"),
};

export default function TenantModules({ me, tenant }: { me: Me; tenant: Tenant }) {
  const [mods, setMods] = useState<ModuleInfo[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);

  useEffect(() => {
    let live = true;
    fetchModules(tenant.id)
      .then((m) => live && setMods(m))
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [tenant.id]);

  const toggle = (m: ModuleInfo) => {
    setBusy(m.key);
    setErr(null);
    switchModule(tenant.id, m.key, !m.enabled)
      .then((next) => {
        setMods(next);
        window.dispatchEvent(new Event(MODULES_CHANGED)); // Navigation nachziehen
      })
      .catch(setErr)
      .finally(() => setBusy(null));
  };

  const title = (key: string) => mods?.find((x) => x.key === key)?.title ?? key;
  return (
    <div className="card" id="tenant-modules" style={{ marginTop: 16 }}>
      <div className="card-head">
        <h2>{L("Module", "Modules")}</h2>
        <span className="faint">
          {L(
            "Je Mandant ein- bzw. ausschalten: aus = keine Ansicht, keine API, keine Arbeit im Hintergrund – die gelesenen Daten bleiben.",
            "Switch on or off per tenant: off = no view, no API, no background work – the data read stays.",
          )}
        </span>
      </div>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {!mods ? (
        !err && <Loading />
      ) : (
        <div className="card-body">
          <table className="compact">
            <thead>
              <tr>
                <th>{L("Modul", "Module")}</th>
                <th>{L("Zweck", "Purpose")}</th>
                <th>{L("Art", "Kind")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {mods.map((m) => (
                <tr key={m.key} data-module={m.key}>
                  <td>
                    <b>{m.title}</b>
                  </td>
                  <td className="faint" style={{ fontSize: 12 }}>
                    {m.purpose}
                    {m.depends.length > 0 && (
                      <div>
                        {L("braucht ", "needs ")}
                        {m.depends.map(title).join(", ")}
                      </div>
                    )}
                  </td>
                  <td style={{ fontSize: 12 }}>{ROLE[m.role]}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    {m.switchable ? (
                      <button
                        className={`toggle ${m.enabled ? "on" : ""}`}
                        aria-label={L(`Modul ${m.title}`, `Module ${m.title}`)}
                        aria-pressed={m.enabled}
                        disabled={!me.isAdmin || busy !== null}
                        onClick={() => toggle(m)}
                      >
                        {m.enabled ? L("an", "on") : L("aus", "off")}
                        {m.enabled && !m.effective && <span className="faint">{L(" (Abhängigkeit aus)", " (dependency off)")}</span>}
                      </button>
                    ) : (
                      <span className="chip info">{L("immer an", "always on")}</span>
                    )}
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
