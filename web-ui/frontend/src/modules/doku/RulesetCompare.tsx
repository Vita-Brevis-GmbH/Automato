// Regelwerke vergleichen (0.120.0; Anwender 2026-11-02: „Vergleich … aber nur mit Klicken auf einen Button, damit nicht
// zu viele Ressourcen verbraucht werden beim Ausrechnen der Regeln“). Das Fenster lädt nur die Auswahl der Profile;
// gerechnet wird erst mit „Vergleichen“ – die Doku mit dem aktiven und mit dem gewählten Regelwerk, nichts gespeichert.
// Zeigt, welche Regeln unter „Regeln & Contracts“ bzw. in den Kategorien dazukommen oder wegfallen, mit der Bedingung,
// die entscheidet, und die gezählten Regeln beider.
import { useEffect, useState } from "react";
import { compareRuleset, fetchRulesetChoice, RulesetChoice, RulesetCompare as Result, RulesetDiffItem, RulesetSide } from "../../api";
import { ErrorBox, Loading, Modal } from "../../ui";
import { L } from "../../i18n";

function Items({ id, title, items, sign, onRule }: { id: string; title: string; items: RulesetDiffItem[]; sign: "+" | "−"; onRule: (id: number) => void }) {
  return (
    <div id={id} style={{ marginTop: 10 }}>
      <div style={{ fontWeight: 600 }}>
        {sign} {items.length} {title}
      </div>
      {items.length === 0 ? (
        <div className="faint" style={{ fontSize: 12 }}>
          {L("keine", "none")}
        </div>
      ) : (
        <table className="compact">
          <tbody>
            {items.map((x) => (
              <tr key={x.rule_id}>
                <td>
                  <button className="linkbtn" onClick={() => onRule(x.rule_id)}>
                    {x.name}
                  </button>
                </td>
                <td className="faint">{x.source}</td>
                <td className="faint" style={{ fontSize: 12 }}>
                  {x.why ? `${x.why} – ${x.effect_label}` : x.effect_label}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Counted({ side }: { side: RulesetSide }) {
  return (
    <div>
      <div style={{ fontWeight: 600 }}>{side.title}</div>
      <div className="faint" style={{ fontSize: 12 }}>
        {L(`${side.rules} Regeln & Contracts · ${side.shown} in den Kategorien`, `${side.rules} rules & contracts · ${side.shown} in the categories`)}
      </div>
      {side.filtered.map((f) => (
        <div key={f.key} className="faint" style={{ fontSize: 12 }}>
          {L(`${f.count} gezählt: ${f.title}`, `${f.count} counted: ${f.title}`)}
        </div>
      ))}
    </div>
  );
}

export default function RulesetCompare({ appId, onRule, onClose }: { appId: number; onRule: (id: number) => void; onClose: () => void }) {
  const [choice, setChoice] = useState<RulesetChoice | null>(null);
  const [key, setKey] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  useEffect(() => {
    fetchRulesetChoice(appId)
      .then((c) => {
        setChoice(c);
        setKey(c.profiles.find((p) => p.key !== c.current.key)?.key ?? "");
      })
      .catch(setErr);
  }, [appId]);

  const run = () => {
    setBusy(true);
    setErr(null);
    setResult(null);
    compareRuleset(appId, key)
      .then(setResult)
      .catch(setErr)
      .finally(() => setBusy(false));
  };
  const others = choice?.profiles.filter((p) => p.key !== choice.current.key) ?? [];

  return (
    <Modal title={L("Regelwerke vergleichen", "Compare rulesets")} onClose={onClose} wide>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {!choice ? (
        <Loading />
      ) : (
        <div id="ruleset-compare">
          <div className="filterbar" style={{ alignItems: "center" }}>
            <span className="faint">
              {L("Aktiv:", "Active:")} <b>{choice.current.title}</b> {L("mit", "with")}
            </span>
            <select className="input" id="ruleset-compare-key" style={{ maxWidth: 380 }} value={key} onChange={(e) => setKey(e.target.value)}>
              {others.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.title}
                </option>
              ))}
            </select>
            <button className="btn btn-primary btn-sm" id="ruleset-compare-run" disabled={busy || !key} onClick={run}>
              {busy ? L("Rechne…", "Computing…") : L("Vergleichen", "Compare")}
            </button>
          </div>
          <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>
            {others.find((p) => p.key === key)?.description}
          </div>
          {!result && !busy && (
            <div className="faint" style={{ fontSize: 12, marginTop: 8 }}>
              {L(
                "Rechnet die Doku dieser Applikation mit beiden Regelwerken – erst auf Knopfdruck, bei grossen Applikationen kann es dauern. Die Doku selbst bleibt beim aktiven Regelwerk.",
                "Computes the documentation of this application with both rulesets – only on click, it may take a while for large applications. The documentation itself stays with the active ruleset.",
              )}
            </div>
          )}
          {busy && <Loading />}
          {result && (
            <div id="ruleset-compare-result" style={{ marginTop: 10 }}>
              <div className="faint" style={{ fontSize: 12 }}>
                {L(`berechnet in ${result.seconds} s`, `computed in ${result.seconds} s`)}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 6 }}>
                <Counted side={result.current} />
                <Counted side={result.other} />
              </div>
              <h3 style={{ marginTop: 14 }}>{L(`Mit „${result.other.title}“ – Regeln & Contracts`, `With “${result.other.title}” – rules & contracts`)}</h3>
              <Items id="ruleset-compare-rules-added" title={L("kommen dazu (Grund: was im aktiven entschied)", "added (reason: what decided in the active one)")} items={result.rules.added} sign="+" onRule={onRule} />
              <Items id="ruleset-compare-rules-removed" title={L("fallen weg", "removed")} items={result.rules.removed} sign="−" onRule={onRule} />
              <h3 style={{ marginTop: 14 }}>{L("In den Kategorien", "In the categories")}</h3>
              <Items id="ruleset-compare-cat-added" title={L("neu in den Kategorien", "new in the categories")} items={result.categories.added} sign="+" onRule={onRule} />
              <Items id="ruleset-compare-cat-removed" title={L("nicht mehr in den Kategorien", "no longer in the categories")} items={result.categories.removed} sign="−" onRule={onRule} />
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
