// Stammdaten von Applikationen aus CSV (0.75.0, Entscheid des Anwenders: Ausnahme vom
// „kein CSV-Import“ nur dafür). Datei wählen → Vorschau je Zeile → übernehmen; die Mitglieder
// folgen danach in der Doku bzw. im Drawer.
import { useState } from "react";
import { AppImportPlan, AppImportRow, fetchAppImportTemplate, importApps } from "./api";
import { download, ErrorBox, Modal, Note } from "./ui";
import { L } from "./i18n";

const ACTION: Record<AppImportRow["action"], [string, string]> = {
  create: ["ok", L("neu", "new")],
  update: ["info", L("ändern", "change")],
  unchanged: ["na", L("unverändert", "unchanged")],
  error: ["crit", L("Fehler", "Error")],
};
const FIELD: Record<string, string> = {
  name: "Name",
  external_name: L("Externer Name", "External name"),
  internal_name: L("Interner Name", "Internal name"),
  purpose: L("Zweck", "Purpose"),
  code: L("Kürzel", "Code"),
  owner: L("Verantwortlich", "Owner"),
  criticality: L("Kritikalität", "Criticality"),
  site: L("Standort", "Site"),
  description: L("Beschreibung", "Description"),
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
  criticality: {
    low: L("niedrig", "low"),
    medium: L("mittel", "medium"),
    high: L("hoch", "high"),
    critical: L("kritisch", "critical"),
  },
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
      title={L("Applikationen aus CSV importieren", "Import applications from CSV")}
      wide
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-primary" id="app-import-apply" disabled={busy || todo === 0} onClick={apply}>
            {L(`${todo} übernehmen`, `Apply ${todo}`)}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <p className="faint" style={{ marginTop: 0 }}>
        {L(
          "Je Zeile eine Applikation mit ihren Stammdaten – Name (Pflicht), externer und interner Name, " +
            "Zweck (dev/test/prod bzw. Entwicklung/Test/Produktion), Kürzel, Verantwortlich, " +
            "Kritikalität (niedrig/mittel/hoch/kritisch), Standort (Code) und Beschreibung. " +
            "Trennzeichen „;“ oder „,“. Gibt es den Namen schon, ändern ausgefüllte Zellen die Applikation, " +
            "leere lassen den Wert. Die Mitglieder ordnest du danach zu.",
          "One application per row with its master data – name (required), external and internal name, " +
            "purpose (dev/test/prod or development/test/production), code, owner, " +
            "criticality (low/medium/high/critical), site (code) and description. " +
            "Separator “;” or “,”. If the name already exists, filled cells change the application, " +
            "empty cells keep the value. You assign the members afterwards.",
        )}
      </p>
      <div className="filterbar" style={{ marginBottom: 10 }}>
        <input
          type="file"
          accept=".csv,text/csv,text/plain"
          id="app-import-file"
          onChange={(e) => choose(e.target.files?.[0])}
        />
        <button className="btn btn-ghost btn-sm" id="app-import-template" onClick={template}>
          {L("Vorlage herunterladen", "Download template")}
        </button>
        {fileName && <span className="faint">{fileName}</span>}
      </div>
      {busy && !plan && <Note>{L("Prüfe die Datei…", "Checking the file…")}</Note>}
      {plan && (
        <>
          <div className="chips" id="app-import-counts" style={{ marginBottom: 8 }}>
            <span className="chip ok">{L(`${plan.counts.create} neu`, `${plan.counts.create} new`)}</span>
            <span className="chip info">{L(`${plan.counts.update} ändern`, `${plan.counts.update} change`)}</span>
            <span className="chip na">
              {L(`${plan.counts.unchanged} unverändert`, `${plan.counts.unchanged} unchanged`)}
            </span>
            {plan.counts.error > 0 && (
              <span className="chip crit">
                {L(
                  `${plan.counts.error} mit Fehler (werden ausgelassen)`,
                  `${plan.counts.error} with errors (will be skipped)`,
                )}
              </span>
            )}
            {plan.ignored.length > 0 && (
              <span
                className="chip warn"
                title={L("Spalten ohne Bedeutung für Applikationen", "Columns without meaning for applications")}
              >
                {L("ignoriert", "ignored")}: {plan.ignored.join(", ")}
              </span>
            )}
          </div>
          <div style={{ maxHeight: 380, overflowY: "auto" }}>
            <table className="compact" id="app-import-rows">
              <thead>
                <tr>
                  <th className="num">{L("Zeile", "Row")}</th>
                  <th>Name</th>
                  <th>{L("Aktion", "Action")}</th>
                  <th>{L("Werte bzw. Fehler", "Values or errors")}</th>
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
