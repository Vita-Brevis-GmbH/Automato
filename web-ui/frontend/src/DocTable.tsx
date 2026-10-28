// Tabellen der Kategorien (Doku nach Kategorien der Applikations-Doku). Bis 0.106.0 in AppDoc.tsx zusammen mit der Doku je
// System; seit 0.107.0 (Anwender: Doku je System entfernt) ein eigener Baustein, den ApplicationDoc nutzt.
import { Fragment } from "react";
import { DocHide, DocTable, DocTableZone } from "./api";
import { Fold, FoldTitle } from "./fold";
import { Expandable } from "./ui";
import { L, locale } from "./i18n";

// Zelle der Kategorien; „Description“ öffnet die Regel (bei einem Paar je Familie ein Link). Seit
// 0.84.0 steht die Seite der Applikation je nach Richtung in Source bzw. Destination (`_app_col`);
// mehrere Einträge einer Zelle je auf einer Zeile
function DocCell({
  row,
  column,
  onRule,
}: {
  row: DocTable["rows"][number];
  column: string;
  onRule?: (id: number) => void;
}) {
  const value = row[column];
  const text = Array.isArray(value) ? value.join(", ") : value;
  const systems = Array.isArray(row._systems) ? (row._systems as string[]) : [];
  if (column === row._app_col && systems.length)
    return (
      <>
        {text || <span className="faint">–</span>}
        <Expandable items={systems} noun={L(["System", "Systeme"], ["system", "systems"])} />
      </>
    );
  if (column !== "Description" || !onRule) return <>{text || <span className="faint">–</span>}</>;
  const ids: number[] = Array.isArray(row._rules)
    ? (row._rules as unknown[]).filter((x): x is number => typeof x === "number")
    : typeof row._rule === "number"
      ? [row._rule]
      : [];
  if (!ids.length) return <>{text || <span className="faint">–</span>}</>;
  return (
    <>
      <button className="linkbtn" title={L("Regel im Detail", "Rule in detail")} onClick={() => onRule(ids[0])}>
        {text || `#${ids[0]}`}
      </button>
      {ids.length > 1 && (
        <span className="faint">
          {" "}
          ·{" "}
          {ids.map((id, i) => (
            <span key={id}>
              {i > 0 && " · "}
              <button className="linkbtn" onClick={() => onRule(id)}>
                {i === 0 ? "IPv4" : "IPv6"}
              </button>
            </span>
          ))}
        </span>
      )}
    </>
  );
}

export type DocRow = DocTable["rows"][number];

// Ausblenden je Kategorie (0.69.0, nur Applikations-Doku): Knopf je Zeile, Liste der ausgeblendeten
export interface HideControls {
  canEdit: boolean;
  onHide: (row: DocRow) => void;
  onShow: (hides: DocHide[]) => void;
}

const dateText = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleDateString(locale) : "");

// ADR 0034: unspezifische Zugriffe (Subnetz, any, vzAny, Preferred Group) stehen je L3Out, über den
// sie hereinkommen (der Server sortiert) – Zwischenüberschrift „über L3O_inet“, wo der L3Out wechselt
const UNSPECIFIC = ["subnet", "any", "vzany", "pref_group"];
// Zonen einer Kategorie (0.101.0): woher die Zugriffe kommen, mit den Netzen der Zone
function DocZones({ zones }: { zones: DocTableZone[] }) {
  return (
    <div className="doc-zones faint">
      {L("Zonen:", "Zones:")}{" "}
      {zones.map((z, i) => (
        <span key={z.id}>
          {i > 0 && " · "}
          <b>{z.name}</b>{" "}
          <span className="mono">
            ({z.nets.join(", ") || L("ohne Netze", "no networks")}
            {z.nets_total > z.nets.length ? L(` +${z.nets_total - z.nets.length} weitere`, ` +${z.nets_total - z.nets.length} more`) : ""}
            {(z.excluded ?? []).length > 0 ? L(` ohne ${z.excluded!.join(", ")}`, ` without ${z.excluded!.join(", ")}`) : ""})
          </span>
        </span>
      ))}
    </div>
  );
}

function l3outGroup(rows: DocRow[], i: number): string | null {
  const broad = (r: DocRow) => UNSPECIFIC.includes(String(r._scope ?? ""));
  if (!broad(rows[i]) || !rows.some((r) => broad(r) && r._l3out_in)) return null;
  const name = rows[i]._l3out_in ? String(rows[i]._l3out_in) : "";
  if (i > 0 && broad(rows[i - 1]) && String(rows[i - 1]._l3out_in ?? "") === name) return null;
  return name ? L(`über ${name}`, `via ${name}`) : L("ohne L3Out", "without L3Out");
}

export function DocTableView({
  table,
  onRule,
  hide,
  fold,
  hiddenOpen,
}: {
  table: DocTable;
  onRule?: (id: number) => void;
  hide?: HideControls;
  fold?: Fold; // 0.73.0: Kategorie zuklappbar (Applikations-Doku)
  hiddenOpen?: boolean; // Druck: ausgeblendete Regeln aufgeklappt
}) {
  const aci = table.enforcement === "aci";
  const hidden = table.hidden ?? [];
  const editable = !!hide?.canEdit;
  const open = fold?.open ?? true;
  return (
    <div className="card">
      <div className="card-head">
        <FoldTitle fold={fold}>
          {table.index}. {table.title}
        </FoldTitle>
        <span className="chip">
          {aci ? L("ACI intern", "ACI internal") : L("Über eine Firewall", "Via a firewall")}
        </span>
        {aci && <span className="chip warn">ACI · Model 1</span>}
        <div className="spacer" />
        <span className="mono faint" style={{ fontSize: 12 }}>
          {L(
            `${table.rows.length} Regel${table.rows.length === 1 ? "" : "n"}`,
            `${table.rows.length} rule${table.rows.length === 1 ? "" : "s"}`,
          )}
          {hidden.length > 0 ? L(` · ${hidden.length} ausgeblendet`, ` · ${hidden.length} hidden`) : ""}
        </span>
      </div>
      {open && (table.zones ?? []).length > 0 && <DocZones zones={table.zones!} />}
      {open && (
      <div className="card-body scroll">
        {table.rows.length === 0 ? (
          <div className="loading doc-rows">{L("Keine Einträge.", "No entries.")}</div>
        ) : (
          <table className="doc-rows">
            <thead>
              <tr>
                {table.columns.map((c, i) => (
                  <th key={c}>{colLabel(table, c, i)}</th>
                ))}
                {editable && <th className="no-print" />}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, i) => {
                const group = l3outGroup(table.rows, i);
                return (
                <Fragment key={i}>
                {group && (
                  <tr className="doc-l3out">
                    <td colSpan={table.columns.length + (editable ? 1 : 0)}>{group}</td>
                  </tr>
                )}
                <tr>
                  {table.columns.map((c) => (
                    <td key={c} className={c === "TCP/UDP" ? "mono" : ""}>
                      {c === "Aktion" && row[c] ? (
                        <span className={`chip ${row[c] === "Allow" ? "accept" : "deny"}`}>{String(row[c])}</span>
                      ) : (
                        <DocCell row={row} column={c} onRule={onRule} />
                      )}
                    </td>
                  ))}
                  {editable && (
                    <td className="no-print">
                      {Array.isArray(row._rules) && row._rules.length > 0 && (
                        <button
                          className="linkbtn doc-hide-btn"
                          title={L(
                            "In dieser Kategorie ausblenden – nicht relevant für die Applikation (mit Begründung)",
                            "Hide in this category – not relevant for the application (with reason)",
                          )}
                          onClick={() => hide!.onHide(row)}
                        >
                          {L("ausblenden", "hide")}
                        </button>
                      )}
                    </td>
                  )}
                </tr>
                </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
        {hidden.length > 0 && (
          <details className="doc-hidden" id={`doc-hidden-${table.index}`} open={hiddenOpen || undefined}>
            <summary>
              {L(
                <>
                  {hidden.length} ausgeblendet <span className="faint">– nicht relevant für die Applikation, mit Begründung</span>
                </>,
                <>
                  {hidden.length} hidden <span className="faint">– not relevant for the application, with reason</span>
                </>,
              )}
            </summary>
            <table className="compact">
              <thead>
                <tr>
                  <th>{L("Regel", "Rule")}</th>
                  <th>{L("System bzw. Objekt", "System or object")}</th>
                  <th>{L("Begründung", "Reason")}</th>
                  <th>{L("von", "by")}</th>
                  <th>{L("am", "on")}</th>
                  {editable && <th className="no-print" />}
                </tr>
              </thead>
              <tbody>
                {hidden.map((row, i) => {
                  const hs = row._hides ?? [];
                  return (
                    <tr key={i}>
                      <td>{String(row.Description ?? "")}</td>
                      <td>{String(row._app ?? "")}</td>
                      <td>
                        {hs.map((h) => (
                          <div key={h.id}>
                            {h.reason}
                            {h.category == null && (
                              <span className="faint">{L(" (in allen Kategorien)", " (in all categories)")}</span>
                            )}
                          </div>
                        ))}
                      </td>
                      <td>{[...new Set(hs.map((h) => h.by))].join(", ")}</td>
                      <td>{dateText(hs[0]?.at)}</td>
                      {editable && (
                        <td className="no-print">
                          <button className="linkbtn" onClick={() => hide!.onShow(hs)}>
                            {L("einblenden", "show")}
                          </button>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </details>
        )}
      </div>
      )}
    </div>
  );
}

// Spaltenkopf in der Sprache der Anfrage (0.87.0: `labels` vom Server, `columns` bleiben Schlüssel)
export const colLabel = (t: DocTable, column: string, i: number): string => t.labels?.[i] ?? column;

