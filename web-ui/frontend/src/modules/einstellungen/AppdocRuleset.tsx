// Regelwerk der Applikations-Doku im Namensprofil (0.118.0, ADR 0043; Anwender 2026-10-30: „Regeln in die Settings
// übernehmen … aktivieren oder deaktivieren … die Wirkung einstellen … die beiden Regelsets hinterlegen, damit man
// umschalten kann. Wenn es neue Reihenfolgen gibt, immer dort ein neues Profil anlegen“). Die Bedingungen stehen im
// Orchestrator (`store/appdoc_ruleset.py`); je Profil Schalter, Wirkung und Reihenfolge. Eingebaute Profile bleiben
// unverändert – Änderungen als neues Profil, eine neue Reihenfolge immer als neues Profil. Umschalten, speichern und
// löschen = Admin; gilt für die Mandanten mit diesem Namensprofil.
import { useEffect, useMemo, useState } from "react";
import {
  activateAppdocRuleset,
  AppdocRuleset,
  createAppdocRuleset,
  deleteAppdocRuleset,
  fetchAppdocRuleset,
  RulesetIn,
  RulesetRule,
  updateAppdocRuleset,
} from "../../api";
import { ErrorBox, Loading } from "../../ui";
import { L } from "../../i18n";

type Draft = Pick<RulesetRule, "key" | "on" | "effect" | "lifts">;

const toDraft = (rules: RulesetRule[]): Draft[] => rules.map((r) => ({ key: r.key, on: r.on, effect: r.effect, lifts: [...r.lifts] }));

// Ausnahmen wirken nur auf Regeln darunter: nach dem Verschieben nur noch diese
function clean(draft: Draft[]): Draft[] {
  return draft.map((r, i) => {
    const below = new Set(draft.slice(i + 1).map((x) => x.key));
    return { ...r, lifts: r.effect === "except" ? r.lifts.filter((k) => below.has(k)) : [] };
  });
}

export default function AppdocRulesetCard({ profileId, editable }: { profileId: number; editable: boolean }) {
  const [data, setData] = useState<AppdocRuleset | null>(null);
  const [sel, setSel] = useState("");
  const [draft, setDraft] = useState<Draft[]>([]);
  const [title, setTitle] = useState("");
  const [naming, setNaming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [note, setNote] = useState("");

  const show = (d: AppdocRuleset, key: string) => {
    const p = d.profiles.find((x) => x.key === key) ?? d.profiles.find((x) => x.key === d.active) ?? d.profiles[0];
    setData(d);
    setSel(p.key);
    setDraft(toDraft(p.rules));
  };
  useEffect(() => {
    fetchAppdocRuleset(profileId)
      .then((d) => show(d, d.active))
      .catch(setErr);
  }, [profileId]);

  const profile = data?.profiles.find((p) => p.key === sel);
  const byKey = useMemo(() => Object.fromEntries((data?.conditions ?? []).map((c) => [c.key, c])), [data]);
  const base = profile ? toDraft(profile.rules) : [];
  const dirty = JSON.stringify(clean(draft)) !== JSON.stringify(clean(base));
  const reordered = draft.map((r) => r.key).join() !== base.map((r) => r.key).join();
  const own = profile !== undefined && !profile.builtin;

  if (err && !data) return <ErrorBox error={err} onClose={() => setErr(null)} />;
  if (!data || !profile) return <Loading />;

  const run = (fn: () => Promise<AppdocRuleset>, key: (d: AppdocRuleset) => string, done: string) => {
    setBusy(true);
    setErr(null);
    fn()
      .then((d) => {
        show(d, key(d));
        setNote(done);
        setNaming(false);
      })
      .catch(setErr)
      .finally(() => setBusy(false));
  };
  const body = (t: string): RulesetIn => ({ title: t.trim(), based_on: profile.key, rules: clean(draft) });
  const patch = (i: number, p: Partial<Draft>) => {
    setNote("");
    setDraft(draft.map((r, j) => (j === i ? { ...r, ...p } : r)));
  };
  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= draft.length) return;
    const next = [...draft];
    [next[i], next[j]] = [next[j], next[i]];
    setNote("");
    setDraft(next);
  };

  return (
    <div className="card" id="appdoc-ruleset">
      <div className="card-head">
        <h2>{L("Regelwerk der Applikations-Doku", "Ruleset of the application documentation")}</h2>
        <span className="faint">
          {L(
            "Von oben nach unten: die erste eingeschaltete Regel, die eine Zeile der Doku trifft, entscheidet die Wirkung; trifft keine, steht die Zeile in ihrer Kategorie.",
            "Top to bottom: the first enabled rule that matches a row of the documentation decides the effect; if none matches, the row stays in its category.",
          )}
        </span>
      </div>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      <div className="card-body pad">
        <div className="filterbar" style={{ marginBottom: 8, alignItems: "center" }}>
          <label className="faint" htmlFor="ruleset-profile">
            {L("Profil", "Profile")}
          </label>
          <select id="ruleset-profile" className="input" style={{ maxWidth: 420 }} value={sel} onChange={(e) => show(data, e.target.value)}>
            {data.profiles.map((p) => (
              <option key={p.key} value={p.key}>
                {p.title}
                {p.key === data.active ? L(" (aktiv)", " (active)") : ""}
                {p.builtin ? "" : L(" – eigen", " – own")}
              </option>
            ))}
          </select>
          {sel === data.active ? (
            <span className="chip ok" id="ruleset-active">
              {L("aktiv", "active")}
            </span>
          ) : (
            editable && (
              <button
                className="btn btn-primary btn-sm"
                id="ruleset-activate"
                disabled={busy || dirty}
                title={dirty ? L("Zuerst speichern oder verwerfen", "Save or discard first") : undefined}
                onClick={() => run(() => activateAppdocRuleset(profileId, sel), () => sel, L("Aktiviert – die Doku ordnet beim nächsten Laden neu.", "Activated – the documentation re-sorts on the next load."))}
              >
                {L("Aktivieren", "Activate")}
              </button>
            )
          )}
          <div className="spacer" />
          {editable && dirty && (
            <button className="btn btn-ghost btn-sm" id="ruleset-discard" disabled={busy} onClick={() => show(data, sel)}>
              {L("Verwerfen", "Discard")}
            </button>
          )}
          {editable && own && (
            <button
              className="btn btn-sm"
              id="ruleset-save"
              disabled={busy || !dirty || reordered}
              title={reordered ? L("Neue Reihenfolge – als neues Profil speichern", "New order – save as a new profile") : undefined}
              onClick={() => run(() => updateAppdocRuleset(profileId, sel, body(profile.title_de)), () => sel, L("Gespeichert.", "Saved."))}
            >
              {L("Speichern", "Save")}
            </button>
          )}
          {editable && (
            <button
              className="btn btn-sm"
              id="ruleset-save-new"
              disabled={busy}
              onClick={() => {
                setTitle(dirty ? "" : L(`Kopie von ${profile.title}`, `Copy of ${profile.title}`));
                setNaming(true);
              }}
            >
              {L("Als neues Profil speichern", "Save as new profile")}
            </button>
          )}
          {editable && own && (
            <button
              className="btn btn-ghost btn-sm"
              id="ruleset-delete"
              disabled={busy}
              onClick={() => {
                if (!window.confirm(L(`Profil „${profile.title}“ löschen?`, `Delete profile “${profile.title}”?`))) return;
                run(() => deleteAppdocRuleset(profileId, sel), (d) => d.active, L("Gelöscht.", "Deleted."));
              }}
            >
              {L("Löschen", "Delete")}
            </button>
          )}
        </div>
        {naming && (
          <div className="filterbar" id="ruleset-new" style={{ marginBottom: 8 }}>
            <input
              className="input"
              id="ruleset-title"
              autoFocus
              style={{ maxWidth: 420 }}
              placeholder={L("Titel des neuen Profils", "Title of the new profile")}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
            <button
              className="btn btn-primary btn-sm"
              id="ruleset-create"
              disabled={busy || !title.trim()}
              onClick={() => run(() => createAppdocRuleset(profileId, body(title)), (d) => d.saved ?? sel, L("Als neues Profil gespeichert – zum Verwenden aktivieren.", "Saved as a new profile – activate it to use it."))}
            >
              {L("Anlegen", "Create")}
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => setNaming(false)}>
              {L("Abbrechen", "Cancel")}
            </button>
          </div>
        )}
        {note && !dirty && (
          <div className="faint" id="ruleset-note" style={{ fontSize: 12, marginBottom: 6 }}>
            {note}
          </div>
        )}
        {editable && profile.builtin && dirty && (
          <div className="faint" style={{ fontSize: 12, marginBottom: 6, color: "var(--warn)" }}>
            {L("Eingebaute Profile bleiben unverändert – Änderungen als neues Profil speichern.", "Built-in profiles stay unchanged – save changes as a new profile.")}
          </div>
        )}
        <table className="compact form-table" id="ruleset-rules">
          <colgroup>
            <col style={{ width: 36 }} />
            <col style={{ width: 44 }} />
            <col />
            <col style={{ width: "34%" }} />
            <col style={{ width: 70 }} />
          </colgroup>
          <thead>
            <tr>
              <th>{L("Nr.", "No.")}</th>
              <th>{L("Ein", "On")}</th>
              <th>{L("Bedingung", "Condition")}</th>
              <th>{L("Wirkung", "Effect")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {draft.map((r, i) => {
              const c = byKey[r.key];
              const below = draft.slice(i + 1);
              return (
                <tr key={r.key} data-rule={r.key} className={r.on ? "" : "faint"}>
                  <td>
                    <b>{i + 1}</b>
                  </td>
                  <td>
                    <input type="checkbox" aria-label={L(`${c?.title ?? r.key} einschalten`, `Enable ${c?.title ?? r.key}`)} checked={r.on} disabled={!editable} onChange={(e) => patch(i, { on: e.target.checked })} />
                  </td>
                  <td>
                    <div>{c?.title ?? r.key}</div>
                    <div className="faint" style={{ fontSize: 12 }}>
                      {c?.text}
                      {c?.since ? L(` · seit ${c.since}`, ` · since ${c.since}`) : ""}
                    </div>
                  </td>
                  <td>
                    {(c?.effects.length ?? 0) > 1 ? (
                      <select className="input effect" aria-label={L(`Wirkung ${c?.title ?? r.key}`, `Effect ${c?.title ?? r.key}`)} value={r.effect} disabled={!editable} onChange={(e) => patch(i, { effect: e.target.value })}>
                        {c?.effects.map((e) => (
                          <option key={e.key} value={e.key}>
                            {e.label}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span>{c?.effects[0]?.label ?? r.effect}</span>
                    )}
                    {r.effect === "except" && (
                      <div className="chips" style={{ marginTop: 4 }}>
                        {below.map((b) => (
                          <label key={b.key} className={`chip ${r.lifts.includes(b.key) ? "info" : ""}`} style={{ cursor: editable ? "pointer" : "default" }}>
                            <input
                              type="checkbox"
                              style={{ marginRight: 4 }}
                              checked={r.lifts.includes(b.key)}
                              disabled={!editable}
                              onChange={(e) => patch(i, { lifts: e.target.checked ? [...r.lifts, b.key] : r.lifts.filter((k) => k !== b.key) })}
                            />
                            {draft.indexOf(b) + 1}
                          </label>
                        ))}
                        <span className="faint" style={{ fontSize: 11 }}>
                          {L("hebt diese Regeln auf", "lifts these rules")}
                        </span>
                      </div>
                    )}
                  </td>
                  <td>
                    {editable && (
                      <span style={{ whiteSpace: "nowrap" }}>
                        <button className="btn btn-ghost btn-sm" title={L("nach oben", "up")} disabled={i === 0} onClick={() => move(i, -1)}>
                          ↑
                        </button>
                        <button className="btn btn-ghost btn-sm" title={L("nach unten", "down")} disabled={i === draft.length - 1} onClick={() => move(i, 1)}>
                          ↓
                        </button>
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>
          {L(
            "Wirkungen: „nicht gezeigt, nur gezählt“ steht als Zahl im Kopf von „Regeln & Contracts“; „nicht gezeigt, nicht gezählt“ fällt ganz weg; „nur als Treffer“ steht unter „Regeln & Contracts“, nicht in den Kategorien.",
            "Effects: “not shown, only counted” appears as a number in the head of “Rules & contracts”; “not shown, not counted” is dropped entirely; “only as a hit” appears under “Rules & contracts”, not in the categories.",
          )}
        </div>
      </div>
    </div>
  );
}
