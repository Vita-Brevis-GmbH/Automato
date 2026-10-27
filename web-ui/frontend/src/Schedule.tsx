// Zeitplan eines Quellsystems (Cron, 5 Felder, Zeitzone des Orchestrators - Standard
// Europe/Zurich): Vorlagen für die üblichen Fälle, sonst eigener Ausdruck.
import { useState } from "react";
import { L } from "./i18n";

const PRESETS: [string, string][] = [
  ["0 * * * *", L("stündlich", "hourly")],
  ["0 2 * * *", L("täglich 02:00", "daily 02:00")],
  ["0 6 * * 1-5", L("werktags 06:00", "weekdays 06:00")],
  ["0 3 * * 0", L("wöchentlich, Sonntag 03:00", "weekly, Sunday 03:00")],
];
const CUSTOM = "custom";

export function scheduleLabel(expr: string | null): string | null {
  if (!expr) return null;
  return PRESETS.find(([e]) => e === expr)?.[1] ?? expr;
}

export function ScheduleField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const preset = value === "" || PRESETS.some(([e]) => e === value) ? value : CUSTOM;
  const [custom, setCustom] = useState(preset === CUSTOM);
  const mode = custom ? CUSTOM : preset;
  return (
    <div className="field">
      <label htmlFor="ss-schedule">{L("Zeitplan", "Schedule")}</label>
      <select
        id="ss-schedule"
        className="sel input"
        value={mode}
        onChange={(e) => {
          const v = e.target.value;
          setCustom(v === CUSTOM);
          if (v !== CUSTOM) onChange(v);
        }}
      >
        <option value="">{L("kein Zeitplan (nur „Jetzt einlesen“)", "no schedule (only “Collect now”)")}</option>
        {PRESETS.map(([e, label]) => (
          <option key={e} value={e}>
            {label}
          </option>
        ))}
        <option value={CUSTOM}>{L("eigener Ausdruck (cron)", "custom expression (cron)")}</option>
      </select>
      {mode === CUSTOM && (
        <input
          className="input mono"
          style={{ marginTop: 6 }}
          value={value}
          placeholder={L(
            "Minute Stunde Tag Monat Wochentag, z. B. 30 1 * * 1-5",
            "Minute hour day month weekday, e.g. 30 1 * * 1-5",
          )}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      <div className="hint">
        {L(
          "Legt zum Termin einen Lauf an (Gerät mit Agent: der Agent holt ihn ab). Läuft oder wartet noch einer, fällt der Termin aus.",
          "Creates a run at the scheduled time (device with agent: the agent picks it up). If one is still running or waiting, the slot is skipped.",
        )}
      </div>
    </div>
  );
}
