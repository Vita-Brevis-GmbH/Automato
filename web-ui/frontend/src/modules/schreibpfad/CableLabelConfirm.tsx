// NetBox-Sync (1.19.0, ADR 0031 Nachtrag 1.19.0): Kabelnummern an bestehenden Kabeln bestätigen.
// Die Vorschau zeigt je Kabel beide Werte (NetBox alt, Automato neu); übernommen wird nur, was hier
// angekreuzt ist – auch an von Hand gepflegten Kabeln dann nur das Label. Was nur in NetBox steht,
// bleibt.
import { useEffect, useState } from "react";
import { fetchSyncItems, SyncItem, SyncPlan } from "../../api";
import { L } from "../../i18n";
import { ErrorBox, Loading } from "../../ui";

export function CableLabelConfirm({ plan, value, onChange }: { plan: SyncPlan; value: string[]; onChange: (keys: string[]) => void }) {
  const [rows, setRows] = useState<SyncItem[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    let live = true;
    setRows(null);
    fetchSyncItems(plan.id, { kind: "cable_label", action: "update", limit: 2000, offset: 0 })
      .then((d) => live && setRows(d.rows.filter((r) => r.confirm)))
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [plan.id]);
  if (err) return <ErrorBox error={err} />;
  if (rows === null) return <Loading />;
  if (!rows.length) return null;
  const chosen = new Set(value);
  const toggle = (key: string, on: boolean) => onChange(on ? [...value, key] : value.filter((k) => k !== key));
  return (
    <div className="card" id="sync-cable-labels">
      <div className="card-head">
        <h2>{L("Kabelnummern bestätigen", "Confirm cable numbers")}</h2>
        <span className="faint" style={{ fontSize: 12 }}>
          {L(`${value.length} von ${rows.length} bestätigt`, `${value.length} of ${rows.length} confirmed`)}
        </span>
        <div className="spacer" />
        <button className="btn btn-ghost btn-sm" id="sync-cable-labels-all" onClick={() => onChange(rows.map((r) => r.key))}>
          {L("alle", "all")}
        </button>
        <button className="btn btn-ghost btn-sm" onClick={() => onChange([])}>
          {L("keine", "none")}
        </button>
      </div>
      <div className="card-body pad faint" style={{ fontSize: 12 }}>
        {L(
          "Die Nummer aus Automato wird nur an den angekreuzten Kabeln als Label geschrieben – an von Hand gepflegten Kabeln nur das Label. Die übrigen bleiben in NetBox, wie sie sind.",
          "The number from Automato is written as the label only on the ticked cables – on cables maintained by hand only the label. The others stay in NetBox as they are.",
        )}
      </div>
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th />
              <th>{L("Kabel", "Cable")}</th>
              <th>NetBox</th>
              <th>Automato</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const c = r.changes?.[0];
              return (
                <tr key={r.key} data-key={r.key}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={L("bestätigen", "confirm")}
                      checked={chosen.has(r.key)}
                      onChange={(e) => toggle(r.key, e.target.checked)}
                    />
                  </td>
                  <td className="mono">
                    {r.label}
                    {r.manual && <span className="chip na">{L("von Hand gepflegt", "maintained by hand")}</span>}
                  </td>
                  <td className="mono">{c?.old ? String(c.old) : <span className="faint">{L("(leer)", "(empty)")}</span>}</td>
                  <td className="mono">
                    <b>{c ? String(c.new) : ""}</b>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
