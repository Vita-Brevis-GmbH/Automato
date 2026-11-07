// Regeln einer Applikation neu einlesen (0.139.0, ADR 0047, Anwender: „Wenn ich im Realen System was
// ändere für eine App auf diversen Firewalls und ACI, würde ich gerne nicht alle Firewalls und ACI neu
// integrieren, sondern nur die Regeln neu berechnen“): je FortiGate bzw. Cisco ACI ein Lauf nur der
// Regeln (Regeln, Objekte, NAT – ohne Interfaces und Routing), danach rechnet nur diese Applikation neu.
import { useEffect, useState } from "react";

import { fetchRun, fetchRuleSources, readAppRules, ReadRulesResult, Run, RuleSource } from "../../api";
import { L } from "../../i18n";
import { ErrorBox, fmtTime, Loading, Modal } from "../../ui";

const POLL_MS = 3000;
const OPEN = ["queued", "running"];

const REASON: Record<string, () => string> = {
  rules: () => L("Regeln der Doku", "rules of the documentation"),
  path: () => L("auf dem Weg", "on the path"),
};

export function ReadRulesDialog({ appId, onClose, onDone }: { appId: number; onClose: () => void; onDone: () => void }) {
  const [sources, setSources] = useState<RuleSource[] | null>(null);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ReadRulesResult | null>(null);
  const [runs, setRuns] = useState<Record<string, Run>>({});
  useEffect(() => {
    fetchRuleSources(appId)
      .then((s) => {
        setSources(s);
        setSel(new Set(s.filter((x) => x.own && !x.open_run).map((x) => x.source_system_id)));
      })
      .catch(setErr);
  }, [appId]);
  // gestartete Läufe verfolgen, bis alle fertig sind – dann lädt die Doku neu
  const started = (result?.items ?? []).filter((i) => i.run_id).map((i) => i.run_id as string);
  const pending = started.filter((id) => !runs[id] || OPEN.includes(runs[id].status));
  useEffect(() => {
    if (!pending.length) return;
    const t = setTimeout(() => {
      Promise.all(pending.map((id) => fetchRun(id).catch(() => null))).then((got) => {
        setRuns((old) => {
          const next = { ...old };
          for (const r of got) if (r) next[r.id] = r;
          return next;
        });
      });
    }, POLL_MS);
    return () => clearTimeout(t);
  }, [pending.join(","), runs]); // eslint-disable-line react-hooks/exhaustive-deps
  const finished = started.length > 0 && pending.length === 0;
  useEffect(() => {
    if (finished) onDone();
  }, [finished]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (id: number) =>
    setSel((old) => {
      const next = new Set(old);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const start = async () => {
    setBusy(true);
    setErr(null);
    try {
      setResult(await readAppRules(appId, [...sel]));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const status = (runId: string) => {
    const r = runs[runId];
    if (!r || r.status === "queued") return L("wartet", "waiting");
    if (r.status === "running") return L("liest", "reading");
    if (r.status === "completed") return L("eingelesen", "read");
    return L("fehlgeschlagen", "failed") + (r.message ? ` – ${r.message}` : "");
  };
  return (
    <Modal
      title={L("Regeln neu einlesen", "Re-read rules")}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {result ? L("Schliessen", "Close") : L("Abbrechen", "Cancel")}
          </button>
          {!result && (
            <button className="btn btn-primary" id="read-rules-start" disabled={busy || sel.size === 0} onClick={() => void start()}>
              {busy ? "…" : L(`Nur Regeln einlesen (${sel.size})`, `Read rules only (${sel.size})`)}
            </button>
          )}
        </>
      }
    >
      <div id="read-rules-dialog">
        <ErrorBox error={err} prefix={L("Regeln einlesen", "Read rules")} />
        <p className="faint" style={{ marginTop: 0, fontSize: 12 }}>
          {L(
            "Liest nur Regeln, Objekte und NAT bzw. Contracts – Interfaces, Routing und Fabric bleiben vom letzten vollen Lauf. Danach rechnet nur diese Applikation neu; andere zeigen „veraltet“ und rechnen beim nächsten Durchgang bzw. beim Öffnen. Rein lesend gegenüber den Geräten.",
            "Reads only rules, objects and NAT or contracts – interfaces, routing and fabric stay from the last full run. Afterwards only this application is recomputed; others show “stale” and are recomputed in the next round or when opened. Read-only towards the devices.",
          )}
        </p>
        {sources === null && !err && <Loading />}
        {sources && sources.length === 0 && (
          <div className="empty">{L("Keine FortiGate bzw. Cisco ACI im Mandanten.", "No FortiGate or Cisco ACI in the tenant.")}</div>
        )}
        {!result &&
          sources?.map((s) => (
            <label key={s.source_system_id} className="check" style={{ display: "block", margin: "4px 0" }} data-source={s.name}>
              <input
                type="checkbox"
                checked={sel.has(s.source_system_id)}
                disabled={!!s.open_run}
                onChange={() => toggle(s.source_system_id)}
              />{" "}
              <b>{s.name}</b> <span className="faint">({s.platform === "cisco_aci" ? "Cisco ACI" : "FortiGate"})</span>
              {s.reasons.map((r) => (
                <span key={r} className="chip" style={{ marginLeft: 6 }}>
                  {REASON[r]?.() ?? r}
                </span>
              ))}
              {s.open_run && <span className="faint"> – {s.open_run}</span>}
              {!s.open_run && s.last_full_run && (
                <span className="faint"> – {L(`zuletzt voll gelesen ${fmtTime(s.last_full_run)}`, `last fully read ${fmtTime(s.last_full_run)}`)}</span>
              )}
            </label>
          ))}
        {result && (
          <ul id="read-rules-result" style={{ paddingLeft: 18 }}>
            {result.items.map((i) => (
              <li key={i.source_system_id} data-source={i.name ?? ""}>
                <b>{i.name ?? `#${i.source_system_id}`}</b>:{" "}
                {i.run_id ? status(i.run_id) : <span className="faint">{L(`übersprungen – ${i.skipped}`, `skipped – ${i.skipped}`)}</span>}
              </li>
            ))}
          </ul>
        )}
        {finished && (
          <p id="read-rules-finished">
            {L(
              "Eingelesen – die Applikation rechnet neu, die Doku lädt sich, sobald sie fertig ist.",
              "Read – the application is being recomputed, the documentation reloads when done.",
            )}
          </p>
        )}
      </div>
    </Modal>
  );
}
