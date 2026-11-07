// Grosse Netze der Regeln zuordnen (0.111.0, ADR 0040; Anwender: „markieren und zuordnen lassen, wo sie hin gehören, oder sie
// anhand von Angaben in Anbindungen und Zonen ausschliessen oder zuweisen“). Ein Netz wie 10.0.0.0/8, das das Routing nicht als
// Ganzes kennt: von Hand einer Zone bzw. Anbindung zuordnen, „Weg nach dem Routing stimmt“ oder „nur die Teile zählen“.
import { useEffect, useState } from "react";
import {
  deleteNetAssignment,
  fetchExits,
  fetchNetAssignments,
  fetchZones,
  LogicalZone,
  NetAssignKind,
  NetAssignment,
  NetAssignState,
  setNetAssignment,
  TopoExit,
} from "../../api";
import { L } from "../../i18n";
import { ErrorBox, Modal } from "../../ui";

const KIND: Record<NetAssignKind, string> = {
  ignore: L("nur die genauer gerouteten Teile zählen", "only the parts routed more specifically count"),
  route: L("Weg nach dem Routing stimmt", "path by the routing is right"),
  zone: L("gehört zur Zone …", "belongs to zone …"),
  exit: L("über die Anbindung …", "via the exit …"),
};

// Zustand einer Verbindung bzw. eines Befunds als Chip
export function NetAssignChip({ a }: { a: NetAssignState }) {
  return (
    <span className={`chip ${a.open ? "warn" : "local"} net-assign`} title={a.text}>
      {a.open ? L("Netz grösser als die Routen", "network wider than the routes") : a.text}
    </span>
  );
}

export function NetAssignDialog({
  tenantId,
  prefix,
  onClose,
  onSaved,
}: {
  tenantId: number;
  prefix: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [zones, setZones] = useState<LogicalZone[]>([]);
  const [exits, setExits] = useState<TopoExit[]>([]);
  const [current, setCurrent] = useState<NetAssignment | null>(null);
  const [kind, setKind] = useState<NetAssignKind>("ignore");
  const [zoneId, setZoneId] = useState<number | null>(null);
  const [exitId, setExitId] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    fetchZones(tenantId).then(setZones).catch(() => setZones([]));
    fetchExits(tenantId).then(setExits).catch(() => setExits([]));
    fetchNetAssignments(tenantId)
      .then((rows) => {
        const mine = rows.find((r) => r.prefix === prefix) ?? null;
        setCurrent(mine);
        if (mine) {
          setKind(mine.kind);
          setZoneId(mine.zone_id);
          setExitId(mine.exit_id);
          setNote(mine.note ?? "");
        }
      })
      .catch(setError);
  }, [tenantId, prefix]);

  const run = (p: Promise<unknown>) => {
    setBusy(true);
    setError(null);
    p.then(onSaved)
      .catch(setError)
      .finally(() => setBusy(false));
  };
  const save = () =>
    run(setNetAssignment({ tenant_id: tenantId, prefix, kind, zone_id: zoneId, exit_id: exitId, note: note.trim() || null }));
  const ready = (kind !== "zone" || zoneId != null) && (kind !== "exit" || exitId != null);

  return (
    <Modal
      title={L(`Netz ${prefix} zuordnen`, `Assign network ${prefix}`)}
      onClose={onClose}
      footer={
        <>
          {current && (
            <button className="btn btn-ghost" id="net-assign-delete" disabled={busy} onClick={() => run(deleteNetAssignment(current.id))}>
              {L("Zuordnung entfernen", "Remove assignment")}
            </button>
          )}
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-primary" id="net-assign-save" disabled={busy || !ready} onClick={save}>
            {L("Speichern", "Save")}
          </button>
        </>
      }
    >
      <p className="faint" style={{ marginTop: 0 }}>
        {L(
          "Das Routing kennt das Netz nicht als Ganzes (Teile genauer geroutet bzw. nur die Default-Route). Ohne Zuordnung von Hand entscheiden Zonen (Netzwerkdesign) und die Netze der Anbindungen.",
          "The routing does not know the network as a whole (parts routed more specifically or only the default route). Without a manual assignment, zones (network design) and the networks of the exits decide.",
        )}
      </p>
      <ErrorBox error={error} onClose={() => setError(null)} />
      <div className="field" id="net-assign-kind">
        {(Object.keys(KIND) as NetAssignKind[]).map((k) => (
          <label key={k} className="check" style={{ display: "flex" }}>
            <input type="radio" name="net-assign-kind" checked={kind === k} onChange={() => setKind(k)} />
            {KIND[k]}
          </label>
        ))}
      </div>
      {kind === "zone" && (
        <div className="field">
          <label htmlFor="net-assign-zone">{L("Zone", "Zone")}</label>
          <select id="net-assign-zone" className="sel input" value={zoneId ?? ""} onChange={(e) => setZoneId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">{L("– wählen –", "– choose –")}</option>
            {zones.map((z) => (
              <option key={z.id} value={z.id}>
                {z.name}
              </option>
            ))}
          </select>
        </div>
      )}
      {kind === "exit" && (
        <div className="field">
          <label htmlFor="net-assign-exit">{L("Anbindung", "Exit")}</label>
          <select id="net-assign-exit" className="sel input" value={exitId ?? ""} onChange={(e) => setExitId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">{L("– wählen –", "– choose –")}</option>
            {exits.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="field">
        <label htmlFor="net-assign-note">{L("Notiz", "Note")}</label>
        <input id="net-assign-note" className="input" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
      </div>
    </Modal>
  );
}

// Liste der Zuordnungen von Hand (Routing → Anbindungen)
export function NetAssignList({ tenantId, canEdit }: { tenantId: number; canEdit: boolean }) {
  const [rows, setRows] = useState<NetAssignment[] | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  // Wegsucher aus (403): keine Liste
  const load = () =>
    fetchNetAssignments(tenantId)
      .then(setRows)
      .catch(() => setRows([]));
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId]);
  if (rows === null || !rows.length) return null;
  return (
    <div className="card" id="net-assignments" style={{ marginTop: 16 }}>
      <div className="card-head">
        <h2>{L("Grosse Netze der Regeln", "Wide networks of the rules")}</h2>
        <span className="faint">
          {L(
            "Von Hand zugeordnet (Verbindungen bzw. Befund „Netz der Regel grösser als die Routen“)",
            "Assigned by hand (connections or finding “network of the rule wider than the routes”)",
          )}
        </span>
      </div>
      <div className="card-body">
        <table className="compact">
          <thead>
            <tr>
              <th>{L("Netz", "Network")}</th>
              <th>{L("Zuordnung", "Assignment")}</th>
              <th>{L("Notiz", "Note")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} data-prefix={r.prefix}>
                <td className="mono">{r.prefix}</td>
                <td>
                  {r.kind === "zone"
                    ? L(`Zone ${r.zone ?? "?"}`, `zone ${r.zone ?? "?"}`)
                    : r.kind === "exit"
                      ? L(`Anbindung ${r.exit ?? "?"}`, `exit ${r.exit ?? "?"}`)
                      : KIND[r.kind]}
                </td>
                <td className="faint">{r.note}</td>
                <td>
                  {canEdit && (
                    <button className="btn btn-ghost btn-sm" onClick={() => setEditing(r.prefix)}>
                      {L("ändern", "edit")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && (
        <NetAssignDialog
          tenantId={tenantId}
          prefix={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void load();
          }}
        />
      )}
    </div>
  );
}
