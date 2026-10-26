// Betriebszustand (0.12.0): Ampel je Prüfung – Plattform (Datenbank, Zeitplan, zentraler Worker),
// Agenten, Quellsysteme, NetBox-Sync und Sicherung. Daten aus GET /system/health.
import type { View } from "./App";
import { OpsHealth, HealthCheck } from "./api";
import { fmtTime } from "./ui";

const LIGHT: Record<string, [string, string]> = {
  ok: ["ok", "OK"],
  warn: ["warn", "Warnung"],
  error: ["crit", "Fehler"],
};
const AREAS = ["Plattform", "Agenten", "Quellsysteme", "NetBox-Sync", "Sicherung"];

function Line({ c, onGo }: { c: HealthCheck; onGo?: (v: View) => void }) {
  const [cls, label] = LIGHT[c.status] ?? ["na", c.status];
  return (
    <tr>
      <td className="nowrap">
        <span className={`chip ${cls}`}>{label}</span>
      </td>
      <td>
        {onGo && c.view ? (
          <button className="linkbtn" onClick={() => onGo(c.view as View)}>
            {c.title}
          </button>
        ) : (
          c.title
        )}
        {c.detail && <div className={c.status === "ok" ? "faint" : "run-msg err"}>{c.detail}</div>}
      </td>
      <td className="nowrap faint">{c.since ? fmtTime(c.since) : ""}</td>
    </tr>
  );
}

// Alle Prüfungen nach Bereich (System-Seite) bzw. nur die Probleme (Übersicht)
export function HealthTable({
  health,
  onlyProblems = false,
  onGo,
}: {
  health: OpsHealth;
  onlyProblems?: boolean;
  onGo?: (v: View) => void;
}) {
  const shown = health.checks.filter((c) => !onlyProblems || c.status !== "ok");
  if (!shown.length) return <p className="muted">Alle Prüfungen in Ordnung.</p>;
  return (
    <table>
      <tbody>
        {AREAS.flatMap((area) => {
          const rows = shown.filter((c) => c.area === area);
          if (!rows.length) return [];
          return [
            <tr key={`h-${area}`} className="group-row">
              <td colSpan={3}>
                <b>{area}</b>
              </td>
            </tr>,
            ...rows.map((c) => <Line key={c.key} c={c} onGo={onGo} />),
          ];
        })}
      </tbody>
    </table>
  );
}

export function HealthChip({ health }: { health: OpsHealth }) {
  const [cls] = LIGHT[health.status] ?? ["na"];
  return (
    <span className={`chip ${cls}`}>
      {health.problems ? `${health.problems} ${health.problems === 1 ? "Problem" : "Probleme"}` : "alles in Ordnung"}
    </span>
  );
}
