// Stammdaten von Applikationen aus CSV (0.75.0, Entscheid des Anwenders: Ausnahme vom
// „kein CSV-Import“ nur dafür). Datei wählen → Vorschau je Zeile → übernehmen; die Mitglieder
// folgen danach in der Doku bzw. im Drawer.
import { useState } from "react";
import { AppImportPlan, AppImportRow, fetchAppImportTemplate, importApps } from "./api";
import { download, ErrorBox, Modal, Note } from "./ui";

const ACTION: Record<AppImportRow["action"], [string, string]> = {
  create: ["ok", "neu"],
  update: ["info", "ändern"],
  unchanged: ["na", "unverändert"],
  error: ["crit", "Fehler"],
};
const FIELD: Record<string, string> = {
  name: "Name",
  external_name: "Externer Name",
  internal_name: "Interner Name",
  purpose: "Zweck",
  code: "Kürzel",
  owner: "Verantwortlich",
  criticality: "Kritikalität",
  site: "Standort",
  description: "Beschreibung",
};

// Excel speichert CSV oft als Windows-1252: erst UTF-8 streng, sonst Windows-1252
async function readText(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buf);
  } catch {
    return new TextDecoder("windows-1252").decode(buf);
  }
}

const LABEL: Record<string, Record<string, string>> = {
  purpose: { dev: "Dev", test: "Test", prod: "Prod" },
  criticality: { low: "niedrig", medium: "mittel", high: "hoch", critical: "kritisch" },
};

function value(field: string, v: unknown): string {
  if (v === null || v === undefined || v === "") return "–";
  return LABEL[field]?.[String(v)] ?? String(v);
}

export default function AppImport({
  tenantId,
  onClose,
  onDone,
}: {
  tenantId: number;
  onClose: () => void;
  onDone: () => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const [fileName, setFileName] = useState("");
  const [plan, setPlan] = useState<AppImportPlan | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const choose = async (file: File | undefined) => {
    setErr(null);
    setPlan(null);
    if (!file) return;
    setFileName(file.name);
    try {
      const t = await readText(file);
      setText(t);
      setBusy(true);
      setPlan(await importApps(tenantId, t, false));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const apply = async () => {
    if (text == null) return;
    setBusy(true);
    setErr(null);
    try {
      await importApps(tenantId, text, true);
      onDone();
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  };
  const template = async () => {
    try {
      download("applikationen-vorlage.csv", await fetchAppImportTemplate());
    } catch (e) {
      setErr(e);
    }
  };
  const todo = plan ? plan.counts.create + plan.counts.update : 0;

  return (
    <Modal
      title="Applikationen aus CSV importieren"
      wide
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button className="btn btn-primary" id="app-import-apply" disabled={busy || todo === 0} onClick={apply}>
            {todo} übernehmen
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <p className="faint" style={{ marginTop: 0 }}>
        Je Zeile eine Applikation mit ihren Stammdaten – Name (Pflicht), externer und interner Name, Zweck (dev/test/prod
        bzw. Entwicklung/Test/Produktion), Kürzel, Verantwortlich, Kritikalität (niedrig/mittel/hoch/kritisch),
        Standort (Code) und Beschreibung. Trennzeichen „;“ oder „,“. Gibt es den Namen schon, ändern ausgefüllte Zellen
        die Applikation, leere lassen den Wert. Die Mitglieder ordnest du danach zu.
      </p>
      <div className="filterbar" style={{ marginBottom: 10 }}>
        <input
          type="file"
          accept=".csv,text/csv,text/plain"
          id="app-import-file"
          onChange={(e) => choose(e.target.files?.[0])}
        />
        <button className="btn btn-ghost btn-sm" id="app-import-template" onClick={template}>
          Vorlage herunterladen
        </button>
        {fileName && <span className="faint">{fileName}</span>}
      </div>
      {busy && !plan && <Note>Prüfe die Datei…</Note>}
      {plan && (
        <>
          <div className="chips" id="app-import-counts" style={{ marginBottom: 8 }}>
            <span className="chip ok">{plan.counts.create} neu</span>
            <span className="chip info">{plan.counts.update} ändern</span>
            <span className="chip na">{plan.counts.unchanged} unverändert</span>
            {plan.counts.error > 0 && <span className="chip crit">{plan.counts.error} mit Fehler (werden ausgelassen)</span>}
            {plan.ignored.length > 0 && (
              <span className="chip warn" title="Spalten ohne Bedeutung für Applikationen">
                ignoriert: {plan.ignored.join(", ")}
              </span>
            )}
          </div>
          <div style={{ maxHeight: 380, overflowY: "auto" }}>
            <table className="compact" id="app-import-rows">
              <thead>
                <tr>
                  <th className="num">Zeile</th>
                  <th>Name</th>
                  <th>Aktion</th>
                  <th>Werte bzw. Fehler</th>
                </tr>
              </thead>
              <tbody>
                {plan.rows.map((r) => (
                  <tr key={r.line} data-action={r.action}>
                    <td className="num mono">{r.line}</td>
                    <td>{r.name ?? <span className="faint">–</span>}</td>
                    <td>
                      <span className={`chip ${ACTION[r.action][0]}`}>{ACTION[r.action][1]}</span>
                    </td>
                    <td>
                      {r.errors.length > 0 ? (
                        <span style={{ color: "var(--crit)" }}>{r.errors.join(" · ")}</span>
                      ) : (
                        <div className="objs">
                          {Object.entries(r.changes).map(([f, [old, neu]]) => (
                            <span key={f}>
                              <span className="faint">{FIELD[f] ?? f}</span>{" "}
                              {r.action === "update" && <span className="faint">{value(f, old)} → </span>}
                              {value(f, neu)}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Modal>
  );
}
