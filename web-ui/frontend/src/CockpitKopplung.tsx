import { useCallback, useEffect, useState } from "react";
import {
  CockpitCode,
  CockpitCoupling,
  createCockpitCode,
  fetchCockpitCoupling,
  uncoupleCockpit,
  withdrawCockpitCode,
} from "./api";
import { L } from "./i18n";
import { ErrorBox, fmtTime, Note } from "./ui";

// Kopplung mit dem Cockpit (1.13.0, ADR 0057; Anwender 2026-11-16, Cockpit ADR 0011 Stufe C:
// „künftig Kopplung mit Einmal-Code“ statt Token kopieren). Der Admin erzeugt hier einen Einmal-Code
// (nur jetzt angezeigt, 15 Minuten, einmal, höchstens 5 Fehlversuche) und gibt ihn im Cockpit ein;
// das Cockpit löst ihn mit POST /api/kopplung ein und bekommt sein Token direkt von Automato.
// Solange der Code angezeigt ist, fragt die Karte alle 5 s nach, ob das Cockpit gekoppelt hat.

const POLL_MS = 5000;

function CodeBox({ code }: { code: CockpitCode }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* Zwischenablage nicht verfügbar (ohne HTTPS) -> von Hand markieren */
    }
  };
  return (
    <div id="kopplung-code-box" style={{ marginTop: 10 }}>
      <div className="hint">{L("Einmal-Code – nur jetzt angezeigt", "Pairing code – shown only now")}</div>
      <div className="cmd">
        <pre id="kopplung-code" style={{ fontSize: 20, letterSpacing: 2 }}>
          {code.code}
        </pre>
        <button className="btn btn-ghost btn-sm copy" onClick={copy}>
          {copied ? L("Kopiert ✓", "Copied ✓") : L("Kopieren", "Copy")}
        </button>
      </div>
      <div className="hint">
        {L(
          `Im Cockpit bei der Automato-Instanz unter „Koppeln“ eingeben – gültig bis ${fmtTime(code.expires)}, einmal, höchstens ${code.max_failures} Fehlversuche. Ein neuer Code ersetzt diesen. Nur im Cockpit eingeben, nie in Chat, Ticket oder Datei.`,
          `Enter it in the cockpit at the Automato instance under “Pair” – valid until ${fmtTime(code.expires)}, once, at most ${code.max_failures} failed attempts. A new code replaces this one. Enter it only in the cockpit, never in chat, ticket or file.`,
        )}
      </div>
    </div>
  );
}

export default function CockpitKopplung() {
  const [state, setState] = useState<CockpitCoupling | null>(null);
  const [code, setCode] = useState<CockpitCode | null>(null);
  const [before, setBefore] = useState<string | null>(null); // gekoppelt seit – vor dem Code
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [paired, setPaired] = useState(false);

  const load = useCallback(() => {
    fetchCockpitCoupling().then(setState).catch(setErr);
  }, []);
  useEffect(load, [load]);

  // Code angezeigt: nachfragen, bis das Cockpit ihn eingelöst hat bzw. er nicht mehr offen ist
  useEffect(() => {
    if (!code) return;
    const timer = window.setInterval(() => {
      fetchCockpitCoupling()
        .then((s) => {
          setState(s);
          if (!s.code) {
            setPaired(s.coupled && s.coupled_at !== before);
            setCode(null);
          }
        })
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [code, before]);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await action();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  const newCode = () =>
    run(async () => {
      setPaired(false);
      setBefore(state?.coupled_at ?? null);
      setCode(await createCockpitCode());
      setState(await fetchCockpitCoupling());
    });
  const withdraw = () =>
    run(async () => {
      setCode(null);
      setState(await withdrawCockpitCode());
    });
  const uncouple = () => {
    if (
      !window.confirm(
        L(
          `Vom Cockpit „${state?.name ?? ""}“ entkoppeln? Sein Token gilt ab sofort nicht mehr – Inventar und Freigaben gehen erst nach einem neuen Koppeln wieder (bzw. mit dem Token aus .env).`,
          `Unpair from the cockpit “${state?.name ?? ""}”? Its token is no longer valid from now on – inventory and releases only work again after pairing anew (or with the token from .env).`,
        ),
      )
    )
      return;
    void run(async () => {
      setPaired(false);
      setState(await uncoupleCockpit());
    });
  };

  return (
    <div className="card" id="kopplung-cockpit">
      <div className="card-head">
        <h2>{L("Cockpit", "Cockpit")}</h2>
        {state &&
          (state.coupled ? (
            <span className="chip ok" id="kopplung-stand">
              {L(`Gekoppelt mit „${state.name}“`, `Paired with “${state.name}”`)}
              {state.coupled_at && ` · ${L("seit", "since")} ${fmtTime(state.coupled_at)}`}
            </span>
          ) : (
            <span className="chip" id="kopplung-stand">
              {L("Nicht gekoppelt", "Not paired")}
            </span>
          ))}
        <div className="spacer" />
        {state?.coupled && (
          <button className="linkbtn danger" onClick={uncouple} disabled={busy}>
            {L("Entkoppeln", "Unpair")}
          </button>
        )}
        {(state?.code || code) && (
          <button className="linkbtn" onClick={withdraw} disabled={busy}>
            {L("Code verwerfen", "Withdraw code")}
          </button>
        )}
        <button className="btn btn-primary btn-sm" onClick={newCode} disabled={busy} id="kopplung-neu">
          {state?.code || code ? L("Neuer Code", "New code") : L("Code zum Koppeln erzeugen", "Create pairing code")}
        </button>
      </div>
      <div className="card-body">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        <div className="hint">
          {L(
            "Das Cockpit holt das Inventar und schickt die Freigaben mit einem eigenen Token. Zum Koppeln hier einen Einmal-Code erzeugen und im Cockpit eingeben – das Cockpit bekommt sein Token dann direkt von Automato. Ein neues Koppeln ersetzt das bisherige Token.",
            "The cockpit fetches the inventory and sends the releases with its own token. To pair, create a pairing code here and enter it in the cockpit – the cockpit then gets its token directly from Automato. Pairing anew replaces the previous token.",
          )}
        </div>
        {state?.coupled && (
          <div className="hint" id="kopplung-zuletzt" style={{ marginTop: 6 }}>
            {state.last_used
              ? L(`Zuletzt benutzt ${fmtTime(state.last_used)}`, `Last used ${fmtTime(state.last_used)}`)
              : L("Noch nicht benutzt", "Not used yet")}
            {state.coupled_by && L(` · Code von ${state.coupled_by}`, ` · code by ${state.coupled_by}`)}
          </div>
        )}
        {paired && state?.coupled && (
          <Note>{L(`Gekoppelt mit „${state.name}“.`, `Paired with “${state.name}”.`)}</Note>
        )}
        {code ? (
          <CodeBox code={code} />
        ) : (
          state?.code && (
            <div className="hint" id="kopplung-offen" style={{ marginTop: 6 }}>
              {L(
                `Ein Code ist offen bis ${fmtTime(state.code.expires)} (${state.code.failures} von ${state.max_failures} Fehlversuchen) – angezeigt wird er nur beim Erzeugen.`,
                `A code is open until ${fmtTime(state.code.expires)} (${state.code.failures} of ${state.max_failures} failed attempts) – it is shown only when created.`,
              )}
            </div>
          )
        )}
        {state?.env_token && (
          <Note warn>
            {L(
              <>
                Zusätzlich gilt das Token aus <span className="mono">.env</span> (
                <span className="mono">AUTOMATO_COCKPIT_TOKEN</span>, früherer Weg). Nach dem Koppeln dort leeren und den
                Orchestrator neu starten.
              </>,
              <>
                The token from <span className="mono">.env</span> (<span className="mono">AUTOMATO_COCKPIT_TOKEN</span>,
                former way) is also valid. After pairing, clear it there and restart the orchestrator.
              </>,
            )}
          </Note>
        )}
      </div>
    </div>
  );
}
