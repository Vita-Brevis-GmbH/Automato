// Kunden als Ausschnitt der Umgebung (ADR 0018, Schritt 1): je Kunde Selektoren („gehört dazu,
// wenn …“), die Zugehörigkeit leitet Automato nach jedem Lauf ab. Vorschau beim Bearbeiten,
// Vorschläge je ACI-Tenant, „keinem Kunden zugeordnet“. Lesen = viewer, Schreiben = operator.
import { Fragment, useCallback, useEffect, useState } from "react";
import {
  createCustomer,
  createCustomerUser,
  CustomerUserRow,
  deleteCustomerUser,
  fetchCustomerUsers,
  updateCustomerUser,
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
import { L } from "./i18n";
import { CUSTOMER_ROLES, customerRoleHint, customerRoleLabel, customerRoleRank, type Me } from "./auth";

export const SELECTOR_LABEL: Record<SelectorKind, string> = {
  aci_tenant: L("ACI-Tenant", "ACI tenant"),
  aci_vrf: "VRF",
  aci_ap: "Application Profile",
  aci_epg: "EPG",
  aci_bd: "Bridge Domain",
  fw_vdom: "VDOM",
  fw_zone: "Zone",
  fw_interface: "Interface",
  prefix: L("Netz", "Network"),
  code: L("Kürzel", "Code"),
  ipam_tenant: L("IPAM-Tenant", "IPAM tenant"),
  ipam_vrf: L("IPAM-VRF/Netzansicht", "IPAM VRF/network view"),
  ipam_tag: L("IPAM-Tag/Attribut", "IPAM tag/attribute"),
};
const PLACEHOLDER: Record<SelectorKind, string> = {
  aci_tenant: "TE_ka",
  aci_vrf: L("TE_ka:VRF_ka bzw. VRF_ka", "TE_ka:VRF_ka or VRF_ka"),
  aci_ap: "TE_ka/AP_007",
  aci_epg: L("TE_ka/AP_007/EPG_web bzw. DN", "TE_ka/AP_007/EPG_web or DN"),
  aci_bd: "TE_ka/BD_web",
  fw_vdom: L("kunde-a", "customer-a"),
  fw_zone: L("kunde-a-lan", "customer-a-lan"),
  fw_interface: "port5",
  prefix: "10.20.0.0/16",
  code: "ka",
  ipam_tenant: L("Kunde A (NetBox-Tenant)", "Customer A (NetBox tenant)"),
  ipam_vrf: L("VRF_ka bzw. Netzansicht", "VRF_ka or network view"),
  ipam_tag: L("kunde-a bzw. Kunde=A", "customer-a or Customer=A"),
};
const FW_KINDS: SelectorKind[] = ["fw_vdom", "fw_zone", "fw_interface"];
const KINDS: [CustomerMemberKind, string][] = [
  ["epg", "EPGs"],
  ["net", L("Netze", "Networks")],
  ["object", L("Objekte", "Objects")],
  ["rule", L("Regeln", "Rules")],
  ["vm", "VMs"],
  ["nat", "NAT"],
  ["finding", "Findings"],
];

function selectorText(s: CustomerSelector, systems: System[]): string {
  const where = s.source_system_id ? ` (${systems.find((x) => x.id === s.source_system_id)?.name ?? "?"})` : "";
  return `${s.exclude ? L("ohne ", "without ") : ""}${SELECTOR_LABEL[s.kind] ?? s.kind} ${s.value}${where}`;
}

export default function Customers({
  tenantId,
  canEdit,
  isAdmin = false,
  onChanged,
}: {
  tenantId: number | null;
  canEdit: boolean;
  isAdmin?: boolean; // Kunden-Benutzer zuordnen (0.53.0)
  onChanged?: () => void; // Kunden geändert: Umschalter oben neu laden
}) {
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
  const changed = () => {
    load();
    onChanged?.();
  };
  useEffect(() => {
    setRows(null);
    load();
    if (tenantId != null)
      fetchSystems(tenantId)
        .then(setSystems)
        .catch(() => setSystems([]));
  }, [tenantId, load]);

  if (tenantId == null) return <Note>{L("Bitte oben eine Umgebung (Mandant) wählen.", "Please select an environment (tenant) above.")}</Note>;

  const accept = async (s: CustomerSuggestion) => {
    try {
      await createCustomer({ tenant_id: tenantId, name: s.name, code: s.code, description: null, shared: s.shared, selectors: s.selectors });
      changed();
    } catch (e) {
      setErr(e);
    }
  };
  const remove = async (c: Customer) => {
    if (
      !window.confirm(
        L(
          `Kunde „${c.name}“ löschen? Nur der Ausschnitt fällt weg, die gelesenen Daten bleiben.`,
          `Delete customer “${c.name}”? Only the scope is removed, the collected data remains.`,
        ),
      )
    )
      return;
    try {
      await deleteCustomer(c.id);
      changed();
    } catch (e) {
      setErr(e);
    }
  };

  return (
    <>
      <PageHead
        title={L("Kunden", "Customers")}
        actions={
          canEdit && (
            <button className="btn btn-primary btn-sm" onClick={() => setEditing("new")}>
              {L("+ Kunde", "+ Customer")}
            </button>
          )
        }
      >
        {L(
          <>
            Ein Kunde ist ein Ausschnitt der Umgebung, beschrieben durch Selektoren: ACI-Tenant, VRF, AP, EPG, Bridge
            Domain, VDOM, Zone, Interface, Netz, Kürzel oder ein Merkmal aus dem IPAM (Tenant, VRF bzw. Netzansicht,
            Tag bzw. Attribut) – ein- bzw. ausschliessend – und seit 0.53.0 durch seine logischen Zonen (Netzwerkdesign).
            Was dazugehört, leitet Automato nach jedem Lauf neu ab; neue EPGs oder Netze gehören ohne Pflege dazu.
          </>,
          <>
            A customer is a scope of the environment, described by selectors: ACI tenant, VRF, AP, EPG, Bridge Domain,
            VDOM, zone, interface, network, code or an attribute from the IPAM (tenant, VRF or network view, tag or
            attribute) – including or excluding – and since 0.53.0 by its logical zones (network design). Automato
            derives what belongs to it after every run; new EPGs or networks belong to it without maintenance.
          </>,
        )}
      </PageHead>
      <ErrorBox error={err} onClose={() => setErr(null)} />

      <div className="card">
        <div className="card-body scroll">
          {rows == null ? (
            <Loading />
          ) : rows.length === 0 ? (
            <Empty title={L("Noch keine Kunden", "No customers yet")}>
              {L("Vorschläge unten übernehmen oder einen Kunden anlegen.", "Apply suggestions below or create a customer.")}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>{L("Kunde", "Customer")}</th>
                  <th>{L("Kürzel", "Code")}</th>
                  <th>{L("Selektoren", "Selectors")}</th>
                  <th>{L("Zonen", "Zones")}</th>
                  {KINDS.map(([k, label]) => (
                    <th key={k} className="num">
                      {label}
                    </th>
                  ))}
                  <th>{L("Abgeleitet", "Derived")}</th>
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
                          <span className="chip info" title={L("gilt für alle Kunden", "applies to all customers")}>
                            {L("geteilt", "shared")}
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
                      <td>
                        {c.zones.length ? (
                          <div className="objs">
                            {c.zones.map((z) => (
                              <a key={z} className="chip info" href="#design" onClick={(e) => e.stopPropagation()}>
                                {z}
                              </a>
                            ))}
                          </div>
                        ) : (
                          <Dash />
                        )}
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
                              {L("Bearbeiten", "Edit")}
                            </button>{" "}
                            <button className="btn btn-sm btn-ghost" onClick={() => remove(c)}>
                              {L("Löschen", "Delete")}
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                    {open === c.id && (
                      <tr className="subrow">
                        <td colSpan={KINDS.length + 6}>
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
            <h2>{L("Vorschläge", "Suggestions")}</h2>
            <span className="faint">
              {L(
                <>je ACI-Tenant ohne Kunden ein Kunde, das Kürzel aus TE_&lt;kürzel&gt;; common als geteilter Teil</>,
                <>one customer per ACI tenant without a customer, the code from TE_&lt;code&gt;; common as shared part</>,
              )}
            </span>
          </div>
          <div className="card-body scroll">
            <table className="compact">
              <thead>
                <tr>
                  <th>{L("Kunde", "Customer")}</th>
                  <th>{L("Kürzel", "Code")}</th>
                  <th>{L("Selektor", "Selector")}</th>
                  <th className="num">EPGs</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {suggestions.map((s) => (
                  <tr key={s.name}>
                    <td>
                      <b>{s.name}</b> {s.shared && <span className="chip info">{L("geteilt", "shared")}</span>}
                    </td>
                    <td className="mono">{s.code ?? <Dash />}</td>
                    <td>{s.selectors.map((x) => selectorText(x, systems)).join(", ")}</td>
                    <td className="num">{s.epgs}</td>
                    <td className="nowrap">
                      {canEdit && (
                        <>
                          <button className="btn btn-sm" onClick={() => accept(s)}>
                            {L("Übernehmen", "Apply")}
                          </button>{" "}
                          <button className="btn btn-sm btn-ghost" onClick={() => setEditing(s)}>
                            {L("Anpassen…", "Adjust…")}
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
            <h2>{L("Keinem Kunden zugeordnet", "Not assigned to any customer")}</h2>
            <span className="faint">{L("auch keinem geteilten Teil", "nor to any shared part")}</span>
          </div>
          <div className="card-body">
            <table className="compact">
              <tbody>
                {(
                  [
                    ["epg", "EPGs"],
                    ["rule", L("Regeln", "Rules")],
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

      {isAdmin && rows && <CustomerUsers tenantId={tenantId} customers={rows} />}

      {editing && (
        <CustomerEditor
          tenantId={tenantId}
          systems={systems}
          initial={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            changed();
          }}
        />
      )}
    </>
  );
}

// Link zum Element in seiner Ansicht (Dev-Befund 0.33.0): Regel direkt, sonst über die Suche
function memberLink(kind: CustomerMemberKind, m: { id: number | null; name: string }): string | null {
  const q = (v: string) => encodeURIComponent(v);
  switch (kind) {
    case "rule":
      return m.id ? `#regeln?rule=${m.id}` : null;
    case "epg":
    case "net":
      return `#regeln?q=${q(m.name)}`;
    case "object":
      return `#objekte?q=${q(m.name)}`;
    case "vm":
      return `#objekte?tab=vms&q=${q(m.name)}`;
    case "nat":
      return `#nat?q=${q(m.name)}`;
    case "finding":
      return `#findings?q=${q(m.name.split(": ").slice(1).join(": ") || m.name)}`;
    default:
      return null;
  }
}

// Zugehörigkeit eines Kunden je Art mit Grund (Selektor)
function Members({ customer }: { customer: Customer }) {
  const [kind, setKind] = useState<CustomerMemberKind>("epg");
  const [items, setItems] = useState<{ id: number | null; name: string; via: string | null }[] | null>(null);
  useEffect(() => {
    setItems(null);
    let current = true; // nur die Antwort zur letzten Anfrage (Reiter schnell gewechselt)
    fetchCustomerMembers(customer.id, kind)
      .then((r) => current && setItems(r))
      .catch(() => current && setItems([]));
    return () => {
      current = false;
    };
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
        <span className="faint">{L("keine", "none")}</span>
      ) : (
        <table className="compact">
          <tbody>
            {items.map((m, i) => {
              const href = memberLink(kind, m);
              return (
                <tr key={`${m.id ?? m.name}-${i}`}>
                  <td className="mono">{href ? <a href={href}>{m.name}</a> : m.name}</td>
                  <td className="faint">{m.via}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {items && items.length >= 100 && <div className="faint">{L("die ersten 100", "the first 100")}</div>}
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
      previewCustomer(tenantId, valid, existing?.id)
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
      wide
      title={existing ? L(`Kunde „${existing.name}“`, `Customer “${existing.name}”`) : L("Neuer Kunde", "New customer")}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-primary" onClick={save} disabled={busy || !name.trim()}>
            {busy ? L("Speichere…", "Saving…") : L("Speichern", "Save")}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="field">
        <label htmlFor="cu-name">Name</label>
        <input id="cu-name" className="input" value={name} maxLength={200} placeholder={L("z. B. Kunde A", "e.g. Customer A")} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="cu-code">{L("Kürzel (optional)", "Code (optional)")}</label>
        <input id="cu-code" className="input mono" value={code} maxLength={50} placeholder={L("z. B. ka", "e.g. ka")} onChange={(e) => setCode(e.target.value)} />
        <div className="hint">
          {L(
            <>Aus der Namenskonvention (TE_&lt;kürzel&gt;); als Selektor „Kürzel“ ordnet es alles mit diesem Kürzel zu.</>,
            <>From the naming convention (TE_&lt;code&gt;); as selector “Code” it assigns everything with this code.</>,
          )}
        </div>
      </div>
      <div className="field">
        <label htmlFor="cu-desc">{L("Beschreibung (optional)", "Description (optional)")}</label>
        <input id="cu-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <label className="check">
        <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />{" "}
        {L(
          "Geteilter Teil der Umgebung (gilt für alle Kunden, z. B. Tenant common)",
          "Shared part of the environment (applies to all customers, e.g. tenant common)",
        )}
      </label>

      <h3 style={{ marginTop: 12 }}>{L("Selektoren", "Selectors")}</h3>
      <table className="compact form-table" id="customer-selectors">
        <colgroup>
          <col style={{ width: "24%" }} />
          <col />
          <col style={{ width: "22%" }} />
          <col style={{ width: 64 }} />
          <col style={{ width: 92 }} />
        </colgroup>
        <thead>
          <tr>
            <th>{L("Art", "Type")}</th>
            <th>{L("Wert", "Value")}</th>
            <th>{L("Quellsystem", "Source system")}</th>
            <th title={L("ausschliessen", "exclude")}>{L("aus­schl.", "excl.")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {selectors.map((s, i) => (
            <tr key={i}>
              <td>
                <select className="sel" aria-label={L("Art", "Type")} value={s.kind} onChange={(e) => set(i, { kind: e.target.value as SelectorKind })}>
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
                  aria-label={L("Wert", "Value")}
                  value={s.value}
                  placeholder={s.kind === "code" && code ? code : PLACEHOLDER[s.kind]}
                  onChange={(e) => set(i, { value: e.target.value })}
                />
              </td>
              <td>
                {FW_KINDS.includes(s.kind) ? (
                  <select
                    className="sel"
                    aria-label={L("Quellsystem", "Source system")}
                    value={s.source_system_id ?? ""}
                    onChange={(e) => set(i, { source_system_id: e.target.value ? Number(e.target.value) : null })}
                  >
                    <option value="">{L("alle Firewalls", "all firewalls")}</option>
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
                <input type="checkbox" aria-label={L("ausschliessen", "exclude")} checked={s.exclude} onChange={(e) => set(i, { exclude: e.target.checked })} />
              </td>
              <td>
                <button className="btn btn-sm btn-ghost" onClick={() => setSelectors((xs) => xs.filter((_, j) => j !== i))}>
                  {L("Entfernen", "Remove")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="btn btn-sm" onClick={() => setSelectors((xs) => [...xs, { ...EMPTY_SELECTOR }])}>
        {L("+ Selektor", "+ Selector")}
      </button>

      <h3 style={{ marginTop: 12 }}>{L("Vorschau", "Preview")}</h3>
      {preview == null ? (
        <Loading />
      ) : (
        <table className="compact form-table" id="customer-preview">
          <colgroup>
            <col style={{ width: "24%" }} />
            <col style={{ width: 70 }} />
            <col />
          </colgroup>
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
            {(preview.off_path?.count ?? 0) > 0 && (
              <tr id="customer-preview-offpath">
                <td>{L("nicht auf dem Weg", "not on the path")}</td>
                <td className="num">{preview.off_path!.count}</td>
                <td className="faint">
                  {L(
                    <>
                      Regeln über ein Netz des Kunden, die der Verkehr nach dem Routing nicht erreicht (Firewall: Ein- bzw.
                      Ausgang der Regel, ACI: Zuordnung in der VRF) – zählen nicht:{" "}
                    </>,
                    <>
                      Rules via a network of the customer that traffic does not reach after routing (firewall: incoming
                      or outgoing interface of the rule, ACI: assignment in the VRF) – not counted:{" "}
                    </>,
                  )}
                  {preview.off_path!.samples.map((x, i) => (
                    <Fragment key={x.id}>
                      {i > 0 && ", "}
                      <span title={x.why}>{x.name}</span>
                    </Fragment>
                  ))}
                  {preview.off_path!.count > preview.off_path!.samples.length ? " …" : ""}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </Modal>
  );
}

// Kunden-Admin (feinere Rollen, ADR 0018 Ergänzung): schlanke Seite mit den Kunden-Benutzern der
// eigenen Kunden – der Orchestrator lässt nur diese Kunden, höchstens die eigene Rolle und nie das
// eigene Konto zu
export function OwnCustomerUsers({ me, tenantId }: { me: Me; tenantId: number | null }) {
  const own = me.customers.filter((c) => me.customerRoles[String(c.id)] === "customer_admin");
  return (
    <>
      <PageHead title={L("Kunden-Benutzer", "Customer users")}>
        {L(
          <>
            Wer die Doku Ihres Kunden sieht und was er darf. Die Anmeldung läuft über Keycloak; hier ordnen Sie
            Benutzername bzw. E-Mail Ihrem Kunden zu und wählen die Rolle. Ihr eigenes Konto ändern Sie hier nicht.
          </>,
          <>
            Who sees the documentation of your customer and what they may do. Sign-in runs via Keycloak; here you
            assign username or email to your customer and choose the role. You do not change your own account here.
          </>,
        )}
      </PageHead>
      {tenantId == null ? (
        <Note>{L("Bitte oben eine Umgebung (Mandant) wählen.", "Please select an environment (tenant) above.")}</Note>
      ) : (
        <CustomerUsers tenantId={tenantId} customers={own} me={me} />
      )}
    </>
  );
}

// Rollen mit Erklärung (feinere Rollen) – für die Auswahl und die Legende
function RoleLegend() {
  return (
    <dl className="kv tight" id="customer-role-legend" style={{ margin: "8px 0 12px" }}>
      {CUSTOMER_ROLES.map((r) => (
        <Fragment key={r}>
          <dt>{customerRoleLabel(r)}</dt>
          <dd className="faint">{customerRoleHint(r)}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

function RoleOptions({ max, current }: { max: number; current?: string }) {
  return (
    <>
      {current === "operator" && (
        <option value="operator" title={customerRoleHint("operator")}>
          {L("Triage (operator, bisher)", "Triage (operator, legacy)")}
        </option>
      )}
      {CUSTOMER_ROLES.filter((r) => customerRoleRank(r) <= max).map((r) => (
        <option key={r} value={r} title={customerRoleHint(r)}>
          {customerRoleLabel(r)}
        </option>
      ))}
    </>
  );
}

// Zuordnung Benutzer → Kunde in Automato (0.53.0, Admin): Keycloak meldet, wer jemand ist;
// hier steht, welchen Kunden er sieht und mit welcher Kunden-Rolle (feinere Rollen). Eine
// Zuordnung schränkt immer ein (nie Admin). Mit `me` als Kunden-Admin: nur seine Kunden, höchstens
// seine Rolle, das eigene Konto nicht.
function CustomerUsers({ tenantId, customers, me }: { tenantId: number; customers: { id: number; name: string }[]; me?: Me }) {
  const [rows, setRows] = useState<CustomerUserRow[] | null>(null);
  const [login, setLogin] = useState("");
  const [customerId, setCustomerId] = useState<number | "">(me && customers.length === 1 ? customers[0].id : "");
  const [role, setRole] = useState("viewer");
  const selfAdmin = !!me;
  // höchste vergebbare Rolle: Betreuer alle, Kunden-Admin höchstens die eigene je Kunde
  const maxRank = (cid: number | "") =>
    !me ? CUSTOMER_ROLES.length - 1 : cid === "" ? -1 : customerRoleRank(me.customerRoles[String(cid)]);
  const isOwn = (r: CustomerUserRow) =>
    !!me && (me.logins.includes(r.login) || r.login === me.username.trim().toLowerCase());
  const [note, setNote] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    fetchCustomerUsers(tenantId).then(setRows).catch(setErr);
  }, [tenantId]);
  useEffect(load, [load]);
  const add = async () => {
    if (customerId === "") return;
    setBusy(true);
    setErr(null);
    try {
      await createCustomerUser({ customer_id: customerId, login: login.trim(), role, note: note.trim() || null });
      setLogin("");
      setNote("");
      load();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const change = async (r: CustomerUserRow, next: string) => {
    try {
      await updateCustomerUser(r.id, { role: next });
      load();
    } catch (e) {
      setErr(e);
    }
  };
  const remove = async (r: CustomerUserRow) => {
    if (
      !window.confirm(
        L(
          `Zuordnung von „${r.login}“ zu „${r.customer ?? "?"}“ entfernen? Der Benutzer sieht den Kunden ab der nächsten Anfrage nicht mehr.`,
          `Remove assignment of “${r.login}” to “${r.customer ?? "?"}”? The user no longer sees the customer from the next request on.`,
        ),
      )
    )
      return;
    try {
      await deleteCustomerUser(r.id);
      load();
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <div className="card" id="customer-users">
      <div className="card-head">
        <h2>{L("Kunden-Benutzer", "Customer users")}</h2>
        <span className="faint">
          {selfAdmin
            ? L(
                "Benutzername bzw. E-Mail → Ihr Kunde, mit Rolle. Wirkt ab der nächsten Anfrage des Benutzers.",
                "Username or email → your customer, with role. Takes effect from the user's next request.",
              )
            : L(
                <>
                  Anmeldung in Keycloak, Zuordnung hier: Benutzername bzw. E-Mail → Kunde, mit Kunden-Rolle. Ein zugeordneter
                  Benutzer sieht nur die Doku seines Kunden (nie Admin); ein Kunden-Admin ordnet die Benutzer seines Kunden
                  selbst zu. Alternativ die Attribute customer_ids und customer_role in Keycloak (die höhere Rolle gilt).
                </>,
                <>
                  Login in Keycloak, assignment here: username or email → customer, with customer role. An assigned user only
                  sees the documentation of their customer (never admin); a customer admin assigns the users of their
                  customer themselves. Alternatively the attributes customer_ids and customer_role in Keycloak (the higher
                  role applies).
                </>,
              )}
        </span>
      </div>
      <div className="card-body">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        <RoleLegend />
        <div className="zone-intent" id="customer-user-add">
          <input className="input mono" aria-label={L("Benutzer", "User")} placeholder={L("Benutzername bzw. E-Mail", "Username or email")} value={login} onChange={(e) => setLogin(e.target.value)} />
          <select
            className="sel"
            aria-label={L("Kunde", "Customer")}
            value={customerId}
            onChange={(e) => {
              const next = e.target.value ? Number(e.target.value) : "";
              setCustomerId(next);
              if (customerRoleRank(role) > maxRank(next)) setRole("viewer");
            }}
          >
            <option value="">{L("– Kunde –", "– Customer –")}</option>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <select className="sel" aria-label={L("Rolle", "Role")} value={role} onChange={(e) => setRole(e.target.value)}>
            <RoleOptions max={selfAdmin ? Math.max(maxRank(customerId), 0) : CUSTOMER_ROLES.length - 1} />
          </select>
          <input className="input" aria-label={L("Notiz", "Note")} placeholder={L("Notiz (optional)", "Note (optional)")} value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="btn btn-sm btn-primary" disabled={busy || !login.trim() || customerId === ""} onClick={add}>
            {L("Zuordnen", "Assign")}
          </button>
        </div>
        {rows == null ? (
          <Loading />
        ) : rows.length === 0 ? (
          <span className="faint">{L("noch keine Zuordnungen", "no assignments yet")}</span>
        ) : (
          <table className="compact" id="customer-user-list">
            <thead>
              <tr>
                <th>{L("Benutzer", "User")}</th>
                <th>{L("Kunde", "Customer")}</th>
                <th>{L("Rolle", "Role")}</th>
                <th>{L("Notiz", "Note")}</th>
                <th>{L("Angelegt", "Created")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="mono">
                    {r.login}
                    {isOwn(r) && <span className="chip na">{L("Sie", "you")}</span>}
                  </td>
                  <td>{r.customer ?? "?"}</td>
                  <td>
                    <select
                      className="sel"
                      aria-label={L("Rolle ändern", "Change role")}
                      value={r.role}
                      disabled={isOwn(r) || customerRoleRank(r.role) > maxRank(r.customer_id)}
                      title={customerRoleHint(r.role)}
                      onChange={(e) => change(r, e.target.value)}
                    >
                      <RoleOptions max={Math.max(maxRank(r.customer_id), customerRoleRank(r.role))} current={r.role} />
                    </select>
                  </td>
                  <td className="faint">{r.note ?? ""}</td>
                  <td className="faint nowrap">
                    {fmtTime(r.created_at)} ·{" "}
                    {r.created_by ?? (r.origin === "staff" ? L("Betreuer", "staff") : "?")}
                    {!selfAdmin && r.origin === "customer" && (
                      <span className="chip info" title={L("vom Kunden-Admin angelegt – wirkt nie auf Betreuer", "created by the customer admin – never applies to staff")}>
                        {L("Kunden-Admin", "customer admin")}
                      </span>
                    )}
                  </td>
                  <td>
                    {!isOwn(r) && (
                      <button className="btn btn-sm btn-ghost" onClick={() => remove(r)}>
                        {L("Entfernen", "Remove")}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
