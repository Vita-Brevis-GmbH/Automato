// Stand der Doku (0.108.0, ADR 0037, Anwender: „das Speichern dauert zu lange“): die Doku bzw. die
// Verbindungen kommen sofort als letzter Stand; hat sich seither etwas geändert, rechnet der Server im
// Hintergrund neu. Die Zeile sagt, von wann der Stand ist, fragt während des Rechnens nach und lädt neu,
// sobald er fertig ist; „↻ neu berechnen“ stösst eine Berechnung an.
import { useEffect, useState } from "react";

import { DocSnapshot, fetchDocState } from "./api";
import { L } from "./i18n";
import { fmtTime } from "./ui";

const POLL_MS = 2000;

export function SnapshotBar({
  snapshot,
  appId,
  kind,
  onReload,
}: {
  snapshot?: DocSnapshot | null;
  appId: number;
  kind: "doc" | "connections";
  onReload: (force: boolean) => void; // neu laden (force: neu berechnen lassen)
}) {
  const [building, setBuilding] = useState(!!snapshot?.building);
  useEffect(() => setBuilding(!!snapshot?.building), [snapshot]);
  useEffect(() => {
    if (!building) return;
    let stop = false;
    const timer = setInterval(() => {
      fetchDocState(appId, kind)
        .then((s) => {
          if (stop || s.building) return;
          setBuilding(false);
          onReload(false);
        })
        .catch(() => undefined); // nächster Versuch
    }, POLL_MS);
    return () => {
      stop = true;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [building, appId, kind]);
  if (!snapshot?.built_at) return null;
  const seconds = snapshot.seconds >= 1 ? Math.round(snapshot.seconds) : null;
  return (
    <div className={`snapshot-bar no-print ${building ? "building" : ""}`} id={`snapshot-${kind}`} data-fresh={snapshot.fresh ? "1" : "0"}>
      <span title={seconds ? L(`berechnet in ${seconds} s`, `computed in ${seconds} s`) : undefined}>
        {L("Stand von", "As of")} {fmtTime(snapshot.built_at)}
      </span>
      {building ? (
        <span className="faint">{L(" – wird im Hintergrund aktualisiert…", " – updating in the background…")}</span>
      ) : (
        !snapshot.fresh && <span className="faint">{L(" – nicht mehr aktuell", " – no longer current")}</span>
      )}
      <button
        className="linkbtn"
        disabled={building}
        title={L("im Hintergrund neu berechnen – der Stand bleibt bis dahin sichtbar", "recalculate in the background – the current state stays visible until then")}
        onClick={() => onReload(true)}
      >
        ↻ {L("neu berechnen", "recalculate")}
      </button>
    </div>
  );
}
