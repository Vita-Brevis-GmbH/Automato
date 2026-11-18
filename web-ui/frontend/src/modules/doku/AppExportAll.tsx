// Gesamt-Export der Applikations-Doku (1.0.1, Anwender: „Export aller Dokumentationen der Applikationen als
// mehrere Zips, welche mit einem Export Passwort verschlüsselt sind“): je Applikation ein ZIP (Word und
// Netzwerkplan), jedes mit dem Export-Passwort AES-256-verschlüsselt, alle in einem Download.
// 1.21.0 (ADR 0069, Anwender: „eine Auswahl treffen …, welche Applikationen ich herunterladen möchte“, „als
// Hintergrunddienst laufen lassen“): Auswahl mit Suche, „alle“/„keine“; der Export läuft als Auftrag im
// Hintergrund - „Meine Exporte“ zeigt Fortschritt, Fehler je Applikation und den Download.
import { FormEvent, useMemo, useState } from "react";
import type { Application } from "../../api";
import { L } from "../../i18n";
import { ErrorBox, Icon, Modal, Note } from "../../ui";
import { startAppExport } from "./exportApi";
import MyExports from "./MyExports";

const MIN = 8;

export default function AppExportAll({
  tenantId,
  apps,
  preselected,
  onClose,
  onStarted,
}: {
  tenantId: number;
  apps: Application[]; // Applikationen der Liste (Mandant, Kunde)
  preselected: number[]; // Auswahl der Liste (leer = alle)
  onClose: () => void;
  onStarted?: () => void;
}) {
  const [chosen, setChosen] = useState<Set<number>>(
    () => new Set(preselected.length ? preselected : apps.map((a) => a.id)),
  );
  const [q, setQ] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [started, setStarted] = useState(0); // Anzahl gestarteter Exporte (lädt „Meine Exporte“ neu)

  const sorted = useMemo(() => [...apps].sort((a, b) => a.name.localeCompare(b.name)), [apps]);
  const needle = q.trim().toLowerCase();
  const shown = needle
    ? sorted.filter((a) =>
        [a.name, a.external_name, a.internal_name, a.code, a.owner].some((v) => v?.toLowerCase().includes(needle)),
      )
    : sorted;
  const count = apps.filter((a) => chosen.has(a.id)).length;
  const tooShort = pw.length < MIN;
  const differ = pw2 !== "" && pw !== pw2;
  const ready = !tooShort && pw === pw2 && count > 0 && !busy;

  const toggle = (id: number) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const setShown = (on: boolean) =>
    setChosen((prev) => {
      const next = new Set(prev);
      for (const a of shown) {
        if (on) next.add(a.id);
        else next.delete(a.id);
      }
      return next;
    });

  const run = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!ready) return;
    setBusy(true);
    setErr(null);
    try {
      await startAppExport(
        tenantId,
        pw,
        sorted.filter((a) => chosen.has(a.id)).map((a) => a.id),
      );
      setPw(""); // das Passwort bleibt nicht im Formular stehen
      setPw2("");
      setStarted((n) => n + 1);
      onStarted?.();
    } catch (ex) {
      setErr(ex);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      wide
      title={L("Doku der Applikationen exportieren", "Export documentation of the applications")}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Schliessen", "Close")}
          </button>
          <button className="btn btn-primary" id="apps-export-all-run" disabled={!ready} onClick={() => run()}>
            {busy ? L("Starte …", "Starting …") : L(`${count} exportieren`, `Export ${count}`)}
          </button>
        </>
      }
    >
      <ErrorBox error={err} onClose={() => setErr(null)} />
      <p>
        {L(
          "Je Applikation ein ZIP mit der Doku als Word und dem Netzwerkplan (draw.io), jedes mit dem Export-Passwort verschlüsselt (AES-256), alle in einem Download. Der Export läuft im Hintergrund – der Dialog darf geschlossen werden.",
          "One ZIP per application with the documentation as Word and the network diagram (draw.io), each encrypted with the export password (AES-256), all in one download. The export runs in the background – the dialog may be closed.",
        )}
      </p>
      <div className="field">
        <div className="label">
          {L(`Applikationen (${count} von ${apps.length} gewählt)`, `Applications (${count} of ${apps.length} selected)`)}
        </div>
        <div className="filterbar" style={{ marginBottom: 8 }}>
          <div className="search">
            <span className="ico">
              <Icon name="search" />
            </span>
            <input
              id="exp-search"
              type="search"
              placeholder={L("Suchen (Name, Kürzel, Verantwortlich)", "Search (name, code, owner)")}
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          <button className="btn btn-ghost btn-sm" id="exp-all" onClick={() => setShown(true)}>
            {needle ? L("alle gefundenen", "all found") : L("alle", "all")}
          </button>
          <button className="btn btn-ghost btn-sm" id="exp-none" onClick={() => setShown(false)}>
            {needle ? L("keine der gefundenen", "none of the found") : L("keine", "none")}
          </button>
        </div>
        <div id="exp-apps" style={{ maxHeight: 240, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 8, padding: "4px 8px" }}>
          {shown.length === 0 ? (
            <p className="faint">{L("Keine Applikation gefunden.", "No application found.")}</p>
          ) : (
            shown.map((a) => (
              <label key={a.id} style={{ display: "flex", gap: 8, alignItems: "center", padding: "2px 0" }} data-app-id={a.id}>
                <input type="checkbox" checked={chosen.has(a.id)} onChange={() => toggle(a.id)} />
                <span>{a.name}</span>
                {a.code && <span className="faint mono">{a.code}</span>}
              </label>
            ))
          )}
        </div>
      </div>
      <form onSubmit={run}>
        <div className="field">
          <label htmlFor="exp-pw">{L("Export-Passwort", "Export password")}</label>
          <input
            id="exp-pw"
            type="password"
            autoComplete="new-password"
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
          "Das Passwort wird nicht gespeichert – ohne es lassen sich die ZIPs nicht öffnen; startet der Orchestrator während des Exports neu, ist der Export abgebrochen. Öffnen mit 7-Zip oder WinRAR – der Windows-Explorer kennt AES je nach Version nicht.",
          "The password is not stored – without it the ZIPs cannot be opened; if the orchestrator restarts during the export, the export is aborted. Open with 7-Zip or WinRAR – depending on the version, the Windows Explorer does not support AES.",
        )}
      </Note>
      <h3 style={{ marginTop: 16 }}>{L("Meine Exporte", "My exports")}</h3>
      <MyExports tenantId={tenantId} refresh={started} />
    </Modal>
  );
}
