// Kunden als Ausschnitt der Umgebung (ADR 0018, Schritt 1): je Kunde Selektoren („gehört dazu,
// wenn …“), die Zugehörigkeit leitet Automato nach jedem Lauf ab. Vorschau beim Bearbeiten,
// Vorschläge je ACI-Tenant, „keinem Kunden zugeordnet“. Lesen = viewer, Schreiben = operator.
import { Fragment, useCallback, useEffect, useState } from "react";
import {
  createCustomer,
  Customer,
  CustomerIn,
  CustomerPreview,
  CustomerSelector,
  CustomerSuggestion,
  deleteCustomer,
  fetchCustomerMembers,
  fetchCustomers,
  fetchCustomerSuggestions,
  fetchSystems,
  fetchUnassigned,
  CustomerMemberKind,
  previewCustomer,
  SelectorKind,
  System,
  Unassigned,
  updateCustomer,
} from "./api";
import { Dash, Empty, ErrorBox, fmtTime, Loading, Modal, Note, PageHead } from "./ui";

export const SELECTOR_LABEL: Record<SelectorKind, string> = {
  aci_tenant: "ACI-Tenant",
  aci_vrf: "VRF",
  aci_ap: "Application Profile",
  aci_epg: "EPG",
  aci_bd: "Bridge Domain",
  fw_vdom: "VDOM",
  fw_zone: "Zone",
  fw_interface: "Interface",
  prefix: "Netz",
  code: "Kürzel",
};
const PLACEHOLDER: Record<SelectorKind, string> = {
  aci_tenant: "TE_ka",
  aci_vrf: "TE_ka:VRF_ka bzw. VRF_ka",
  aci_ap: "TE_ka/AP_007",
  aci_epg: "TE_ka/AP_007/EPG_web bzw. DN",
  aci_bd: "TE_ka/BD_web",
  fw_vdom: "kunde-a",
  fw_zone: "kunde-a-lan",
  fw_interface: "port5",
  prefix: "10.20.0.0/16",
  code: "ka",
};
const FW_KINDS: SelectorKind[] = ["fw_vdom", "fw_zone", "fw_interface"];
const KINDS: [CustomerMemberKind, string][] = [
  ["epg", "EPGs"],
  ["net", "Netze"],
  ["object", "Objekte"],
  ["rule", "Regeln"],
  ["vm", "VMs"],
];

function selectorText(s: CustomerSelector, systems: System[]): string {
  const where = s.source_system_id ? ` (${systems.find((x) => x.id === s.source_system_id)?.name ?? "?"})` : "";
  return `${s.exclude ? "ohne " : ""}${SELECTOR_LABEL[s.kind] ?? s.kind} ${s.value}${where}`;
}

export default function Customers({ tenantId, canEdit }: { tenantId: number | null; canEdit: boolean }) {
  const [rows, setRows] = useState<Customer[] | null>(null);
  const [suggestions, setSuggestions] = useState<CustomerSuggestion[]>([]);
  const [unassigned, setUnassigned] = useState<Unassigned | null>(null);
  const [systems, setSystems] = useState<System[]>([]);
  const [err, setErr] = useState<unknown>(null);
  const [editing, setEditing] = useState<Customer | CustomerSuggestion | "new" | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  const load = useCallback(() => {
    if (tenantId == null) return;
    setErr(null);
    Promise.all([fetchCustomers(tenantId), fetchCustomerSuggestions(tenantId), fetchUnassigned(tenantId)])
      .then(([c, s, u]) => {
        setRows(c);
        setSuggestions(s);
        setUnassigned(u);
      })
      .catch(setErr);
  }, [tenantId]);
  useEffect(() => {
    setRows(null);
    load();
    if (tenantId != null)
      fetchSystems(tenantId)
        .then(setSystems)
        .catch(() => setSystems([]));
  }, [tenantId, load]);

  if (tenantId == null) return <Note>Bitte oben eine Umgebung (Mandant) wählen.</Note>;

  const accept = async (s: CustomerSuggestion) => {
    try {
      await createCustomer({ tenant_id: tenantId, name: s.name, code: s.code, description: null, shared: s.shared, selectors: s.selectors });
      load();
    } catch (e) {
      setErr(e);
    }
  };
  const remove = async (c: Customer) => {
    if (!window.confirm(`Kunde „${c.name}“ löschen? Nur der Ausschnitt fällt weg, die gelesenen Daten bleiben.`)) return;
    try {
      await deleteCustomer(c.id);
      load();
    } catch (e) {
      setErr(e);
    }
  };

  return (
    <>
      <PageHead
        title="Kunden"
        actions={
          canEdit && (
            <button className="btn btn-primary btn-sm" onClick={() => setEditing("new")}>
              + Kunde
            </button>
          )
        }
      >
        Ein Kunde ist ein Ausschnitt der Umgebung, beschrieben durch Selektoren: ACI-Tenant, VRF, AP, EPG, Bridge
        Domain, VDOM, Zone, Interface, Netz oder Kürzel – ein- bzw. ausschliessend. Was dazugehört, leitet Automato
        nach jedem Lauf neu ab; neue EPGs oder Netze gehören ohne Pflege dazu.
      </PageHead>
      <ErrorBox error={err} onClose={() => setErr(null)} />

      <div className="card">
        <div className="card-body scroll">
          {rows == null ? (
            <Loading />
          ) : rows.length === 0 ? (
            <Empty title="Noch keine Kunden">Vorschläge unten übernehmen oder einen Kunden anlegen.</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Kunde</th>
                  <th>Kürzel</th>
                  <th>Selektoren</th>
                  {KINDS.map(([k, label]) => (
                    <th key={k} className="num">
                      {label}
                    </th>
                  ))}
                  <th>Abgeleitet</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <Fragment key={c.id}>
                    <tr className="clickable" onClick={() => setOpen(open === c.id ? null : c.id)}>
                      <td>
                        <b>{c.name}</b>{" "}
                        {c.shared && (
                          <span className="chip info" title="gilt für alle Kunden">
                            geteilt
                          </span>
                        )}
                        {c.description && <div className="faint">{c.description}</div>}
                      </td>
                      <td className="mono">{c.code ?? <Dash />}</td>
                      <td>
                        <div className="objs">
                          {c.selectors.map((s) => (
                            <span key={s.id} className={`chip ${s.exclude ? "warn" : ""}`}>
                              {selectorText(s, systems)}
                            </span>
                          ))}
                        </div>
                      </td>
                      {KINDS.map(([k]) => (
                        <td key={k} className="num">
                          {c.counts[k]}
                        </td>
                      ))}
                      <td className="faint nowrap">{fmtTime(c.derived_at)}</td>
                      <td className="nowrap" onClick={(e) => e.stopPropagation()}>
                        {canEdit && (
                          <>
                            <button className="btn btn-sm" onClick={() => setEditing(c)}>
                              Bearbeiten
                            </button>{" "}
                            <button className="btn btn-sm btn-ghost" onClick={() => remove(c)}>
                              Löschen
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                    {open === c.id && (
                      <tr className="subrow">
                        <td colSpan={KINDS.length + 5}>
                          <Members customer={c} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {suggestions.length > 0 && (
        <div className="card" id="customer-suggestions">
          <div className="card-head">
            <h2>Vorschläge</h2>
            <span className="faint">je ACI-Tenant ohne Kunden ein Kunde, das Kürzel aus TE_&lt;kürzel&gt;; common als geteilter Teil</span>
          </div>
          <div className="card-body scroll">
            <table className="compact">
              <thead>
                <tr>
                  <th>Kunde</th>
                  <th>Kürzel</th>
                  <th>Selektor</th>
                  <th className="num">EPGs</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {suggestions.map((s) => (
                  <tr key={s.name}>
                    <td>
                      <b>{s.name}</b> {s.shared && <span className="chip info">geteilt</span>}
                    </td>
                    <td className="mono">{s.code ?? <Dash />}</td>
                    <td>{s.selectors.map((x) => selectorText(x, systems)).join(", ")}</td>
                    <td className="num">{s.epgs}</td>
                    <td className="nowrap">
                      {canEdit && (
                        <>
                          <button className="btn btn-sm" onClick={() => accept(s)}>
                            Übernehmen
                          </button>{" "}
                          <button className="btn btn-sm btn-ghost" onClick={() => setEditing(s)}>
                            Anpassen…
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {unassigned && (
        <div className="card" id="customer-unassigned">
          <div className="card-head">
            <h2>Keinem Kunden zugeordnet</h2>
            <span className="faint">auch keinem geteilten Teil</span>
          </div>
          <div className="card-body">
            <table className="compact">
              <tbody>
                {(
                  [
                    ["epg", "EPGs"],
                    ["rule", "Regeln"],
                    ["vm", "VMs"],
                  ] as const
                ).map(([k, label]) => (
                  <tr key={k}>
                    <td>{label}</td>
                    <td className="num">{unassigned[k].count}</td>
                    <td className="faint">
                      {unassigned[k].samples.join(", ")}
                      {unassigned[k].count > unassigned[k].samples.length ? " …" : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {editing && (
        <CustomerEditor
          tenantId={tenantId}
          systems={systems}
          initial={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            load();
          }}
        />
      )}
    </>
  );
}

// Zugehörigkeit eines Kunden je Art mit Grund (Selektor)
function Members({ customer }: { customer: Customer }) {
  const [kind, setKind] = useState<CustomerMemberKind>("epg");
  const [items, setItems] = useState<{ id: number | null; name: string; via: string | null }[] | null>(null);
  useEffect(() => {
    setItems(null);
    fetchCustomerMembers(customer.id, kind)
      .then(setItems)
      .catch(() => setItems([]));
  }, [customer.id, kind]);
  return (
    <div>
      <div className="tabbar">
        {KINDS.map(([k, label]) => (
          <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>
            {label} ({customer.counts[k]})
          </button>
        ))}
      </div>
      {items == null ? (
        <Loading />
      ) : items.length === 0 ? (
        <span className="faint">keine</span>
      ) : (
        <table className="compact">
          <tbody>
            {items.map((m, i) => (
              <tr key={`${m.id ?? m.name}-${i}`}>
                <td className="mono">
                  {kind === "rule" && m.id ? <a href={`#regeln?rule=${m.id}`}>{m.name}</a> : m.name}
                </td>
                <td className="faint">{m.via}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {items && items.length >= 100 && <div className="faint">die ersten 100</div>}
    </div>
  );
}

const EMPTY_SELECTOR: CustomerSelector = { kind: "aci_tenant", value: "", source_system_id: null, exclude: false };

function CustomerEditor({
  tenantId,
  systems,
  initial,
  onClose,
  onSaved,
}: {
  tenantId: number;
  systems: System[];
  initial: Customer | CustomerSuggestion | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const existing = initial && "id" in initial ? initial : null;
  const [name, setName] = useState(initial?.name ?? "");
  const [code, setCode] = useState(initial?.code ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [shared, setShared] = useState(initial?.shared ?? false);
  const [selectors, setSelectors] = useState<CustomerSelector[]>(
    initial?.selectors.map((s) => ({ ...s })) ?? [{ ...EMPTY_SELECTOR }],
  );
  const [preview, setPreview] = useState<CustomerPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const firewalls = systems.filter((s) => s.platform === "fortigate");
  const valid = selectors.filter((s) => s.value.trim());

  // Vorschau: was die Selektoren erfassen (verzögert, ohne zu speichern)
  useEffect(() => {
    const t = window.setTimeout(() => {
      previewCustomer(tenantId, valid)
        .then((p) => {
          setPreview(p);
          setErr(null);
        })
        .catch(setErr);
    }, 400);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, JSON.stringify(valid)]);

  const set = (i: number, patch: Partial<CustomerSelector>) =>
    setSelectors((xs) => xs.map((s, j) => (j === i ? { ...s, ...patch } : s)));

  const save = async () => {
    setBusy(true);
    setErr(null);
    const body: CustomerIn = {
      tenant_id: tenantId,
      name: name.trim(),
      code: code.trim() || null,
      description: description.trim() || null,
      shared,
      selectors: valid.map((s) => ({ ...s, value: s.value.trim(), source_system_id: FW_KINDS.includes(s.kind) ? s.source_system_id : null })),
    };
    try {
      if (existing) await updateCustomer(existing.id, body);
      else await createCustomer(body);
      onSaved();
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  };

  return (
    <Modal
      title={existing ? `Kunde „${existing.name}“` : "Neuer Kunde"}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button className="btn btn-primary" onClick={save} disabled={busy || !name.trim()}>
            {busy ? "Speichere…" : "Speichern"}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="field">
        <label htmlFor="cu-name">Name</label>
        <input id="cu-name" className="input" value={name} maxLength={200} placeholder="z. B. Kunde A" onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="cu-code">Kürzel (optional)</label>
        <input id="cu-code" className="input mono" value={code} maxLength={50} placeholder="z. B. ka" onChange={(e) => setCode(e.target.value)} />
        <div className="hint">Aus der Namenskonvention (TE_&lt;kürzel&gt;); als Selektor „Kürzel“ ordnet es alles mit diesem Kürzel zu.</div>
      </div>
      <div className="field">
        <label htmlFor="cu-desc">Beschreibung (optional)</label>
        <input id="cu-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <label className="check">
        <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} /> Geteilter Teil der Umgebung (gilt für alle Kunden,
        z. B. Tenant common)
      </label>

      <h3 style={{ marginTop: 12 }}>Selektoren</h3>
      <table className="compact">
        <thead>
          <tr>
            <th>Art</th>
            <th>Wert</th>
            <th>Quellsystem</th>
            <th>ausschliessen</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {selectors.map((s, i) => (
            <tr key={i}>
              <td>
                <select className="sel" aria-label="Art" value={s.kind} onChange={(e) => set(i, { kind: e.target.value as SelectorKind })}>
                  {(Object.keys(SELECTOR_LABEL) as SelectorKind[]).map((k) => (
                    <option key={k} value={k}>
                      {SELECTOR_LABEL[k]}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <input
                  className="input mono"
                  aria-label="Wert"
                  value={s.value}
                  placeholder={s.kind === "code" && code ? code : PLACEHOLDER[s.kind]}
                  onChange={(e) => set(i, { value: e.target.value })}
                />
              </td>
              <td>
                {FW_KINDS.includes(s.kind) ? (
                  <select
                    className="sel"
                    aria-label="Quellsystem"
                    value={s.source_system_id ?? ""}
                    onChange={(e) => set(i, { source_system_id: e.target.value ? Number(e.target.value) : null })}
                  >
                    <option value="">alle Firewalls</option>
                    {firewalls.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Dash />
                )}
              </td>
              <td>
                <input type="checkbox" aria-label="ausschliessen" checked={s.exclude} onChange={(e) => set(i, { exclude: e.target.checked })} />
              </td>
              <td>
                <button className="btn btn-sm btn-ghost" onClick={() => setSelectors((xs) => xs.filter((_, j) => j !== i))}>
                  Entfernen
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="btn btn-sm" onClick={() => setSelectors((xs) => [...xs, { ...EMPTY_SELECTOR }])}>
        + Selektor
      </button>

      <h3 style={{ marginTop: 12 }}>Vorschau</h3>
      {preview == null ? (
        <Loading />
      ) : (
        <table className="compact" id="customer-preview">
          <tbody>
            {KINDS.map(([k, label]) => (
              <tr key={k}>
                <td>{label}</td>
                <td className="num">{preview.counts[k]}</td>
                <td className="faint">
                  {preview.samples[k].join(", ")}
                  {preview.counts[k] > preview.samples[k].length ? " …" : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Modal>
  );
}
