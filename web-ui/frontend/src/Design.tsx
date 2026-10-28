// Netzwerkdesign (0.51.0, ADR 0024): logische Zonen in Automato pflegen (Selektoren wie beim
// Kunden, dazu „Anbindung“), die Zonenmatrix (Regeln je Paar Quell-/Zielzone mit Absicht und
// Verstössen) und der Zonenplan (Zonen an Firewalls/VDOMs und ACI-VRFs, Übergänge, Netze ohne
// Zone). Lesen = viewer, Zonen und Absicht ändern = operator. Seit 0.52.0 Export (draw.io, SVG, PDF)
// und Versionen des Plans (speichern = operator, löschen = admin). Seit 0.54.0 Vorschläge für Zonen aus
// dem Gelesenen (übernehmen bzw. ausblenden = operator). Seit 0.56.0 das Zonenmodell nach Ebenen: Zonen mit
// Knoten (Firewall/VDOM, ACI-VRF), Übergänge zwischen den Zonen, Farben aus Grundschema bzw. Vorlage, Plan je
// Kunde (andere Kunden je als eine Zone).
import { CSSProperties, Fragment, PointerEvent as ReactPointerEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  acceptZoneSuggestions,
  AppdocArea,
  AppdocCatalog,
  applySchemaIntents,
  assignZoneNodes,
  createZone,
  Customer,
  deletePlanVersion,
  dismissZoneSuggestions,
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
  fetchZoneSuggestions,
  getCustomerFilter,
  LogicalZone,
  PlanBox,
  PlanDiff,
  PlanDirection,
  PlanEdge,
  PlanFilter,
  PlanFormat,
  PlanLayout,
  PlanTransition,
  PlanVersion,
  PlanVersionFull,
  PlanView,
  PlanZoneId,
  previewZone,
  assistZone,
  type ZoneAssistItem,
  applyPlanImport,
  PlanImportParts,
  PlanImportPreview,
  previewPlanImport,
  resetPlanArrangement,
  savePlanArrangement,
  savePlanVersion,
  SchemaZone,
  setAppdocZones,
  fetchAppdocCatalog,
  setZonePolicy,
  SuggestionNode,
  System,
  TopoExit,
  updateZone,
  ZoneCell,
  ZoneCellRules,
  ZoneIn,
  ZoneIntent,
  ZoneKind,
  ZoneColorSource,
  ZoneMatrix,
  ZoneNetsData,
  ZonePlan,
  ZonePreview,
  ZoneSelector,
  ZoneSelectorKind,
  ZoneSchemaStatus,
  ZoneSuggestion,
  ZoneSuggestions,
  ZoneSuggestionSource,
  ZoneSummary,
} from "./api";
import { areaChoices, areaLabel } from "./zoneAreas";
import { SELECTOR_LABEL } from "./Customers";
import { Dash, download, Empty, ErrorBox, ExportLangSelect, fmtTime, Loading, Modal, Note, PageHead, SharedChip } from "./ui";
import { L } from "./i18n";

// Reihenfolge = Ebenen des Zonenmodells (0.56.0) von links nach rechts im Plan, danach die bisherigen Arten
export const ZONE_KIND: Record<ZoneKind, string> = {
  dc: L("Datacenter", "Data center"),
  edge: "Edge",
  transfer: "Transfer",
  core: "Core",
  distribution: "Distribution",
  access: "Access",
  external: L("Public / extern", "Public / external"),
  internal: L("intern", "internal"),
  dmz: "DMZ",
  management: "Management",
  shared: L("geteilt", "shared"),
};
const COLOR_SOURCE: Record<ZoneColorSource, string> = {
  schema: L("aus dem Grundschema", "from the base schema"),
  template: L("aus der Vorlage", "from the template"),
  zone: L("an der Zone gesetzt", "set on the zone"),
  kind: L("Farbe der Art", "color of the kind"),
};
const INTENT: Record<ZoneIntent, [string, string]> = {
  allow: ["ok", L("erlaubt", "allow")],
  limited: ["warn", L("eingeschränkt", "limited")],
  deny: ["crit", L("gesperrt", "deny")],
};
const ZONE_SELECTOR_LABEL: Record<ZoneSelectorKind, string> = {
  ...SELECTOR_LABEL,
  exit: L("Anbindung", "Exit"),
  // Ausgänge (0.105.0): über diese erreicht die Firewall bzw. das ACI die Zone
  via_fw_interface: L("Ausgang Interface", "Exit interface"),
  via_fw_zone: L("Ausgang Firewall-Zone", "Exit firewall zone"),
  via_l3out: L("Ausgang L3Out", "Exit L3Out"),
};
const PLACEHOLDER: Partial<Record<ZoneSelectorKind, string>> = {
  aci_tenant: "TE_ka",
  aci_epg: "TE_ka/AP_007/EPG_web",
  fw_zone: "dmz",
  fw_interface: "port5",
  prefix: "10.20.0.0/16",
  via_fw_interface: "port3",
  via_fw_zone: "transfer-core",
  via_l3out: "L3O_inet",
  exit: "Internet",
};
const FW_KINDS: ZoneSelectorKind[] = ["fw_vdom", "fw_zone", "fw_interface", "via_fw_interface", "via_fw_zone"];
const NODE_KIND: Record<string, string> = { firewall: "Firewall", vdom: "VDOM", vrf: "ACI-VRF" };

const SOURCE: Record<ZoneSuggestionSource, string> = {
  aci: L("ACI-Tenants", "ACI tenants"),
  node: "Firewall",
  exit: L("Anbindung", "Exit"),
  ipam: "IPAM",
  schema: L("Grundschema", "Base schema"),
};

type Tab = "zones" | "appdoc" | "suggestions" | "matrix" | "plan" | "versions";
const TAB_KEYS: Tab[] = ["zones", "appdoc", "suggestions", "matrix", "plan", "versions"];

export default function Design({
  tenantId,
  tenantLabel,
  canEdit,
  isAdmin,
  isCustomer = false,
  params = {},
}: {
  tenantId: number | null;
  tenantLabel: string;
  canEdit: boolean;
  isAdmin: boolean;
  isCustomer?: boolean; // Kunden-Benutzer (0.53.0): eigene Zonen, geteilte nur mit Namen
  params?: Record<string, string>; // 0.102.0: `tab` (z. B. aus der Applikations-Doku)
}) {
  const [tab, setTab] = useState<Tab>(
    TAB_KEYS.includes(params.tab as Tab) && !(isCustomer && params.tab === "appdoc") ? (params.tab as Tab) : "zones",
  );
  return (
    <>
      <PageHead title={L("Netzwerkdesign", "Network design")}>
        {isCustomer
          ? L(
              <>
                Ihre logischen Zonen, die Zonenmatrix mit den Regeln Ihrer Zonen (Zonen anderer Kunden als „anderer
                Kunde“) und Ihr Zonenplan mit den geteilten Elementen, an denen Ihre Zonen hängen – exportierbar als draw.io,
                SVG und PDF.
              </>,
              <>
                Your logical zones, the zone matrix with the rules of your zones (zones of other customers as “other
                customer”) and your zone plan with the shared elements your zones are attached to – exportable as draw.io,
                SVG and PDF.
              </>,
            )
          : L(
              <>
                Logische Zonen von „{tenantLabel}“ – in Automato gepflegt, nach Ebenen: Datacenter (ACI-Tenants), Edge, Transfer,
                Core, Distribution, Access und Public. Eine Zone umfasst Knoten (Firewall bzw. VDOM, ACI-VRF) und Netze; zwischen
                den Zonen liegen Übergänge (L3Outs, Interfaces). Die Zonenmatrix zeigt je Paar Quell- und Zielzone die Regeln
                und die Absicht, der Zonenplan die Zonen mit ihren Übergängen – je Kunde, exportierbar als draw.io, SVG und PDF,
                mit Versionen zum Vergleichen. Rein lesend gegenüber den Geräten.
              </>,
              <>
                Logical zones of “{tenantLabel}” – maintained in Automato, by layer: data center (ACI tenants), edge, transfer,
                core, distribution, access and public. A zone comprises nodes (firewall or VDOM, ACI VRF) and networks; between
                the zones lie transitions (L3Outs, interfaces). The zone matrix shows the rules and the intent for each pair of
                source and destination zone, the zone plan the zones with their transitions – per customer, exportable as draw.io,
                SVG and PDF, with versions to compare. Strictly read-only towards the devices.
              </>,
            )}
      </PageHead>
      {tenantId === null ? (
        <Note>{L("Bitte oben einen Mandanten wählen.", "Please select a tenant at the top.")}</Note>
      ) : (
        <>
          <div className="tabbar sticky">
            {(
              [
                ["zones", L("Zonen", "Zones")],
                ["appdoc", L("Kategorien", "Categories")],
                ["suggestions", L("Vorschläge", "Suggestions")],
                ["matrix", L("Zonenmatrix", "Zone matrix")],
                ["plan", L("Zonenplan", "Zone plan")],
                ["versions", L("Versionen", "Versions")],
              ] as const
            )
              .filter(([k]) => !isCustomer || (k !== "versions" && k !== "suggestions" && k !== "appdoc"))
              .map(([k, label]) => (
              <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
                {label}
              </button>
            ))}
          </div>
          {tab === "zones" && <ZonesTab tenantId={tenantId} canEdit={canEdit} isCustomer={isCustomer} />}
          {tab === "appdoc" && !isCustomer && <AppdocTab tenantId={tenantId} canEdit={canEdit} />}
          {tab === "suggestions" && <SuggestionsTab tenantId={tenantId} canEdit={canEdit} />}
          {tab === "matrix" && <MatrixTab tenantId={tenantId} canEdit={canEdit} />}
          {tab === "plan" && <PlanTab tenantId={tenantId} isCustomer={isCustomer} canArrange={canEdit && !isCustomer} />}
          {tab === "versions" && <VersionsTab tenantId={tenantId} canEdit={canEdit} isAdmin={isAdmin} />}
        </>
      )}
    </>
  );
}

// -- Kategorien der Applikations-Doku ------------------------------------------------------------
// 0.102.0 (Anwender: „das Zuordnen gehört zu den Zonen ins Netzwerkdesign“): je Zone die Kategorie, aus der
// ihre Zugriffe kommen, und ihre Netze mit Ausschlüssen (Selektoren „Netz“; die übrigen Selektoren bleiben,
// gepflegt im Reiter Zonen). Die Doku benennt die Kategorien nach ihren Zonen („Zugriff aus Edge
// Internet“); später lässt sich daraus z. B. ein BGP-Filter je Zone ableiten.
// 0.105.0 (ADR 0035): je Zone der Zonentyp (Zone des Grundschemas, erkannt bzw. von Hand) und die Ausgänge (Interface,
// Firewall-Zone, L3Out); mit Richtungen im Namenskonzept entscheidet der Zonentyp statt der Kategorie der Zone.
type AppdocDraft = { area: AppdocArea | null; type: string | null; nets: string; excluded: string };
const EXIT_KINDS: ZoneSelectorKind[] = ["via_fw_interface", "via_fw_zone", "via_l3out"];
const splitNets = (text: string) =>
  text
    .split(/[\s,;]+/)
    .map((x) => x.trim())
    .filter(Boolean);
const zonePrefixes = (z: LogicalZone, exclude: boolean) =>
  z.selectors.filter((x) => x.kind === "prefix" && !!x.exclude === exclude).map((x) => x.value);
const sameNets = (a: string, b: string) => splitNets(a).join(" ") === splitNets(b).join(" ");

function AppdocTab({ tenantId, canEdit }: { tenantId: number; canEdit: boolean }) {
  const [rows, setRows] = useState<LogicalZone[] | null>(null);
  // Auswahl je Zone aus dem Katalog des Mandanten (Namenskonzept, 0.104.0) samt Vorschlag nach dem Namen
  const [catalog, setCatalog] = useState<AppdocCatalog | null>(null);
  const [systems, setSystems] = useState<System[]>([]);
  const [draft, setDraft] = useState<Record<number, AppdocDraft>>({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    // eine spätere Antwort überschreibt die Eingaben nicht (StrictMode lädt zweimal, 0.101.1)
    let live = true;
    Promise.all([fetchZones(tenantId), fetchAppdocCatalog(tenantId)])
      .then(([zs, cat]) => {
        if (!live) return;
        setRows(zs);
        setCatalog(cat);
        setDraft({});
      })
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [tenantId, tick]);
  useEffect(() => {
    fetchSystems(tenantId).then(setSystems).catch(() => setSystems([]));
  }, [tenantId]);

  const initial = (z: LogicalZone): AppdocDraft => ({
    area: z.appdoc_area ?? null,
    type: z.zone_type ?? null,
    nets: zonePrefixes(z, false).join("\n"),
    excluded: zonePrefixes(z, true).join("\n"),
  });
  const value = (z: LogicalZone) => draft[z.id] ?? initial(z);
  const set = (z: LogicalZone, patch: Partial<AppdocDraft>) => {
    setSaved(false);
    setDraft((d) => ({ ...d, [z.id]: { ...(d[z.id] ?? initial(z)), ...patch } }));
  };
  const netsChanged = (z: LogicalZone) => {
    const v = value(z);
    const i = initial(z);
    return !sameNets(v.nets, i.nets) || !sameNets(v.excluded, i.excluded);
  };
  const changed = (rows ?? []).filter(
    (z) => draft[z.id] && (value(z).area !== initial(z).area || value(z).type !== initial(z).type || netsChanged(z)),
  );
  const flows = !!catalog?.has_flows; // Richtungen gepflegt: die Kategorie der Zone gilt nicht mehr
  const hintOf = (z: LogicalZone) => catalog?.suggestions?.[String(z.id)] ?? null;
  const suggestions = (rows ?? []).filter((z) => !value(z).area && hintOf(z));
  const suggest = () => {
    setSaved(false);
    setDraft((d) => {
      const out = { ...d };
      for (const z of suggestions) out[z.id] = { ...(out[z.id] ?? initial(z)), area: hintOf(z) };
      return out;
    });
  };
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await setAppdocZones(
        tenantId,
        changed.map((z) => {
          const v = value(z);
          return {
            id: z.id,
            appdoc_area: v.area,
            zone_type: v.type,
            ...(netsChanged(z) ? { nets: splitNets(v.nets), excluded: splitNets(v.excluded) } : {}),
          };
        }),
      );
      setSaved(true);
      setTick((t) => t + 1);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      <div className="card" id="appdoc-zones">
        <div className="card-head">
          <h2>{L("Kategorien der Applikations-Doku", "Categories of the application documentation")}</h2>
          <span className="faint">
            {L(
              <>
                Je Zone die Kategorie, aus der ihre Zugriffe kommen – die Doku benennt die Kategorie nach ihren Zonen. Eine
                Firewall-Regel bzw. ein Contract steht in der Kategorie der Zone der Gegenstelle, auch über einen grossen
                Bereich; nennt die Gegenstelle Zonen mehrerer Kategorien, bleibt sie unspezifisch. Die Auswahl (Kategorien
                und Bereiche) passt ein Admin im Namenskonzept an (Administration → Namensprofile).
              </>,
              <>
                Per zone the category its accesses come from – the documentation names the category after its zones. A
                firewall rule or contract goes into the category of its peer's zone, also via a large range; if the peer
                covers zones of several categories, it stays unspecific. An admin adjusts the choice (categories and
                areas) in the naming concept (Administration → Naming profiles).
              </>,
            )}
          </span>
          {canEdit && (
            <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
              <button className="btn btn-sm" id="appdoc-suggest" disabled={suggestions.length === 0} onClick={suggest}>
                {L(`Vorschlag nach Namen (${suggestions.length})`, `Suggest by name (${suggestions.length})`)}
              </button>
              <button className="btn btn-primary btn-sm" id="appdoc-save" disabled={busy || changed.length === 0} onClick={() => void save()}>
                {busy ? L("Speichere…", "Saving…") : L(`Speichern (${changed.length})`, `Save (${changed.length})`)}
              </button>
            </div>
          )}
        </div>
        {saved && changed.length === 0 && (
          <div className="faint" id="appdoc-saved" style={{ padding: "0 14px", fontSize: 12 }}>
            {L("Gespeichert – die Applikations-Doku ordnet beim nächsten Laden neu.", "Saved – the application documentation re-sorts on the next load.")}
          </div>
        )}
        <div className="card-body scroll">
          {rows == null ? (
            <Loading />
          ) : rows.length === 0 ? (
            <Empty title={L("Noch keine Zonen", "No zones yet")}>
              {L("Zonen im Reiter „Zonen“ anlegen bzw. unter „Vorschläge“ übernehmen.", "Create zones in the “Zones” tab or apply them under “Suggestions”.")}
            </Empty>
          ) : (
            <table className="compact form-table">
              <colgroup>
                <col style={{ width: "15%" }} />
                <col style={{ width: "14%" }} />
                {!flows && <col style={{ width: "14%" }} />}
                <col style={{ width: "16%" }} />
                <col style={{ width: "16%" }} />
                <col style={{ width: "12%" }} />
                <col />
              </colgroup>
              <thead>
                <tr>
                  <th>{L("Zone", "Zone")}</th>
                  <th title={L("Zone des Grundschemas im Namenskonzept", "zone of the base schema in the naming concept")}>{L("Zonentyp", "Zone type")}</th>
                  {!flows && <th>{L("Kategorie", "Category")}</th>}
                  <th title={L("ein Netz je Zeile (CIDR)", "one network per line (CIDR)")}>{L("Netze (Quell-Adressen)", "Networks (source addresses)")}</th>
                  <th title={L("ein Netz je Zeile – aus den Netzen herausgeschnitten", "one network per line – cut out of the networks")}>
                    {L("ohne (Ausschlüsse)", "without (exclusions)")}
                  </th>
                  <th title={L("Firewall-Interface, Firewall-Zone bzw. L3Out, über die die Zone erreicht wird – im Reiter Zonen pflegen", "firewall interface, firewall zone or L3Out through which the zone is reached – maintain in the Zones tab")}>
                    {L("Ausgänge", "Exits")}
                  </th>
                  <th>{L("weitere Selektoren", "other selectors")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((z) => {
                  const v = value(z);
                  const others = z.selectors.filter((x) => x.kind !== "prefix" && !EXIT_KINDS.includes(x.kind));
                  const exitsOf = z.selectors.filter((x) => EXIT_KINDS.includes(x.kind) && !x.exclude);
                  const auto = catalog?.auto_types?.[String(z.id)] ?? null;
                  const hint = !v.area ? hintOf(z) : null;
                  return (
                    <tr key={z.id} className={changed.includes(z) ? "row-changed" : ""}>
                      <td>
                        <Swatch color={z.color} /> <b>{z.name}</b>
                        {z.code ? <span className="faint"> · {z.code}</span> : null}
                        <div className="faint" style={{ fontSize: 11 }}>
                          {L(`${z.nets} Netze`, `${z.nets} networks`)}
                          {z.epgs ? L(` · ${z.epgs} EPGs`, ` · ${z.epgs} EPGs`) : ""}
                        </div>
                      </td>
                      <td>
                        <select
                          className="sel"
                          aria-label={L(`Zonentyp der Zone ${z.name}`, `Zone type of zone ${z.name}`)}
                          value={v.type ?? ""}
                          disabled={!canEdit}
                          onChange={(e) => set(z, { type: e.target.value || null })}
                        >
                          <option value="">{auto ? L(`automatisch: ${auto}`, `automatic: ${auto}`) : L("— keiner (automatisch)", "— none (automatic)")}</option>
                          {(catalog?.zone_types ?? []).map((t) => (
                            <option key={t} value={t}>
                              {t}
                            </option>
                          ))}
                          {v.type && !(catalog?.zone_types ?? []).includes(v.type) ? <option value={v.type}>{v.type}</option> : null}
                        </select>
                      </td>
                      {!flows && (
                        <td>
                          <select
                            className="sel"
                            aria-label={L(`Kategorie der Zone ${z.name}`, `Category of zone ${z.name}`)}
                            value={v.area ?? ""}
                            disabled={!canEdit}
                            onChange={(e) => set(z, { area: (e.target.value || null) as AppdocArea | null })}
                          >
                            <option value="">{L("— keine", "— none")}</option>
                            {areaChoices(catalog, v.area).map((x) => (
                              <option key={x.key} value={x.key}>
                                {areaLabel(catalog, x.key)}
                                {x.off ? L(" – nicht mehr angeboten", " – no longer offered") : ""}
                              </option>
                            ))}
                          </select>
                          {hint && canEdit && (
                            <div className="faint" style={{ fontSize: 11 }}>
                              {L(`Vorschlag: ${areaLabel(catalog, hint)}`, `Suggestion: ${areaLabel(catalog, hint)}`)}
                            </div>
                          )}
                        </td>
                      )}
                      <td>
                        <textarea
                          className="input mono"
                          rows={Math.min(6, Math.max(2, splitNets(v.nets).length + 1))}
                          aria-label={L(`Netze der Zone ${z.name}`, `Networks of zone ${z.name}`)}
                          placeholder="10.70.0.0/16"
                          value={v.nets}
                          readOnly={!canEdit}
                          onChange={(e) => set(z, { nets: e.target.value })}
                        />
                      </td>
                      <td>
                        <textarea
                          className="input mono"
                          rows={Math.min(6, Math.max(2, splitNets(v.excluded).length + 1))}
                          aria-label={L(`Ausschlüsse der Zone ${z.name}`, `Exclusions of zone ${z.name}`)}
                          placeholder="10.70.99.0/24"
                          value={v.excluded}
                          readOnly={!canEdit}
                          onChange={(e) => set(z, { excluded: e.target.value })}
                        />
                      </td>
                      <td className="appdoc-exits">
                        {exitsOf.length === 0 ? (
                          <Dash />
                        ) : (
                          exitsOf.map((x, i) => (
                            <span key={i} className="chip info">
                              {selectorText(x, systems)}
                            </span>
                          ))
                        )}
                      </td>
                      <td>
                        {others.length === 0 ? (
                          <Dash />
                        ) : (
                          others.map((x, i) => (
                            <span key={i} className={`chip ${x.exclude ? "warn" : ""}`}>
                              {selectorText(x, systems)}
                            </span>
                          ))
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}

function Swatch({ color, title }: { color: string | null; title?: string }) {
  return <span className="zone-swatch" title={title} style={{ background: color ?? "var(--surface-3)" }} />;
}

function colorTitle(z: { color_source?: ZoneColorSource; schema?: string | null }): string | undefined {
  if (!z.color_source) return undefined;
  const schema = z.schema && z.color_source !== "zone" && z.color_source !== "kind";
  return L(
    `Farbe ${COLOR_SOURCE[z.color_source]}${schema ? ` („${z.schema}“)` : ""}`,
    `Color: ${COLOR_SOURCE[z.color_source]}${schema ? ` (“${z.schema}”)` : ""}`,
  );
}

function selectorText(s: ZoneSelector, systems: System[]): string {
  const where = s.source_system_id ? ` (${systems.find((x) => x.id === s.source_system_id)?.name ?? "?"})` : "";
  return `${s.exclude ? L("ohne ", "without ") : ""}${ZONE_SELECTOR_LABEL[s.kind] ?? s.kind} ${s.value}${where}`;
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
    if (
      !window.confirm(
        L(
          `Zone „${z.name}“ löschen? Ihre Absicht in der Zonenmatrix fällt mit weg.`,
          `Delete zone “${z.name}”? Its intent in the zone matrix is removed as well.`,
        ),
      )
    )
      return;
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
          <h2>{L("Zonen", "Zones")}</h2>
          <span className="faint">
            {L(
              <>
                Knoten über VDOM bzw. ACI-Tenant/VRF, Netze über die übrigen Selektoren (längstes Präfix); Netze zwischen Knoten
                verschiedener Zonen sind Übergänge
              </>,
              <>
                Nodes via VDOM or ACI tenant/VRF, networks via the other selectors (longest prefix); networks between nodes
                of different zones are transitions
              </>,
            )}
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
            <Empty title={L("Noch keine Zonen", "No zones yet")}>
              {L(
                <>
                  Zonen anlegen bzw. im Reiter „Vorschläge“ übernehmen: z. B. „DC“ über die ACI-Tenants, „Edge Internet“ über
                  die VDOMs der Internet-Firewalls, „Public“ über die Anbindung.
                </>,
                <>
                  Create zones or apply them in the “Suggestions” tab: e.g. “DC” via the ACI tenants, “Edge Internet” via
                  the VDOMs of the internet firewalls, “Public” via the exit.
                </>,
              )}
            </Empty>
          ) : (
            <table id="zone-list">
              <thead>
                <tr>
                  <th>Zone</th>
                  <th>{L("Art", "Kind")}</th>
                  <th>{L("Kunde", "Customer")}</th>
                  {!isCustomer && <th>{L("Selektoren", "Selectors")}</th>}
                  <th>{L("Knoten · Übergänge", "Nodes · transitions")}</th>
                  <th className="num">{L("Netze", "Networks")}</th>
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
                        <Swatch color={z.color} title={colorTitle(z)} /> <b>{z.name}</b>
                        <SharedChip shared={z.shared} />{" "}
                        {z.code && <span className="mono faint">{z.code}</span>}
                        {z.description && <div className="faint">{z.description}</div>}
                      </td>
                      <td>{ZONE_KIND[z.kind] ?? z.kind}</td>
                      <td>{z.customer ?? <span className="faint">{L("geteilt", "shared")}</span>}</td>
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
                      <td>
                        {(z.nodes ?? []).length === 0 && (z.transitions ?? []).length === 0 ? (
                          <Dash />
                        ) : (
                          <>
                            {(z.nodes ?? []).length > 0 && <div>{(z.nodes ?? []).join(", ")}</div>}
                            {(z.transitions ?? []).length > 0 && (
                              <div className="objs">
                                {(z.transitions ?? []).map((t) => (
                                  <span
                                    key={String(t.to_id)}
                                    className="chip"
                                    title={L(`Übergang zu ${t.to} · ${t.count} Netz(e)`, `Transition to ${t.to} · ${t.count} network(s)`)}
                                  >
                                    {t.name}
                                  </span>
                                ))}
                              </div>
                            )}
                          </>
                        )}
                      </td>
                      <td className="num">{z.own === false ? <Dash /> : z.nets}</td>
                      <td className="num">{z.own === false ? <Dash /> : z.epgs}</td>
                      <td className="nowrap" onClick={(e) => e.stopPropagation()}>
                        {canEdit && (
                          <>
                            <button className="btn btn-sm" onClick={() => setEditing(z)}>
                              {L("Bearbeiten", "Edit")}
                            </button>{" "}
                            <button className="btn btn-sm btn-ghost" onClick={() => remove(z)}>
                              {L("Löschen", "Delete")}
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                    {open === z.id && (
                      <tr className="subrow">
                        <td colSpan={isCustomer ? 7 : 8}>
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

// -- Vorschläge (0.54.0) ----------------------------------------------------------------------

function SuggestionsTab({ tenantId, canEdit }: { tenantId: number; canEdit: boolean }) {
  const [data, setData] = useState<ZoneSuggestions | null>(null);
  const [showDismissed, setShowDismissed] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [zones, setZones] = useState<LogicalZone[]>([]);
  const [systems, setSystems] = useState<System[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [exits, setExits] = useState<TopoExit[]>([]);
  const [editing, setEditing] = useState<ZoneSuggestion | null>(null);
  const [fromSchema, setFromSchema] = useState<SchemaZone | null>(null);
  const [onlySchema, setOnlySchema] = useState(false);
  const [q, setQ] = useState(""); // 0.65.1: Vorschläge filtern (Name, Herkunft, Selektoren)
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);

  const load = useCallback(() => {
    setErr(null);
    fetchZoneSuggestions(tenantId, showDismissed)
      .then((d) => {
        setData(d);
        setPicked((p) => new Set([...p].filter((k) => d.items.some((s) => s.key === k && !s.dismissed))));
      })
      .catch(setErr);
    fetchZones(tenantId).then(setZones).catch(() => setZones([]));
  }, [tenantId, showDismissed]);
  useEffect(() => {
    setData(null);
    load();
  }, [load]);
  useEffect(() => {
    fetchSystems(tenantId).then(setSystems).catch(() => setSystems([]));
    fetchCustomers(tenantId).then(setCustomers).catch(() => setCustomers([]));
    fetchExits(tenantId).then(setExits).catch(() => setExits([]));
  }, [tenantId]);

  const toggle = (key: string) =>
    setPicked((p) => {
      const n = new Set(p);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });
  const needle = q.trim().toLowerCase();
  const hit = (s: ZoneSuggestion) =>
    !needle ||
    [s.name, s.code ?? "", s.customer ?? "", ...s.evidence, ...s.selectors.map((x) => x.value)].some((x) => x.toLowerCase().includes(needle));
  const shown = data?.items.filter((s) => (!onlySchema || s.schema) && hit(s)) ?? [];
  const open = shown.filter((s) => !s.dismissed);

  const accept = async (keys: string[]) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await acceptZoneSuggestions(tenantId, keys);
      setDone(
        L(
          `${r.created.length} Zone(n) angelegt, ${r.updated.length} ergänzt` + (r.missing.length ? ` – ${r.missing.length} nicht mehr vorgeschlagen` : ""),
          `${r.created.length} zone(s) created, ${r.updated.length} extended` + (r.missing.length ? ` – ${r.missing.length} no longer suggested` : ""),
        ),
      );
      setPicked(new Set());
      load();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const applyIntents = async (pairs?: [number, number][], overwrite = false) => {
    setErr(null);
    try {
      const r = await applySchemaIntents(tenantId, pairs, overwrite);
      setDone(L(`${r.changed.length} Absicht(en) aus dem Grundschema gesetzt`, `${r.changed.length} intent(s) set from the base schema`));
      load();
    } catch (e) {
      setErr(e);
    }
  };
  const dismiss = async (s: ZoneSuggestion) => {
    try {
      await dismissZoneSuggestions(tenantId, [s.key], s.dismissed);
      load();
    } catch (e) {
      setErr(e);
    }
  };
  // mehrere auf einmal ausblenden (0.65.1), z. B. alle mit „SZ_“ nach dem Filter
  const dismissPicked = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await dismissZoneSuggestions(tenantId, [...picked]);
      setDone(
        L(
          `${r.keys.length} Vorschlag/Vorschläge ausgeblendet – mit „ausgeblendete zeigen“ wieder vorschlagen`,
          `${r.keys.length} suggestion(s) hidden – suggest again with “show hidden”`,
        ),
      );
      setPicked(new Set());
      load();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  const target = editing?.zone_id ? zones.find((z) => z.id === editing.zone_id) ?? null : null;
  return (
    <>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {done && (
        <Note>
          {done}{" "}
          <button className="btn btn-sm btn-ghost" onClick={() => setDone(null)}>
            OK
          </button>
        </Note>
      )}
      <div className="card">
        <div className="card-head">
          <h2>{L("Vorschläge", "Suggestions")}</h2>
          {data && (
            <span className="faint" id="zone-suggestions-summary">
              {L(
                <>
                  {data.summary.create} neu, {data.summary.extend} ergänzen eine Zone · {data.summary.nodes} Knoten ohne Zone ·{" "}
                  {data.summary.rest} Netze ohne Zone und Vorschlag
                </>,
                <>
                  {data.summary.create} new, {data.summary.extend} extend a zone · {data.summary.nodes} nodes without zone ·{" "}
                  {data.summary.rest} networks without zone and suggestion
                </>,
              )}
              {data.summary.template && L(" · nach der eingebauten Vorlage", " · following the built-in template")}
            </span>
          )}
          {data?.schema && (
            <label className="check" style={{ marginLeft: "auto" }}>
              <input type="checkbox" checked={onlySchema} onChange={(e) => setOnlySchema(e.target.checked)} />{" "}
              {L("nur aus dem Grundschema", "only from the base schema")}
            </label>
          )}
          <label className="check" style={data?.schema ? undefined : { marginLeft: "auto" }}>
            <input type="checkbox" checked={showDismissed} onChange={(e) => setShowDismissed(e.target.checked)} />{" "}
            {L("ausgeblendete zeigen", "show hidden")}
            {data && data.summary.dismissed > 0 && ` (${data.summary.dismissed})`}
          </label>
          <div className="search" style={{ flex: "0 1 200px" }}>
            <input
              type="search"
              aria-label={L("Vorschläge filtern", "Filter suggestions")}
              placeholder={L("Filtern, z. B. SZ_", "Filter, e.g. SZ_")}
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          {canEdit && (
            <button
              id="zone-suggestions-dismiss"
              className="btn btn-sm"
              disabled={busy || picked.size === 0}
              onClick={() => void dismissPicked()}
              title={L(
                "Gewählte Vorschläge ausblenden (bleiben ausgeblendet, bis sie wieder vorgeschlagen werden)",
                "Hide the selected suggestions (they stay hidden until they are suggested again)",
              )}
            >
              {L(`Ausgewählte ausblenden (${picked.size})`, `Hide selected (${picked.size})`)}
            </button>
          )}
          {canEdit && (
            <button
              id="zone-suggestions-accept"
              className="btn btn-primary btn-sm"
              disabled={busy || picked.size === 0}
              onClick={() => accept([...picked])}
            >
              {busy ? L("Übernehme…", "Applying…") : L(`Ausgewählte übernehmen (${picked.size})`, `Apply selected (${picked.size})`)}
            </button>
          )}
        </div>
        <div className="card-body scroll">
          <p className="faint" style={{ margin: "4px 16px 10px" }}>
            {L(
              <>
                Nach dem Zonenmodell: die ACI-Tenants sind das Datacenter (je Kunde eine Zone), Firewalls kommen in die Zone des
                Grundschemas bzw. der Vorlage, deren Name oder Muster passt (Firewalls mit einem Bein an der Internet-Anbindung in
                Edge Internet), die Anbindungen in Public, dazu Firewall-Zonen und Interfaces nach den Mustern und Zone bzw. Rolle im
                IPAM (ohne Werte, die ein Objekt im ACI nennen, z. B. ESGs). Netze zwischen Knoten verschiedener Zonen werden
                Übergänge. Unerwünschte Vorschläge filtern, wählen und gesammelt ausblenden. Übernehmen legt eine normale Zone mit Selektoren
                an bzw. ergänzt die bestehende; Knoten ohne Treffer stehen darunter zum Zuordnen.
              </>,
              <>
                Following the zone model: the ACI tenants are the data center (one zone per customer), firewalls go into the zone of
                the base schema or the template whose name or pattern matches (firewalls with a leg on the internet exit into
                Edge Internet), the exits into Public, plus firewall zones and interfaces by the patterns and zone or role in
                IPAM (without values that name an object in the ACI, e.g. ESGs). Networks between nodes of different zones become
                transitions. Filter unwanted suggestions, select them and hide them together. Applying creates a normal zone with selectors
                or extends the existing one; nodes without a match are listed below for assignment.
              </>,
            )}
          </p>
          {data == null ? (
            <Loading />
          ) : data.items.length === 0 ? (
            <Empty title={L("Keine Vorschläge", "No suggestions")}>
              {L("Alles Gelesene liegt in einer Zone oder ist ausgeblendet.", "Everything collected lies in a zone or is hidden.")}
            </Empty>
          ) : shown.length === 0 ? (
            <Empty title={L("Kein Vorschlag passt zum Filter", "No suggestion matches the filter")} />
          ) : (
            <table id="zone-suggestions">
              <thead>
                <tr>
                  {canEdit && (
                    <th>
                      <input
                        type="checkbox"
                        aria-label={L("alle wählen", "select all")}
                        checked={open.length > 0 && open.every((s) => picked.has(s.key))}
                        onChange={(e) => setPicked(e.target.checked ? new Set(open.map((s) => s.key)) : new Set())}
                      />
                    </th>
                  )}
                  <th>Zone</th>
                  <th>{L("Art", "Kind")}</th>
                  <th>{L("Kunde", "Customer")}</th>
                  <th>{L("Erkannt aus", "Detected from")}</th>
                  <th>{L("Selektoren", "Selectors")}</th>
                  <th className="num">{L("Netze", "Networks")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {shown.map((s) => (
                  <tr key={s.key} className={s.dismissed ? "muted" : ""} data-key={s.key}>
                    {canEdit && (
                      <td>
                        {!s.dismissed && (
                          <input
                            type="checkbox"
                            aria-label={L(`${s.name} wählen`, `Select ${s.name}`)}
                            checked={picked.has(s.key)}
                            onChange={() => toggle(s.key)}
                          />
                        )}
                      </td>
                    )}
                    <td>
                      <Swatch color={s.color} /> <b>{s.name}</b>{" "}
                      {s.action === "extend" ? (
                        <span className="chip">{L("ergänzt Zone", "extends zone")}</span>
                      ) : (
                        <span className="chip ok">{L("neu", "new")}</span>
                      )}
                      {s.dismissed && <span className="chip warn">{L("ausgeblendet", "hidden")}</span>}
                      {data.schema &&
                        (s.schema ? (
                          <span className="chip local" title={L(`Zone „${s.schema}“ im Grundschema`, `Zone “${s.schema}” in the base schema`)}>
                            Schema
                          </span>
                        ) : (
                          <span className="chip warn">{L("nicht im Schema", "not in the schema")}</span>
                        ))}
                      {s.code && <span className="mono faint"> {s.code}</span>}
                    </td>
                    <td>
                      {ZONE_KIND[s.kind] ?? s.kind}
                      {s.kind_reason && <div className="faint">{s.kind_reason}</div>}
                    </td>
                    <td>{s.customer ?? <span className="faint">{L("geteilt", "shared")}</span>}</td>
                    <td>
                      <div className="faint">{s.sources.map((x) => SOURCE[x] ?? x).join(", ")}</div>
                      {s.evidence.slice(0, 3).map((e) => (
                        <div key={e}>{e}</div>
                      ))}
                      {s.evidence_total > 3 && (
                        <div className="faint">{L(`… ${s.evidence_total - 3} weitere`, `… ${s.evidence_total - 3} more`)}</div>
                      )}
                    </td>
                    <td>
                      <div className="objs">
                        {s.selectors.map((x, i) => (
                          <span key={i} className={`chip ${x.exclude ? "warn" : ""}`}>
                            {selectorText(x, systems)}
                          </span>
                        ))}
                      </div>
                      {s.nodes.length > 0 && <div className="faint">{L(`Knoten: ${s.nodes.join(", ")}`, `Nodes: ${s.nodes.join(", ")}`)}</div>}
                    </td>
                    <td className="num nowrap" title={s.samples.join("\n")}>
                      {s.counts.nets}
                      {s.counts.epgs > 0 && <div className="faint">{s.counts.epgs} EPGs</div>}
                    </td>
                    <td className="nowrap">
                      {canEdit && !s.dismissed && (
                        <>
                          <button className="btn btn-sm" onClick={() => setEditing(s)}>
                            {L("Übernehmen…", "Apply…")}
                          </button>{" "}
                        </>
                      )}
                      {canEdit && (
                        <button className="btn btn-sm btn-ghost" onClick={() => dismiss(s)}>
                          {s.dismissed ? L("Wieder vorschlagen", "Suggest again") : L("Ausblenden", "Hide")}
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
      {data && data.nodes.length > 0 && (
        <NodesCard nodes={data.nodes} zones={zones} canEdit={canEdit} onAssigned={(msg) => {
            setDone(msg);
            load();
          }} onError={setErr} />
      )}
      {data?.schema && (
        <SchemaStatus status={data.schema} canEdit={canEdit} onCreate={setFromSchema} onApply={applyIntents} />
      )}
      {data && data.rest.total > 0 && (
        <div className="card">
          <div className="card-head">
            <h2>{L("Nicht erkannt", "Not detected")}</h2>
            <span className="faint">
              {L(
                "Gelesene Netze ohne Zone und ohne Vorschlag – von Hand einer Zone zuordnen",
                "Collected networks without zone and without suggestion – assign them to a zone by hand",
              )}
            </span>
          </div>
          <div className="card-body scroll">
            <table id="zone-suggestions-rest" className="compact">
              <thead>
                <tr>
                  <th>{L("Netz", "Network")}</th>
                  <th>{L("Knoten", "Node")}</th>
                  <th>{L("Wo", "Where")}</th>
                </tr>
              </thead>
              <tbody>
                {data.rest.items.map((r) => (
                  <tr key={r.network}>
                    <td className="mono">{r.network}</td>
                    <td>{r.node}</td>
                    <td className="faint">{r.where}</td>
                  </tr>
                ))}
                {data.rest.total > data.rest.items.length && (
                  <tr>
                    <td colSpan={3} className="faint">
                      {L(`… ${data.rest.total - data.rest.items.length} weitere`, `… ${data.rest.total - data.rest.items.length} more`)}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {fromSchema && (
        <ZoneEditor
          tenantId={tenantId}
          systems={systems}
          customers={customers}
          exits={exits}
          initial={null}
          draft={{
            tenant_id: tenantId,
            name: fromSchema.name,
            code: fromSchema.code,
            kind: fromSchema.kind,
            color: fromSchema.color,
            customer_id: null,
            position: 0,
            description: fromSchema.description ?? L("aus dem Grundschema", "from the base schema"),
            selectors: [],
          }}
          onClose={() => setFromSchema(null)}
          onSaved={() => {
            setFromSchema(null);
            load();
          }}
        />
      )}
      {editing && (editing.action === "create" || target) && (
        <ZoneEditor
          tenantId={tenantId}
          systems={systems}
          customers={customers}
          exits={exits}
          initial={target ? { ...target, selectors: [...target.selectors, ...editing.selectors] } : null}
          draft={
            target
              ? null
              : {
                  tenant_id: tenantId,
                  name: editing.name,
                  code: editing.code,
                  kind: editing.kind,
                  color: editing.color,
                  customer_id: editing.customer_id,
                  position: 0,
                  description: L(
                    `Vorschlag aus ${editing.sources.map((x) => SOURCE[x] ?? x).join(", ")}`,
                    `Suggestion from ${editing.sources.map((x) => SOURCE[x] ?? x).join(", ")}`,
                  ),
                  selectors: editing.selectors,
                }
          }
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

// Knoten ohne Zone und ohne Vorschlag (0.56.0): einer Zone zuordnen = Selektor VDOM bzw. ACI-VRF
function NodesCard({
  nodes,
  zones,
  canEdit,
  onAssigned,
  onError,
}: {
  nodes: SuggestionNode[];
  zones: LogicalZone[];
  canEdit: boolean;
  onAssigned: (message: string) => void;
  onError: (e: unknown) => void;
}) {
  const [target, setTarget] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const assign = async (n: SuggestionNode) => {
    const zoneId = target[n.id];
    if (!zoneId) return;
    setBusy(n.id);
    try {
      await assignZoneNodes(zoneId, [n.id]);
      onAssigned(`${n.label} → ${zones.find((z) => z.id === zoneId)?.name ?? "Zone"}`);
    } catch (e) {
      onError(e);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="card" id="zone-suggestions-nodes">
      <div className="card-head">
        <h2>{L("Knoten ohne Zone", "Nodes without zone")}</h2>
        <span className="faint">
          {L(
            "Firewalls bzw. VDOMs ohne passende Zone – einer Zone zuordnen (Selektor VDOM)",
            "Firewalls or VDOMs without a matching zone – assign them to a zone (selector VDOM)",
          )}
        </span>
      </div>
      <div className="card-body scroll">
        <table className="compact">
          <thead>
            <tr>
              <th>{L("Knoten", "Node")}</th>
              <th>{L("Art", "Kind")}</th>
              <th>{L("Hinweis", "Note")}</th>
              {canEdit && <th>Zone</th>}
            </tr>
          </thead>
          <tbody>
            {nodes.map((n) => (
              <tr key={n.id} data-node={n.id}>
                <td>
                  <b>{n.label}</b>
                  {n.group && <span className="faint">{L(` · Verbund ${n.group}`, ` · parallel group ${n.group}`)}</span>}
                </td>
                <td>{NODE_KIND[n.kind] ?? n.kind}</td>
                <td className="faint">
                  {n.hint ??
                    (n.kind_hint
                      ? L(`Name deutet auf ${ZONE_KIND[n.kind_hint] ?? n.kind_hint}`, `Name points to ${ZONE_KIND[n.kind_hint] ?? n.kind_hint}`)
                      : "–")}
                </td>
                {canEdit && (
                  <td className="nowrap">
                    <select
                      className="sel"
                      aria-label={L(`Zone für ${n.label}`, `Zone for ${n.label}`)}
                      value={target[n.id] ?? ""}
                      onChange={(e) => setTarget((t) => ({ ...t, [n.id]: Number(e.target.value) }))}
                    >
                      <option value="">{L("– Zone wählen –", "– Select zone –")}</option>
                      {zones.map((z) => (
                        <option key={z.id} value={z.id}>
                          {z.name}
                        </option>
                      ))}
                    </select>{" "}
                    <button className="btn btn-sm" disabled={!target[n.id] || busy === n.id} onClick={() => assign(n)}>
                      {L("Zuordnen", "Assign")}
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Grundschema des Mandanten (0.55.0): fehlende Zonen, Absichten aus dem Schema, Zonen ausserhalb
function SchemaStatus({
  status,
  canEdit,
  onCreate,
  onApply,
}: {
  status: ZoneSchemaStatus;
  canEdit: boolean;
  onCreate: (z: SchemaZone) => void;
  onApply: (pairs?: [number, number][], overwrite?: boolean) => void;
}) {
  const missing = status.intents.filter((x) => x.current == null);
  return (
    <div className="card" id="zone-schema-status">
      <div className="card-head">
        <h2>{L("Grundschema", "Base schema")}</h2>
        <span className="faint">
          {L(
            <>
              „{status.profile}“: {status.zones} Zonen, {status.links} Übergänge –{" "}
              <a href="#dict">in den Namensprofilen pflegen</a>
            </>,
            <>
              “{status.profile}”: {status.zones} zones, {status.links} transitions –{" "}
              <a href="#dict">maintain in the naming profiles</a>
            </>,
          )}
        </span>
      </div>
      <div className="card-body pad">
        <div className="field">
          <label>{L("Fehlen (im Schema, ohne Zone und ohne Vorschlag)", "Missing (in the schema, without zone and without suggestion)")}</label>
          {status.missing.length === 0 ? (
            <span className="faint">{L("keine", "none")}</span>
          ) : (
            <div className="objs" id="zone-schema-missing">
              {status.missing.map((z) => (
                <span key={z.key} className="chip">
                  <Swatch color={z.color} /> {z.name} <span className="faint">{ZONE_KIND[z.kind]}</span>
                  {canEdit && (
                    <button className="linkbtn" title={L("Zone anlegen", "Create zone")} onClick={() => onCreate(z)}>
                      {L("anlegen…", "create…")}
                    </button>
                  )}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="field" style={{ marginTop: 10 }}>
          <label>
            {L(
              "Absichten aus dem Schema, die in der Zonenmatrix fehlen bzw. abweichen",
              "Intents from the schema that are missing from or differ in the zone matrix",
            )}
          </label>
          {status.intents.length === 0 ? (
            <span className="faint">{L("keine – die Zonenmatrix entspricht dem Schema", "none – the zone matrix matches the schema")}</span>
          ) : (
            <>
              <table className="compact" id="zone-schema-intents">
                <tbody>
                  {status.intents.map((x) => (
                    <tr key={`${x.src_zone_id}>${x.dst_zone_id}`}>
                      <td>
                        {x.src} → {x.dst}
                        {(x.schema_src !== x.src || x.schema_dst !== x.dst) && (
                          <div className="faint">
                            Schema: {x.schema_src} → {x.schema_dst}
                          </div>
                        )}
                      </td>
                      <td>
                        <span className={`chip ${INTENT[x.intent][0]}`}>{INTENT[x.intent][1]}</span>
                        {x.services && x.services.length > 0 && (
                          <div className="faint">{L(`nur ${x.services.join(", ")}`, `only ${x.services.join(", ")}`)}</div>
                        )}
                      </td>
                      <td className="faint">{x.current ? `in Automato: ${INTENT[x.current][1]}` : L("fehlt", "missing")}</td>
                      <td className="nowrap">
                        {canEdit && x.current && (
                          <button className="btn btn-sm btn-ghost" onClick={() => onApply([[x.src_zone_id, x.dst_zone_id]], true)}>
                            {L("Ersetzen", "Replace")}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {canEdit && missing.length > 0 && (
                <button className="btn btn-sm btn-primary" id="zone-schema-apply" style={{ marginTop: 8 }} onClick={() => onApply()}>
                  {L(`Fehlende Absichten übernehmen (${missing.length})`, `Apply missing intents (${missing.length})`)}
                </button>
              )}
            </>
          )}
        </div>
        {status.outside.length > 0 && (
          <div className="field" style={{ marginTop: 10 }}>
            <label>{L("Zonen ausserhalb des Schemas", "Zones outside the schema")}</label>
            <div className="objs">
              {status.outside.map((z) => (
                <span key={z.id} className="chip warn">
                  {z.name}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ZoneNets({ zoneId }: { zoneId: number }) {
  const [data, setData] = useState<ZoneNetsData | null>(null);
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
  const transitions = data.transitions ?? [];
  return (
    <div className="zone-detail">
      {(data.nodes ?? []).length > 0 && (
        <div className="field">
          <label>{L("Knoten", "Nodes")}</label>
          <div>{(data.nodes ?? []).join(", ")}</div>
        </div>
      )}
      <div className="field">
        <label>{L("Netze in der Zone", "Networks in the zone")}</label>
        {data.items.length === 0 ? (
          <span className="faint">
            {L(
              "keine – die Zone ist ein reiner Übergang (normal z. B. für eine Internet-Firewall)",
              "none – the zone is a pure transition (normal e.g. for an internet firewall)",
            )}
          </span>
        ) : (
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
                    {L(`… ${data.total - data.items.length} weitere`, `… ${data.total - data.items.length} more`)}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>
      {transitions.length > 0 && (
        <div className="field">
          <label>{L("Übergänge zu anderen Zonen", "Transitions to other zones")}</label>
          <table className="compact zone-transitions">
            <tbody>
              {transitions.flatMap((t) =>
                t.networks.map((n, i) => (
                  <tr key={`${t.to_id}-${n.network}`}>
                    <td className="nowrap">{i === 0 && <b className="mono">{t.name}</b>}</td>
                    <td className="faint nowrap">{i === 0 && `→ ${t.to}`}</td>
                    <td className="mono">{n.network}</td>
                    <td className="faint">{legsText(n)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function legsText(n: {
  legs: { label: string; interface: string | null; l3out: boolean }[];
  routers: { ip: string; names: string[] }[];
  routes?: { zone: number; prefixes: string[] }[];
}): string {
  const legs = n.legs.map((l) => `${l.label} (${l.l3out ? "L3Out" : "Interface"} ${l.interface ?? "?"})`);
  const legsUnique = [...new Set(legs)];
  const routers = n.routers.map((r) => `Router ${r.names[0] ?? r.ip}`);
  // Zone nur hinter dem Router (0.81.0): die Routen dorthin
  const routes = (n.routes ?? []).map((r) => `Route ${r.prefixes.join(", ")}`);
  return [...legsUnique, ...routers, ...routes].join(", ");
}

const EMPTY_SELECTOR: ZoneSelector = { kind: "prefix", value: "", source_system_id: null, exclude: false };

function ZoneEditor({
  tenantId,
  systems,
  customers,
  exits,
  initial,
  draft = null,
  onClose,
  onSaved,
}: {
  tenantId: number;
  systems: System[];
  customers: Customer[];
  exits: TopoExit[];
  initial: LogicalZone | null;
  draft?: ZoneIn | null; // neue Zone aus einem Vorschlag (0.54.0)
  onClose: () => void;
  onSaved: () => void;
}) {
  const base = initial ?? draft;
  const [name, setName] = useState(base?.name ?? "");
  const [code, setCode] = useState(base?.code ?? "");
  const [kind, setKind] = useState<ZoneKind>(base?.kind ?? "internal");
  // eigene Farbe der Zone; gilt nur, wenn weder Grundschema noch Vorlage die Zone kennen (0.56.0)
  const [color, setColor] = useState((initial ? (initial.own_color ?? initial.color) : draft?.color) ?? "#3b82c4");
  const fixedColor = initial && (initial.color_source === "schema" || initial.color_source === "template") ? initial : null;
  const [customerId, setCustomerId] = useState<number | null>(base?.customer_id ?? null);
  const [position, setPosition] = useState(base?.position ?? 0);
  const [description, setDescription] = useState(base?.description ?? "");
  // Kategorie der Applikations-Doku (0.101.0): Zugriffe aus dieser Zone stehen in dieser Kategorie
  const [area, setArea] = useState<AppdocArea | null>(base?.appdoc_area ?? null);
  // Zonentyp von Hand (0.105.0): Zone des Grundschemas; leer = automatisch
  const [zoneType, setZoneType] = useState<string | null>(base?.zone_type ?? null);
  const [catalog, setCatalog] = useState<AppdocCatalog | null>(null);
  useEffect(() => {
    let live = true;
    fetchAppdocCatalog(tenantId)
      .then((c) => live && setCatalog(c))
      .catch(() => live && setCatalog(null));
    return () => {
      live = false;
    };
  }, [tenantId]);
  const [selectors, setSelectors] = useState<ZoneSelector[]>(base?.selectors.map((s) => ({ ...s })) ?? [{ ...EMPTY_SELECTOR }]);
  const [preview, setPreview] = useState<ZonePreview | null>(null);
  // Vorschläge beim Erfassen (0.66.0): Firewall-Zonen, Interfaces, IPAM, ACI, Anbindungen nach dem Namen
  const [assist, setAssist] = useState<{ items: ZoneAssistItem[]; total: number; schema: string | null } | null>(null);
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

  useEffect(() => {
    if (name.trim().length < 2 && !code.trim()) {
      setAssist(null);
      return;
    }
    const t = window.setTimeout(() => {
      assistZone(tenantId, { name: name.trim(), code: code.trim() || null, kind, zone_id: initial?.id ?? null, selectors: valid })
        .then(setAssist)
        .catch(() => setAssist(null)); // z. B. ein halb getipptes Netz – die Vorschau meldet es
    }, 500);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, name, code, kind, JSON.stringify(valid)]);

  const set = (i: number, patch: Partial<ZoneSelector>) => setSelectors((xs) => xs.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  // Vorschlag übernehmen: als Selektor anhängen (eine leere Zeile ersetzt er)
  const take = (items: ZoneSelector[]) => setSelectors((xs) => [...xs.filter((x) => x.value.trim()), ...items.map((s) => ({ ...s }))]);
  const takeable = (assist?.items ?? []).filter((a) => !a.covered && a.conflicts.length === 0);

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
      appdoc_area: area,
      zone_type: zoneType,
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
      title={
        initial
          ? L(`Zone „${initial.name}“`, `Zone “${initial.name}”`)
          : draft
            ? L(`Vorschlag „${draft.name}“ übernehmen`, `Apply suggestion “${draft.name}”`)
            : L("Neue Zone", "New zone")
      }
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
      <div className="zone-form">
        <div className="field">
          <label htmlFor="zo-name">Name</label>
          <input
            id="zo-name"
            className="input"
            value={name}
            maxLength={200}
            placeholder={L("z. B. DMZ", "e.g. DMZ")}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="zo-code">{L("Kurzname (optional)", "Short name (optional)")}</label>
          <input
            id="zo-code"
            className="input mono"
            value={code}
            maxLength={50}
            placeholder={L("z. B. DMZ1", "e.g. DMZ1")}
            onChange={(e) => setCode(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="zo-kind">{L("Art", "Kind")}</label>
          <select id="zo-kind" className="sel" value={kind} onChange={(e) => setKind(e.target.value as ZoneKind)}>
            {(Object.keys(ZONE_KIND) as ZoneKind[]).map((k) => (
              <option key={k} value={k}>
                {ZONE_KIND[k]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="zo-customer">{L("Kunde", "Customer")}</label>
          <select
            id="zo-customer"
            className="sel"
            value={customerId ?? ""}
            onChange={(e) => setCustomerId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">{L("keiner (geteilt bzw. Infrastruktur)", "none (shared or infrastructure)")}</option>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="zo-color">{L("Farbe", "Color")}</label>
          {fixedColor ? (
            <div id="zo-color" className="zone-fixed-color">
              <Swatch color={fixedColor.color} /> {COLOR_SOURCE[fixedColor.color_source as ZoneColorSource]}
              {fixedColor.schema && L(<> („{fixedColor.schema}“)</>, <> (“{fixedColor.schema}”)</>)}
              <div className="hint">{L("überall gleich – im Grundschema der Namensprofile ändern", "the same everywhere – change it in the base schema of the naming profiles")}</div>
            </div>
          ) : (
            <input id="zo-color" type="color" className="input" value={color} onChange={(e) => setColor(e.target.value)} />
          )}
        </div>
        <div className="field">
          <label htmlFor="zo-pos">{L("Reihenfolge", "Order")}</label>
          <input id="zo-pos" type="number" min={0} className="input" value={position} onChange={(e) => setPosition(Number(e.target.value) || 0)} />
          <div className="hint">{L("0 = ans Ende", "0 = at the end")}</div>
        </div>
      </div>
      <div className="field">
        <label htmlFor="zo-desc">{L("Beschreibung (optional)", "Description (optional)")}</label>
        <input id="zo-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="zo-type">{L("Zonentyp (Zone des Grundschemas)", "Zone type (zone of the base schema)")}</label>
        <select id="zo-type" className="input" value={zoneType ?? ""} onChange={(e) => setZoneType(e.target.value || null)}>
          <option value="">
            {initial && catalog?.auto_types?.[String(initial.id)]
              ? L(`automatisch: ${catalog.auto_types[String(initial.id)]}`, `automatic: ${catalog.auto_types[String(initial.id)]}`)
              : L("— automatisch (Name, Kurzname, Vorlage je Kunde)", "— automatic (name, short name, template per customer)")}
          </option>
          {(catalog?.zone_types ?? []).map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
          {zoneType && !(catalog?.zone_types ?? []).includes(zoneType) ? <option value={zoneType}>{zoneType}</option> : null}
        </select>
        <div className="hint">
          {L(
            "Mit Richtungen im Namenskonzept (z. B. „Edge Internet → DC“) bestimmt der Zonentyp die Kategorie in der Applikations-Doku",
            "With directions in the naming concept (e.g. “Edge Internet → DC”) the zone type determines the category in the application documentation",
          )}
        </div>
      </div>
      <div className="field">
        <label htmlFor="zo-area">{L("Kategorie in der Applikations-Doku", "Category in the application documentation")}</label>
        <select id="zo-area" className="input" value={area ?? ""} onChange={(e) => setArea((e.target.value || null) as AppdocArea | null)}>
          <option value="">{L("— keine", "— none")}</option>
          {areaChoices(catalog, area).map((x) => (
            <option key={x.key} value={x.key}>
              {areaLabel(catalog, x.key)}
              {x.off ? L(" – nicht mehr angeboten", " – no longer offered") : ""}
            </option>
          ))}
        </select>
        <div className="hint">
          {L(
            "Firewall-Regeln mit einer Gegenstelle in dieser Zone (auch über einen grossen Bereich) stehen in dieser Kategorie; die Quell-Adressen sind die Netze der Selektoren",
            "Firewall rules with a peer in this zone (also via a large range) go into this category; the source addresses are the networks of the selectors",
          )}
        </div>
      </div>

      <h3 style={{ marginTop: 12 }}>{L("Selektoren", "Selectors")}</h3>
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
            <th>{L("Art", "Kind")}</th>
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
                <select className="sel" aria-label={L("Art", "Kind")} value={s.kind} onChange={(e) => set(i, { kind: e.target.value as ZoneSelectorKind })}>
                  {(Object.keys(ZONE_SELECTOR_LABEL) as ZoneSelectorKind[]).map((k) => (
                    <option key={k} value={k}>
                      {ZONE_SELECTOR_LABEL[k]}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                {s.kind === "exit" && exits.length > 0 ? (
                  <select className="sel" aria-label={L("Wert", "Value")} value={s.value} onChange={(e) => set(i, { value: e.target.value })}>
                    <option value="">{L("– Anbindung wählen –", "– Select exit –")}</option>
                    {exits.map((x) => (
                      <option key={x.id} value={x.name}>
                        {x.name} {x.prefixes?.length ? `(${x.prefixes.join(", ")})` : L("(ohne Netze)", "(no networks)")}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    className="input mono"
                    aria-label={L("Wert", "Value")}
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
                <input
                  type="checkbox"
                  aria-label={L("ausschliessen", "exclude")}
                  checked={s.exclude}
                  onChange={(e) => set(i, { exclude: e.target.checked })}
                />
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

      {assist && assist.items.length > 0 && (
        <div id="zone-assist" className="zone-assist">
          <h3 style={{ marginTop: 12 }}>
            {L(
              <>Vorschläge zu „{name.trim() || code.trim()}“</>,
              <>Suggestions for “{name.trim() || code.trim()}”</>,
            )}{" "}
            <span className="faint">
              {L(
                <>
                  {assist.items.length}
                  {assist.total > assist.items.length ? ` von ${assist.total}` : ""} Treffer
                  {assist.schema ? ` · Grundschema „${assist.schema}“` : ""}
                </>,
                <>
                  {assist.items.length}
                  {assist.total > assist.items.length ? ` of ${assist.total}` : ""} matches
                  {assist.schema ? ` · base schema “${assist.schema}”` : ""}
                </>,
              )}
            </span>
          </h3>
          <p className="faint" style={{ margin: "0 0 6px" }}>
            {L(
              <>
                Firewall-Zonen, Interfaces und VDOMs, Netze und Merkmale im IPAM, ACI-Objekte und Anbindungen, deren Name zur
                Zone passt (gleicher Name bzw. Kurzname, alle Wörter, Muster des Grundschemas, Rolle des Interfaces zur Art).
              </>,
              <>
                Firewall zones, interfaces and VDOMs, networks and attributes in IPAM, ACI objects and exits whose name matches the
                zone (same name or short name, all words, patterns of the base schema, role of the interface for the kind).
              </>,
            )}
          </p>
          <table className="compact" id="zone-assist-items">
            <thead>
              <tr>
                <th>{L("Treffer", "Match")}</th>
                <th>{L("Selektor", "Selector")}</th>
                <th className="num">{L("Netze", "Networks")}</th>
                <th>{L("Hinweis", "Note")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {assist.items.map((a) => (
                <tr key={`${a.selector.kind}|${a.selector.value}|${a.selector.source_system_id ?? ""}`} data-selector={`${a.selector.kind}:${a.selector.value}`}>
                  <td>
                    {a.label}
                    <div className="faint">
                      {a.source_label} · {a.reason}
                    </div>
                  </td>
                  <td>
                    <span className="chip">{selectorText(a.selector, systems)}</span>
                  </td>
                  <td className="num nowrap" title={a.samples.join("\n")}>
                    {a.nets}
                    {a.epgs > 0 && <div className="faint">{a.epgs} EPGs</div>}
                  </td>
                  <td>
                    {a.covered ? (
                      <span className="faint">{L("schon erfasst", "already covered")}</span>
                    ) : a.conflicts.length > 0 ? (
                      <span className="warn-text">
                        {L("in Zone", "in zone")} {a.conflicts.map((c) => `${c.zone} (${c.network})`).join(", ")}
                        {a.conflicts_total > a.conflicts.length ? " …" : ""}
                      </span>
                    ) : (
                      <span className="faint mono">
                        {a.samples.join(", ")}
                        {a.nets > a.samples.length ? " …" : ""}
                      </span>
                    )}
                  </td>
                  <td className="nowrap">
                    <button className="btn btn-sm" onClick={() => take([a.selector])}>
                      {L("+ Übernehmen", "+ Apply")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {takeable.length > 1 && (
            <button className="btn btn-sm" id="zone-assist-all" onClick={() => take(takeable.map((a) => a.selector))}>
              {L(`Alle ohne Überschneidung übernehmen (${takeable.length})`, `Apply all without overlap (${takeable.length})`)}
            </button>
          )}
        </div>
      )}

      <h3 style={{ marginTop: 12 }}>{L("Vorschau", "Preview")}</h3>
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
                <td>{L("Netze", "Networks")}</td>
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
              {L(
                <>
                  Gleiches Netz schon in einer anderen Zone: {preview.overlaps.map((o) => `${o.network} (${o.zone})`).join(", ")}
                  {preview.overlaps_total > preview.overlaps.length ? " …" : ""} – das wird ein Befund „Netz in mehreren Zonen“.
                </>,
                <>
                  Same network already in another zone: {preview.overlaps.map((o) => `${o.network} (${o.zone})`).join(", ")}
                  {preview.overlaps_total > preview.overlaps.length ? " …" : ""} – this becomes a finding “network in several zones”.
                </>,
              )}
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
  if (c === "none") return L("ohne Zone", "no zone");
  if (c === "other") return L("anderer Kunde", "other customer");
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
  if (m.zones.length === 0) return <Note>{L("Noch keine Zonen – zuerst im Reiter „Zonen“ anlegen.", "No zones yet – create them first in the “Zones” tab.")}</Note>;
  const s = m.summary;
  return (
    <>
      <div className="card">
        <div className="card-head">
          <h2>{L("Zonenmatrix", "Zone matrix")}</h2>
          <span className="faint" id="zone-matrix-summary">
            {L(
              <>
                {s.assigned} von {s.rules} Regeln zugeordnet
                {s.without ? ` · ${s.without} ohne IP-Bereich (z. B. FQDN)` : ""}
                {s.off_path ? ` · ${s.off_path} nach dem Routing nicht möglich` : ""} ·{" "}
                <span className={s.violations ? "crit" : ""}>
                  {s.violations} {s.violations === 1 ? "Regel" : "Regeln"} gegen die Absicht
                </span>
              </>,
              <>
                {s.assigned} of {s.rules} rules assigned
                {s.without ? ` · ${s.without} without IP range (e.g. FQDN)` : ""}
                {s.off_path ? ` · ${s.off_path} not possible according to the routing` : ""} ·{" "}
                <span className={s.violations ? "crit" : ""}>
                  {s.violations} {s.violations === 1 ? "rule" : "rules"} against the intent
                </span>
              </>,
            )}
          </span>
        </div>
        <div className="card-body scroll">
          <table className="zone-matrix" id="zone-matrix">
            <thead>
              <tr>
                <th className="corner">{L("Quelle ↓ · Ziel →", "Source ↓ · destination →")}</th>
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
                        title={
                          [c?.services?.length ? L(`nur ${c.services.join(", ")}`, `only ${c.services.join(", ")}`) : "", c?.note ?? ""]
                            .filter(Boolean)
                            .join(" · ") || undefined
                        }
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
                        {c?.services?.length ? <div className="z-svc">{L(`nur ${c.services.join(", ")}`, `only ${c.services.join(", ")}`)}</div> : null}
                        {c?.violations ? <div className="z-viol">{L(`${c.violations} gegen Absicht`, `${c.violations} against intent`)}</div> : null}
                        {c?.impossible ? (
                          <div
                            className="z-off"
                            title={L(
                              "Regeln, die das Routing ihres Geräts für diese Zonen ausschliesst – nicht gezählt",
                              "Rules that the routing of their device rules out for these zones – not counted",
                            )}
                          >
                            {L(`${c.impossible} nicht möglich`, `${c.impossible} not possible`)}
                          </div>
                        ) : null}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="faint zone-legend">
            {L("✓ erlaubende, ✕ sperrende Regeln · Hintergrund = Absicht:", "✓ allowing, ✕ denying rules · background = intent:")}{" "}
            {(Object.keys(INTENT) as ZoneIntent[]).map((k) => (
              <span key={k} className={`chip ${INTENT[k][0]}`}>
                {INTENT[k][1]}
              </span>
            ))}{" "}
            {L(
              <>
                · eingeschränkt mit Diensten: andere Dienste sind gegen die Absicht · „any“ und „ohne Zone“ zählen nicht
                als Verstoss
              </>,
              <>
                · limited with services: other services are against the intent · “any” and “no zone” do not count
                as a violation
              </>,
            )}
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
  const [rules, setRules] = useState<ZoneCellRules | null>(null);
  const [offPath, setOffPath] = useState(false); // 0.81.0: die nach dem Weg unmöglichen Regeln
  const [intent, setIntent] = useState<ZoneIntent | "">(cell?.intent ?? "");
  const [note, setNote] = useState(cell?.note ?? "");
  const [services, setServices] = useState((cell?.services ?? []).join(", "));
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  // nach dem Speichern der Absicht (Dienste) neu laden: Verstösse und Dienste ausserhalb
  const stamp = `${cell?.intent ?? ""}|${(cell?.services ?? []).join(",")}|${cell?.violations ?? 0}`;
  useEffect(() => {
    let current = true;
    fetchZoneCellRules(tenantId, src, dst, 0, offPath)
      .then((r) => current && setRules(r))
      .catch((e) => current && setErr(e));
    return () => {
      current = false;
    };
  }, [tenantId, src, dst, stamp, offPath]);
  useEffect(() => setOffPath(false), [src, dst]);
  const zonePair = typeof src === "number" && typeof dst === "number";
  const save = async () => {
    if (!zonePair) return;
    setBusy(true);
    try {
      const list = services
        .split(/[,;\n]/)
        .map((x) => x.trim())
        .filter(Boolean);
      await setZonePolicy(tenantId, src, dst, intent || null, note.trim() || null, intent === "limited" ? list : undefined);
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
        {cell?.intent && (
          <span className={`chip ${INTENT[cell.intent][0]}`}>{L(`Absicht: ${INTENT[cell.intent][1]}`, `Intent: ${INTENT[cell.intent][1]}`)}</span>
        )}
        {cell?.services?.length ? <span className="faint">{L(`nur ${cell.services.join(", ")}`, `only ${cell.services.join(", ")}`)}</span> : null}
      </div>
      <div className="card-body">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        {zonePair && canEdit && (
          <div className="zone-intent">
            <select className="sel" aria-label={L("Absicht", "Intent")} value={intent} onChange={(e) => setIntent(e.target.value as ZoneIntent | "")}>
              <option value="">{L("keine Absicht", "no intent")}</option>
              {(Object.keys(INTENT) as ZoneIntent[]).map((k) => (
                <option key={k} value={k}>
                  {INTENT[k][1]}
                </option>
              ))}
            </select>
            {intent === "limited" && (
              <input
                className="input mono"
                aria-label={L("Dienste", "Services")}
                placeholder={L("erlaubte Dienste, z. B. tcp/443, udp/53, FT_https", "allowed services, e.g. tcp/443, udp/53, FT_https")}
                value={services}
                onChange={(e) => setServices(e.target.value)}
              />
            )}
            <input
              className="input"
              aria-label={L("Notiz", "Note")}
              placeholder={L("Notiz, z. B. nur über Reverse Proxy", "Note, e.g. only via reverse proxy")}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <button className="btn btn-sm btn-primary" onClick={save} disabled={busy}>
              {L("Absicht speichern", "Save intent")}
            </button>
          </div>
        )}
        {((rules?.impossible ?? 0) > 0 || offPath) && (
          <div className="seg" id="zone-cell-mode" style={{ marginBottom: 8 }}>
            <button className={offPath ? "" : "on"} onClick={() => setOffPath(false)}>
              {L("Regeln der Zelle", "Rules of the cell")}
            </button>
            <button
              className={offPath ? "on" : ""}
              id="zone-cell-impossible"
              title={L(
                "Regeln, deren Gerät diese Zonen nach dem Routing nicht über Ein- bzw. Ausgang der Regel erreicht",
                "Rules whose device does not reach these zones via the incoming or outgoing interface of the rule according to the routing",
              )}
              onClick={() => setOffPath(true)}
            >
              {L("nach dem Weg nicht möglich", "not possible according to the path")} {rules?.impossible ?? ""}
            </button>
          </div>
        )}
        {rules == null ? (
          <Loading />
        ) : rules.items.length === 0 ? (
          <span className="faint">{L("keine Regeln", "no rules")}</span>
        ) : (
          <table className="compact" id="zone-cell-rules">
            <thead>
              <tr>
                <th>{L("Regel", "Rule")}</th>
                <th>{L("Aktion", "Action")}</th>
                <th>{L("Gerät", "Device")}</th>
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
                    <span className={`chip ${r.action === "allow" ? "accept" : "deny"}`}>
                      {r.action === "allow" ? L("erlaubt", "accept") : L("gesperrt", "deny")}
                    </span>
                  </td>
                  <td>
                    {r.source ?? "?"}
                    {r.vdom && r.vdom !== "root" ? ` · VDOM ${r.vdom}` : ""}
                  </td>
                  <td>
                    {r.violation && <span className="chip crit">{L("gegen Absicht", "against intent")}</span>}
                    {r.outside && r.outside.length > 0 && (
                      <div className="faint">{L(`Dienste ausserhalb: ${r.outside.join(", ")}`, `Services outside: ${r.outside.join(", ")}`)}</div>
                    )}
                    {r.impossible && <div className="faint zone-off-why">{r.impossible}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {rules && rules.total > rules.items.length && (
          <div className="faint">
            {L(`die ersten ${rules.items.length} von ${rules.total}`, `the first ${rules.items.length} of ${rules.total}`)}
          </div>
        )}
      </div>
    </div>
  );
}

// -- Zonenplan -----------------------------------------------------------------------------

// Ebenen (Spalten) und Elemente des Zonenplans (0.61.0, `store/plan_view`) – ausblenden je Betrachter
const PLAN_LAYERS: [string, string][] = [
  ["dc", "DC"],
  ["customer", L("Kunden", "Customers")],
  ["edge", "Edge"],
  ["transfer", "Transfer"],
  ["core", "Core"],
  ["distribution", "Distribution"],
  ["access", "Access"],
  ["public", "Public"],
  ["none", L("Knoten ohne Zone", "Nodes without zone")],
  ["outside", L("ausserhalb", "outside")],
];
const PLAN_ELEMENTS: [string, string][] = [
  ["nodes", L("Knoten in den Zonen", "Nodes in the zones")],
  ["labels", L("Namen der Übergänge", "Names of the transitions")],
  ["contacts", L("Kontakte zu Knoten ohne Zone", "Contacts to nodes without zone")],
  ["empty", L("Zonen ohne Knoten und Übergang", "Zones without nodes and transition")],
  ["intents", L("Absichten (Pfeile je Richtung)", "Intents (arrows per direction)")],
];
const PLAN_HIDDEN_KEY = "automato_plan_hidden";
type PlanHidden = { layers: string[]; hide: string[] };

function loadPlanHidden(): PlanHidden {
  try {
    const v = JSON.parse(localStorage.getItem(PLAN_HIDDEN_KEY) ?? "{}") as Partial<PlanHidden>;
    return { layers: Array.isArray(v.layers) ? v.layers : [], hide: Array.isArray(v.hide) ? v.hide : [] };
  } catch {
    return { layers: [], hide: [] };
  }
}

const PLAN_FLOWS_KEY = "automato_plan_flows";

function loadPlanFlows(): boolean {
  try {
    return localStorage.getItem(PLAN_FLOWS_KEY) === "1";
  } catch {
    return false;
  }
}

function storePlanFlows(v: boolean) {
  try {
    localStorage.setItem(PLAN_FLOWS_KEY, v ? "1" : "0");
  } catch {
    // ohne Speicher im Browser gilt die Auswahl nur bis zum Neuladen
  }
}

function storePlanHidden(v: PlanHidden) {
  try {
    localStorage.setItem(PLAN_HIDDEN_KEY, JSON.stringify(v));
  } catch {
    // ohne Speicher im Browser gilt die Auswahl nur bis zum Neuladen
  }
}

function PlanTab({ tenantId, isCustomer = false, canArrange = false }: { tenantId: number; isCustomer?: boolean; canArrange?: boolean }) {
  const [plan, setPlan] = useState<ZonePlan | null>(null);
  // "" Übersicht, "all" alle Zonen, sonst Kunde – vorgewählt nach dem Kunden-Filter der Kopfzeile
  const [view, setView] = useState<string>(() => (!isCustomer && getCustomerFilter() ? String(getCustomerFilter()) : ""));
  const [err, setErr] = useState<unknown>(null);
  // Filter (0.61.0): Suche bzw. Fokus mit Nachbarn, Ebenen und Elemente ausblenden – Ansicht und Export gleich
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [focus, setFocus] = useState<string[]>([]);
  const [depth, setDepth] = useState(1);
  const [hidden, setHidden] = useState<PlanHidden>(loadPlanHidden);
  const [flows, setFlows] = useState(loadPlanFlows); // 0.66.0: Kommunikation (Regeln) einblenden
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(q), 300);
    return () => window.clearTimeout(t);
  }, [q]);
  const filter: PlanFilter = { q: query, focus, depth, hideLayers: hidden.layers, hide: hidden.hide, flows };
  const planView: PlanView = { ...(view === "all" ? { all: true } : view ? { customerId: Number(view) } : {}), filter };
  const key = JSON.stringify(planView);
  useEffect(() => {
    let live = true;
    fetchZonePlan(tenantId, JSON.parse(key) as PlanView)
      .then((p) => live && setPlan(p))
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [tenantId, key, tick]);
  const toggle = (kind: keyof PlanHidden, value: string) =>
    setHidden((h) => {
      const next = { ...h, [kind]: h[kind].includes(value) ? h[kind].filter((x) => x !== value) : [...h[kind], value] };
      storePlanHidden(next);
      return next;
    });
  const clearFilter = () => {
    setQ("");
    setQuery("");
    setFocus([]);
    setDepth(1);
    setHidden({ layers: [], hide: [] });
    storePlanHidden({ layers: [], hide: [] });
  };
  if (err) return <ErrorBox error={err} onClose={() => setErr(null)} />;
  if (plan == null) return <Loading />;
  const own = plan.view?.mode === "customer";
  const f = plan.filter;
  if (plan.zones.length === 0 && !own && !f?.active)
    return <Note>{L("Noch keine Zonen – zuerst im Reiter „Zonen“ anlegen.", "No zones yet – create them first in the “Zones” tab.")}</Note>;
  const s = plan.summary;
  const hiddenNodes = plan.zones.reduce((n, z) => n + Math.max(0, (z.nodes_total ?? 0) - (z.nodes?.length ?? 0)), 0);
  const zoneName = new Map(plan.zones.map((z) => [String(z.id), z.name]));
  const choiceName = new Map((plan.zone_choices ?? []).map((c) => [c.id, c.name]));
  const transitions = (plan.transitions ?? []).filter((t) => !t.contact);
  const contacts = (plan.transitions ?? []).filter((t) => t.contact);
  const customers = plan.customers ?? [];
  const arrangeView = plan.arrangement?.view ?? null;
  const narrowed = Boolean(query.trim() || focus.length);
  const hiddenCount = hidden.layers.length + hidden.hide.length;
  const arrange = async (positions: Record<string, [number, number]>) => {
    if (!canArrange || !arrangeView) return;
    setBusy(true);
    try {
      await savePlanArrangement(tenantId, arrangeView, positions);
      setTick((t) => t + 1);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const resetArrangement = async () => {
    if (
      !arrangeView ||
      !window.confirm(
        L(
          "Anordnung dieser Ansicht zurücksetzen? Die Zonen stehen wieder nach Ebenen.",
          "Reset the arrangement of this view? The zones are placed by layer again.",
        ),
      )
    )
      return;
    setBusy(true);
    try {
      await resetPlanArrangement(tenantId, arrangeView);
      setTick((t) => t + 1);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="card">
        <div className="card-head">
          <h2>{L("Zonenplan", "Zone plan")}</h2>
          <span className="faint" id="zone-plan-summary">
            {L(
              <>
                {s.zones} Zonen · {s.nodes} Knoten · {s.transitions ?? 0} Übergänge
                {s.collapsed ? ` · ${s.collapsed} ${s.collapsed === 1 ? "Kunde" : "Kunden"} zusammengefasst` : ""}
                {s.unassigned ? ` · ${s.unassigned} Knoten ohne Zone` : ""}
                {s.without_node ? ` · ${s.without_node} Zonen ohne Knoten und Netze` : ""}
                {s.unzoned ? ` · ${s.unzoned} Netze ohne Zone` : ""}
              </>,
              <>
                {s.zones} zones · {s.nodes} nodes · {s.transitions ?? 0} transitions
                {s.collapsed ? ` · ${s.collapsed} ${s.collapsed === 1 ? "customer" : "customers"} collapsed` : ""}
                {s.unassigned ? ` · ${s.unassigned} nodes without zone` : ""}
                {s.without_node ? ` · ${s.without_node} zones without nodes and networks` : ""}
                {s.unzoned ? ` · ${s.unzoned} networks without zone` : ""}
              </>,
            )}
          </span>
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {!isCustomer && customers.length > 0 && (
              <select
                className="sel"
                id="zone-plan-view"
                aria-label={L("Ansicht", "View")}
                value={view}
                onChange={(e) => {
                  setView(e.target.value);
                  setFocus([]);
                  setPlan(null);
                }}
              >
                <option value="">{L("Übersicht – Kunden je als eine Zone", "Overview – customers each as one zone")}</option>
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>
                    {L(`Kunde ${c.name}`, `Customer ${c.name}`)}
                  </option>
                ))}
                {own && plan.view?.customer_id != null && !customers.some((c) => c.id === plan.view?.customer_id) && (
                  <option value={plan.view.customer_id}>
                    {L(
                      `Kunde ${plan.view.customer ?? plan.view.customer_id} (ohne Zonen)`,
                      `Customer ${plan.view.customer ?? plan.view.customer_id} (no zones)`,
                    )}
                  </option>
                )}
                <option value="all">{L("alle Zonen einzeln", "all zones individually")}</option>
              </select>
            )}
            <ExportButtons tenantId={tenantId} view={planView} />
            {canArrange && <PlanImport tenantId={tenantId} onDone={() => setTick((t) => t + 1)} />}
          </span>
        </div>
        <div className="card-body scroll">
          <div className="plan-filter" id="zone-plan-filter">
            <input
              className="input"
              type="search"
              aria-label={L("Im Plan suchen", "Search in the plan")}
              placeholder={L("Suchen: Zone, Kunde, Firewall/VRF, Übergang, Netz", "Search: zone, customer, firewall/VRF, transition, network")}
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <select
              className="sel"
              aria-label={L("Fokus auf Zone", "Focus on zone")}
              value=""
              onChange={(e) => e.target.value && setFocus((list) => [...list, e.target.value])}
            >
              <option value="">{L("Fokus auf Zone …", "Focus on zone …")}</option>
              {(plan.zone_choices ?? [])
                .filter((c) => !focus.includes(c.id))
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
            </select>
            {narrowed && (
              <select className="sel" aria-label={L("Nachbarn", "Neighbors")} value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
                <option value={0}>{L("nur die Treffer", "only the matches")}</option>
                <option value={1}>{L("+ direkte Nachbarn", "+ direct neighbors")}</option>
                <option value={2}>{L("+ 2 Übergänge weit", "+ 2 transitions away")}</option>
                <option value={3}>{L("+ 3 Übergänge weit", "+ 3 transitions away")}</option>
              </select>
            )}
            <details className="plan-layers" id="zone-plan-layers">
              <summary className="btn btn-sm">
                {L(`Ebenen${hiddenCount ? ` (${hiddenCount} ausgeblendet)` : ""}`, `Layers${hiddenCount ? ` (${hiddenCount} hidden)` : ""}`)}
              </summary>
              <div className="plan-layers-body">
                <div>
                  <div className="faint">{L("Spalten", "Columns")}</div>
                  {PLAN_LAYERS.map(([k, label]) => (
                    <label key={k} className="check">
                      <input type="checkbox" checked={!hidden.layers.includes(k)} onChange={() => toggle("layers", k)} /> {label}
                    </label>
                  ))}
                </div>
                <div>
                  <div className="faint">{L("Elemente", "Elements")}</div>
                  {PLAN_ELEMENTS.map(([k, label]) => (
                    <label key={k} className="check">
                      <input type="checkbox" checked={!hidden.hide.includes(k)} onChange={() => toggle("hide", k)} /> {label}
                    </label>
                  ))}
                </div>
              </div>
            </details>
            <label
              className="check"
              id="zone-plan-flows"
              title={L(
                "Erlaubende Regeln je Zonenpaar als eigene (blaue, gepunktete) Linie – auch zwischen Zonen ohne Übergang; mit Fokus bzw. Suche kommen die Zonen dazu, mit denen die Treffer kommunizieren; im Plan eines Kunden die anderen Kunden einzeln",
                "Allowing rules per zone pair as a separate (blue, dotted) line – also between zones without a transition; with focus or search the zones the matches communicate with are added; in the plan of a customer the other customers individually",
              )}
            >
              <input
                type="checkbox"
                checked={flows}
                onChange={(e) => {
                  setFlows(e.target.checked);
                  storePlanFlows(e.target.checked);
                }}
              />{" "}
              {L("Kommunikation (Regeln)", "Communication (rules)")}
            </label>
            {(f?.active || q) && (
              <button className="btn btn-sm" onClick={clearFilter}>
                {L("Filter zurücksetzen", "Reset filter")}
              </button>
            )}
          </div>
          {focus.length > 0 && (
            <div className="plan-focus" id="zone-plan-focus">
              <span className="faint">{L("Fokus:", "Focus:")}</span>
              {focus.map((id) => (
                <span key={id} className="chip">
                  {choiceName.get(id) ?? id}{" "}
                  <button
                    className="linkbtn"
                    aria-label={L(`Fokus ${choiceName.get(id) ?? id} entfernen`, `Remove focus ${choiceName.get(id) ?? id}`)}
                    onClick={() => setFocus((list) => list.filter((x) => x !== id))}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          {f?.active && (
            <p className="faint" id="zone-plan-filtered" style={{ margin: "0 0 6px" }}>
              {L(
                <>
                  {f.zones} von {f.total} Zonen gezeigt{f.label ? ` · ${f.label}` : ""}
                  {narrowed && f.matches === 0 ? " · kein Treffer" : ""} – der Export zeigt dasselbe.
                </>,
                <>
                  {f.zones} of {f.total} zones shown{f.label ? ` · ${f.label}` : ""}
                  {narrowed && f.matches === 0 ? " · no match" : ""} – the export shows the same.
                </>,
              )}
            </p>
          )}
          {own && (
            <p className="faint" style={{ margin: "4px 0 8px" }} id="zone-plan-customer">
              {L(
                <>
                  Zonen von {plan.view?.customer ?? "Kunde"}, die genutzten Zugänge zu anderen Kunden (je als eine Zone, mit
                  „Kommunikation“ die Zonen der Partner einzeln) und von
                  den geteilten Zonen nur die Firewalls/VRFs, die der Kunde braucht – an seinen Netzen und auf den Wegen zu den
                  Zonen seiner Regeln
                  {hiddenNodes > 0 ? ` (${hiddenNodes} weitere Knoten ausgeblendet – „alle Zonen einzeln“ zeigt sie)` : ""}.
                  {plan.zones.length === 0 && !f?.active && " Dem Kunden sind keine Zonen zugeordnet."}
                </>,
                <>
                  Zones of {plan.view?.customer ?? "customer"}, the used accesses to other customers (each as one zone, with
                  “Communication” the zones of the partners individually) and of
                  the shared zones only the firewalls/VRFs the customer needs – at its networks and on the paths to the
                  zones of its rules
                  {hiddenNodes > 0 ? ` (${hiddenNodes} more nodes hidden – “all zones individually” shows them)` : ""}.
                  {plan.zones.length === 0 && !f?.active && " No zones are assigned to the customer."}
                </>,
              )}
            </p>
          )}
          <p className="faint plan-arrange" id="zone-plan-arrange">
            {canArrange && arrangeView
              ? L(
                  "Zonen lassen sich ziehen – die Anordnung gilt für diese Ansicht, Export und neue Versionen folgen.",
                  "Zones can be dragged – the arrangement applies to this view, export and new versions follow.",
                )
              : L("Zonen lassen sich ziehen (nur hier, nicht gespeichert).", "Zones can be dragged (only here, not saved).")}{" "}
            {L("Doppelklick auf eine Zone setzt den Fokus.", "Double-click on a zone sets the focus.")}
            {plan.arrangement?.saved && (
              <>
                {" "}
                {L(
                  <>
                    Anordnung gespeichert ({plan.arrangement.count} Zonen
                    {plan.arrangement.updated_by ? `, ${plan.arrangement.updated_by}` : ""}
                    {plan.arrangement.updated_at ? `, ${fmtTime(plan.arrangement.updated_at)}` : ""}).
                  </>,
                  <>
                    Arrangement saved ({plan.arrangement.count} zones
                    {plan.arrangement.updated_by ? `, ${plan.arrangement.updated_by}` : ""}
                    {plan.arrangement.updated_at ? `, ${fmtTime(plan.arrangement.updated_at)}` : ""}).
                  </>,
                )}{" "}
                {canArrange && (
                  <button className="btn btn-sm" disabled={busy} onClick={resetArrangement}>
                    {L("Anordnung zurücksetzen", "Reset arrangement")}
                  </button>
                )}
              </>
            )}
          </p>
          {plan.layout && plan.layout.zones.length > 0 ? (
            <PlanDrawing
              layout={plan.layout}
              onArrange={canArrange && arrangeView ? arrange : undefined}
              onFocus={(id) => setFocus((list) => (list.includes(id) ? list : [...list, id]))}
            />
          ) : (
            f?.active && <Note>{L("Keine Zone passt zum Filter.", "No zone matches the filter.")}</Note>
          )}
          {(plan.layout?.edges ?? []).some((e) => e.ab || e.ba) && (
            <p className="faint zone-legend" id="zone-plan-intents">
              {L("Pfeil = Absicht in diese Richtung:", "Arrow = intent in this direction:")}{" "}
              {(Object.keys(INTENT) as ZoneIntent[]).map((k) => (
                <span key={k} className="nowrap">
                  <span className="intent-swatch" style={{ background: INTENT_COLOR[k] }} /> {INTENT[k][1]}{" "}
                </span>
              ))}
              {L(
                "· farbig gestrichelt = Absicht zwischen Zonen ohne Übergang · ohne Pfeil = keine Absicht (in der Zonenmatrix setzen)",
                "· colored dashed = intent between zones without a transition · no arrow = no intent (set it in the zone matrix)",
              )}
            </p>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>{L("Übergänge", "Transitions")}</h2>
          <span className="faint">
            {L(
              "Netze zwischen Knoten verschiedener Zonen – L3Outs im ACI, Interfaces der Firewalls, Border-Router",
              "Networks between nodes of different zones – L3Outs in the ACI, interfaces of the firewalls, border routers",
            )}
          </span>
        </div>
        <div className="card-body scroll">
          {transitions.length === 0 ? (
            <span className="faint">
              {L(
                "keine Übergänge – Knoten über VDOM bzw. ACI-Tenant/VRF den Zonen zuordnen",
                "no transitions – assign nodes to the zones via VDOM or ACI tenant/VRF",
              )}
            </span>
          ) : (
            <TransitionTable transitions={transitions} zoneName={zoneName} id="zone-transitions" />
          )}
        </div>
      </div>

      {(plan.intents ?? []).length > 0 && (
        <div className="card" id="zone-plan-intent-lines">
          <div className="card-head">
            <h2>{L("Absichten ohne Übergang", "Intents without transition")}</h2>
            <span className="faint">
              {L(
                "Zonen ohne gemeinsames Netz – die Absicht gilt trotzdem (Regeln über mehrere Übergänge)",
                "Zones without a common network – the intent applies anyway (rules across several transitions)",
              )}
            </span>
          </div>
          <div className="card-body scroll">
            <table className="compact">
              <thead>
                <tr>
                  <th>{L("Zonen", "Zones")}</th>
                  <th>{L("Absicht je Richtung", "Intent per direction")}</th>
                </tr>
              </thead>
              <tbody>
                {(plan.intents ?? []).map((i) => (
                  <tr key={i.id} data-intent-line={i.id}>
                    <td className="nowrap">{`${zoneName.get(String(i.a)) ?? i.a} ↔ ${zoneName.get(String(i.b)) ?? i.b}`}</td>
                    <td>
                      <DirectionChips a={i.a} b={i.b} ab={i.intent_ab} ba={i.intent_ba} zoneName={zoneName} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {contacts.length > 0 && (
        <div className="card" id="zone-contacts">
          <div className="card-head">
            <h2>{L("Knoten ohne Zone an Netzen einer Zone", "Nodes without zone at networks of a zone")}</h2>
            <span className="faint">
              {L(
                "kein Übergang – das Netz bleibt in seiner Zone; den Knoten einer Zone zuordnen",
                "no transition – the network stays in its zone; assign the node to a zone",
              )}
            </span>
          </div>
          <div className="card-body scroll">
            <TransitionTable transitions={contacts} zoneName={zoneName} />
          </div>
        </div>
      )}

      {(plan.conflicts ?? []).length > 0 && (
        <Note warn>
          {L(
            "Knoten in mehreren Zonen (es gilt die erste in der Reihenfolge):",
            "Nodes in several zones (the first one in the order applies):",
          )}{" "}
          {(plan.conflicts ?? []).map((c) => `${c.label} (${c.zones.join(", ")})`).join("; ")}
        </Note>
      )}

      {!isCustomer && (
        <div className="card" id="zone-unzoned">
          <div className="card-head">
            <h2>{L("Netze ohne Zone", "Networks without zone")}</h2>
            <span className="faint">
              {L(
                "an Interfaces und L3Outs gelesen, an Knoten ohne Zone und keiner Zone zugeordnet",
                "collected at interfaces and L3Outs, at nodes without zone and assigned to no zone",
              )}
            </span>
          </div>
          <div className="card-body scroll">
            {plan.unzoned.total === 0 ? (
              <span className="faint">
                {L("alle gelesenen Netze gehören zu einer Zone bzw. einem Übergang", "all collected networks belong to a zone or a transition")}
              </span>
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
              <div className="faint">
                {L(`die ersten ${plan.unzoned.items.length} von ${plan.unzoned.total}`, `the first ${plan.unzoned.items.length} of ${plan.unzoned.total}`)}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

// 0.78.0: Absicht je Richtung als Chips („Office → DC gesperrt“)
function DirectionChips({ a, b, ab, ba, zoneName }: { a: PlanZoneId; b: PlanZoneId; ab?: PlanDirection | null; ba?: PlanDirection | null; zoneName: Map<string, string> }) {
  const items = [
    [a, b, ab],
    [b, a, ba],
  ] as const;
  const shown = items.filter(([, , d]) => d);
  if (!shown.length) return <span className="faint">–</span>;
  return (
    <span className="plan-directions">
      {shown.map(([x, y, d]) => (
        <span key={`${x}>${y}`} className="nowrap" data-direction={`${x}>${y}`} title={d?.note ?? undefined}>
          {zoneName.get(String(x)) ?? x} → {zoneName.get(String(y)) ?? y}{" "}
          <span className={`chip ${INTENT[d!.intent][0]}`}>{INTENT[d!.intent][1]}</span>
          {d!.services.length > 0 && <span className="faint mono"> {d!.services.join(", ")}</span>}
          {d!.mixed && <span className="faint">{L(" (teils weniger streng)", " (partly less strict)")}</span>}
        </span>
      ))}
    </span>
  );
}

function TransitionTable({ transitions, zoneName, id }: { transitions: PlanTransition[]; zoneName: Map<string, string>; id?: string }) {
  const intents = transitions.some((t) => t.intent_ab || t.intent_ba);
  return (
    <table className="compact" id={id}>
      <thead>
        <tr>
          <th>{L("Übergang", "Transition")}</th>
          <th>{L("Zonen", "Zones")}</th>
          {intents && <th>{L("Absicht", "Intent")}</th>}
          <th>{L("Netz", "Network")}</th>
          <th>{L("über", "via")}</th>
        </tr>
      </thead>
      <tbody>
        {transitions.map((t) =>
          (t.networks.length ? t.networks : [null]).map((n, i) => (
            <tr key={`${t.id}-${n?.network ?? i}`} data-transition={t.name}>
              <td className="nowrap">
                {i === 0 && (
                  <>
                    <b className="mono">{t.contact ? "–" : t.name}</b> {t.l3out && <span className="chip na">L3Out</span>}
                  </>
                )}
              </td>
              <td className="nowrap">{i === 0 && `${zoneName.get(String(t.a)) ?? t.a} ↔ ${zoneName.get(String(t.b)) ?? t.b}`}</td>
              {intents && <td>{i === 0 && <DirectionChips a={t.a} b={t.b} ab={t.intent_ab} ba={t.intent_ba} zoneName={zoneName} />}</td>}
              <td className="mono">{n ? n.network : <span className="faint">{t.count ? L(`${t.count} Netze`, `${t.count} networks`) : "–"}</span>}</td>
              <td className="faint">{n ? legsText(n) : ""}</td>
            </tr>
          )),
        )}
      </tbody>
    </table>
  );
}

// Zeichnung aus dem Layout des Servers (`plan_export.layout`) – dieselbe wie im Export. Seit 0.56.0
// (Format 2) Zonen in Spalten nach Ebene (DC links, Public rechts) mit ihren Knoten, Übergänge als Linien
// beschriftet mit dem Namen („DC-EINET“), gestrichelt zu „Knoten ohne Zone“ bzw. „ausserhalb“; Versionen
// bis 0.55.0 im alten Layout (Zonen links/rechts, Knoten in der Mitte). Seit 0.61.0 lassen sich die Zonen
// ziehen (`onArrange` speichert die Anordnung), Doppelklick setzt den Fokus.
export function PlanDrawing({
  layout,
  id = "zone-plan",
  onArrange,
  onFocus,
}: {
  layout: PlanLayout;
  id?: string;
  onArrange?: (positions: Record<string, [number, number]>) => void;
  onFocus?: (zoneId: string) => void;
}) {
  if (layout.format === 2) return <PlanDrawing2 layout={layout} id={id} onArrange={onArrange} onFocus={onFocus} />;
  return (
    <svg className="zone-plan" id={id} viewBox={`0 0 ${layout.width} ${layout.height}`} width="100%" role="img" aria-label={L("Zonenplan", "Zone plan")}>
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

// Linien der Übergänge nach der Lage der Zonen – gleich wie `plan_export.route` im Orchestrator (seit 0.65.1
// weich): die Linie verlässt die Zone senkrecht an der Seite zur anderen Zone (nebeneinander rechts/links,
// übereinander unten/oben, sonst rechts herum) und läuft als Kurve hinüber; Anschlusspunkte je Seite nach der
// Lage der Gegenseite geordnet
type Side = "r" | "l" | "t" | "b";
const SIDE_NORMAL: Record<Side, [number, number]> = { r: [1, 0], l: [-1, 0], t: [0, -1], b: [0, 1] };
const SIDE_GAP = 12;
const CURVE_STEPS = 16;

function edgeSides(a: PlanBox, b: PlanBox, boxes: PlanBox[]): [Side, Side] {
  if (a.x + a.w + SIDE_GAP <= b.x) return ["r", "l"];
  if (b.x + b.w + SIDE_GAP <= a.x) return ["l", "r"];
  const [up, low] = a.y <= b.y ? [a, b] : [b, a];
  if (up.y + up.h + SIDE_GAP <= low.y) {
    const x0 = Math.max(up.x, low.x);
    const x1 = Math.min(up.x + up.w, low.x + low.w);
    const y0 = up.y + up.h;
    const y1 = low.y;
    const blocked = boxes.some((o) => o !== a && o !== b && o.x < x1 && o.x + o.w > x0 && o.y < y1 && o.y + o.h > y0);
    if (!blocked) return up === a ? ["b", "t"] : ["t", "b"];
  }
  return ["r", "r"];
}

const round1 = (v: number) => Math.round(v * 10) / 10;

function bezier(p0: [number, number], c1: [number, number], c2: [number, number], p3: [number, number], t: number): [number, number] {
  const u = 1 - t;
  const at = (i: 0 | 1) => u ** 3 * p0[i] + 3 * u * u * t * c1[i] + 3 * u * t * t * c2[i] + t ** 3 * p3[i];
  return [round1(at(0)), round1(at(1))];
}

export function routeEdges(boxes: PlanBox[], edges: PlanEdge[]): PlanEdge[] {
  // gleiche Box-Objekte wie in `boxes` (Hindernisse vergleichen über die Identität)
  const pos = new Map(boxes.map((b) => [String(b.id), b]));
  const ends = new Map<string, [number, string][]>();
  const plans: { e: PlanEdge; a: PlanBox; b: PlanBox; sa: Side; sb: Side }[] = [];
  const center = (box: PlanBox): [number, number] => [box.x + box.w / 2, box.y + box.h / 2];
  for (const e of edges) {
    const a = pos.get(String(e.a));
    const b = pos.get(String(e.b));
    if (!a || !b || a === b) continue;
    const [sa, sb] = edgeSides(a, b, boxes);
    plans.push({ e, a, b, sa, sb });
    for (const [box, side, other] of [
      [a, sa, b],
      [b, sb, a],
    ] as const) {
      const [cx, cy] = center(other);
      const k = `${box.id}|${side}`;
      ends.set(k, [...(ends.get(k) ?? []), [side === "l" || side === "r" ? cy : cx, String(e.id)]]);
    }
  }
  const order = new Map(
    [...ends].map(([k, v]) => [k, [...v].sort((x, y) => x[0] - y[0] || (x[1] < y[1] ? -1 : x[1] > y[1] ? 1 : 0)).map((x) => x[1])]),
  );
  const at = (box: PlanBox, side: Side, eid: string): [number, number] => {
    const ids = order.get(`${box.id}|${side}`) ?? [];
    const k = ids.indexOf(eid);
    const n = ids.length;
    if (side === "l" || side === "r") return [side === "r" ? box.x + box.w : box.x, box.y + 14 + ((box.h - 20) * (k + 1)) / (n + 1)];
    return [box.x + 24 + ((box.w - 48) * (k + 1)) / (n + 1), side === "t" ? box.y : box.y + box.h];
  };
  return plans.map(({ e, a, b, sa, sb }, n) => {
    const p0 = at(a, sa, String(e.id));
    const p3 = at(b, sb, String(e.id));
    const [x1, y1] = p0;
    const [x2, y2] = p3;
    const reach = sa === sb ? 40 + (n % 4) * 10 + Math.abs(y2 - y1) * 0.12 : Math.min(Math.max(Math.hypot(x2 - x1, y2 - y1) * 0.4, 24), 160);
    const c1: [number, number] = [x1 + SIDE_NORMAL[sa][0] * reach, y1 + SIDE_NORMAL[sa][1] * reach];
    const c2: [number, number] = [x2 + SIDE_NORMAL[sb][0] * reach, y2 + SIDE_NORMAL[sb][1] * reach];
    const points = Array.from({ length: CURVE_STEPS + 1 }, (_, i) => bezier(p0, c1, c2, p3, i / CURVE_STEPS));
    const [mx, my] = bezier(p0, c1, c2, p3, 0.5);
    const across = (sa === "l" || sa === "r") && (sb === "l" || sb === "r") && sa !== sb;
    return across
      ? { ...e, points, x1, y1, x2, y2, lx: mx, ly: my - 5, anchor: "middle" as const }
      : { ...e, points, x1, y1, x2, y2, lx: mx + 6, ly: my + 4, anchor: "start" as const };
  });
}

// 0.78.0: Farben der Absicht – wie im Export und im Grundschema
const INTENT_COLOR: Record<ZoneIntent, string> = { allow: "#2d7d46", limited: "#d79b00", deny: "#b85450" };
const INTENT_RANK: Record<ZoneIntent, number> = { allow: 1, limited: 2, deny: 3 };
const ARROW_LEN = 10;
const ARROW_W = 4.5;

// Pfeilspitzen der Absicht je Richtung (gleich wie `plan_export.arrows`): `ab` am Ende (Zone b),
// `ba` am Anfang, entlang des letzten bzw. ersten Stücks der Linie
export function intentArrows(e: PlanEdge): { dir: "ab" | "ba"; intent: ZoneIntent; points: [number, number][] }[] {
  const pts = e.points ?? [];
  if (pts.length < 2) return [];
  const out: { dir: "ab" | "ba"; intent: ZoneIntent; points: [number, number][] }[] = [];
  for (const [dir, tip, back] of [
    ["ab", pts[pts.length - 1], pts[pts.length - 2]],
    ["ba", pts[0], pts[1]],
  ] as const) {
    const intent = e[dir];
    if (!intent) continue;
    const dx = tip[0] - back[0];
    const dy = tip[1] - back[1];
    const n = Math.hypot(dx, dy) || 1;
    const [ux, uy] = [dx / n, dy / n];
    const [bx, by] = [tip[0] - ux * ARROW_LEN, tip[1] - uy * ARROW_LEN];
    out.push({
      dir,
      intent,
      points: [
        [tip[0], tip[1]],
        [bx - uy * ARROW_W, by + ux * ARROW_W],
        [bx + uy * ARROW_W, by - ux * ARROW_W],
      ],
    });
  }
  return out;
}

function edgeStroke(e: PlanEdge): string | undefined {
  if (!e.intent) return undefined;
  const worst = [e.ab, e.ba].filter((x): x is ZoneIntent => !!x).sort((x, y) => INTENT_RANK[y] - INTENT_RANK[x])[0];
  return worst ? INTENT_COLOR[worst] : undefined;
}

function edgeTitle(e: PlanEdge): string {
  if (e.flow) return e.name ?? "";
  if (e.intent) return L(`Absicht ${e.intent_text ?? ""}`, `Intent ${e.intent_text ?? ""}`);
  const text = `${e.name || e.label || L("Kontakt", "Contact")}: ${(e.networks ?? []).join(", ")}`;
  return e.intent_text ? L(`${text} – Absicht ${e.intent_text}`, `${text} – intent ${e.intent_text}`) : text;
}

function PlanDrawing2({
  layout,
  id,
  onArrange,
  onFocus,
}: {
  layout: PlanLayout;
  id: string;
  onArrange?: (positions: Record<string, [number, number]>) => void;
  onFocus?: (zoneId: string) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<{ id: string; dx: number; dy: number; cx: number; cy: number; moved: boolean } | null>(null);
  const [moved, setMoved] = useState<Record<string, [number, number]>>({});
  const movedRef = useRef<Record<string, [number, number]>>({}); // auch vor dem nächsten Rendern aktuell
  const [dragging, setDragging] = useState<string | null>(null);
  useEffect(() => {
    movedRef.current = {};
    setMoved({});
  }, [layout]);
  const boxes = layout.zones.map((z) => (moved[String(z.id)] ? { ...z, x: moved[String(z.id)][0], y: moved[String(z.id)][1] } : z));
  const local = Object.keys(moved).length > 0;
  const edges = local ? routeEdges(boxes, layout.edges) : layout.edges;
  // Ausschnitt: während des Ziehens fest (sonst springt die Skalierung), danach um alle Zonen und Linien
  let view = `0 0 ${layout.width} ${layout.height}`;
  if (local && !dragging) {
    const xs = boxes.flatMap((b) => [b.x, b.x + b.w]).concat(edges.flatMap((e) => (e.points ?? []).map(([x]) => x + 60)));
    const ys = boxes.flatMap((b) => [b.y, b.y + b.h]);
    const x0 = Math.min(0, ...xs.map((x) => x - 16));
    const y0 = Math.min(0, ...ys.map((y) => y - 12));
    view = `${x0} ${y0} ${Math.max(...xs) + 16 - x0} ${Math.max(...ys) + 24 - y0}`;
  }
  const point = (e: ReactPointerEvent): { x: number; y: number } => {
    const svg = svgRef.current;
    const m = svg?.getScreenCTM();
    if (!svg || !m) return { x: e.clientX, y: e.clientY };
    const p = svg.createSVGPoint();
    p.x = e.clientX;
    p.y = e.clientY;
    const r = p.matrixTransform(m.inverse());
    return { x: r.x, y: r.y };
  };
  const down = (e: ReactPointerEvent, box: PlanBox) => {
    if (e.button !== 0) return;
    e.preventDefault(); // keine Textauswahl bzw. kein Ziehen des Browsers (bricht den Zeiger sonst ab)
    const p = point(e);
    drag.current = { id: String(box.id), dx: p.x - box.x, dy: p.y - box.y, cx: e.clientX, cy: e.clientY, moved: false };
  };
  const move = (e: ReactPointerEvent) => {
    const d = drag.current;
    if (!d) return;
    if (e.buttons === 0) {
      // Taste schon losgelassen (z. B. ausserhalb des Plans): kein Ziehen mehr
      drag.current = null;
      setDragging(null);
      return;
    }
    if (!d.moved && Math.hypot(e.clientX - d.cx, e.clientY - d.cy) < 4) return;
    // erst beim Ziehen festhalten – sonst kommt der Doppelklick nicht mehr bei der Zone an
    if (!d.moved) svgRef.current?.setPointerCapture?.(e.pointerId);
    d.moved = true;
    setDragging(d.id);
    const p = point(e);
    movedRef.current = { ...movedRef.current, [d.id]: [Math.round(p.x - d.dx), Math.round(p.y - d.dy)] };
    setMoved(movedRef.current);
  };
  const up = () => {
    const d = drag.current;
    drag.current = null;
    setDragging(null);
    if (!d?.moved || !onArrange) return;
    // alle Zonen der Ansicht festhalten (sonst ordnet das Layout die übrigen neu), in gespeicherten Koordinaten
    const [ox, oy] = layout.offset ?? [0, 0];
    const positions: Record<string, [number, number]> = {};
    for (const b of layout.zones) {
      const [x, y] = movedRef.current[String(b.id)] ?? [b.x, b.y];
      positions[String(b.id)] = [Math.round(x - ox), Math.round(y - oy)];
    }
    onArrange(positions);
  };
  // Abbruch durch den Browser: die Zone springt zurück, nichts wird gespeichert
  const cancel = () => {
    const d = drag.current;
    drag.current = null;
    setDragging(null);
    if (!d?.moved) return;
    const { [d.id]: _, ...rest } = movedRef.current;
    movedRef.current = rest;
    setMoved(rest);
  };
  return (
    <svg
      ref={svgRef}
      className={`zone-plan ${dragging ? "dragging" : ""}`}
      id={id}
      viewBox={view}
      width="100%"
      role="img"
      aria-label={L("Zonenplan", "Zone plan")}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={cancel}
    >
      {edges.map((e) => (
        <g
          key={e.id}
          className={`zp-edge ${e.contact ? "contact" : ""} ${e.flow ? "flow" : ""} ${e.intent ? "intent" : ""}`}
          data-flow={e.flow ? `${e.a}~${e.b}` : undefined}
          data-intent={e.ab || e.ba ? `${e.ab ?? ""}|${e.ba ?? ""}` : undefined}
        >
          <polyline
            points={(e.points ?? []).map(([x, y]) => `${x},${y}`).join(" ")}
            strokeDasharray={e.flow ? "2 4" : e.intent ? "6 3" : e.dashed ? "5 4" : undefined}
            style={e.intent ? { stroke: edgeStroke(e) } : undefined}
          >
            <title>{edgeTitle(e)}</title>
          </polyline>
          {intentArrows(e).map((a) => (
            <polygon key={a.dir} className="zp-arrow" data-dir={a.dir} points={a.points.map(([x, y]) => `${x},${y}`).join(" ")} fill={INTENT_COLOR[a.intent]}>
              <title>{edgeTitle(e)}</title>
            </polygon>
          ))}
          {e.label && (
            <text x={e.lx} y={e.ly} textAnchor={e.anchor ?? ((e.points ?? []).length > 2 ? "start" : "middle")} style={e.intent ? { fill: edgeStroke(e) } : undefined}>
              {e.label}
            </text>
          )}
        </g>
      ))}
      {boxes.map((z) => (
        <g
          key={z.id}
          className={`zp-zone2 draggable ${z.pseudo ? "pseudo" : ""} ${z.collapsed ? "collapsed" : ""} ${z.partner ? "partner" : ""} ${dragging === String(z.id) ? "dragging" : ""}`}
          data-zone={String(z.id)}
          data-x={Math.round(z.x)}
          data-y={Math.round(z.y)}
          onPointerDown={(e) => down(e, z)}
          onDoubleClick={() => onFocus?.(String(z.id))}
        >
          <title>{L(`${z.title} – ziehen zum Anordnen, Doppelklick = Fokus`, `${z.title} – drag to arrange, double-click = focus`)}</title>
          <rect x={z.x} y={z.y} width={z.w} height={z.h} rx={8} style={zoneStyle(z.color)} />
          <text x={z.x + 12} y={z.y + 18} className="zp-title">
            {z.title}
            {z.code && <tspan className="zp-code"> {z.code}</tspan>}
          </text>
          <text x={z.x + 12} y={z.y + 33} className="zp-sub">
            {z.sub}
          </text>
          {(z.lines ?? []).map((line, k) => (
            <text key={k} x={z.x + 12} y={z.y + 52 + k * 15} className="zp-line">
              {line}
            </text>
          ))}
        </g>
      ))}
    </svg>
  );
}

// Helle Farben (Pastell der Vorlage) kräftiger füllen, dunkle wie bisher mit 22 % – beides lesbar im
// hellen und dunklen Design; Rahmen in der Zonenfarbe bzw. neutral
function zoneStyle(color: string | null | undefined): CSSProperties {
  if (!color) return { fill: "var(--surface-3)", fillOpacity: 0.6 };
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(color);
  const light = m ? (0.299 * parseInt(m[1], 16) + 0.587 * parseInt(m[2], 16) + 0.114 * parseInt(m[3], 16)) / 255 > 0.8 : false;
  return light ? { fill: color, fillOpacity: 0.55 } : { fill: color, fillOpacity: 0.22, stroke: color };
}

// Export-Knöpfe: draw.io (bearbeitbar), SVG, PDF (mit Matrix und Tabellen) – aktueller Stand (in der gewählten
// Ansicht) bzw. Version
function ExportButtons({ tenantId, versionId, view }: { tenantId: number; versionId?: number; view?: PlanView }) {
  const [busy, setBusy] = useState<PlanFormat | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const run = async (format: PlanFormat) => {
    setBusy(format);
    setErr(null);
    try {
      const { blob, filename } = await exportZonePlan(tenantId, format, versionId, view);
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
          ["docx", "Word"],
        ] as const
      ).map(([f, label]) => (
        <button key={f} className="btn btn-sm" disabled={busy !== null} onClick={() => run(f)}>
          {busy === f ? "…" : label}
        </button>
      ))}
      <button
        className="btn btn-sm"
        id="zone-plan-confluence"
        disabled={busy !== null}
        title={L(
          "Word mit Zonen, Übergängen und Matrix plus der Plan als draw.io – zum Verbessern und Importieren in Confluence",
          "Word with zones, transitions and matrix plus the plan as draw.io – to refine and import into Confluence",
        )}
        onClick={() => run("zip")}
      >
        {busy === "zip" ? "…" : L("Für Confluence (Word + draw.io)", "For Confluence (Word + draw.io)")}
      </button>
      <ExportLangSelect />
    </span>
  );
}

// Zonenplan aus draw.io zurück einlesen (0.83.0, Operator): Datei wählen, Vorschau, Teile übernehmen
const PLAN_MAX_BYTES = 2_000_000;

function PlanImport({ tenantId, onDone }: { tenantId: number; onDone: () => void }) {
  const file = useRef<HTMLInputElement>(null);
  const [upload, setUpload] = useState<{ content: string; name: string; preview: PlanImportPreview } | null>(null);
  const [parts, setParts] = useState<PlanImportParts>({ positions: true, renames: true, intents: true, new_zones: false });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);
  const pick = async (f: File | undefined) => {
    if (!f) return;
    setErr(null);
    setDone(null);
    try {
      if (f.size > PLAN_MAX_BYTES) throw new Error(L("Zeichnung zu gross (höchstens 2 MB)", "Drawing too large (at most 2 MB)"));
      const content = await f.text();
      setUpload({ content, name: f.name, preview: await previewPlanImport(tenantId, content) });
      setParts({ positions: true, renames: true, intents: true, new_zones: false });
    } catch (e) {
      setErr(e);
    } finally {
      if (file.current) file.current.value = "";
    }
  };
  const apply = async () => {
    if (!upload) return;
    setBusy(true);
    try {
      const r = await applyPlanImport(tenantId, upload.content, parts);
      const said = [
        r.positions ? L(`${r.positions} Zonen angeordnet`, `${r.positions} zones arranged`) : "",
        r.renames.length ? L(`${r.renames.length} umbenannt`, `${r.renames.length} renamed`) : "",
        r.intents.length ? L(`${r.intents.length} Absichten`, `${r.intents.length} intents`) : "",
        r.new_zones.length ? L(`${r.new_zones.length} neue Zonen`, `${r.new_zones.length} new zones`) : "",
        ...r.skipped,
      ].filter(Boolean);
      setDone(said.join(" · ") || L("nichts geändert", "nothing changed"));
      setUpload(null);
      onDone();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const p = upload?.preview;
  const check = (k: keyof PlanImportParts, label: string, n: number) => (
    <label className="check">
      <input type="checkbox" id={`zone-plan-import-${k}`} checked={parts[k]} disabled={!n} onChange={(e) => setParts({ ...parts, [k]: e.target.checked })} />{" "}
      {label}
    </label>
  );
  return (
    <span className="plan-import">
      <input ref={file} type="file" accept=".drawio,.xml" id="zone-plan-import-file" style={{ display: "none" }} onChange={(e) => pick(e.target.files?.[0])} />
      <button
        className="btn btn-sm"
        id="zone-plan-import"
        title={L(
          "Den exportierten Plan nach dem Bearbeiten in draw.io zurück einlesen: Lage, Namen, neue Pfeile als Absicht",
          "Read the exported plan back after editing it in draw.io: position, names, new arrows as intent",
        )}
        onClick={() => file.current?.click()}
      >
        {L("draw.io einlesen…", "Import draw.io…")}
      </button>
      {done && (
        <span className="faint" id="zone-plan-import-done">
          {done}
        </span>
      )}
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {p && upload && (
        <Modal
          wide
          title={L(`Zonenplan aus draw.io: ${upload.name}`, `Zone plan from draw.io: ${upload.name}`)}
          onClose={() => setUpload(null)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setUpload(null)}>
                {L("Abbrechen", "Cancel")}
              </button>
              <button className="btn btn-primary" id="zone-plan-import-apply" onClick={apply} disabled={busy}>
                {L("Übernehmen", "Apply")}
              </button>
            </>
          }
        >
          <div id="zone-plan-import-preview">
            <p className="faint" style={{ marginTop: 0 }}>
              {L(
                <>
                  Ansicht {p.view === "all" ? "alle Zonen" : p.view === "overview" ? "Übersicht" : p.view}
                  {p.page ? ` · Seite „${p.page}“` : ""}. Gelöschte Formen löschen keine Zone; die Linien des Exports sind keine neue Absicht –
                  dafür einen neuen Pfeil ziehen bzw. an der Linie die Eigenschaft <span className="mono">automato_intent</span> setzen.
                </>,
                <>
                  View {p.view === "all" ? "all zones" : p.view === "overview" ? "overview" : p.view}
                  {p.page ? ` · page “${p.page}”` : ""}. Deleted shapes do not delete a zone; the lines of the export are not a new intent –
                  for that draw a new arrow or set the property <span className="mono">automato_intent</span> on the line.
                </>,
              )}
            </p>
            {check(
              "positions",
              L(
                `Lage: ${p.positions} Zonen (${p.moved.length} verschoben) als Anordnung dieser Ansicht`,
                `Position: ${p.positions} zones (${p.moved.length} moved) as arrangement of this view`,
              ),
              p.positions,
            )}
            {check("renames", L(`Namen: ${p.renames.length}`, `Names: ${p.renames.length}`), p.renames.length)}
            {p.renames.length > 0 && (
              <ul className="plan-import-list" id="zone-plan-import-rename-list">
                {p.renames.map((r) => (
                  <li key={r.id}>
                    {r.from} → <b>{r.to}</b>
                  </li>
                ))}
              </ul>
            )}
            {check("intents", L(`Absichten: ${p.intents.length}`, `Intents: ${p.intents.length}`), p.intents.length)}
            {p.intents.length > 0 && (
              <ul className="plan-import-list" id="zone-plan-import-intent-list">
                {p.intents.map((x) => (
                  <li key={`${x.src_zone_id}>${x.dst_zone_id}`}>
                    {x.src} → {x.dst}: <span className={`chip ${INTENT[x.intent][0]}`}>{INTENT[x.intent][1]}</span>
                    {x.services.length > 0 && <span className="faint">{L(` nur ${x.services.join(", ")}`, ` only ${x.services.join(", ")}`)}</span>}
                    <span className="faint">
                      {" "}
                      ({x.reason}
                      {x.current ? L(`, bisher ${INTENT[x.current][1]}`, `, previously ${INTENT[x.current][1]}`) : L(", neu", ", new")})
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {check(
              "new_zones",
              L(`Neue Formen als Zonen anlegen (ohne Selektoren): ${p.new_zones.length}`, `Create new shapes as zones (without selectors): ${p.new_zones.length}`),
              p.new_zones.length,
            )}
            {p.new_zones.length > 0 && (
              <ul className="plan-import-list" id="zone-plan-import-new-list">
                {p.new_zones.map((n) => (
                  <li key={n.name}>
                    {n.name} <span className="faint">({ZONE_KIND[n.kind] ?? n.kind})</span>
                  </li>
                ))}
              </ul>
            )}
            {p.missing.length > 0 && (
              <p className="faint">{L(`Nicht in der Zeichnung (bleiben): ${p.missing.join(", ")}`, `Not in the drawing (kept): ${p.missing.join(", ")}`)}</p>
            )}
            {p.warnings.map((w) => (
              <Note key={w}>{w}</Note>
            ))}
          </div>
        </Modal>
      )}
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
    if (
      !window.confirm(
        L(
          `Version ${v.number} „${v.label}“ löschen? Der gespeicherte Stand fällt weg, die Nummer bleibt vergeben.`,
          `Delete version ${v.number} “${v.label}”? The saved state is removed, the number stays taken.`,
        ),
      )
    )
      return;
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
          <h2>{L("Versionen des Zonenplans", "Versions of the zone plan")}</h2>
          <span className="faint" id="plan-version-state">
            {latest == null
              ? L("noch keine Version gespeichert", "no version saved yet")
              : unchanged
                ? L(`aktueller Stand = Version ${unchanged.number}`, `current state = version ${unchanged.number}`)
                : L(`geändert seit Version ${latest.number}`, `changed since version ${latest.number}`)}
          </span>
        </div>
        <div className="card-body">
          {canEdit && (
            <div className="zone-intent" id="plan-version-save">
              <input
                className="input"
                aria-label={L("Bezeichnung", "Label")}
                placeholder={L("Bezeichnung, z. B. Stand nach Umbau DMZ", "Label, e.g. state after DMZ rebuild")}
                value={label}
                maxLength={200}
                onChange={(e) => setLabel(e.target.value)}
              />
              <input
                className="input"
                aria-label={L("Notiz", "Note")}
                placeholder={L("Notiz (optional)", "Note (optional)")}
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
              <button className="btn btn-sm btn-primary" disabled={busy || !label.trim()} onClick={save}>
                {busy ? L("Speichere…", "Saving…") : L("Version speichern", "Save version")}
              </button>
            </div>
          )}
          {list.items.length === 0 ? (
            <Empty title={L("Noch keine Versionen", "No versions yet")}>
              {L(
                <>
                  Eine Version hält Zonen, Plan, Matrix und Absichten so fest, wie sie jetzt abgeleitet sind – zum Nachlesen,
                  Vergleichen und Exportieren.
                </>,
                <>
                  A version records zones, plan, matrix and intents as they are derived now – to look up,
                  compare and export.
                </>,
              )}
            </Empty>
          ) : (
            <table className="compact" id="plan-versions">
              <thead>
                <tr>
                  <th className="num">{L("Nr.", "No.")}</th>
                  <th>{L("Bezeichnung", "Label")}</th>
                  <th>{L("Gespeichert", "Saved")}</th>
                  <th className="num">{L("Zonen", "Zones")}</th>
                  <th className="num">{L("Knoten", "Nodes")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.items.map((v) => (
                  <Fragment key={v.id}>
                    <tr className="clickable" onClick={() => setOpen(open === v.id ? null : v.id)}>
                      <td className="num">{v.number}</td>
                      <td>
                        <b>{v.label}</b> {v.fingerprint === list.current_fingerprint && <span className="chip ok">{L("= aktuell", "= current")}</span>}
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
                            {L("Löschen", "Delete")}
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
          {L(`Vergleich von Version ${version.number} mit`, `Comparison of version ${version.number} with`)}{" "}
          <select className="sel" aria-label={L("Vergleich mit", "Compare with")} value={to} onChange={(e) => setTo(e.target.value ? Number(e.target.value) : "")}>
            <option value="">{L("dem aktuellen Stand", "the current state")}</option>
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
  const added = L("neu", "new");
  const removed = L("entfernt", "removed");
  const intentWord = L("Absicht", "Intent");
  const unzonedWord = L("Netz ohne Zone", "Network without zone");
  for (const z of diff.zones.added) rows.push(["Zone", z, added]);
  for (const z of diff.zones.removed) rows.push(["Zone", z, removed]);
  for (const c of diff.zones.changed) {
    const what: string[] = [];
    if (c.old.kind !== c.new.kind)
      what.push(
        L(
          `Art ${ZONE_KIND[c.old.kind as ZoneKind] ?? c.old.kind} → ${ZONE_KIND[c.new.kind as ZoneKind] ?? c.new.kind}`,
          `Kind ${ZONE_KIND[c.old.kind as ZoneKind] ?? c.old.kind} → ${ZONE_KIND[c.new.kind as ZoneKind] ?? c.new.kind}`,
        ),
      );
    if (c.old.customer !== c.new.customer)
      what.push(L(`Kunde ${c.old.customer ?? "–"} → ${c.new.customer ?? "–"}`, `Customer ${c.old.customer ?? "–"} → ${c.new.customer ?? "–"}`));
    if (c.old.color !== c.new.color) what.push(L("Farbe", "Color"));
    if (JSON.stringify(c.old.selectors) !== JSON.stringify(c.new.selectors)) what.push(L("Selektoren", "Selectors"));
    rows.push(["Zone", c.key, L(`geändert: ${what.join(", ")}`, `changed: ${what.join(", ")}`)]);
  }
  for (const k of diff.intents.added) rows.push([intentWord, k, added]);
  for (const k of diff.intents.removed) rows.push([intentWord, k, removed]);
  for (const c of diff.intents.changed) rows.push([intentWord, c.key, `${INTENT[c.old.intent]?.[1] ?? c.old.intent} → ${INTENT[c.new.intent]?.[1] ?? c.new.intent}`]);
  // seit 0.56.0: „Zone @ Knoten“ = Knoten der Zone, „A ↔ B“ = Übergang mit seinen Netzen
  const part = (k: string) => (k.includes(" ↔ ") ? L("Übergang", "Transition") : L("Zone am Knoten", "Zone at node"));
  for (const k of diff.attachments.added) rows.push([part(k), k, added]);
  for (const k of diff.attachments.removed) rows.push([part(k), k, removed]);
  for (const c of diff.attachments.changed)
    rows.push([
      part(c.key),
      c.key,
      `${c.key.includes(" ↔ ") ? L("Netze", "Networks") : "Interfaces"} ${c.old.interfaces.join(", ") || "–"} → ${c.new.interfaces.join(", ") || "–"}`,
    ]);
  for (const n of diff.unzoned.added) rows.push([unzonedWord, n, added]);
  for (const n of diff.unzoned.removed) rows.push([unzonedWord, n, L("jetzt in einer Zone bzw. weg", "now in a zone or gone")]);
  return (
    <div id="plan-diff">
      {diff.same ? (
        <div className="faint">
          {L(
            "Design unverändert (Zonen, Absichten, Knoten der Zonen, Übergänge, Netze ohne Zone).",
            "Design unchanged (zones, intents, nodes of the zones, transitions, networks without zone).",
          )}
        </div>
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
          <summary>
            {L(`${diff.matrix.length} Zellen der Matrix mit anderen Regel-Zahlen`, `${diff.matrix.length} cells of the matrix with different rule counts`)}
          </summary>
          <table className="compact">
            <thead>
              <tr>
                <th>{L("Zelle", "Cell")}</th>
                <th>{L("vorher (erlaubend / sperrend / gegen Absicht)", "before (allowing / denying / against intent)")}</th>
                <th>{L("nachher", "after")}</th>
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
