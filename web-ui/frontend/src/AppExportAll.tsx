// Gesamt-Export der Applikations-Doku (1.0.1, Anwender: „Export aller Dokumentationen der Applikationen als
// mehrere Zips, welche mit einem Export Passwort verschlüsselt sind“): je Applikation ein ZIP (Word und
// Netzwerkplan), jedes mit dem Export-Passwort AES-256-verschlüsselt, alle in einem Download.
import { FormEvent, useState } from "react";
import { exportAllApplications } from "./api";
import { L } from "./i18n";
import { download, ErrorBox, Modal, Note } from "./ui";

const MIN = 8;

export default function AppExportAll({
  tenantId,
  ids,
  total,
  onClose,
}: {
  tenantId: number;
  ids: number[]; // Auswahl (leer = alle)
  total: number;
  onClose: () => void;
}) {
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const count = ids.length || total;
  const tooShort = pw.length < MIN;
  const differ = pw2 !== "" && pw !== pw2;
  const ready = !tooShort && pw === pw2 && !busy;

  const run = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!ready) return;
    setBusy(true);
    setErr(null);
    try {
      const { blob, filename } = await exportAllApplications(tenantId, pw, ids);
      download(filename, blob);
      onClose();
    } catch (ex) {
      setErr(ex);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={L("Doku aller Applikationen exportieren", "Export documentation of all applications")}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-primary" id="apps-export-all-run" disabled={!ready} onClick={() => run()}>
            {busy
              ? L("Exportiere …", "Exporting …")
              : L(`${count} exportieren`, `Export ${count}`)}
          </button>
        </>
      }
    >
      <ErrorBox error={err} onClose={() => setErr(null)} />
      <p>
        {L(
          `${count} Applikation${count === 1 ? "" : "en"}${ids.length ? " (Auswahl)" : ""}: je Applikation ein ZIP mit der Doku als Word und dem Netzwerkplan (draw.io), jedes mit dem Export-Passwort verschlüsselt (AES-256), alle in einem Download.`,
          `${count} application${count === 1 ? "" : "s"}${ids.length ? " (selection)" : ""}: one ZIP per application with the documentation as Word and the network diagram (draw.io), each encrypted with the export password (AES-256), all in one download.`,
        )}
      </p>
      <form onSubmit={run}>
        <div className="field">
          <label htmlFor="exp-pw">{L("Export-Passwort", "Export password")}</label>
          <input
            id="exp-pw"
            type="password"
            autoComplete="new-password"
            autoFocus
            value={pw}
            onChange={(e) => setPw(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="exp-pw2">{L("Passwort wiederholen", "Repeat password")}</label>
          <input
            id="exp-pw2"
            type="password"
            autoComplete="new-password"
            value={pw2}
            onChange={(e) => setPw2(e.target.value)}
          />
        </div>
        {pw !== "" && tooShort && (
          <p className="faint">{L(`mindestens ${MIN} Zeichen`, `at least ${MIN} characters`)}</p>
        )}
        {differ && <p className="faint">{L("Passwörter stimmen nicht überein", "Passwords do not match")}</p>}
        <button type="submit" hidden />
      </form>
      <Note>
        {L(
          "Das Passwort wird nicht gespeichert – ohne es lassen sich die ZIPs nicht öffnen. Öffnen mit 7-Zip oder WinRAR – der Windows-Explorer kennt AES je nach Version nicht. Bei vielen Applikationen dauert der Export einige Minuten.",
          "The password is not stored – without it the ZIPs cannot be opened. Open with 7-Zip or WinRAR – depending on the version, the Windows Explorer does not support AES. With many applications the export takes a few minutes.",
        )}
      </Note>
    </Modal>
  );
}
