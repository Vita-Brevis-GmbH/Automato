// NetBox-Sync (ADR 0010): einziger Schreibweg der Plattform. Vorschau berechnen (liest NetBox nur),
// prüfen, per Klick übernehmen (admin). Automato ändert/löscht nur Objekte mit Tag „automato“;
// von Hand gepflegte Objekte bleiben unverändert (Konflikt wird angezeigt).
import { useCallback, useEffect, useState } from "react";
import {
  applySyncPlan,
  createSyncPlan,
  fetchSyncItems,
  fetchSyncPlan,
  fetchSyncPlans,
  fetchSyncTargets,
  Paged,
  SyncItem,
  SyncPlan,
  SyncTarget,
} from "./api";
import type { View } from "./App";
import { REF_ROLE_LABEL } from "./refs";
import { Dash, Empty, ErrorBox, fmtTime, Icon, Loading, Modal, Note, PageHead } from "./ui";

const KIND_LABEL: Record<string, string> = {
  tag: "Tag",
  tenant: "Tenant",
  site_group: "Standort-Gruppe",
  site: "Standort",
  manufacturer: "Hersteller",
  device_role: "Geräterolle",
  device_type: "Gerätetyp",
  cluster_type: "Cluster-Typ",
  cluster_group: "Cluster-Gruppe",
  cluster: "Cluster",
  vrf: "VRF",
  vlan_group: "VLAN-Gruppe",
  vlan: "VLAN",
  prefix: "Netz",
  ip_range: "Range",
  device: "Gerät",
  interface: "Port",
  virtual_machine: "VM",
  vm_interface: "VM-Interface",
  ip_address: "IP-Adresse",
};
const ACTION: Record<string, [string, string]> = {
  create: ["ok", "anlegen"],
  update: ["info", "ändern"],
  unchanged: ["na", "unverändert"],
  conflict: ["warn", "Konflikt (manuell)"],
  delete: ["crit", "löschen"],
  obsolete: ["na", "veraltet"],
};
const COLUMNS = ["create", "update", "unchanged", "conflict", "delete", "obsolete"];
const STATUS: Record<string, [string, string]> = {
  planning: ["info", "Vorschau läuft…"],
  ready: ["ok", "Vorschau bereit"],
  applying: ["info", "wird übernommen…"],
  applied: ["ok", "übernommen"],
  failed: ["crit", "fehlgeschlagen"],
  superseded: ["na", "ersetzt"],
};
const SECTIONS: { key: string; label: string; hint: string }[] = [
  {
    key: "inventory",
    label: "Netzinventar",
    hint: "Geräte mit Ports (Modus, VLANs, LAG), VLANs je Quellsystem, reale Subnetze inkl. ACI BD/EPG, VRFs",
  },
  {
    key: "mirror",
    label: "DDI-Spiegel",
    hint: "Netze, Ranges und IPs mit DNS-Namen aus dem führenden IPAM (wenn diese NetBox nicht führend ist)",
  },
  {
    key: "virtualization",
    label: "Virtualisierung",
    hint: "VMs aus dem vCenter mit Cluster (Datacenter als Gruppe), VM-Interfaces (MAC, Portgruppe, VLAN, EPG) und IPs – ohne Vorlagen",
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
  const [sections, setSections] = useState<string[]>(["inventory", "mirror", "virtualization"]);
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
        <Note>Bitte oben einen Mandanten wählen – der Sync schreibt die Daten eines Mandanten in seine NetBox.</Note>
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
        Inventar und DDI-Daten von „{tenantLabel}“ in NetBox ablegen. Einziger Schreibweg von Automato: erst Vorschau,
        dann Übernahme per Klick. Geändert oder gelöscht werden nur Objekte mit Tag <code>automato</code>; von Hand
        gepflegte Objekte bleiben, wie sie sind.
      </PageHead>
      <ErrorBox error={error} prefix="NetBox-Sync" onClose={() => setError(null)} />

      <div className="card">
        <div className="card-head">
          <h2>Ziel und Umfang</h2>
        </div>
        <div className="card-body pad">
          {targets === null ? (
            <Loading />
          ) : targets.length === 0 ? (
            <Note warn>
              Für diesen Mandanten ist keine NetBox erfasst. Unter{" "}
              <button className="linkbtn" onClick={() => onGo("tenants")}>
                Mandanten &amp; Standorte
              </button>{" "}
              ein Quellsystem „NetBox“ mit Lese- und Schreib-Token anlegen.
            </Note>
          ) : (
            <>
              <div className="field">
                <label htmlFor="sync-target">Ziel-NetBox</label>
                <select
                  id="sync-target"
                  className="sel input"
                  value={targetId ?? ""}
                  onChange={(e) => setTargetId(Number(e.target.value))}
                >
                  {targets.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name} – {t.base_url || "ohne Adresse"}
                      {t.ref_role ? ` (${REF_ROLE_LABEL[t.ref_role] ?? t.ref_role})` : ""}
                      {t.has_write_token ? "" : " – kein Schreib-Token"}
                    </option>
                  ))}
                </select>
                {target && !target.has_write_token && (
                  <Note warn>
                    Für diese NetBox ist kein Schreib-Token hinterlegt. Im Quellsystem unter „Schreib-Token (optional,
                    nur NetBox-Sync)“ ein Token mit Schreibrecht auf die synchronisierten Objekttypen eintragen.
                  </Note>
                )}
              </div>
              {SECTIONS.map((s) => (
                <div className="field" key={s.key}>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={sections.includes(s.key)}
                      onChange={(e) =>
                        setSections((cur) => (e.target.checked ? [...cur, s.key] : cur.filter((x) => x !== s.key)))
                      }
                    />
                    <span>
                      <b>{s.label}</b> – {s.hint}
                    </span>
                  </label>
                </div>
              ))}
              <div className="field">
                <label className="check">
                  <input type="checkbox" checked={allowDelete} onChange={(e) => setAllowDelete(e.target.checked)} />
                  <span>
                    <b>Löschen zulassen</b> – von Automato angelegte Objekte entfernen, die in der Quelle nicht mehr
                    vorkommen (sonst nur als „veraltet“ angezeigt)
                  </span>
                </label>
              </div>
              <button
                className="btn btn-primary"
                disabled={busy || running || !target?.has_write_token || !sections.length}
                onClick={preview}
              >
                <Icon name="search" size={14} /> {busy ? "Starte…" : "Vorschau erstellen"}
              </button>
              <span className="faint" style={{ marginLeft: 10, fontSize: 12 }}>
                liest NetBox nur – geschrieben wird erst nach „Übernehmen“
              </span>
            </>
          )}
        </div>
      </div>

      {plan && <PlanView plan={plan} onApply={() => setConfirm(true)} />}

      {plans.length > 0 && (
        <div className="card">
          <div className="card-head">
            <h2>Verlauf</h2>
          </div>
          <div className="card-body scroll">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Erstellt</th>
                  <th>Ziel</th>
                  <th>Status</th>
                  <th className="num">anlegen</th>
                  <th className="num">ändern</th>
                  <th className="num">löschen</th>
                  <th className="num">Konflikte</th>
                  <th>Übernommen</th>
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
          title="In NetBox übernehmen?"
          onClose={() => setConfirm(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setConfirm(false)}>
                Abbrechen
              </button>
              <button className="btn btn-primary" onClick={apply}>
                Übernehmen
              </button>
            </>
          }
        >
          <p>
            In <b>{plan.target}</b> (<span className="mono">{plan.target_url}</span>) werden <b>{plan.totals.create}</b>{" "}
            Objekte angelegt, <b>{plan.totals.update}</b> geändert und <b>{plan.totals.delete}</b> gelöscht.
          </p>
          {plan.totals.conflict > 0 && (
            <Note>{plan.totals.conflict} von Hand gepflegte Objekte weichen ab und bleiben unverändert.</Note>
          )}
          <Note>
            Vor dem Schreiben berechnet Automato die Vorschau neu. Hat sich NetBox oder Automato seitdem geändert, wird
            nichts geschrieben. Danach wird NetBox neu eingelesen; die Aktion wird im Audit-Log festgehalten.
          </Note>
        </Modal>
      )}
    </>
  );
}

function PlanView({ plan, onApply }: { plan: SyncPlan; onApply: () => void }) {
  const running = ["planning", "applying"].includes(plan.status);
  const kinds = Object.keys(KIND_LABEL).filter((k) => plan.summary?.[k]);
  const notes =
    running ||
    !!plan.message ||
    plan.warnings.length > 0 ||
    !!plan.result ||
    (plan.status === "ready" && !plan.can_apply);
  return (
    <div className="card">
      <div className="card-head">
        <h2>
          Vorschau #{plan.id} · {plan.target}
        </h2>
        <Chip map={STATUS} value={plan.status} />
        <span className="faint" style={{ fontSize: 12 }}>
          {fmtTime(plan.created_at)} · {plan.created_by} ·{" "}
          {(plan.options.sections ?? []).map((s) => SECTIONS.find((x) => x.key === s)?.label ?? s).join(" + ")}
          {plan.options.allow_delete ? " · Löschen zugelassen" : ""}
        </span>
        <div className="spacer" />
        {plan.status === "ready" && (
          <button className="btn btn-primary" disabled={!plan.can_apply} onClick={onApply}>
            <Icon name="play" size={14} /> Übernehmen
          </button>
        )}
      </div>
      {notes && (
        <div className="card-body pad">
          {running && (
            <Loading>
              {plan.status === "planning" ? "Lese NetBox und berechne die Vorschau…" : "Schreibe nach NetBox…"}
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
                ? "NetBox ist auf dem Stand von Automato – nichts zu schreiben."
                : "Vorschau ist älter als 60 Minuten – bitte neu erstellen."}
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
        </div>
      )}
      {kinds.length > 0 && (
        <div className="card-body scroll">
          <table>
            <thead>
              <tr>
                <th>Art</th>
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
    fetchSyncItems(plan.id, { action, kind: kind || undefined, q: query, limit: PAGE, offset })
      .then(setData)
      .catch(setError);
  }, [plan.id, action, kind, query, offset]);

  const filters: [string, string][] = [
    ["create,update,conflict,delete", "alle Änderungen"],
    ["create", "anlegen"],
    ["update", "ändern"],
    ["conflict", "Konflikte"],
    ["delete", "löschen"],
    ["obsolete", "veraltet"],
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
          <option value="">Art: alle</option>
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
          <input placeholder="Objekt suchen…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      <ErrorBox error={error} />
      <div className="card-body scroll">
        {data === null ? (
          <Loading />
        ) : data.rows.length === 0 ? (
          <Empty title="Keine Einträge">Für diesen Filter gibt es nichts zu tun.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Aktion</th>
                <th>Art</th>
                <th>Objekt</th>
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
          {offset + 1}–{Math.min(offset + PAGE, data.total)} von {data.total}
          <button className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>
            Zurück
          </button>
          <button
            className="btn btn-ghost btn-sm"
            disabled={offset + PAGE >= data.total}
            onClick={() => setOffset(offset + PAGE)}
          >
            Weiter
          </button>
        </div>
      )}
    </>
  );
}
