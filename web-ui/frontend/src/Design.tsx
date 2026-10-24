// Netzwerkdesign (0.51.0, ADR 0024): logische Zonen in Automato pflegen (Selektoren wie beim
// Kunden, dazu „Anbindung“), die Zonenmatrix (Regeln je Paar Quell-/Zielzone mit Absicht und
// Verstössen) und der Zonenplan (Zonen an Firewalls/VDOMs und ACI-VRFs, Übergänge, Netze ohne
// Zone). Lesen = viewer, Zonen und Absicht ändern = operator. Seit 0.52.0 Export (draw.io, SVG, PDF)
// und Versionen des Plans (speichern = operator, löschen = admin).
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import {
  createZone,
  Customer,
  deletePlanVersion,
  deleteZone,
  exportZonePlan,
  fetchCustomers,
  fetchExits,
  fetchPlanVersion,
  fetchPlanVersionDiff,
  fetchPlanVersions,
  fetchSystems,
  fetchZoneCellRules,
  fetchZoneMatrix,
  fetchZoneNets,
  fetchZonePlan,
  fetchZones,
  LogicalZone,
  PlanDiff,
  PlanFormat,
  PlanLayout,
  PlanVersion,
  PlanVersionFull,
  previewZone,
  savePlanVersion,
  setZonePolicy,
  System,
  TopoExit,
  updateZone,
  ZoneCell,
  ZoneCellRule,
  ZoneIn,
  ZoneIntent,
  ZoneKind,
  ZoneMatrix,
  ZonePlan,
  ZonePreview,
  ZoneSelector,
  ZoneSelectorKind,
  ZoneSummary,
} from "./api";
import { SELECTOR_LABEL } from "./Customers";
import { Dash, download, Empty, ErrorBox, fmtTime, Loading, Modal, Note, PageHead } from "./ui";

export const ZONE_KIND: Record<ZoneKind, string> = {
  internal: "intern",
  dmz: "DMZ",
  external: "extern",
  management: "Management",
  shared: "geteilt",
  transfer: "Transfer",
};
const INTENT: Record<ZoneIntent, [string, string]> = {
  allow: ["ok", "erlaubt"],
  limited: ["warn", "eingeschränkt"],
  deny: ["crit", "gesperrt"],
};
const ZONE_SELECTOR_LABEL: Record<ZoneSelectorKind, string> = { ...SELECTOR_LABEL, exit: "Anbindung" };
const PLACEHOLDER: Partial<Record<ZoneSelectorKind, string>> = {
  aci_tenant: "TE_ka",
  aci_epg: "TE_ka/AP_007/EPG_web",
  fw_zone: "dmz",
  fw_interface: "port5",
  prefix: "10.20.0.0/16",
  exit: "Internet",
};
const FW_KINDS: ZoneSelectorKind[] = ["fw_vdom", "fw_zone", "fw_interface"];
const VIA: Record<string, string> = { interface: "Interface", fabric: "im Fabric", exit: "Anbindung", route: "über Route" };

type Tab = "zones" | "matrix" | "plan" | "versions";

export default function Design({
  tenantId,
  tenantLabel,
  canEdit,
  isAdmin,
  isCustomer = false,
}: {
  tenantId: number | null;
  tenantLabel: string;
  canEdit: boolean;
  isAdmin: boolean;
  isCustomer?: boolean; // Kunden-Benutzer (0.53.0): eigene Zonen, geteilte nur mit Namen
}) {
  const [tab, setTab] = useState<Tab>("zones");
  return (
    <>
      <PageHead title="Netzwerkdesign">
        {isCustomer ? (
          <>
            Ihre logischen Zonen, die Zonenmatrix mit den Regeln Ihrer Zonen (Zonen anderer Kunden als „anderer
            Kunde“) und Ihr Zonenplan mit den geteilten Elementen, an denen Ihre Zonen hängen – exportierbar als draw.io,
            SVG und PDF.
          </>
        ) : (
          <>
            Logische Zonen von „{tenantLabel}“ – in Automato gepflegt, die Netze leitet Automato aus Selektoren ab (wie
            beim Kunden). Die Zonenmatrix zeigt je Paar Quell- und Zielzone die Regeln und die Absicht, der Zonenplan, an
            welchen Firewalls und ACI-VRFs die Zonen hängen – exportierbar als draw.io, SVG und PDF, mit Versionen zum
            Vergleichen. Rein lesend gegenüber den Geräten.
          </>
        )}
      </PageHead>
      {tenantId === null ? (
        <Note>Bitte oben einen Mandanten wählen.</Note>
      ) : (
        <>
          <div className="tabbar sticky">
            {(
              [
                ["zones", "Zonen"],
                ["matrix", "Zonenmatrix"],
                ["plan", "Zonenplan"],
                ["versions", "Versionen"],
              ] as const
            )
              .filter(([k]) => !isCustomer || k !== "versions")
              .map(([k, label]) => (
              <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
                {label}
              </button>
            ))}
          </div>
          {tab === "zones" && <ZonesTab tenantId={tenantId} canEdit={canEdit} isCustomer={isCustomer} />}
          {tab === "matrix" && <MatrixTab tenantId={tenantId} canEdit={canEdit} />}
          {tab === "plan" && <PlanTab tenantId={tenantId} isCustomer={isCustomer} />}
          {tab === "versions" && <VersionsTab tenantId={tenantId} canEdit={canEdit} isAdmin={isAdmin} />}
        </>
      )}
    </>
  );
}

function Swatch({ color }: { color: string | null }) {
  return <span className="zone-swatch" style={{ background: color ?? "var(--surface-3)" }} />;
}

function selectorText(s: ZoneSelector, systems: System[]): string {
  const where = s.source_system_id ? ` (${systems.find((x) => x.id === s.source_system_id)?.name ?? "?"})` : "";
  return `${s.exclude ? "ohne " : ""}${ZONE_SELECTOR_LABEL[s.kind] ?? s.kind} ${s.value}${where}`;
}

// -- Zonen ---------------------------------------------------------------------------------

function ZonesTab({ tenantId, canEdit, isCustomer }: { tenantId: number; canEdit: boolean; isCustomer: boolean }) {
  const [rows, setRows] = useState<LogicalZone[] | null>(null);
  const [systems, setSystems] = useState<System[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [exits, setExits] = useState<TopoExit[]>([]);
  const [editing, setEditing] = useState<LogicalZone | "new" | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [err, setErr] = useState<unknown>(null);

  const load = useCallback(() => {
    setErr(null);
    fetchZones(tenantId).then(setRows).catch(setErr);
  }, [tenantId]);
  useEffect(() => {
    setRows(null);
    load();
    if (isCustomer) return; // Kunden-Benutzer: keine Selektoren, kein Editor
    fetchSystems(tenantId).then(setSystems).catch(() => setSystems([]));
    fetchCustomers(tenantId).then(setCustomers).catch(() => setCustomers([]));
    fetchExits(tenantId).then(setExits).catch(() => setExits([]));
  }, [tenantId, load, isCustomer]);

  const remove = async (z: LogicalZone) => {
    if (!window.confirm(`Zone „${z.name}“ löschen? Ihre Absicht in der Zonenmatrix fällt mit weg.`)) return;
    try {
      await deleteZone(z.id);
      load();
    } catch (e) {
      setErr(e);
    }
  };

  return (
    <>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      <div className="card">
        <div className="card-head">
          <h2>Zonen</h2>
          <span className="faint">
            Eine IP gehört zur Zone mit dem längsten Präfix; z. B. Internet = Anbindung „Internet“ ohne die privaten Netze
          </span>
          {canEdit && (
            <button className="btn btn-primary btn-sm" style={{ marginLeft: "auto" }} onClick={() => setEditing("new")}>
              + Zone
            </button>
          )}
        </div>
        <div className="card-body scroll">
          {rows == null ? (
            <Loading />
          ) : rows.length === 0 ? (
            <Empty title="Noch keine Zonen">
              Zonen anlegen: z. B. „Server“ über die Interfaces bzw. Zonen der Firewall, „Kunde A“ über den ACI-Tenant,
              „Internet“ über die Anbindung.
            </Empty>
          ) : (
            <table id="zone-list">
              <thead>
                <tr>
                  <th>Zone</th>
                  <th>Art</th>
                  <th>Kunde</th>
                  {!isCustomer && <th>Selektoren</th>}
                  <th className="num">Netze</th>
                  <th className="num">EPGs</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((z) => (
                  <Fragment key={z.id}>
                    <tr
                      className={z.own === false ? "" : "clickable"}
                      onClick={() => z.own !== false && setOpen(open === z.id ? null : z.id)}
                    >
                      <td>
                        <Swatch color={z.color} /> <b>{z.name}</b> {z.code && <span className="mono faint">{z.code}</span>}
                        {z.description && <div className="faint">{z.description}</div>}
                      </td>
                      <td>{ZONE_KIND[z.kind] ?? z.kind}</td>
                      <td>{z.customer ?? <span className="faint">geteilt</span>}</td>
                      {!isCustomer && (
                        <td>
                          <div className="objs">
                            {z.selectors.map((s) => (
                              <span key={s.id} className={`chip ${s.exclude ? "warn" : ""}`}>
                                {selectorText(s, systems)}
                              </span>
                            ))}
                          </div>
                        </td>
                      )}
                      <td className="num">{z.own === false ? <Dash /> : z.nets}</td>
                      <td className="num">{z.own === false ? <Dash /> : z.epgs}</td>
                      <td className="nowrap" onClick={(e) => e.stopPropagation()}>
                        {canEdit && (
                          <>
                            <button className="btn btn-sm" onClick={() => setEditing(z)}>
                              Bearbeiten
                            </button>{" "}
                            <button className="btn btn-sm btn-ghost" onClick={() => remove(z)}>
                              Löschen
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                    {open === z.id && (
                      <tr className="subrow">
                        <td colSpan={isCustomer ? 6 : 7}>
                          <ZoneNets zoneId={z.id} />
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
      {editing && (
        <ZoneEditor
          tenantId={tenantId}
          systems={systems}
          customers={customers}
          exits={exits}
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

function ZoneNets({ zoneId }: { zoneId: number }) {
  const [data, setData] = useState<{ total: number; items: { network: string; via: string }[] } | null>(null);
  useEffect(() => {
    let current = true;
    fetchZoneNets(zoneId)
      .then((r) => current && setData(r))
      .catch(() => current && setData({ total: 0, items: [] }));
    return () => {
      current = false;
    };
  }, [zoneId]);
  if (data == null) return <Loading />;
  if (data.items.length === 0) return <span className="faint">keine Netze</span>;
  return (
    <table className="compact zone-nets">
      <tbody>
        {data.items.map((n) => (
          <tr key={n.network}>
            <td className="mono">
              <a href={`#regeln?q=${encodeURIComponent(n.network)}`}>{n.network}</a>
            </td>
            <td className="faint">{n.via}</td>
          </tr>
        ))}
        {data.total > data.items.length && (
          <tr>
            <td colSpan={2} className="faint">
              … {data.total - data.items.length} weitere
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

const EMPTY_SELECTOR: ZoneSelector = { kind: "prefix", value: "", source_system_id: null, exclude: false };

function ZoneEditor({
  tenantId,
  systems,
  customers,
  exits,
  initial,
  onClose,
  onSaved,
}: {
  tenantId: number;
  systems: System[];
  customers: Customer[];
  exits: TopoExit[];
  initial: LogicalZone | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [code, setCode] = useState(initial?.code ?? "");
  const [kind, setKind] = useState<ZoneKind>(initial?.kind ?? "internal");
  const [color, setColor] = useState(initial?.color ?? "#3b82c4");
  const [customerId, setCustomerId] = useState<number | null>(initial?.customer_id ?? null);
  const [position, setPosition] = useState(initial?.position ?? 0);
  const [description, setDescription] = useState(initial?.description ?? "");
  const [selectors, setSelectors] = useState<ZoneSelector[]>(initial?.selectors.map((s) => ({ ...s })) ?? [{ ...EMPTY_SELECTOR }]);
  const [preview, setPreview] = useState<ZonePreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const firewalls = systems.filter((s) => s.platform === "fortigate");
  const valid = selectors.filter((s) => s.value.trim());

  useEffect(() => {
    const t = window.setTimeout(() => {
      previewZone(tenantId, valid, initial?.id)
        .then((p) => {
          setPreview(p);
          setErr(null);
        })
        .catch(setErr);
    }, 400);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, JSON.stringify(valid)]);

  const set = (i: number, patch: Partial<ZoneSelector>) => setSelectors((xs) => xs.map((s, j) => (j === i ? { ...s, ...patch } : s)));

  const save = async () => {
    setBusy(true);
    setErr(null);
    const body: ZoneIn = {
      tenant_id: tenantId,
      name: name.trim(),
      code: code.trim() || null,
      kind,
      color,
      customer_id: customerId,
      position,
      description: description.trim() || null,
      selectors: valid.map((s) => ({ ...s, value: s.value.trim(), source_system_id: FW_KINDS.includes(s.kind) ? s.source_system_id : null })),
    };
    try {
      if (initial) await updateZone(initial.id, body);
      else await createZone(body);
      onSaved();
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  };

  return (
    <Modal
      wide
      title={initial ? `Zone „${initial.name}“` : "Neue Zone"}
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
      <div className="zone-form">
        <div className="field">
          <label htmlFor="zo-name">Name</label>
          <input id="zo-name" className="input" value={name} maxLength={200} placeholder="z. B. DMZ" onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="zo-code">Kurzname (optional)</label>
          <input id="zo-code" className="input mono" value={code} maxLength={50} placeholder="z. B. DMZ1" onChange={(e) => setCode(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="zo-kind">Art</label>
          <select id="zo-kind" className="sel" value={kind} onChange={(e) => setKind(e.target.value as ZoneKind)}>
            {(Object.keys(ZONE_KIND) as ZoneKind[]).map((k) => (
              <option key={k} value={k}>
                {ZONE_KIND[k]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="zo-customer">Kunde</label>
          <select
            id="zo-customer"
            className="sel"
            value={customerId ?? ""}
            onChange={(e) => setCustomerId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">keiner (geteilt bzw. Infrastruktur)</option>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="zo-color">Farbe</label>
          <input id="zo-color" type="color" className="input" value={color} onChange={(e) => setColor(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="zo-pos">Reihenfolge</label>
          <input id="zo-pos" type="number" min={0} className="input" value={position} onChange={(e) => setPosition(Number(e.target.value) || 0)} />
          <div className="hint">0 = ans Ende</div>
        </div>
      </div>
      <div className="field">
        <label htmlFor="zo-desc">Beschreibung (optional)</label>
        <input id="zo-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>

      <h3 style={{ marginTop: 12 }}>Selektoren</h3>
      <table className="compact form-table" id="zone-selectors">
        <colgroup>
          <col style={{ width: "24%" }} />
          <col />
          <col style={{ width: "22%" }} />
          <col style={{ width: 64 }} />
          <col style={{ width: 92 }} />
        </colgroup>
        <thead>
          <tr>
            <th>Art</th>
            <th>Wert</th>
            <th>Quellsystem</th>
            <th title="ausschliessen">aus­schl.</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {selectors.map((s, i) => (
            <tr key={i}>
              <td>
                <select className="sel" aria-label="Art" value={s.kind} onChange={(e) => set(i, { kind: e.target.value as ZoneSelectorKind })}>
                  {(Object.keys(ZONE_SELECTOR_LABEL) as ZoneSelectorKind[]).map((k) => (
                    <option key={k} value={k}>
                      {ZONE_SELECTOR_LABEL[k]}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                {s.kind === "exit" && exits.length > 0 ? (
                  <select className="sel" aria-label="Wert" value={s.value} onChange={(e) => set(i, { value: e.target.value })}>
                    <option value="">– Anbindung wählen –</option>
                    {exits.map((x) => (
                      <option key={x.id} value={x.name}>
                        {x.name} {x.prefixes?.length ? `(${x.prefixes.join(", ")})` : "(ohne Netze)"}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    className="input mono"
                    aria-label="Wert"
                    value={s.value}
                    placeholder={PLACEHOLDER[s.kind] ?? ""}
                    onChange={(e) => set(i, { value: e.target.value })}
                  />
                )}
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
        <div id="zone-preview">
          <table className="compact form-table">
            <colgroup>
              <col style={{ width: "24%" }} />
              <col style={{ width: 70 }} />
              <col />
            </colgroup>
            <tbody>
              <tr>
                <td>Netze</td>
                <td className="num">{preview.counts.net}</td>
                <td className="faint">
                  {preview.samples.net.map((n) => `${n.network} (${n.via})`).join(", ")}
                  {preview.counts.net > preview.samples.net.length ? " …" : ""}
                </td>
              </tr>
              <tr>
                <td>EPGs</td>
                <td className="num">{preview.counts.epg}</td>
                <td className="faint">
                  {preview.samples.epg.join(", ")}
                  {preview.counts.epg > preview.samples.epg.length ? " …" : ""}
                </td>
              </tr>
            </tbody>
          </table>
          {preview.overlaps.length > 0 && (
            <Note warn>
              Gleiches Netz schon in einer anderen Zone: {preview.overlaps.map((o) => `${o.network} (${o.zone})`).join(", ")}
              {preview.overlaps_total > preview.overlaps.length ? " …" : ""} – das wird ein Befund „Netz in mehreren Zonen“.
            </Note>
          )}
        </div>
      )}
    </Modal>
  );
}

// -- Zonenmatrix ---------------------------------------------------------------------------

function cellLabel(c: ZoneCell, zones: Map<number, ZoneSummary>): string {
  if (c === "any") return "any";
  if (c === "none") return "ohne Zone";
  if (c === "other") return "anderer Kunde";
  return zones.get(c)?.name ?? `#${c}`;
}

function MatrixTab({ tenantId, canEdit }: { tenantId: number; canEdit: boolean }) {
  const [m, setM] = useState<ZoneMatrix | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [sel, setSel] = useState<{ src: ZoneCell; dst: ZoneCell } | null>(null);
  const load = useCallback(() => {
    fetchZoneMatrix(tenantId).then(setM).catch(setErr);
  }, [tenantId]);
  useEffect(() => {
    setM(null);
    setSel(null);
    load();
  }, [load]);
  const zones = useMemo(() => new Map((m?.zones ?? []).map((z) => [z.id, z])), [m]);
  const cells = useMemo(() => new Map((m?.cells ?? []).map((c) => [`${c.src}>${c.dst}`, c])), [m]);

  if (err) return <ErrorBox error={err} onClose={() => setErr(null)} />;
  if (m == null) return <Loading />;
  if (m.zones.length === 0) return <Note>Noch keine Zonen – zuerst im Reiter „Zonen“ anlegen.</Note>;
  const s = m.summary;
  return (
    <>
      <div className="card">
        <div className="card-head">
          <h2>Zonenmatrix</h2>
          <span className="faint" id="zone-matrix-summary">
            {s.assigned} von {s.rules} Regeln zugeordnet
            {s.without ? ` · ${s.without} ohne IP-Bereich (z. B. FQDN)` : ""} ·{" "}
            <span className={s.violations ? "crit" : ""}>
              {s.violations} {s.violations === 1 ? "Regel" : "Regeln"} gegen die Absicht
            </span>
          </span>
        </div>
        <div className="card-body scroll">
          <table className="zone-matrix" id="zone-matrix">
            <thead>
              <tr>
                <th className="corner">Quelle ↓ · Ziel →</th>
                {m.axes.map((d) => (
                  <th key={String(d)} className="axis">
                    {typeof d === "number" && <Swatch color={zones.get(d)?.color ?? null} />} {cellLabel(d, zones)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {m.axes.map((src) => (
                <tr key={String(src)}>
                  <th className="axis">
                    {typeof src === "number" && <Swatch color={zones.get(src)?.color ?? null} />} {cellLabel(src, zones)}
                  </th>
                  {m.axes.map((dst) => {
                    const c = cells.get(`${src}>${dst}`);
                    const on = sel && sel.src === src && sel.dst === dst;
                    const intent = c?.intent ? INTENT[c.intent][0] : "";
                    return (
                      <td
                        key={String(dst)}
                        className={`zcell ${intent ? `i-${intent}` : ""} ${c?.violations ? "viol" : ""} ${on ? "on" : ""}`}
                        title={c?.note ?? undefined}
                        onClick={() => setSel({ src, dst })}
                      >
                        {c && (c.allow || c.deny) ? (
                          <>
                            {c.allow > 0 && <span className="z-allow">✓ {c.allow}</span>}
                            {c.deny > 0 && <span className="z-deny">✕ {c.deny}</span>}
                          </>
                        ) : (
                          <span className="faint">·</span>
                        )}
                        {c?.violations ? <div className="z-viol">{c.violations} gegen Absicht</div> : null}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="faint zone-legend">
            ✓ erlaubende, ✕ sperrende Regeln · Hintergrund = Absicht:{" "}
            {(Object.keys(INTENT) as ZoneIntent[]).map((k) => (
              <span key={k} className={`chip ${INTENT[k][0]}`}>
                {INTENT[k][1]}
              </span>
            ))}{" "}
            · „any“ und „ohne Zone“ zählen nicht als Verstoss
          </div>
        </div>
      </div>
      {sel && (
        <CellDetail
          key={`${sel.src}>${sel.dst}`}
          tenantId={tenantId}
          src={sel.src}
          dst={sel.dst}
          zones={zones}
          cell={cells.get(`${sel.src}>${sel.dst}`) ?? null}
          canEdit={canEdit}
          onChanged={load}
        />
      )}
    </>
  );
}

function CellDetail({
  tenantId,
  src,
  dst,
  zones,
  cell,
  canEdit,
  onChanged,
}: {
  tenantId: number;
  src: ZoneCell;
  dst: ZoneCell;
  zones: Map<number, ZoneSummary>;
  cell: ZoneMatrix["cells"][number] | null;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const [rules, setRules] = useState<{ total: number; violations: number; items: ZoneCellRule[] } | null>(null);
  const [intent, setIntent] = useState<ZoneIntent | "">(cell?.intent ?? "");
  const [note, setNote] = useState(cell?.note ?? "");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let current = true;
    fetchZoneCellRules(tenantId, src, dst)
      .then((r) => current && setRules(r))
      .catch((e) => current && setErr(e));
    return () => {
      current = false;
    };
  }, [tenantId, src, dst]);
  const zonePair = typeof src === "number" && typeof dst === "number";
  const save = async () => {
    if (!zonePair) return;
    setBusy(true);
    try {
      await setZonePolicy(tenantId, src, dst, intent || null, note.trim() || null);
      onChanged();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card" id="zone-cell">
      <div className="card-head">
        <h2>
          {cellLabel(src, zones)} → {cellLabel(dst, zones)}
        </h2>
        {cell?.intent && <span className={`chip ${INTENT[cell.intent][0]}`}>Absicht: {INTENT[cell.intent][1]}</span>}
      </div>
      <div className="card-body">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        {zonePair && canEdit && (
          <div className="zone-intent">
            <select className="sel" aria-label="Absicht" value={intent} onChange={(e) => setIntent(e.target.value as ZoneIntent | "")}>
              <option value="">keine Absicht</option>
              {(Object.keys(INTENT) as ZoneIntent[]).map((k) => (
                <option key={k} value={k}>
                  {INTENT[k][1]}
                </option>
              ))}
            </select>
            <input className="input" aria-label="Notiz" placeholder="Notiz, z. B. nur über Reverse Proxy" value={note} onChange={(e) => setNote(e.target.value)} />
            <button className="btn btn-sm btn-primary" onClick={save} disabled={busy}>
              Absicht speichern
            </button>
          </div>
        )}
        {rules == null ? (
          <Loading />
        ) : rules.items.length === 0 ? (
          <span className="faint">keine Regeln</span>
        ) : (
          <table className="compact" id="zone-cell-rules">
            <thead>
              <tr>
                <th>Regel</th>
                <th>Aktion</th>
                <th>Gerät</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rules.items.map((r) => (
                <tr key={r.id}>
                  <td>
                    <a href={`#regeln?rule=${r.id}`}>{r.name || `#${r.external_id}`}</a>
                    {r.tenant && <span className="faint"> · {r.tenant}</span>}
                  </td>
                  <td>
                    <span className={`chip ${r.action === "allow" ? "accept" : "deny"}`}>{r.action === "allow" ? "erlaubt" : "gesperrt"}</span>
                  </td>
                  <td>
                    {r.source ?? "?"}
                    {r.vdom && r.vdom !== "root" ? ` · VDOM ${r.vdom}` : ""}
                  </td>
                  <td>{r.violation && <span className="chip crit">gegen Absicht</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {rules && rules.total > rules.items.length && <div className="faint">die ersten {rules.items.length} von {rules.total}</div>}
      </div>
    </div>
  );
}

// -- Zonenplan -----------------------------------------------------------------------------

function PlanTab({ tenantId, isCustomer = false }: { tenantId: number; isCustomer?: boolean }) {
  const [plan, setPlan] = useState<ZonePlan | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    setPlan(null);
    fetchZonePlan(tenantId).then(setPlan).catch(setErr);
  }, [tenantId]);
  if (err) return <ErrorBox error={err} onClose={() => setErr(null)} />;
  if (plan == null) return <Loading />;
  if (plan.zones.length === 0) return <Note>Noch keine Zonen – zuerst im Reiter „Zonen“ anlegen.</Note>;
  const nodes = new Map(plan.nodes.map((n) => [n.id, n]));
  const s = plan.summary;
  return (
    <>
      <div className="card">
        <div className="card-head">
          <h2>Zonenplan</h2>
          <span className="faint" id="zone-plan-summary">
            {s.zones} Zonen an {s.nodes} Knoten · {s.links} Übergänge
            {s.without_node ? ` · ${s.without_node} Zonen ohne Knoten` : ""}
            {s.unzoned ? ` · ${s.unzoned} Netze ohne Zone` : ""}
          </span>
          <span style={{ marginLeft: "auto" }}>
            <ExportButtons tenantId={tenantId} />
          </span>
        </div>
        <div className="card-body scroll">{plan.layout && <PlanDrawing layout={plan.layout} />}</div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Zonen an den Knoten</h2>
          <span className="faint">am Interface, im Fabric, über die Anbindung bzw. über eine Route zu einem Router ohne gelesenes Gerät</span>
        </div>
        <div className="card-body scroll">
          <table className="compact" id="zone-attachments">
            <thead>
              <tr>
                <th>Zone</th>
                <th>Knoten</th>
                <th>über</th>
                <th>Interfaces</th>
                <th>Netze</th>
              </tr>
            </thead>
            <tbody>
              {plan.zones.map((z) => {
                const att = plan.attachments.filter((a) => a.zone === z.id);
                if (att.length === 0)
                  return (
                    <tr key={z.id}>
                      <td>
                        <Swatch color={z.color} /> <b>{z.name}</b>
                      </td>
                      <td colSpan={4} className="faint">
                        an keinem gelesenen Knoten{z.nets === 0 ? " (keine Netze)" : ""}
                      </td>
                    </tr>
                  );
                return att.map((a, i) => (
                  <tr key={`${z.id}-${a.node}`}>
                    <td>
                      {i === 0 && (
                        <>
                          <Swatch color={z.color} /> <b>{z.name}</b>
                        </>
                      )}
                    </td>
                    <td>{nodes.get(a.node)?.label ?? a.node}</td>
                    <td>
                      {a.via.map((v) => (
                        <span key={v} className={`chip ${v === "route" ? "info" : "na"}`}>
                          {VIA[v] ?? v}
                        </span>
                      ))}
                      {a.default && <span className="chip warn">Default-Route</span>}
                    </td>
                    <td className="mono">{a.interfaces.join(", ") || <Dash />}</td>
                    <td className="mono">
                      {a.networks.join(", ")}
                      {a.count > a.networks.length ? ` … (${a.count})` : ""}
                      {a.routes.length > 0 && (
                        <div className="faint">
                          {a.routes.map((r) => `${r.prefix} → ${r.names[0] ?? r.next_hop ?? r.interface ?? "?"}`).join(", ")}
                        </div>
                      )}
                    </td>
                  </tr>
                ));
              })}
            </tbody>
          </table>
        </div>
      </div>

      {!isCustomer && (
      <div className="card" id="zone-unzoned">
        <div className="card-head">
          <h2>Netze ohne Zone</h2>
          <span className="faint">an Interfaces, L3Outs und im Fabric gelesen, keiner Zone zugeordnet</span>
        </div>
        <div className="card-body scroll">
          {plan.unzoned.total === 0 ? (
            <span className="faint">alle gelesenen Netze gehören zu einer Zone</span>
          ) : (
            <table className="compact">
              <tbody>
                {plan.unzoned.items.map((u) => (
                  <tr key={u.network}>
                    <td className="mono">{u.network}</td>
                    <td>{u.node}</td>
                    <td className="faint">{u.where}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {plan.unzoned.total > plan.unzoned.items.length && (
            <div className="faint">die ersten {plan.unzoned.items.length} von {plan.unzoned.total}</div>
          )}
        </div>
      </div>
      )}
    </>
  );
}

// Zeichnung aus dem Layout des Servers (0.52.0, `plan_export.layout`) – dieselbe wie im Export:
// Zonen links (intern, Management, Transfer) und rechts (DMZ, extern, geteilt), die Knoten in der
// Mitte; Linien = Zone am Knoten (gestrichelt: nur über eine Route), beschriftet mit den Interfaces.
export function PlanDrawing({ layout, id = "zone-plan" }: { layout: PlanLayout; id?: string }) {
  return (
    <svg className="zone-plan" id={id} viewBox={`0 0 ${layout.width} ${layout.height}`} width="100%" role="img" aria-label="Zonenplan">
      {layout.edges.map((e) => (
        <g key={`${e.zone}-${e.node}`} className="zp-edge">
          <line x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2} strokeDasharray={e.dashed ? "5 4" : undefined} />
          {e.label && (
            <text x={(e.x1 + e.x2) / 2} y={(e.y1 + e.y2) / 2 - 4} textAnchor="middle">
              {e.label}
            </text>
          )}
        </g>
      ))}
      {layout.zones.map((z) => (
        <g key={z.id} className="zp-zone">
          <rect x={z.x} y={z.y} width={z.w} height={z.h} rx={8} style={{ fill: z.color ?? "var(--surface-3)" }} />
          <text x={z.x + 12} y={z.y + 18} className="zp-title">
            {z.title}
          </text>
          <text x={z.x + 12} y={z.y + 33} className="zp-sub">
            {z.sub}
          </text>
        </g>
      ))}
      {layout.nodes.map((n) => (
        <g key={n.id} className="zp-node">
          <rect x={n.x} y={n.y} width={n.w} height={n.h} rx={6} />
          <text x={n.x + n.w / 2} y={n.y + 18} textAnchor="middle" className="zp-title">
            {n.title}
          </text>
          <text x={n.x + n.w / 2} y={n.y + 33} textAnchor="middle" className="zp-sub">
            {n.sub}
          </text>
        </g>
      ))}
    </svg>
  );
}

// Export-Knöpfe: draw.io (bearbeitbar), SVG, PDF (mit Matrix und Tabellen) – aktueller Stand bzw. Version
function ExportButtons({ tenantId, versionId }: { tenantId: number; versionId?: number }) {
  const [busy, setBusy] = useState<PlanFormat | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const run = async (format: PlanFormat) => {
    setBusy(format);
    setErr(null);
    try {
      const { blob, filename } = await exportZonePlan(tenantId, format, versionId);
      download(filename, blob);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };
  return (
    <span className="plan-export">
      <ErrorBox error={err} onClose={() => setErr(null)} />
      <span className="faint">Export:</span>
      {(
        [
          ["drawio", "draw.io"],
          ["svg", "SVG"],
          ["pdf", "PDF"],
        ] as const
      ).map(([f, label]) => (
        <button key={f} className="btn btn-sm" disabled={busy !== null} onClick={() => run(f)}>
          {busy === f ? "…" : label}
        </button>
      ))}
    </span>
  );
}

// -- Versionen -----------------------------------------------------------------------------

function VersionsTab({ tenantId, canEdit, isAdmin }: { tenantId: number; canEdit: boolean; isAdmin: boolean }) {
  const [list, setList] = useState<{ current_fingerprint: string; items: PlanVersion[] } | null>(null);
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const load = useCallback(() => {
    fetchPlanVersions(tenantId).then(setList).catch(setErr);
  }, [tenantId]);
  useEffect(() => {
    setList(null);
    setOpen(null);
    load();
  }, [load]);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const v = await savePlanVersion(tenantId, label.trim(), note.trim() || null);
      setLabel("");
      setNote("");
      setOpen(v.id);
      load();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const remove = async (v: PlanVersion) => {
    if (!window.confirm(`Version ${v.number} „${v.label}“ löschen? Der gespeicherte Stand fällt weg, die Nummer bleibt vergeben.`)) return;
    try {
      await deletePlanVersion(v.id);
      if (open === v.id) setOpen(null);
      load();
    } catch (e) {
      setErr(e);
    }
  };
  if (list == null) return err ? <ErrorBox error={err} /> : <Loading />;
  const latest = list.items[0];
  const unchanged = list.items.find((v) => v.fingerprint === list.current_fingerprint);
  return (
    <>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      <div className="card">
        <div className="card-head">
          <h2>Versionen des Zonenplans</h2>
          <span className="faint" id="plan-version-state">
            {latest == null
              ? "noch keine Version gespeichert"
              : unchanged
                ? `aktueller Stand = Version ${unchanged.number}`
                : `geändert seit Version ${latest.number}`}
          </span>
        </div>
        <div className="card-body">
          {canEdit && (
            <div className="zone-intent" id="plan-version-save">
              <input className="input" aria-label="Bezeichnung" placeholder="Bezeichnung, z. B. Stand nach Umbau DMZ" value={label} maxLength={200} onChange={(e) => setLabel(e.target.value)} />
              <input className="input" aria-label="Notiz" placeholder="Notiz (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
              <button className="btn btn-sm btn-primary" disabled={busy || !label.trim()} onClick={save}>
                {busy ? "Speichere…" : "Version speichern"}
              </button>
            </div>
          )}
          {list.items.length === 0 ? (
            <Empty title="Noch keine Versionen">
              Eine Version hält Zonen, Plan, Matrix und Absichten so fest, wie sie jetzt abgeleitet sind – zum Nachlesen,
              Vergleichen und Exportieren.
            </Empty>
          ) : (
            <table className="compact" id="plan-versions">
              <thead>
                <tr>
                  <th className="num">Nr.</th>
                  <th>Bezeichnung</th>
                  <th>Gespeichert</th>
                  <th className="num">Zonen</th>
                  <th className="num">Knoten</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.items.map((v) => (
                  <Fragment key={v.id}>
                    <tr className="clickable" onClick={() => setOpen(open === v.id ? null : v.id)}>
                      <td className="num">{v.number}</td>
                      <td>
                        <b>{v.label}</b> {v.fingerprint === list.current_fingerprint && <span className="chip ok">= aktuell</span>}
                        {v.note && <div className="faint">{v.note}</div>}
                      </td>
                      <td className="faint nowrap">
                        {fmtTime(v.created_at)} · {v.created_by ?? "?"}
                      </td>
                      <td className="num">{v.summary.zones ?? 0}</td>
                      <td className="num">{v.summary.nodes ?? 0}</td>
                      <td className="nowrap" onClick={(e) => e.stopPropagation()}>
                        {isAdmin && (
                          <button className="btn btn-sm btn-ghost" onClick={() => remove(v)}>
                            Löschen
                          </button>
                        )}
                      </td>
                    </tr>
                    {open === v.id && (
                      <tr className="subrow">
                        <td colSpan={6}>
                          <VersionDetail tenantId={tenantId} version={v} others={list.items.filter((x) => x.id !== v.id)} />
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
    </>
  );
}

function VersionDetail({ tenantId, version, others }: { tenantId: number; version: PlanVersion; others: PlanVersion[] }) {
  const [full, setFull] = useState<PlanVersionFull | null>(null);
  const [to, setTo] = useState<number | "">("");
  const [diff, setDiff] = useState<PlanDiff | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    let current = true;
    fetchPlanVersion(version.id)
      .then((v) => current && setFull(v))
      .catch((e) => current && setErr(e));
    return () => {
      current = false;
    };
  }, [version.id]);
  useEffect(() => {
    let current = true;
    setDiff(null);
    fetchPlanVersionDiff(version.id, to === "" ? undefined : to)
      .then((d) => current && setDiff(d))
      .catch((e) => current && setErr(e));
    return () => {
      current = false;
    };
  }, [version.id, to]);
  return (
    <div className="plan-version">
      <ErrorBox error={err} onClose={() => setErr(null)} />
      <div className="plan-version-head">
        <span>
          Vergleich von Version {version.number} mit{" "}
          <select className="sel" aria-label="Vergleich mit" value={to} onChange={(e) => setTo(e.target.value ? Number(e.target.value) : "")}>
            <option value="">dem aktuellen Stand</option>
            {others.map((o) => (
              <option key={o.id} value={o.id}>
                Version {o.number} – {o.label}
              </option>
            ))}
          </select>
        </span>
        <ExportButtons tenantId={tenantId} versionId={version.id} />
      </div>
      {diff == null ? <Loading /> : <DiffView diff={diff} />}
      {full?.data.plan.layout && (
        <div className="scroll">
          <PlanDrawing layout={full.data.plan.layout} id={`plan-version-${version.number}`} />
        </div>
      )}
    </div>
  );
}

function DiffView({ diff }: { diff: PlanDiff }) {
  const rows: [string, string, string][] = [];
  for (const z of diff.zones.added) rows.push(["Zone", z, "neu"]);
  for (const z of diff.zones.removed) rows.push(["Zone", z, "entfernt"]);
  for (const c of diff.zones.changed) {
    const what: string[] = [];
    if (c.old.kind !== c.new.kind) what.push(`Art ${ZONE_KIND[c.old.kind as ZoneKind] ?? c.old.kind} → ${ZONE_KIND[c.new.kind as ZoneKind] ?? c.new.kind}`);
    if (c.old.customer !== c.new.customer) what.push(`Kunde ${c.old.customer ?? "–"} → ${c.new.customer ?? "–"}`);
    if (c.old.color !== c.new.color) what.push("Farbe");
    if (JSON.stringify(c.old.selectors) !== JSON.stringify(c.new.selectors)) what.push("Selektoren");
    rows.push(["Zone", c.key, `geändert: ${what.join(", ")}`]);
  }
  for (const k of diff.intents.added) rows.push(["Absicht", k, "neu"]);
  for (const k of diff.intents.removed) rows.push(["Absicht", k, "entfernt"]);
  for (const c of diff.intents.changed) rows.push(["Absicht", c.key, `${INTENT[c.old.intent]?.[1] ?? c.old.intent} → ${INTENT[c.new.intent]?.[1] ?? c.new.intent}`]);
  for (const k of diff.attachments.added) rows.push(["Zone am Knoten", k, "neu"]);
  for (const k of diff.attachments.removed) rows.push(["Zone am Knoten", k, "entfernt"]);
  for (const c of diff.attachments.changed) rows.push(["Zone am Knoten", c.key, `Interfaces ${c.old.interfaces.join(", ") || "–"} → ${c.new.interfaces.join(", ") || "–"}`]);
  for (const n of diff.unzoned.added) rows.push(["Netz ohne Zone", n, "neu"]);
  for (const n of diff.unzoned.removed) rows.push(["Netz ohne Zone", n, "jetzt in einer Zone bzw. weg"]);
  return (
    <div id="plan-diff">
      {diff.same ? (
        <div className="faint">Design unverändert (Zonen, Absichten, Zonen an den Knoten, Netze ohne Zone).</div>
      ) : (
        <table className="compact">
          <tbody>
            {rows.map(([what, key, change], i) => (
              <tr key={i}>
                <td className="faint nowrap">{what}</td>
                <td>{key}</td>
                <td>{change}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {diff.matrix.length > 0 && (
        <details className="plan-diff-matrix">
          <summary>{diff.matrix.length} Zellen der Matrix mit anderen Regel-Zahlen</summary>
          <table className="compact">
            <thead>
              <tr>
                <th>Zelle</th>
                <th>vorher (erlaubend / sperrend / gegen Absicht)</th>
                <th>nachher</th>
              </tr>
            </thead>
            <tbody>
              {diff.matrix.map((c) => (
                <tr key={c.cell}>
                  <td>{c.cell}</td>
                  <td className="mono">{c.old.join(" / ")}</td>
                  <td className="mono">{c.new.join(" / ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}
