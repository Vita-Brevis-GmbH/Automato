// NetBox-Sync, Abschnitt „DNS-Namen aus dem AD“ (1.19.0, ADR 0045 Nachtrag): Teil der Vorschau mit den Einträgen,
// die eine Entscheidung brauchen. Abweichende Namen (NetBox ≠ A-Record, Aktion „ändern“ mit `confirm` wie die
// Kabelnummern) werden nur übernommen, wenn der Betreiber sie hier je Eintrag bestätigt; mehrdeutige IPs (mehrere
// A-Records ohne entscheidenden PTR, IP mehrfach in NetBox) bekommen keinen Vorschlag. Leere Namen setzt der Sync
// ohne Rückfrage.
import { useEffect, useMemo, useState } from "react";
import { fetchSyncItems, SyncItem, SyncPlan } from "../../api";
import { L } from "../../i18n";
import { ErrorBox, Loading, Note } from "../../ui";

const LIMIT = 2000; // höchstens so viele Einträge je Liste (Grenze der API) – darüber ein Hinweis

const WHY: Record<string, string> = {
  no_ptr: L("mehrere A-Records, kein PTR", "several A records, no PTR"),
  ptr_other: L("PTR nennt keinen der A-Records", "PTR names none of the A records"),
  ptr_several: L("PTR nennt mehrere der A-Records", "PTR names several of the A records"),
  no_reverse: L("mehrere A-Records, Rückwärtszone nicht gelesen", "several A records, reverse zone not collected"),
  netbox_several: L("IP mehrfach in NetBox (VRFs)", "IP several times in NetBox (VRFs)"),
  invalid: L("Name in NetBox nicht zulässig", "name not allowed in NetBox"),
};
const BY: Record<string, string> = {
  a: L("A-Record", "A record"),
  ptr: L("PTR entscheidet", "PTR decides"),
};

const text = (v: unknown) => (v === null || v === undefined || v === "" ? "–" : String(v));
const ip = (i: SyncItem) => i.label.replace(/^(DNS-Name|DNS name) /, "");

export default function SyncDns({
  plan,
  picked,
  onPick,
  onAsked,
}: {
  plan: SyncPlan;
  picked: Set<string>;
  onPick: (keys: Set<string>) => void;
  onAsked: (n: number) => void; // wie viele Abweichungen zur Bestätigung anstehen
}) {
  const counts = plan.summary?.dns_name ?? {};
  const [updates, setUpdates] = useState<{ rows: SyncItem[]; total: number } | null>(null);
  const [ambiguous, setAmbiguous] = useState<{ rows: SyncItem[]; total: number } | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState<unknown>(null);
  const ready = plan.status === "ready";
  const confirmed = useMemo(() => new Set(plan.options.confirm ?? []), [plan.options.confirm]);

  useEffect(() => {
    setUpdates(null);
    setAmbiguous(null);
    let current = true;
    const load = (action: string, set: (d: { rows: SyncItem[]; total: number }) => void) =>
      fetchSyncItems(plan.id, { kind: "dns_name", action, limit: LIMIT })
        .then((d) => current && set(d))
        .catch((e) => current && setError(e));
    if (counts.update) load("update", setUpdates);
    else setUpdates({ rows: [], total: 0 });
    if (counts.ambiguous) load("ambiguous", setAmbiguous);
    else setAmbiguous({ rows: [], total: 0 });
    return () => {
      current = false;
    };
  }, [plan.id, counts.update, counts.ambiguous]);
  // abweichend = „ändern“ mit Bestätigung; die übrigen „ändern“ setzen einen leeren Namen
  const confirm = useMemo(() => {
    if (updates === null) return null;
    const rows = updates.rows.filter((i) => i.confirm);
    return { rows, total: rows.length + (updates.total - updates.rows.length) };
  }, [updates]);
  const toSet = (counts.update ?? 0) - (confirm?.rows.length ?? 0);
  const done = (confirm?.rows ?? []).filter((i) => confirmed.has(i.key)).length; // nach dem Übernehmen
  useEffect(() => onAsked(confirm?.rows.length ?? 0), [confirm, onAsked]);

  const match = (i: SyncItem) => {
    const s = q.trim().toLowerCase();
    if (!s) return true;
    return [i.key, i.label, ...(i.dns?.names ?? []), ...(i.changes ?? []).map((c) => text(c.old))].some((x) =>
      String(x).toLowerCase().includes(s),
    );
  };
  const shownConfirm = (confirm?.rows ?? []).filter(match);
  const shownAmbiguous = (ambiguous?.rows ?? []).filter(match);
  const allPicked = shownConfirm.length > 0 && shownConfirm.every((i) => picked.has(i.key));
  const toggleAll = () => {
    const next = new Set(picked);
    for (const i of shownConfirm) {
      if (allPicked) next.delete(i.key);
      else next.add(i.key);
    }
    onPick(next);
  };
  const toggle = (key: string) => {
    const next = new Set(picked);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onPick(next);
  };

  return (
    <div className="card-body pad" id="sync-dns">
      <h3 style={{ margin: "0 0 8px" }}>{L("DNS-Namen aus dem AD", "DNS names from the AD")}</h3>
      <div className="chips" id="sync-dns-counts" style={{ marginBottom: 8 }}>
        <span className="chip info">{L(`${toSet} setzen (leer in NetBox)`, `${toSet} to set (empty in NetBox)`)}</span>
        <span className="chip warn">{L(`${confirm?.rows.length ?? 0} abweichend`, `${confirm?.rows.length ?? 0} differing`)}</span>
        <span className="chip na">{L(`${counts.ambiguous ?? 0} mehrdeutig`, `${counts.ambiguous ?? 0} ambiguous`)}</span>
        <span className="chip na">{L(`${counts.unchanged ?? 0} gleich`, `${counts.unchanged ?? 0} equal`)}</span>
      </div>
      <ErrorBox error={error} onClose={() => setError(null)} />
      {(confirm?.total ?? 0) + (ambiguous?.total ?? 0) > 10 && (
        <div className="search" style={{ maxWidth: 320, marginBottom: 8 }}>
          <input
            id="sync-dns-search"
            placeholder={L("IP oder Name suchen…", "Search IP or name…")}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
      )}
      {confirm === null || ambiguous === null ? (
        <Loading />
      ) : (
        <>
          {confirm.total > 0 && (
            <div id="sync-dns-confirm" style={{ marginBottom: 12 }}>
              <Note>
                {L(
                  <>
                    <b>Abweichend:</b> NetBox hat einen anderen Namen als der A-Record im AD. Übernommen wird nur, was Sie
                    hier bestätigen – ohne Bestätigung bleibt NetBox, wie es ist.
                  </>,
                  <>
                    <b>Differing:</b> NetBox has another name than the A record in the AD. Only what you confirm here is
                    applied – without confirmation NetBox stays as it is.
                  </>,
                )}
              </Note>
              <table className="tight">
                <thead>
                  <tr>
                    <th>
                      {ready && (
                        <input
                          type="checkbox"
                          aria-label={L("alle bestätigen", "confirm all")}
                          id="sync-dns-all"
                          checked={allPicked}
                          onChange={toggleAll}
                        />
                      )}
                    </th>
                    <th>{L("IP-Adresse", "IP address")}</th>
                    <th>{L("In NetBox", "In NetBox")}</th>
                    <th>{L("Aus dem AD", "From the AD")}</th>
                    <th>{L("Grundlage", "Basis")}</th>
                  </tr>
                </thead>
                <tbody>
                  {shownConfirm.map((i) => {
                    const c = i.changes?.[0];
                    const on = ready ? picked.has(i.key) : confirmed.has(i.key);
                    return (
                      <tr key={i.key} data-ip={i.key} className={on ? "selected" : ""}>
                        <td>
                          <input
                            type="checkbox"
                            aria-label={L(`${i.key} bestätigen`, `confirm ${i.key}`)}
                            checked={on}
                            disabled={!ready}
                            onChange={() => toggle(i.key)}
                          />
                        </td>
                        <td className="mono">{ip(i)}</td>
                        <td className="mono">{text(c?.old)}</td>
                        <td className="mono">
                          <b>{text(c?.new)}</b>
                        </td>
                        <td className="faint" style={{ fontSize: 12 }}>
                          {BY[i.dns?.by ?? ""] ?? "–"}
                          {i.manual ? L(" · von Hand gepflegt", " · maintained by hand") : ""}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <div className="faint" style={{ fontSize: 12, marginTop: 4 }} id="sync-dns-picked">
                {ready
                  ? L(`${picked.size} von ${confirm.rows.length} bestätigt`, `${picked.size} of ${confirm.rows.length} confirmed`)
                  : L(`${done} von ${confirm.rows.length} bestätigt`, `${done} of ${confirm.rows.length} confirmed`)}
              </div>
              {confirm.total > confirm.rows.length && (
                <Note warn>
                  {L(
                    `Nur die ersten ${LIMIT} Änderungen geladen – weitere Abweichungen nach dem Übernehmen mit einer neuen Vorschau.`,
                    `Only the first ${LIMIT} changes loaded – further differences after applying with a new preview.`,
                  )}
                </Note>
              )}
            </div>
          )}
          {ambiguous.total > 0 && (
            <div id="sync-dns-ambiguous">
              <Note>
                {L(
                  <>
                    <b>Mehrdeutig:</b> mehrere A-Records und kein PTR, der genau einen davon nennt (bzw. die IP steht mehrfach
                    in NetBox) – kein Vorschlag, NetBox bleibt, wie es ist.
                  </>,
                  <>
                    <b>Ambiguous:</b> several A records and no PTR naming exactly one of them (or the IP is in NetBox several
                    times) – no proposal, NetBox stays as it is.
                  </>,
                )}
              </Note>
              <table className="tight">
                <thead>
                  <tr>
                    <th>{L("IP-Adresse", "IP address")}</th>
                    <th>{L("In NetBox", "In NetBox")}</th>
                    <th>{L("A-Records", "A records")}</th>
                    <th>PTR</th>
                    <th>{L("Grund", "Reason")}</th>
                  </tr>
                </thead>
                <tbody>
                  {shownAmbiguous.map((i) => (
                    <tr key={i.key} data-ip={i.key}>
                      <td className="mono">{ip(i)}</td>
                      <td className="mono">{text(i.changes?.[0]?.old)}</td>
                      <td className="mono">{(i.dns?.names ?? []).join(", ") || "–"}</td>
                      <td className="mono">{(i.dns?.ptr ?? []).join(", ") || "–"}</td>
                      <td>{WHY[i.dns?.why ?? ""] ?? i.dns?.why ?? "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {ambiguous.total > ambiguous.rows.length && (
                <div className="faint" style={{ fontSize: 12 }}>
                  {L(`… ${ambiguous.total - ambiguous.rows.length} weitere`, `… ${ambiguous.total - ambiguous.rows.length} more`)}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
