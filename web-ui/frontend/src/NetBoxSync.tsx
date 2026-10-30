// NetBox-Sync (ADR 0010): einziger Schreibweg der Plattform. Vorschau berechnen (liest NetBox nur),
// prüfen, per Klick übernehmen (admin). Automato ändert/löscht nur Objekte mit Tag „automato“;
// von Hand gepflegte Objekte bleiben unverändert (Konflikt wird angezeigt). Verkabelung und
// Applikationen (ADR 0031) sind neue Abschnitte, standardmässig aus.
import { ReactNode, useCallback, useEffect, useState } from "react";
import {
  applySyncPlan,
  cancelSyncPlan,
  createSyncPlan,
  fetchSyncItems,
  fetchSyncPlan,
  fetchSyncPlans,
  fetchSyncTargets,
  Paged,
  SyncItem,
  SyncPlan,
  SyncSection,
  SyncTarget,
  SyncVerification,
} from "./api";
import type { View } from "./App";
import { L } from "./i18n";
import { REF_ROLE_LABEL } from "./refs";
import { Dash, Empty, ErrorBox, fmtTime, Icon, Loading, Modal, Note, PageHead } from "./ui";

const KIND_LABEL: Record<string, string> = {
  tag: "Tag",
  tenant: "Tenant",
  site_group: L("Standort-Gruppe", "Site group"),
  site: L("Standort", "Site"),
  manufacturer: L("Hersteller", "Manufacturer"),
  device_role: L("Geräterolle", "Device role"),
  device_type: L("Gerätetyp", "Device type"),
  cluster_type: L("Cluster-Typ", "Cluster type"),
  cluster_group: L("Cluster-Gruppe", "Cluster group"),
  cluster: "Cluster",
  vrf: "VRF",
  vlan_group: L("VLAN-Gruppe", "VLAN group"),
  vlan: "VLAN",
  prefix: L("Netz", "Network"),
  ip_range: "Range",
  device: L("Gerät", "Device"),
  interface: "Port",
  cable: L("Kabel", "Cable"),
  virtual_machine: "VM",
  vm_interface: L("VM-Interface", "VM interface"),
  ip_address: L("IP-Adresse", "IP address"),
  app_tag_vm: L("Applikations-Tag an VM", "Application tag on VM"),
  app_tag_ip: L("Applikations-Tag an IP-Adresse", "Application tag on IP address"),
};
const ACTION: Record<string, [string, string]> = {
  create: ["ok", L("anlegen", "create")],
  update: ["info", L("ändern", "update")],
  unchanged: ["na", L("unverändert", "unchanged")],
  conflict: ["warn", L("Konflikt (manuell)", "Conflict (manual)")],
  delete: ["crit", L("löschen", "delete")],
  obsolete: ["na", L("veraltet", "obsolete")],
};
const COLUMNS = ["create", "update", "unchanged", "conflict", "delete", "obsolete"];
const STATUS: Record<string, [string, string]> = {
  planning: ["info", L("Vorschau läuft…", "Preview running…")],
  ready: ["ok", L("Vorschau bereit", "Preview ready")],
  applying: ["info", L("wird übernommen…", "Applying…")],
  applied: ["ok", L("übernommen", "applied")],
  failed: ["crit", L("fehlgeschlagen", "failed")],
  superseded: ["na", L("ersetzt", "superseded")],
};
const DEFAULT_SECTIONS: SyncSection[] = ["inventory", "mirror", "virtualization"];
// `more`: Erklärung, sobald der Abschnitt gewählt ist (neue Abschnitte, standardmässig aus)
const SECTIONS: { key: SyncSection; label: string; hint: string; more?: ReactNode }[] = [
  {
    key: "inventory",
    label: L("Netzinventar", "Network inventory"),
    hint: L(
      "Geräte mit Ports (Modus, VLANs, LAG), VLANs je Quellsystem, reale Subnetze inkl. ACI BD/EPG, VRFs",
      "Devices with ports (mode, VLANs, LAG), VLANs per source system, real subnets incl. ACI BD/EPG, VRFs",
    ),
  },
  {
    key: "mirror",
    label: L("DDI-Spiegel", "DDI mirror"),
    hint: L(
      "Netze, Ranges und IPs mit DNS-Namen aus dem führenden IPAM (wenn diese NetBox nicht führend ist)",
      "Networks, ranges and IPs with DNS names from the primary IPAM (if this NetBox is not primary)",
    ),
  },
  {
    key: "virtualization",
    label: L("Virtualisierung", "Virtualization"),
    hint: L(
      "VMs aus dem vCenter mit Cluster (Datacenter als Gruppe), VM-Interfaces (MAC, Portgruppe, VLAN, EPG) und IPs – ohne Vorlagen",
      "VMs from the vCenter with cluster (datacenter as group), VM interfaces (MAC, port group, VLAN, EPG) and IPs – without templates",
    ),
  },
  {
    key: "cabling",
    label: L("Verkabelung", "Cabling"),
    hint: L(
      "gelesene Switches und Router mit Ports und die Kabel zwischen zwei Ports, die beide Seiten per CDP/LLDP melden",
      "collected switches and routers with ports and the cables between two ports that both sides report via CDP/LLDP",
    ),
    more: L(
      <>
        Geräte wie im Netzinventar (Standort des Quellsystems, Rolle, Hersteller); ohne Standort oder gelesenes Modell
        wird nichts geraten – Hinweis in der Vorschau. Ports mit Name, Typ (soweit aus Name, Speed und Medium
        ableitbar), Beschreibung, aktiviert und Port-Channel. <b>Modus und VLANs</b> nur, wenn alle VLANs des Ports in
        NetBox eindeutig vorhanden sind (VLAN-Gruppe des Quellsystems, am Standort oder global) – sonst bleiben sie
        weg und der Hinweis nennt die VLANs; Automato legt dafür keine VLANs an. <b>Kabel</b> nur zwischen zwei
        gelesenen Ports, die es in NetBox gibt oder die der Sync anlegt (Ports der ACI-Leafs: Abschnitt
        Netzinventar mitwählen); einseitige Verbindungen und Gegenstellen ohne CDP/LLDP nur als Hinweis. Ein
        umgesteckter Port: mit „Löschen zulassen“ wird das alte Kabel zuerst entfernt.
      </>,
      <>
        Devices as in the network inventory (site of the source system, role, manufacturer); without site or
        collected model nothing is guessed – hint in the preview. Ports with name, type (as far as derivable from name,
        speed and medium), description, enabled and port channel. <b>Mode and VLANs</b> only if all VLANs of the port
        exist uniquely in NetBox (VLAN group of the source system, at the site or global) – otherwise they are left
        out and the hint names the VLANs; Automato does not create VLANs for this. <b>Cables</b> only between two
        collected ports that exist in NetBox or are created by the sync (ports of the ACI leafs: also choose the
        network inventory section); links reported by one side and peers without CDP/LLDP only as a hint. A re-patched
        port: with “Allow deletion” the old cable is removed first.
      </>,
    ),
  },
  {
    key: "applications",
    label: L("Applikationen", "Applications"),
    hint: L(
      "je Applikation ein Tag automato-app-… an den VMs und IP-Adressen ihrer Systeme",
      "one tag automato-app-… per application on the VMs and IP addresses of its systems",
    ),
    more: L(
      <>
        Tag-Name = Applikation, Beschreibung = Kürzel, Zweck und Kritikalität (Farbe nach Kritikalität); keine Custom
        Fields. Systeme wie in der Applikations-Doku (VMs an den Portgruppen ihrer EPGs bzw. hinter ihren Servern). Tags
        nur an Objekten mit Tag <code>automato</code>, die es in NetBox gibt oder die der Sync anlegt (Abschnitte
        Virtualisierung bzw. DDI-Spiegel); an von Hand gepflegten Objekten ist eine Abweichung ein Konflikt. Gehört
        ein Objekt nicht mehr dazu, wird nur der Applikations-Tag entfernt – andere Tags bleiben. Tags gelöschter
        Applikationen sind veraltet und werden nur mit „Löschen zulassen“ entfernt.
      </>,
      <>
        Tag name = application, description = code, purpose and criticality (color by criticality); no custom fields.
        Systems as in the application documentation (VMs at the port groups of its EPGs or behind its servers). Tags
        only on objects with the tag <code>automato</code> that exist in NetBox or are created by the sync (sections
        virtualization or DDI mirror); on objects maintained by hand a difference is a conflict. If an object no
        longer belongs to it, only the application tag is removed – other tags stay. Tags of deleted applications are
        obsolete and are only removed with “Allow deletion”.
      </>,
    ),
  },
];
const PAGE = 200;

function Chip({ map, value }: { map: Record<string, [string, string]>; value: string }) {
  const [cls, label] = map[value] ?? ["na", value];
  return <span className={`chip ${cls}`}>{label}</span>;
}

function fmt(v: unknown): string {
  if (v === null || v === undefined || v === "") return "–";
  if (Array.isArray(v)) return v.length ? v.map(fmt).join(", ") : "–";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export default function NetBoxSync({
  tenantId,
  tenantLabel,
  onGo,
}: {
  tenantId: number | null;
  tenantLabel: string;
  onGo: (v: View) => void;
}) {
  const [targets, setTargets] = useState<SyncTarget[] | null>(null);
  const [targetId, setTargetId] = useState<number | null>(null);
  const [sections, setSections] = useState<SyncSection[]>(DEFAULT_SECTIONS);
  const [allowDelete, setAllowDelete] = useState(false);
  const [plans, setPlans] = useState<SyncPlan[]>([]);
  const [plan, setPlan] = useState<SyncPlan | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);

  const loadPlans = useCallback(() => {
    if (tenantId === null) return;
    fetchSyncPlans(tenantId)
      .then(setPlans)
      .catch(() => setPlans([]));
  }, [tenantId]);

  useEffect(() => {
    setTargets(null);
    setPlan(null);
    setError(null);
    if (tenantId === null) return;
    fetchSyncTargets(tenantId)
      .then((list) => {
        setTargets(list);
        const usable = list.find((t) => t.has_write_token) ?? list[0];
        setTargetId(usable?.id ?? null);
      })
      .catch((e) => {
        setError(e);
        setTargets([]);
      });
    fetchSyncPlans(tenantId)
      .then((list) => {
        setPlans(list);
        const latest = list.find((p) => ["planning", "ready", "applying"].includes(p.status)) ?? null;
        setPlan(latest);
      })
      .catch(() => setPlans([]));
  }, [tenantId]);

  // Laufende Vorschau/Übernahme nachverfolgen
  useEffect(() => {
    if (!plan || !["planning", "applying"].includes(plan.status)) return;
    const t = setTimeout(() => {
      fetchSyncPlan(plan.id)
        .then((p) => {
          setPlan(p);
          if (!["planning", "applying"].includes(p.status)) loadPlans();
        })
        .catch(setError);
    }, 1500);
    return () => clearTimeout(t);
  }, [plan, loadPlans]);

  if (tenantId === null) {
    return (
      <>
        <PageHead title="NetBox-Sync" />
        <Note>
          {L(
            "Bitte oben einen Mandanten wählen – der Sync schreibt die Daten eines Mandanten in seine NetBox.",
            "Please choose a tenant above – the sync writes the data of a tenant to its NetBox.",
          )}
        </Note>
      </>
    );
  }

  const target = targets?.find((t) => t.id === targetId) ?? null;
  const preview = async () => {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      const p = await createSyncPlan({
        tenant_id: tenantId,
        source_system_id: target.id,
        sections,
        allow_delete: allowDelete,
      });
      setPlan(p);
      loadPlans();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const apply = async () => {
    if (!plan) return;
    setConfirm(false);
    setError(null);
    try {
      setPlan(await applySyncPlan(plan.id));
    } catch (e) {
      setError(e);
    }
  };
  const running = !!plan && ["planning", "applying"].includes(plan.status);

  return (
    <>
      <PageHead title="NetBox-Sync">
        {L(
          <>
            Inventar, DDI-Daten, VMs, Verkabelung und Applikationen von „{tenantLabel}“ in NetBox ablegen. Einziger
            Schreibweg von Automato: erst Vorschau, dann Übernahme per Klick. Geändert oder gelöscht werden nur Objekte
            mit Tag <code>automato</code>; von Hand gepflegte Objekte bleiben, wie sie sind.
          </>,
          <>
            Store inventory, DDI data, VMs, cabling and applications of “{tenantLabel}” in NetBox. The only write path of
            Automato: preview first, then apply with a click. Only objects with the tag <code>automato</code> are changed
            or deleted; objects maintained by hand stay as they are.
          </>,
        )}
      </PageHead>
      <ErrorBox error={error} prefix="NetBox-Sync" onClose={() => setError(null)} />

      <div className="card">
        <div className="card-head">
          <h2>{L("Ziel und Umfang", "Target and scope")}</h2>
        </div>
        <div className="card-body pad">
          {targets === null ? (
            <Loading />
          ) : targets.length === 0 ? (
            <Note warn>
              {L(
                <>
                  Für diesen Mandanten ist keine NetBox erfasst. Unter{" "}
                  <button className="linkbtn" onClick={() => onGo("tenants")}>
                    Mandanten &amp; Standorte
                  </button>{" "}
                  ein Quellsystem „NetBox“ mit Lese- und Schreib-Token anlegen.
                </>,
                <>
                  No NetBox is registered for this tenant. Under{" "}
                  <button className="linkbtn" onClick={() => onGo("tenants")}>
                    Tenants &amp; sites
                  </button>{" "}
                  create a source system “NetBox” with read and write token.
                </>,
              )}
            </Note>
          ) : (
            <>
              <div className="field">
                <label htmlFor="sync-target">{L("Ziel-NetBox", "Target NetBox")}</label>
                <select
                  id="sync-target"
                  className="sel input"
                  value={targetId ?? ""}
                  onChange={(e) => setTargetId(Number(e.target.value))}
                >
                  {targets.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name} – {t.base_url || L("ohne Adresse", "no address")}
                      {t.ref_role ? ` (${REF_ROLE_LABEL[t.ref_role] ?? t.ref_role})` : ""}
                      {t.has_write_token ? "" : L(" – kein Schreib-Token", " – no write token")}
                    </option>
                  ))}
                </select>
                {target && !target.has_write_token && (
                  <Note warn>
                    {L(
                      <>
                        Für diese NetBox ist kein Schreib-Token hinterlegt. Im Quellsystem unter „Schreib-Token (optional,
                        nur NetBox-Sync)“ ein Token mit Schreibrecht auf die synchronisierten Objekttypen eintragen.
                      </>,
                      <>
                        No write token is stored for this NetBox. In the source system, under “Write token (optional,
                        NetBox sync only)”, enter a token with write permission on the synchronized object types.
                      </>,
                    )}
                  </Note>
                )}
              </div>
              {SECTIONS.map((s) => (
                <div className="field" key={s.key}>
                  <label className="check">
                    <input
                      type="checkbox"
                      id={`sync-section-${s.key}`}
                      checked={sections.includes(s.key)}
                      onChange={(e) =>
                        setSections((cur) => (e.target.checked ? [...cur, s.key] : cur.filter((x) => x !== s.key)))
                      }
                    />
                    <span>
                      <b>{s.label}</b> – {s.hint}
                      {s.more && !sections.includes(s.key) && (
                        <span className="faint">{L(" (standardmässig aus)", " (off by default)")}</span>
                      )}
                    </span>
                  </label>
                  {s.more && sections.includes(s.key) && (
                    <div id={`sync-section-${s.key}-more`}>
                      <Note>{s.more}</Note>
                    </div>
                  )}
                </div>
              ))}
              <div className="field">
                <label className="check">
                  <input type="checkbox" checked={allowDelete} onChange={(e) => setAllowDelete(e.target.checked)} />
                  <span>
                    {L(
                      <>
                        <b>Löschen zulassen</b> – von Automato angelegte Objekte entfernen, die in der Quelle nicht mehr
                        vorkommen (sonst nur als „veraltet“ angezeigt)
                      </>,
                      <>
                        <b>Allow deletion</b> – remove objects created by Automato that no longer occur in the source
                        (otherwise only shown as “obsolete”)
                      </>,
                    )}
                  </span>
                </label>
              </div>
              <button
                className="btn btn-primary"
                disabled={busy || running || !target?.has_write_token || !sections.length}
                onClick={preview}
              >
                <Icon name="search" size={14} />{" "}
                {busy ? L("Starte…", "Starting…") : L("Vorschau erstellen", "Create preview")}
              </button>
              <span className="faint" style={{ marginLeft: 10, fontSize: 12 }}>
                {L(
                  "liest NetBox nur – geschrieben wird erst nach „Übernehmen“",
                  "only reads NetBox – writing happens only after “Apply”",
                )}
              </span>
            </>
          )}
        </div>
      </div>

      {plan && (
        <PlanView
          plan={plan}
          onApply={() => setConfirm(true)}
          onCancel={() => cancelSyncPlan(plan.id).then((p) => (setPlan(p), loadPlans()), setError)}
        />
      )}

      {plans.length > 0 && (
        <div className="card">
          <div className="card-head">
            <h2>{L("Verlauf", "History")}</h2>
          </div>
          <div className="card-body scroll">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>{L("Erstellt", "Created")}</th>
                  <th>{L("Ziel", "Target")}</th>
                  <th>Status</th>
                  <th className="num">{L("anlegen", "create")}</th>
                  <th className="num">{L("ändern", "update")}</th>
                  <th className="num">{L("löschen", "delete")}</th>
                  <th className="num">{L("Konflikte", "Conflicts")}</th>
                  <th>{L("Übernommen", "Applied")}</th>
                </tr>
              </thead>
              <tbody>
                {plans.map((p) => (
                  <tr key={p.id} className="clickable" onClick={() => setPlan(p)}>
                    <td className="mono">{p.id}</td>
                    <td className="nowrap">
                      {fmtTime(p.created_at)} <span className="faint">{p.created_by}</span>
                    </td>
                    <td>{p.target}</td>
                    <td>
                      <Chip map={STATUS} value={p.status} />
                    </td>
                    <td className="num">{p.totals.create}</td>
                    <td className="num">{p.totals.update}</td>
                    <td className="num">{p.totals.delete}</td>
                    <td className="num">{p.totals.conflict}</td>
                    <td className="nowrap">
                      {p.applied_at ? (
                        <>
                          {fmtTime(p.applied_at)} <span className="faint">{p.applied_by}</span>
                        </>
                      ) : (
                        <Dash />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {confirm && plan && (
        <Modal
          title={L("In NetBox übernehmen?", "Apply to NetBox?")}
          onClose={() => setConfirm(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setConfirm(false)}>
                {L("Abbrechen", "Cancel")}
              </button>
              <button className="btn btn-primary" onClick={apply}>
                {L("Übernehmen", "Apply")}
              </button>
            </>
          }
        >
          <p>
            {L(
              <>
                In <b>{plan.target}</b> (<span className="mono">{plan.target_url}</span>) werden <b>{plan.totals.create}</b>{" "}
                Objekte angelegt, <b>{plan.totals.update}</b> geändert und <b>{plan.totals.delete}</b> gelöscht.
              </>,
              <>
                In <b>{plan.target}</b> (<span className="mono">{plan.target_url}</span>), <b>{plan.totals.create}</b>{" "}
                objects will be created, <b>{plan.totals.update}</b> updated and <b>{plan.totals.delete}</b> deleted.
              </>,
            )}
          </p>
          {plan.totals.conflict > 0 && (
            <Note>
              {L(
                <>{plan.totals.conflict} von Hand gepflegte Objekte weichen ab und bleiben unverändert.</>,
                <>{plan.totals.conflict} objects maintained by hand differ and stay unchanged.</>,
              )}
            </Note>
          )}
          <Note>
            {L(
              <>
                Vor dem Schreiben berechnet Automato die Vorschau neu. Hat sich NetBox oder Automato seitdem geändert, wird
                nichts geschrieben. Danach wird NetBox neu eingelesen; die Aktion wird im Audit-Log festgehalten.
              </>,
              <>
                Before writing, Automato recalculates the preview. If NetBox or Automato has changed since, nothing is
                written. Afterwards NetBox is collected again; the action is recorded in the audit log.
              </>,
            )}
          </Note>
        </Modal>
      )}
    </>
  );
}

function PlanView({ plan, onApply, onCancel }: { plan: SyncPlan; onApply: () => void; onCancel: () => void }) {
  const running = ["planning", "applying"].includes(plan.status);
  const kinds = Object.keys(KIND_LABEL).filter((k) => plan.summary?.[k]);
  const notes =
    running ||
    !!plan.message ||
    plan.warnings.length > 0 ||
    !!plan.result ||
    !!plan.verification ||
    (plan.status === "ready" && !plan.can_apply);
  return (
    <div className="card">
      <div className="card-head">
        <h2>
          {L(<>Vorschau #{plan.id} · {plan.target}</>, <>Preview #{plan.id} · {plan.target}</>)}
        </h2>
        <Chip map={STATUS} value={plan.status} />
        <span className="faint" style={{ fontSize: 12 }}>
          {fmtTime(plan.created_at)} · {plan.created_by} ·{" "}
          {(plan.options.sections ?? []).map((s) => SECTIONS.find((x) => x.key === s)?.label ?? s).join(" + ")}
          {plan.options.allow_delete ? L(" · Löschen zugelassen", " · deletion allowed") : ""}
        </span>
        <div className="spacer" />
        {plan.status === "ready" && (
          <button className="btn btn-primary" disabled={!plan.can_apply} onClick={onApply}>
            <Icon name="play" size={14} /> {L("Übernehmen", "Apply")}
          </button>
        )}
        {running && (
          <button
            className="btn btn-ghost btn-sm"
            title={L(
              "Beenden – z. B. hängt der Plan nach einem Neustart des Orchestrators. Die Übernahme hört vor der nächsten Objektart auf; bereits Geschriebenes bleibt.",
              "Stop – e.g. the plan hangs after a restart of the orchestrator. Applying stops before the next object type; what has already been written stays.",
            )}
            onClick={onCancel}
          >
            {L("Abbrechen", "Cancel")}
          </button>
        )}
      </div>
      {notes && (
        <div className="card-body pad">
          {running && (
            <Loading>
              {plan.status === "planning"
                ? L("Lese NetBox und berechne die Vorschau…", "Reading NetBox and calculating the preview…")
                : L("Schreibe nach NetBox…", "Writing to NetBox…")}
            </Loading>
          )}
          {plan.message && <Note warn={plan.status === "failed"}>{plan.message}</Note>}
          {plan.warnings.map((w) => (
            <Note warn key={w}>
              {w}
            </Note>
          ))}
          {plan.status === "ready" && !plan.can_apply && (
            <Note>
              {plan.totals.create + plan.totals.update + plan.totals.delete === 0
                ? L(
                    "NetBox ist auf dem Stand von Automato – nichts zu schreiben.",
                    "NetBox is up to date with Automato – nothing to write.",
                  )
                : L(
                    "Vorschau ist älter als 60 Minuten – bitte neu erstellen.",
                    "Preview is older than 60 minutes – please create it again.",
                  )}
            </Note>
          )}
          {plan.result && (
            <div className="chips" style={{ marginBottom: 10 }}>
              {Object.entries(plan.result).map(([k, c]) => (
                <span key={k} className="chip ok">
                  {KIND_LABEL[k] ?? k}: {c.create ? `+${c.create} ` : ""}
                  {c.update ? `~${c.update} ` : ""}
                  {c.delete ? `−${c.delete}` : ""}
                </span>
              ))}
            </div>
          )}
          {plan.verification && <Verification v={plan.verification} />}
        </div>
      )}
      {kinds.length > 0 && (
        <div className="card-body scroll">
          <table>
            <thead>
              <tr>
                <th>{L("Art", "Type")}</th>
                {COLUMNS.map((c) => (
                  <th key={c} className="num">
                    {ACTION[c][1]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {kinds.map((k) => (
                <tr key={k}>
                  <td>{KIND_LABEL[k]}</td>
                  {COLUMNS.map((c) => (
                    <td key={c} className="num">
                      {plan.summary?.[k]?.[c] ? <b>{plan.summary[k][c]}</b> : <span className="faint">0</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!running && plan.summary && <Items plan={plan} kinds={kinds} />}
    </div>
  );
}

const PROBLEM: Record<string, string> = {
  missing: L("fehlt in NetBox", "missing in NetBox"),
  differs: L("andere Werte", "other values"),
  still_there: L("noch in NetBox", "still in NetBox"),
};

const shown = (v: unknown) => (v === null || v === undefined || v === "" ? "–" : typeof v === "object" ? JSON.stringify(v) : String(v));

// Nachkontrolle (0.115.0, ADR 0042): nach dem Schreiben liest Automato NetBox rein lesend neu und
// vergleicht je geschriebenem Objekt – „geschrieben = gelesen“ bzw. die Abweichungen
function Verification({ v }: { v: SyncVerification }) {
  if (v.error)
    return (
      <div id="sync-verification">
        <span className="chip warn">{L("Nachkontrolle nicht möglich", "Verification not possible")}</span>
      </div>
    );
  const n = v.deviation_count ?? 0;
  return (
    <div id="sync-verification">
      <span
        className={`chip ${n ? "warn" : "ok"}`}
        title={L(
          "Nach dem Schreiben NetBox über einen rein lesenden Zugang neu gelesen und jedes geschriebene Objekt verglichen",
          "After writing, NetBox was read again through a read-only access and every written object compared",
        )}
      >
        {n
          ? L(`Nachkontrolle: ${n} Abweichungen von ${v.checked ?? 0}`, `Verification: ${n} deviations of ${v.checked ?? 0}`)
          : L(`Nachkontrolle: geschrieben = gelesen (${v.checked ?? 0})`, `Verification: written = read (${v.checked ?? 0})`)}
      </span>
      {v.unchecked ? (
        <span
          className="faint"
          style={{ marginLeft: 8, fontSize: 12 }}
          title={L(
            "Automato will diese Objekte inzwischen anders – sie zählen nicht als Abweichung",
            "Automato now wants these objects differently – they do not count as deviations",
          )}
        >
          {L(`${v.unchecked} nicht prüfbar`, `${v.unchecked} not checkable`)}
        </span>
      ) : null}
      {n > 0 && (
        <>
          <table className="tight" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>{L("Art", "Type")}</th>
                <th>{L("Objekt", "Object")}</th>
                <th>{L("Geschrieben", "Written")}</th>
                <th>{L("Nach dem Lesen", "After reading")}</th>
              </tr>
            </thead>
            <tbody>
              {(v.deviations ?? []).map((d) => (
                <tr key={`${d.kind}|${d.key}`}>
                  <td className="nowrap">{KIND_LABEL[d.kind] ?? d.kind}</td>
                  <td className="mono">{d.label}</td>
                  <td>{ACTION[d.action]?.[1] ?? d.action}</td>
                  <td>
                    {PROBLEM[d.problem] ?? d.problem}
                    {(d.changes ?? []).map((c) => (
                      <div key={c.field} className="faint mono" style={{ fontSize: 12 }}>
                        {c.field}: {shown(c.old)} → {shown(c.new)}
                      </div>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {n > (v.deviations?.length ?? 0) && (
            <div className="faint" style={{ fontSize: 12 }}>
              {L(`… ${n - (v.deviations?.length ?? 0)} weitere`, `… ${n - (v.deviations?.length ?? 0)} more`)}
            </div>
          )}
          <a href="#findings?type=netbox_sync_mismatch" style={{ fontSize: 12 }}>
            {L("Befunde unter Findings →", "Findings →")}
          </a>
        </>
      )}
    </div>
  );
}

function Items({ plan, kinds }: { plan: SyncPlan; kinds: string[] }) {
  const [action, setAction] = useState("create,update,conflict,delete");
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<Paged<SyncItem> | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    const t = setTimeout(() => setQuery(q), 300);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => setOffset(0), [action, kind, query, plan.id]);
  useEffect(() => {
    setData(null);
    let current = true; // nur die Antwort zur letzten Anfrage (Filter schnell gewechselt)
    fetchSyncItems(plan.id, { action, kind: kind || undefined, q: query, limit: PAGE, offset })
      .then((d) => current && setData(d))
      .catch((e) => current && setError(e));
    return () => {
      current = false;
    };
  }, [plan.id, action, kind, query, offset]);

  const filters: [string, string][] = [
    ["create,update,conflict,delete", L("alle Änderungen", "all changes")],
    ["create", L("anlegen", "create")],
    ["update", L("ändern", "update")],
    ["conflict", L("Konflikte", "Conflicts")],
    ["delete", L("löschen", "delete")],
    ["obsolete", L("veraltet", "obsolete")],
  ];
  return (
    <>
      <div className="filterbar" style={{ padding: "12px 16px 0" }}>
        <div className="seg">
          {filters.map(([k, label]) => (
            <button key={k} className={action === k ? "on" : ""} onClick={() => setAction(k)}>
              {label}
            </button>
          ))}
        </div>
        <select className="sel" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">{L("Art: alle", "Type: all")}</option>
          {kinds.map((k) => (
            <option key={k} value={k}>
              {KIND_LABEL[k]}
            </option>
          ))}
        </select>
        <div className="search">
          <span className="ico">
            <Icon name="search" />
          </span>
          <input
            placeholder={L("Objekt suchen…", "Search object…")}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
      </div>
      <ErrorBox error={error} />
      <div className="card-body scroll">
        {data === null ? (
          <Loading />
        ) : data.rows.length === 0 ? (
          <Empty title={L("Keine Einträge", "No entries")}>
            {L("Für diesen Filter gibt es nichts zu tun.", "There is nothing to do for this filter.")}
          </Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>{L("Aktion", "Action")}</th>
                <th>{L("Art", "Type")}</th>
                <th>{L("Objekt", "Object")}</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((i) => (
                <tr key={`${i.kind}-${i.key}-${i.action}`}>
                  <td className="nowrap">
                    <Chip map={ACTION} value={i.action} />
                  </td>
                  <td className="nowrap">{KIND_LABEL[i.kind] ?? i.kind}</td>
                  <td className="mono">{i.label}</td>
                  <td className="muted" style={{ fontSize: 12 }}>
                    {i.changes?.length ? (
                      i.changes.map((c) => (
                        <div key={c.field}>
                          <b>{c.field}</b>: {fmt(c.old)} → {fmt(c.new)}
                        </div>
                      ))
                    ) : i.fields ? (
                      Object.entries(i.fields)
                        .filter(([k]) => !["slug", "assigned_object_type"].includes(k))
                        .map(([k, v]) => `${k}: ${fmt(v)}`)
                        .join(" · ")
                    ) : (
                      <Dash />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {data && data.total > PAGE && (
        <div className="pager">
          {L(
            <>
              {offset + 1}–{Math.min(offset + PAGE, data.total)} von {data.total}
            </>,
            <>
              {offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}
            </>,
          )}
          <button className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>
            {L("Zurück", "Back")}
          </button>
          <button
            className="btn btn-ghost btn-sm"
            disabled={offset + PAGE >= data.total}
            onClick={() => setOffset(offset + PAGE)}
          >
            {L("Weiter", "Next")}
          </button>
        </div>
      )}
    </>
  );
}
