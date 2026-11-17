// Verkabelung im Detail (1.19.0, ADR 0031 Nachtrag 1.19.0): Kabelnummer mit Herkunft (NetBox bzw.
// Automato), Dialog zum Erfassen je Port-Paar, Transceiver je Port und der Reiter „Kabelnummern“.
// Erfasst wird nur in Automato (Operator); nach NetBox schreibt allein der NetBox-Sync (Vorschau →
// Übernehmen, je Kabel bestätigt). Die Geräte liest Automato nur.
import { FormEvent, useEffect, useState } from "react";
import { L } from "../../i18n";
import { Dash, Empty, ErrorBox, Loading, Modal, Note } from "../../ui";
import {
  CableLabelInfo,
  CableLabelRow,
  CableLabelSummary,
  deleteCableLabel,
  fetchCableLabels,
  PortRef,
  saveCableLabel,
  Transceiver,
  TransceiverStatus,
} from "./kabelApi";

const ORIGIN: Record<string, [string, string, string]> = {
  same: ["ok", L("Automato = NetBox", "Automato = NetBox"), L("in Automato erfasst und in NetBox gleich", "captured in Automato and the same in NetBox")],
  differs: ["warn", L("verschieden", "differs"), L("in Automato und NetBox verschieden – der NetBox-Sync zeigt beide Werte", "differs between Automato and NetBox – the NetBox sync shows both values")],
  automato: ["info", "Automato", L("nur in Automato erfasst – der NetBox-Sync schreibt sie als Label des Kabels", "captured in Automato only – the NetBox sync writes it as the label of the cable")],
  netbox: ["na", "NetBox", L("nur in NetBox – wird angezeigt, nie gelöscht", "only in NetBox – shown, never deleted")],
};

// Kabelnummer einer Verbindung bzw. eines Ports mit Herkunft; Stift zum Erfassen (Operator)
export function LabelCell({ info, onEdit }: { info: CableLabelInfo | null | undefined; onEdit?: () => void }) {
  const mine = info?.automato?.label ?? null;
  const theirs = [...new Set((info?.netbox ?? []).map((c) => c.label).filter((x): x is string => !!x))];
  const state = info?.state ?? null;
  const edit = onEdit && (
    <button
      className="chip cable-label-edit"
      style={{ cursor: "pointer", fontFamily: "inherit" }}
      onClick={onEdit}
      title={mine ? L("Kabelnummer ändern", "Change cable number") : L("Kabelnummer erfassen", "Capture cable number")}
    >
      {mine ? "✎" : L("+ Nummer", "+ number")}
    </button>
  );
  if (!state) return <span className="cable-label">{edit ?? <Dash />}</span>;
  const [cls, label, title] = ORIGIN[state];
  return (
    <span className="cable-label" data-state={state}>
      {state === "differs" ? (
        <>
          <b className="mono">{mine}</b> <span className="faint">(Automato)</span> ·{" "}
          <b className="mono">{theirs.join(", ")}</b> <span className="faint">(NetBox)</span>
        </>
      ) : (
        <b className="mono">{mine ?? theirs.join(", ")}</b>
      )}{" "}
      <span className={`chip ${cls}`} title={title}>
        {label}
      </span>{" "}
      {edit}
    </span>
  );
}

export function transceiverText(t: Transceiver | null | undefined): string {
  if (!t) return "";
  const parts = [t.type, t.part && t.part !== t.type ? t.part : null, t.vendor, t.serial ? `S/N ${t.serial}` : null];
  return parts.filter(Boolean).join(" · ");
}

export function TransceiverCell({ t }: { t: Transceiver | null | undefined }) {
  if (!t) return <span className="faint">–</span>;
  return (
    <span className="transceiver" title={L("Transceiver am Port (gelesen)", "Transceiver on the port (read)")}>
      {transceiverText(t)}
    </span>
  );
}

const READ: Record<TransceiverStatus, [string, string, string]> = {
  read: ["ok", L("gelesen", "read"), L("Transceiver gelesen", "transceivers read")],
  unreadable: ["warn", L("nicht lesbar", "not readable"), L("Befehl gescheitert – Grund in der Meldung des Laufs", "command failed – reason in the message of the run")],
  unsupported: ["na", L("nicht unterstützt", "not supported"), L("über diesen Zugang nicht lesbar (z. B. SNMPv3)", "not readable via this access (e.g. SNMPv3)")],
};

export function TransceiverStatusChip({ status, count }: { status: TransceiverStatus | null | undefined; count?: number }) {
  if (!status) return <span className="faint" title={L("nicht geliefert (älterer Agent bzw. kein Switch)", "not delivered (older agent or not a switch)")}>–</span>;
  const [cls, label, title] = READ[status];
  return (
    <span className={`chip ${cls}`} title={title}>
      {label}
      {status === "read" && count !== undefined ? ` · ${count}` : ""}
    </span>
  );
}

// Dialog: Kabelnummer eines Port-Paars erfassen, ändern bzw. löschen (eine Erfassung je Paar)
export function CableLabelDialog({
  tenantId,
  a,
  b,
  info,
  fixed = true,
  onClose,
  onSaved,
}: {
  tenantId: number;
  a: Partial<PortRef>;
  b: Partial<PortRef>;
  info?: CableLabelInfo | null;
  fixed?: boolean; // Enden aus der Verbindung: nicht änderbar
  onClose: () => void;
  onSaved: () => void;
}) {
  const [ends, setEnds] = useState({ ad: a.device ?? "", ap: a.port ?? "", bd: b.device ?? "", bp: b.port ?? "" });
  const [label, setLabel] = useState(info?.automato?.label ?? "");
  const [note, setNote] = useState(info?.automato?.note ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const netbox = [...new Set((info?.netbox ?? []).map((c) => c.label).filter(Boolean))];
  const ok = label.trim() && ends.ad.trim() && ends.ap.trim() && ends.bd.trim() && ends.bp.trim();
  const save = async (ev?: FormEvent) => {
    ev?.preventDefault();
    if (!ok) return;
    setBusy(true);
    setErr(null);
    try {
      await saveCableLabel({ tenant_id: tenantId, a_device: ends.ad, a_port: ends.ap, b_device: ends.bd, b_port: ends.bp, label, note: note || null });
      onSaved();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!info?.automato) return;
    setBusy(true);
    try {
      await deleteCableLabel(info.automato.id, tenantId);
      onSaved();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const field = (id: string, label: string, key: keyof typeof ends) => (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} className="input mono" value={ends[key]} readOnly={fixed} maxLength={200} onChange={(e) => setEnds({ ...ends, [key]: e.target.value })} />
    </div>
  );
  return (
    <Modal
      title={info?.automato ? L("Kabelnummer ändern", "Change cable number") : L("Kabelnummer erfassen", "Capture cable number")}
      onClose={onClose}
      footer={
        <>
          {info?.automato && (
            <button className="btn btn-ghost" id="cable-label-delete" onClick={remove} disabled={busy} style={{ marginRight: "auto" }}>
              {L("In Automato löschen", "Delete in Automato")}
            </button>
          )}
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-primary" id="cable-label-save" onClick={() => save()} disabled={busy || !ok}>
            {busy ? L("Speichere…", "Saving…") : L("Speichern", "Save")}
          </button>
        </>
      }
    >
      <form id="cable-label-dialog" onSubmit={save}>
        <ErrorBox error={err} onClose={() => setErr(null)} />
        <div className="inline-form" style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {field("cl-a-device", L("Gerät A", "Device A"), "ad")}
          {field("cl-a-port", "Port A", "ap")}
        </div>
        <div className="inline-form" style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {field("cl-b-device", L("Gerät B", "Device B"), "bd")}
          {field("cl-b-port", "Port B", "bp")}
        </div>
        <div className="field">
          <label htmlFor="cl-label">{L("Kabelnummer", "Cable number")}</label>
          <input id="cl-label" className="input mono" value={label} maxLength={100} autoFocus placeholder={L("z. B. K-0815", "e.g. K-0815")} onChange={(e) => setLabel(e.target.value)} />
          {netbox.length > 0 && (
            <div className="hint">
              {L("In NetBox:", "In NetBox:")} <b className="mono">{netbox.join(", ")}</b>
            </div>
          )}
        </div>
        <div className="field">
          <label htmlFor="cl-note">{L("Notiz (optional)", "Note (optional)")}</label>
          <input id="cl-note" className="input" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </div>
        <Note>
          {L(
            "Gespeichert wird in Automato, je Port-Paar eine Nummer (Lang- und Kurzform des Ports sind dasselbe Paar). Nach NetBox schreibt sie der NetBox-Sync (Abschnitt Verkabelung) als Label des Kabels – an bestehenden Kabeln nur, wenn Sie es dort bestätigen. Was nur in NetBox steht, bleibt.",
            "Stored in Automato, one number per port pair (long and short form of the port are the same pair). The NetBox sync (cabling section) writes it as the label of the cable – on existing cables only if you confirm it there. What is only in NetBox stays.",
          )}
        </Note>
      </form>
    </Modal>
  );
}

const STATE_FILTER: [string, string][] = [
  ["", L("alle", "all")],
  ["differs", L("verschieden", "differs")],
  ["automato", L("nur Automato", "Automato only")],
  ["netbox", L("nur NetBox", "NetBox only")],
  ["same", L("gleich", "same")],
  ["mismatch", L("Nachbar passt nicht", "neighbor does not match")],
];

// Reiter „Kabelnummern“: alle Port-Paare mit Nummer (Automato bzw. NetBox) und ob CDP/LLDP zum Kabel
// in NetBox passt; Erfassen auch ohne Verbindung (z. B. Port ohne CDP/LLDP)
export function CableLabelsTab({ tenantId, canEdit, initialQ }: { tenantId: number; canEdit: boolean; initialQ?: string }) {
  const [state, setState] = useState("");
  const [q, setQ] = useState(initialQ ?? "");
  const [data, setData] = useState<{ total: number; summary: CableLabelSummary; items: CableLabelRow[] } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [edit, setEdit] = useState<{ row: CableLabelRow | null } | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    const t = setTimeout(() => {
      fetchCableLabels(tenantId, { state, q })
        .then((d) => live && setData(d))
        .catch((e) => live && setErr(e));
    }, 200);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [tenantId, state, q, tick]);
  const s = data?.summary;
  return (
    <div className="card" id="cabling-labels">
      <div className="card-head">
        <h2>{L("Kabelnummern", "Cable numbers")}</h2>
        {s && (
          <span className="faint" id="cabling-labels-summary" style={{ fontSize: 12 }}>
            {L(
              `${s.automato_total} in Automato · ${s.netbox_labels} von ${s.netbox_total} Kabeln in NetBox mit Nummer · ${s.differs} verschieden · ${s.mismatch} Nachbar passt nicht`,
              `${s.automato_total} in Automato · ${s.netbox_labels} of ${s.netbox_total} cables in NetBox with a number · ${s.differs} differ · ${s.mismatch} neighbor does not match`,
            )}
          </span>
        )}
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <select className="sel" id="cabling-labels-state" aria-label={L("Herkunft", "Origin")} value={state} onChange={(e) => setState(e.target.value)}>
            {STATE_FILTER.map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
          <input className="input" type="search" id="cabling-labels-q" aria-label={L("Suchen", "Search")} placeholder={L("Gerät, Port, Nummer", "Device, port, number")} value={q} onChange={(e) => setQ(e.target.value)} />
          {canEdit && (
            <button className="btn btn-primary btn-sm" id="cabling-labels-new" onClick={() => setEdit({ row: null })}>
              {L("Kabelnummer erfassen", "Capture cable number")}
            </button>
          )}
        </span>
      </div>
      <div className="card-body scroll">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        {data === null ? (
          <Loading />
        ) : data.items.length === 0 ? (
          <Empty title={L("keine Kabelnummern", "no cable numbers")}>
            {L(
              "Nummern kommen aus NetBox (Label der Kabel, mit der Referenzquelle gelesen) bzw. werden hier oder bei den Verbindungen erfasst.",
              "Numbers come from NetBox (label of the cables, read with the reference source) or are captured here or at the links.",
            )}
          </Empty>
        ) : (
          <table className="compact">
            <thead>
              <tr>
                <th>{L("Ende A", "End A")}</th>
                <th />
                <th>{L("Ende B", "End B")}</th>
                <th>{L("Kabelnummer", "Cable number")}</th>
                <th>{L("CDP/LLDP", "CDP/LLDP")}</th>
                <th>NetBox</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((r) => (
                <tr key={r.pair} data-pair={r.pair} className={r.neighbor === "mismatch" ? "row-crit" : undefined}>
                  <td className="nowrap">
                    <b>{r.a.device}</b> <span className="mono">{r.a.port}</span>
                  </td>
                  <td className="faint">↔</td>
                  <td className="nowrap">
                    <b>{r.b.device}</b> <span className="mono">{r.b.port}</span>
                  </td>
                  <td>
                    <LabelCell info={r} onEdit={canEdit ? () => setEdit({ row: r }) : undefined} />
                  </td>
                  <td>
                    {r.neighbor === "mismatch" ? (
                      <span className="crit">
                        {L("passt nicht: ", "does not match: ")}
                        {r.wrong
                          .map((w) => `${w.at.device} ${w.at.port} → ${w.seen.map((x) => `${x.device} ${x.port ?? "?"}`).join(", ")}`)
                          .join("; ")}
                      </span>
                    ) : r.neighbor === "ok" ? (
                      <span className="chip ok">{L("passt", "matches")}</span>
                    ) : r.link ? (
                      <span className="faint">{r.link.protocols.join("/").toUpperCase()}</span>
                    ) : (
                      <span className="faint" title={L("keine Nachbar-Daten an beiden Enden", "no neighbor data at either end")}>
                        {L("unbekannt", "unknown")}
                      </span>
                    )}
                  </td>
                  <td className="faint">
                    {r.netbox.map((c) =>
                      c.url ? (
                        <a key={c.id} href={c.url} target="_blank" rel="noreferrer">
                          #{c.id}
                        </a>
                      ) : (
                        <span key={c.id}>#{c.id}</span>
                      ),
                    )}
                    {r.netbox.length === 0 && <Dash />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {edit && (
        <CableLabelDialog
          tenantId={tenantId}
          a={edit.row?.a ?? {}}
          b={edit.row?.b ?? {}}
          info={edit.row}
          fixed={edit.row !== null}
          onClose={() => setEdit(null)}
          onSaved={() => {
            setEdit(null);
            setTick((t) => t + 1);
          }}
        />
      )}
    </div>
  );
}
