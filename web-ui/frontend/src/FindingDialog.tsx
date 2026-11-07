// Finding von Hand erfassen (0.109.0): aus „Warum dieser Weg?“ (vorausgefüllt mit dem Schritt) bzw.
// frei auf der Seite Findings. Typ `manual`, bleibt nach dem Abgleich bestehen, Triage wie jedes Finding.
import { useState } from "react";
import { createFinding, Finding, ManualFindingInput } from "./api";
import { ErrorBox, Modal } from "./ui";
import { L } from "./i18n";

export type FindingDraft = Partial<Omit<ManualFindingInput, "tenant_id">>;

const SEVERITY: [ManualFindingInput["severity"], string][] = [
  ["error", L("Fehler", "Error")],
  ["warning", L("Warnung", "Warning")],
  ["info", "Info"],
];

export default function FindingDialog({
  tenantId,
  draft,
  systems,
  onClose,
  onSaved,
}: {
  tenantId: number;
  draft: FindingDraft;
  systems?: { id: number; name: string }[]; // Gerät wählen (frei erfassen)
  onClose: () => void;
  onSaved: (f: Finding) => void;
}) {
  const [f, setF] = useState<FindingDraft>(() => ({ severity: "warning", ...draft, title: (draft.title ?? "").slice(0, 300) }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const set = (k: keyof FindingDraft) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));
  const title = (f.title ?? "").trim();
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const out = await createFinding({
        tenant_id: tenantId,
        title,
        text: f.text?.trim() || undefined,
        severity: f.severity ?? "warning",
        source_system_id: f.source_system_id ?? undefined,
        hop: f.hop?.trim() || undefined,
        src: f.src?.trim() || undefined,
        dst: f.dst?.trim() || undefined,
        service: f.service?.trim() || undefined,
      });
      onSaved(out);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title={L("Finding erfassen", "Capture finding")}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-primary" id="finding-save" onClick={() => void save()} disabled={busy || title.length < 3}>
            {busy ? L("Speichere…", "Saving…") : L("Finding erfassen", "Capture finding")}
          </button>
        </>
      }
    >
      <ErrorBox error={error} prefix={L("Finding erfassen", "Capture finding")} onClose={() => setError(null)} />
      <div className="field">
        <label htmlFor="finding-title">{L("Was stimmt nicht", "What is wrong")}</label>
        <input id="finding-title" className="input" value={f.title ?? ""} maxLength={300} onChange={set("title")} autoFocus />
      </div>
      <div className="field">
        <label htmlFor="finding-text">{L("Was anzupassen ist", "What to adjust")}</label>
        <textarea id="finding-text" className="input" rows={4} maxLength={4000} value={f.text ?? ""} onChange={set("text")} />
        <div className="hint">
          {L(
            "z. B. Route auf dem Gerät ergänzen, Konfiguration prüfen bzw. was Automato falsch erkennt",
            "e.g. add a route on the device, check the configuration or what Automato detects wrongly",
          )}
        </div>
      </div>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="finding-severity">{L("Schwere", "Severity")}</label>
          <select id="finding-severity" className="sel" value={f.severity} onChange={set("severity")}>
            {SEVERITY.map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </div>
        {systems ? (
          <div className="field">
            <label htmlFor="finding-system">{L("Gerät", "Device")}</label>
            <select
              id="finding-system"
              className="sel"
              value={f.source_system_id ?? ""}
              onChange={(e) => setF((x) => ({ ...x, source_system_id: e.target.value ? Number(e.target.value) : null }))}
            >
              <option value="">{L("– keines –", "– none –")}</option>
              {systems.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <div className="field">
            <label htmlFor="finding-hop">{L("Bei", "At")}</label>
            <input id="finding-hop" className="input" value={f.hop ?? ""} maxLength={300} onChange={set("hop")} />
          </div>
        )}
        <div className="field">
          <label htmlFor="finding-src">{L("Quelle", "Source")}</label>
          <input id="finding-src" className="input mono" value={f.src ?? ""} maxLength={100} onChange={set("src")} />
        </div>
        <div className="field">
          <label htmlFor="finding-dst">{L("Ziel", "Destination")}</label>
          <input id="finding-dst" className="input mono" value={f.dst ?? ""} maxLength={100} onChange={set("dst")} />
        </div>
        <div className="field">
          <label htmlFor="finding-service">{L("Dienst", "Service")}</label>
          <input id="finding-service" className="input mono" value={f.service ?? ""} maxLength={100} onChange={set("service")} />
        </div>
      </div>
    </Modal>
  );
}
