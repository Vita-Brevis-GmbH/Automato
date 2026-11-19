// Reservierungen von VLAN- und AS-Nummern (1.19.0, ADR 0066; Anwender 2026-10-30 Roadmap Version 2 Nr. 10, 2026-11-17
// Prio 6 „Reservationen auch ins Netbox“): je Mandant in der App anlegen, ändern und freigeben – VLANs je Standort
// (wie die VLAN-Ansicht seit 1.9.0) bzw. für alle Standorte, AS-Nummern; Stand gegen das Gelesene (frei, jetzt belegt,
// Konflikt) und Überschneidungen. Nach NetBox nur über den NetBox-Sync (Abschnitt „Reservierungen“).
import { useEffect, useState } from "react";
import { L } from "../../i18n";
import { replaceHash } from "../../places";
import { findingLabel } from "../../refs";
import { Dash, Empty, ErrorBox, fmtTime, Loading, Note, PageHead } from "../../ui";
import type { View, ViewParams } from "../types";
import {
  createReservation,
  fetchReservationOptions,
  fetchReservations,
  releaseReservation,
  Reservation,
  ReservationIn,
  ReservationKind,
  ReservationList,
  ReservationOptions,
  ReservationState,
  updateReservation,
} from "./reservationsApi";

type Tab = ReservationKind;
const TABS: { key: Tab; label: string }[] = [
  { key: "vlan", label: "VLANs" },
  { key: "asn", label: L("AS-Nummern", "AS numbers") },
];

export const RESERVATION_STATE: Record<ReservationState, [string, string, string]> = {
  free: ["ok", L("frei", "free"), L("gelesen nicht in Gebrauch", "not read in use")],
  in_use: [
    "info",
    L("jetzt belegt", "now in use"),
    L(
      "beim Reservieren frei, jetzt gelesen in Gebrauch – ist die Reservierung umgesetzt, freigeben",
      "free when reserved, now read in use – release it once the reservation has been put in place",
    ),
  ],
  conflict: [
    "warn",
    L("Konflikt", "conflict"),
    L("schon beim Reservieren gelesen in Gebrauch – andere Nummer wählen", "already read in use when reserved – choose another number"),
  ],
};

interface Form {
  id: number | null;
  low: string;
  high: string;
  site_id: string;
  vlan_group: string;
  purpose: string;
}
const EMPTY: Form = { id: null, low: "", high: "", site_id: "", vlan_group: "", purpose: "" };

// Adresse der Stelle (1.19.0, ADR 0067): VLANs in L2 (`#l2/vlan-reservierungen`), AS-Nummern in L3 (`#l3/as-reservierungen`)
function setHash(params: Record<string, string | null>) {
  replaceHash("reservierungen", Object.fromEntries(Object.entries(params).filter((e): e is [string, string] => !!e[1])));
}

const who = (s: string | null) => (s ?? "").replace(/^user:/, "");

export default function Reservations({
  tenantId,
  tenantLabel,
  canEdit,
  params,
  go,
}: {
  tenantId: number | null;
  tenantLabel: string;
  canEdit: boolean;
  params: ViewParams;
  go: (v: View, p?: ViewParams) => void;
}) {
  const [tab, setTab] = useState<Tab>(params.tab === "asn" ? "asn" : "vlan");
  const [q, setQ] = useState(params.q ?? "");
  const [data, setData] = useState<ReservationList | null>(null);
  const [options, setOptions] = useState<ReservationOptions | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [stamp, setStamp] = useState(0);

  useEffect(() => {
    if (tenantId === null) return;
    let live = true;
    setError(null);
    fetchReservations(tenantId, { q: q.trim() || undefined })
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e));
    return () => {
      live = false;
    };
  }, [tenantId, q, stamp]);
  useEffect(() => {
    if (tenantId === null) return;
    fetchReservationOptions(tenantId).then(setOptions).catch(setError);
  }, [tenantId]);

  const choose = (t: Tab) => {
    setTab(t);
    setHash({ tab: t === "vlan" ? null : t, q: q || null });
  };

  if (tenantId === null) {
    return (
      <>
        <PageHead title={L("Reservierungen", "Reservations")} />
        <Note>{L("Bitte oben einen Mandanten wählen.", "Please select a tenant at the top.")}</Note>
      </>
    );
  }
  const items = (data?.items ?? []).filter((r) => r.kind === tab);
  return (
    <>
      <PageHead title={L("Reservierungen", "Reservations")}>
        {L(
          <>
            VLAN- und AS-Nummern für „{tenantLabel}“ reservieren, ändern und freigeben. VLANs gelten je Standort (wie die
            VLAN-Ansicht der Verkabelung) bzw. für alle Standorte. Automato vergleicht mit dem Gelesenen: war eine Nummer beim
            Reservieren frei und ist jetzt in Gebrauch, ist das ein Hinweis „jetzt belegt“, sonst ein Konflikt; doppelte
            Reservierungen sind ein Befund. Nach NetBox kommen die Reservierungen nur über den NetBox-Sync (Abschnitt
            „Reservierungen“, Vorschau → Übernehmen).
          </>,
          <>
            Reserve, change and release VLAN and AS numbers for “{tenantLabel}”. VLANs apply per site (like the VLAN view of
            the cabling) or for all sites. Automato compares with what is read: if a number was free when reserved and is in
            use now, that is a note “now in use”, otherwise a conflict; duplicate reservations are a finding. The
            reservations reach NetBox only through the NetBox sync (section “Reservations”, preview → apply).
          </>,
        )}
      </PageHead>
      <ErrorBox error={error} prefix={L("Reservierungen laden", "Load reservations")} />
      {data && (
        <div className="kpis" id="reservation-kpis">
          {(["vlan", "asn"] as Tab[]).map((k) => {
            const s = data.summary[k];
            return (
              <button key={k} className="kpi" onClick={() => choose(k)}>
                <div className="k-label">{k === "vlan" ? L("VLANs reserviert", "VLANs reserved") : L("AS reserviert", "AS reserved")}</div>
                <div className={`k-val ${s.conflict ? "warn" : ""}`}>{s.total}</div>
                <div className="k-sub">
                  {L(
                    `${s.free} frei · ${s.in_use} jetzt belegt · ${s.conflict} Konflikt`,
                    `${s.free} free · ${s.in_use} now in use · ${s.conflict} conflict`,
                  )}
                </div>
              </button>
            );
          })}
          <button className="kpi" onClick={() => go("asn")} title={L("AS-Nummern im Routing", "AS numbers in routing")}>
            <div className="k-label">{L("AS-Nummern", "AS numbers")}</div>
            <div className="k-val" style={{ fontSize: 18 }}>
              {L("im Routing →", "in routing →")}
            </div>
            <div className="k-sub">{L("je AS Nachbarn, Firewalls und Router", "per AS neighbors, firewalls and routers")}</div>
          </button>
          <button className="kpi" onClick={() => go("netbox")} title={L("NetBox-Sync öffnen", "Open NetBox sync")}>
            <div className="k-label">NetBox</div>
            <div className="k-val" style={{ fontSize: 18 }}>
              {L("Sync →", "sync →")}
            </div>
            <div className="k-sub">{L("Abschnitt „Reservierungen“", "section “Reservations”")}</div>
          </button>
        </div>
      )}
      <div className="tabbar sticky">
        {TABS.map((t) => (
          <button key={t.key} id={`reservation-tab-${t.key}`} className={tab === t.key ? "on" : ""} onClick={() => choose(t.key)}>
            {t.label}
          </button>
        ))}
      </div>
      {canEdit && options && (
        <ReservationForm key={tab} kind={tab} tenantId={tenantId} options={options} onSaved={() => setStamp((x) => x + 1)} />
      )}
      <div className="card" id="reservations" style={{ marginTop: 16 }}>
        <div className="card-head">
          <h2>{tab === "vlan" ? L("Reservierte VLANs", "Reserved VLANs") : L("Reservierte AS-Nummern", "Reserved AS numbers")}</h2>
          <input
            id="reservation-q"
            className="input input-inline"
            placeholder={L("Nummer, Zweck, Standort, wer …", "Number, purpose, site, who …")}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div className="card-body scroll">
          {data === null ? (
            error ? null : <Loading />
          ) : items.length === 0 ? (
            <Empty title={L("Keine Reservierungen", "No reservations")}>
              <span className="faint">
                {q
                  ? L("Für diesen Filter gibt es keine.", "There are none for this filter.")
                  : L("Noch nichts reserviert.", "Nothing reserved yet.")}
              </span>
            </Empty>
          ) : (
            <ReservationTable
              items={items}
              options={options}
              canEdit={canEdit}
              highlight={params.id ? Number(params.id) : null}
              onChanged={() => setStamp((x) => x + 1)}
              onError={setError}
            />
          )}
        </div>
      </div>
    </>
  );
}

function ReservationForm({
  kind,
  tenantId,
  options,
  onSaved,
  edit,
  onCancel,
}: {
  kind: ReservationKind;
  tenantId: number;
  options: ReservationOptions;
  onSaved: () => void;
  edit?: Reservation;
  onCancel?: () => void;
}) {
  const [form, setForm] = useState<Form>(
    edit
      ? {
          id: edit.id,
          low: String(edit.low),
          high: edit.high !== edit.low ? String(edit.high) : "",
          site_id: edit.site_id ? String(edit.site_id) : "",
          vlan_group: edit.vlan_group ?? "",
          purpose: edit.purpose,
        }
      : EMPTY,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState<Reservation | null>(null);
  const field = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });
  const [min, max] = options.limits[kind];
  const valid =
    /^\d+$/.test(form.low.trim()) && (!form.high.trim() || /^\d+$/.test(form.high.trim())) && form.purpose.trim() !== "";
  const save = () => {
    const body: ReservationIn = {
      tenant_id: tenantId,
      kind,
      low: Number(form.low),
      high: form.high.trim() ? Number(form.high) : null,
      site_id: kind === "vlan" && form.site_id ? Number(form.site_id) : null,
      vlan_group: kind === "vlan" && form.vlan_group.trim() ? form.vlan_group.trim() : null,
      purpose: form.purpose.trim(),
    };
    setBusy(true);
    setError(null);
    (form.id ? updateReservation(form.id, body) : createReservation(body))
      .then((r) => {
        setSaved(r);
        if (!edit) setForm(EMPTY);
        onSaved();
      })
      .catch(setError)
      .finally(() => setBusy(false));
  };
  const groups = options.vlan_groups.filter((g) => !form.site_id || String(g.site_id) === form.site_id);
  const prefix = edit ? `reservation-edit-${edit.id}` : "reservation";
  return (
    <div className={edit ? "" : "card"} id={`${prefix}-form`} style={edit ? undefined : { marginTop: 16 }}>
      {!edit && (
        <div className="card-head">
          <h2>{kind === "vlan" ? L("VLAN reservieren", "Reserve VLAN") : L("AS-Nummer reservieren", "Reserve AS number")}</h2>
          <span className="faint">{L(`Nummer bzw. Bereich ${min}–${max}`, `number or range ${min}–${max}`)}</span>
        </div>
      )}
      <div
        className={edit ? "" : "card-body pad"}
        style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))", gap: 10, alignItems: "end" }}
      >
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor={`${prefix}-low`}>{kind === "vlan" ? "VLAN" : "AS"}</label>
          <input id={`${prefix}-low`} className="input mono" inputMode="numeric" value={form.low} onChange={field("low")} />
        </div>
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor={`${prefix}-high`}>{L("bis (Bereich)", "to (range)")}</label>
          <input id={`${prefix}-high`} className="input mono" inputMode="numeric" placeholder={L("einzeln", "single")} value={form.high} onChange={field("high")} />
        </div>
        {kind === "vlan" && (
          <>
            <div className="field" style={{ margin: 0 }}>
              <label htmlFor={`${prefix}-site`}>{L("Standort", "Site")}</label>
              <select id={`${prefix}-site`} className="sel input" value={form.site_id} onChange={field("site_id")}>
                <option value="">{L("alle Standorte", "all sites")}</option>
                {options.sites.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.code} · {s.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field" style={{ margin: 0 }}>
              <label htmlFor={`${prefix}-group`} title={L("Slug der VLAN-Gruppe in NetBox – ohne: die passende Gruppe am Standort", "Slug of the VLAN group in NetBox – without: the matching group at the site")}>
                {L("VLAN-Gruppe (NetBox)", "VLAN group (NetBox)")}
              </label>
              <input
                id={`${prefix}-group`}
                className="input mono"
                list={`${prefix}-groups`}
                placeholder={L("automatisch", "automatic")}
                value={form.vlan_group}
                onChange={field("vlan_group")}
              />
              <datalist id={`${prefix}-groups`}>
                {groups.map((g) => (
                  <option key={g.slug} value={g.slug}>
                    {g.source}
                    {g.site ? ` · ${g.site}` : ""}
                  </option>
                ))}
              </datalist>
            </div>
          </>
        )}
        <div className="field" style={{ margin: 0, gridColumn: "span 2" }}>
          <label htmlFor={`${prefix}-purpose`}>{L("Zweck", "Purpose")}</label>
          <input id={`${prefix}-purpose`} className="input" maxLength={500} value={form.purpose} onChange={field("purpose")} />
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button id={`${prefix}-save`} className="btn btn-primary" disabled={busy || !valid} onClick={save}>
            {edit ? L("Speichern", "Save") : L("Reservieren", "Reserve")}
          </button>
          {onCancel && (
            <button className="btn btn-ghost" onClick={onCancel}>
              {L("Abbrechen", "Cancel")}
            </button>
          )}
        </div>
      </div>
      <div className={edit ? "" : "card-body pad"} style={{ paddingTop: 0 }}>
        <ErrorBox error={error} onClose={() => setError(null)} />
        {saved && <SavedNote r={saved} />}
      </div>
    </div>
  );
}

function SavedNote({ r }: { r: Reservation }) {
  const used = r.in_use_numbers.filter((n) => r.used_before.includes(n));
  return (
    <div id="reservation-saved">
      {used.length > 0 ? (
        <Note warn>
          {L(
            `${r.label} gespeichert – schon gelesen in Gebrauch: ${used.join(", ")} (${r.in_use.map((u) => u.where).slice(0, 3).join(", ")}). Das ist ein Konflikt.`,
            `${r.label} saved – already read in use: ${used.join(", ")} (${r.in_use.map((u) => u.where).slice(0, 3).join(", ")}). This is a conflict.`,
          )}
        </Note>
      ) : (
        <Note>{L(`${r.label} reserviert – gelesen frei.`, `${r.label} reserved – free as read.`)}</Note>
      )}
      {(r.overlaps ?? []).length > 0 && (
        <Note warn>
          {L("Überschneidet sich mit: ", "Overlaps with: ")}
          {(r.overlaps ?? []).map((o) => `${o.label} „${o.purpose}“ (${o.overlap})`).join(", ")}
        </Note>
      )}
    </div>
  );
}

function ReservationTable({
  items,
  options,
  canEdit,
  highlight,
  onChanged,
  onError,
}: {
  items: Reservation[];
  options: ReservationOptions | null;
  canEdit: boolean;
  highlight: number | null;
  onChanged: () => void;
  onError: (e: unknown) => void;
}) {
  const [editing, setEditing] = useState<number | null>(null);
  const kind = items[0]?.kind ?? "vlan";
  const release = (r: Reservation) => {
    if (!window.confirm(L(`${r.label} „${r.purpose}“ freigeben?`, `Release ${r.label} “${r.purpose}”?`))) return;
    releaseReservation(r.id).then(onChanged).catch(onError);
  };
  return (
    <table className="compact">
      <thead>
        <tr>
          <th>{kind === "vlan" ? "VLAN" : "AS"}</th>
          {kind === "vlan" && <th>{L("Standort · Gruppe", "Site · group")}</th>}
          <th>{L("Zweck", "Purpose")}</th>
          <th>{L("Stand", "State")}</th>
          <th>{L("Gelesen in Gebrauch", "Read in use")}</th>
          <th>{L("Reserviert", "Reserved")}</th>
          {canEdit && <th />}
        </tr>
      </thead>
      <tbody>
        {items.map((r) =>
          editing === r.id && options ? (
            <tr key={r.id} data-reservation={r.id}>
              <td colSpan={canEdit ? 7 : 6}>
                <ReservationForm
                  kind={r.kind}
                  tenantId={r.tenant_id}
                  options={options}
                  edit={r}
                  onSaved={() => {
                    setEditing(null);
                    onChanged();
                  }}
                  onCancel={() => setEditing(null)}
                />
              </td>
            </tr>
          ) : (
            <tr key={r.id} data-reservation={r.id} className={highlight === r.id ? "selected" : undefined}>
              <td className="mono nowrap">
                <b>{r.low === r.high ? r.low : `${r.low}–${r.high}`}</b>
                {r.count > 1 && <span className="faint"> ({r.count})</span>}
              </td>
              {kind === "vlan" && (
                <td>
                  {r.site ? r.site.name : r.site_missing ? <span className="chip warn">{L("Standort fehlt", "site missing")}</span> : L("alle Standorte", "all sites")}
                  {r.vlan_group && <div className="faint mono">{r.vlan_group}</div>}
                </td>
              )}
              <td>{r.purpose}</td>
              <td className="nowrap">
                <StateChip state={r.state} />
                {r.findings.map((f) => (
                  <div key={f.id} className="faint" style={{ fontSize: 12 }}>
                    {findingLabel(f.type)}
                  </div>
                ))}
              </td>
              <td>
                {r.in_use.length === 0 ? (
                  <Dash />
                ) : (
                  <div className="objs">
                    {r.in_use.slice(0, 3).map((u, i) => (
                      <span key={i}>
                        <span className="mono">{u.number}</span> {u.where}
                        {u.name ? <span className="faint"> „{u.name}“</span> : null}
                      </span>
                    ))}
                    {r.in_use_total > 3 && <span className="faint">{L(`+${r.in_use_total - 3} weitere`, `+${r.in_use_total - 3} more`)}</span>}
                  </div>
                )}
              </td>
              <td className="faint nowrap">
                {who(r.created_by)} · {fmtTime(r.created_at)}
                {r.changed_at && (
                  <div>
                    {L("geändert", "changed")} {who(r.changed_by)} · {fmtTime(r.changed_at)}
                  </div>
                )}
              </td>
              {canEdit && (
                <td className="nowrap">
                  <button className="btn btn-sm btn-ghost reservation-edit" onClick={() => setEditing(r.id)}>
                    {L("Ändern", "Edit")}
                  </button>{" "}
                  <button className="btn btn-sm btn-danger reservation-release" onClick={() => release(r)}>
                    {L("Freigeben", "Release")}
                  </button>
                </td>
              )}
            </tr>
          ),
        )}
      </tbody>
    </table>
  );
}

export function StateChip({ state }: { state: ReservationState }) {
  const [cls, label, title] = RESERVATION_STATE[state] ?? ["", state, ""];
  return (
    <span className={`chip ${cls} reservation-state`} title={title} data-state={state}>
      {label}
    </span>
  );
}
