// Zeitplan eines Quellsystems (Cron, 5 Felder, Zeitzone des Orchestrators - Standard
// Europe/Zurich): Vorlagen für die üblichen Fälle, sonst eigener Ausdruck.
import { useState } from "react";

const PRESETS: [string, string][] = [
  ["0 * * * *", "stündlich"],
  ["0 2 * * *", "täglich 02:00"],
  ["0 6 * * 1-5", "werktags 06:00"],
  ["0 3 * * 0", "wöchentlich, Sonntag 03:00"],
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
      <label htmlFor="ss-schedule">Zeitplan</label>
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
        <option value="">kein Zeitplan (nur „Jetzt einlesen“)</option>
        {PRESETS.map(([e, label]) => (
          <option key={e} value={e}>
            {label}
          </option>
        ))}
        <option value={CUSTOM}>eigener Ausdruck (cron)</option>
      </select>
      {mode === CUSTOM && (
        <input
          className="input mono"
          style={{ marginTop: 6 }}
          value={value}
          placeholder="Minute Stunde Tag Monat Wochentag, z. B. 30 1 * * 1-5"
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      <div className="hint">
        Legt zum Termin einen Lauf an (Gerät mit Agent: der Agent holt ihn ab). Läuft oder wartet noch einer, fällt der
        Termin aus.
      </div>
    </div>
  );
}
