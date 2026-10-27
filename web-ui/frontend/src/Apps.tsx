// Applikationen (ADR 0012): fachliche Klammer über Application Profiles, EPGs und Server.
// Vorschläge kommen aus der Projektnummer der Namenskonvention (AP_<nr>, H_…-<nr>-…);
// Klick auf eine Applikation öffnet ihre Doku (0.14.0, ApplicationDoc.tsx), Mitglieder und
// Stammdaten werden von dort gepflegt.
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  acceptAppSuggestion,
  addAppMembers,
  AppCandidate,
  Application,
  ApplicationDetail,
  ApplicationInput,
  AppSuggestion,
  createApp,
  Criticality,
  deleteApp,
  deleteApps,
  fetchApp,
  fetchAppCandidates,
  fetchApps,
  fetchAppSuggestions,
  fetchSites,
  MemberKind,
  patchApp,
  Purpose,
  removeAppMember,
  Site,
} from "./api";
import type { View, ViewParams } from "./App";
import ApplicationDocView, { PURPOSE, PurposeChip } from "./ApplicationDoc";
import { siteLabel, SiteOptions } from "./sites";
import type { Me } from "./auth";
import { Dash, Drawer, Empty, ErrorBox, Icon, Loading, Modal, Note } from "./ui";
import AppImport from "./AppImport";
import { readStored, writeJson } from "./fold";
import { L } from "./i18n";

const CRIT: Record<Criticality, [string, string]> = {
  low: ["", L("niedrig", "low")],
  medium: ["info", L("mittel", "medium")],
  high: ["warn", L("hoch", "high")],
  critical: ["crit", L("kritisch", "critical")],
};
const KIND: Record<MemberKind, string> = {
  ap: "AP",
  epg: "EPG",
  host: "Server",
  rule: L("Regel", "Rule"),
  norule: L("ohne Regel", "excluded rule"),
};
const PREVIEW = 6;
const HIDE_KEY = "automato_apps_hide_empty"; // 0.75.0: ohne VMs ausblenden (je Browser)

function CritChip({ value }: { value: Criticality | null }) {
  if (!value) return <Dash />;
  const [cls, label] = CRIT[value];
  return <span className={`chip ${cls}`}>{label}</span>;
}

export default function Apps({
  me,
  tenantId,
  appId,
  onOpen,
  onBack,
  onGo,
}: {
  me: Me;
  tenantId: number | null;
  appId: number | null; // geöffnete Doku (#apps?app=<id>)
  onOpen: (id: number) => void;
  onBack: () => void;
  onGo?: (v: View, p?: ViewParams) => void;
}) {
  const [apps, setApps] = useState<Application[] | null>(null);
  const [suggestions, setSuggestions] = useState<AppSuggestion[]>([]);
  const [sites, setSites] = useState<Site[]>([]);
  const [err, setErr] = useState<unknown>(null);
  const [selected, setSelected] = useState<number | null>(null); // Mitglieder (Drawer)
  const [editing, setEditing] = useState<ApplicationDetail | "new" | null>(null);
  const [docReload, setDocReload] = useState(0);
  const [hideEmpty, setHideEmpty] = useState(() => readStored(HIDE_KEY, false)); // 0.75.0
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [confirmBulk, setConfirmBulk] = useState(false);
  const [importing, setImporting] = useState(false);

  const load = useCallback(() => {
    if (tenantId == null) return;
    fetchApps(tenantId)
      .then(setApps)
      .catch((e) => {
        setErr(e);
        setApps([]);
      });
    if (me.isOperator)
      fetchAppSuggestions(tenantId)
        .then(setSuggestions)
        .catch(() => setSuggestions([]));
  }, [tenantId, me.isOperator]);

  useEffect(() => {
    setApps(null);
    setSelected(null);
    setChosen(new Set());
    load();
    if (tenantId != null)
      fetchSites(tenantId)
        .then(setSites)
        .catch(() => setSites([]));
  }, [tenantId, load]);

  if (tenantId == null)
    return (
      <div className="card">
        <Empty title={L("Mandant wählen", "Select tenant")}>
          {L("Applikationen gehören zu einem Mandanten – oben einen wählen.", "Applications belong to a tenant – select one above.")}
        </Empty>
      </div>
    );
  const changed = () => {
    load();
    setDocReload((n) => n + 1);
  };
  const overlays = (
    <>
      {selected != null && (
        <AppDrawer
          me={me}
          id={selected}
          tenantId={tenantId}
          onClose={() => setSelected(null)}
          onChanged={changed}
          onEdit={(d) => setEditing(d)}
          onDoc={appId == null ? () => onOpen(selected) : undefined}
          onDeleted={() => {
            setSelected(null);
            load();
            onBack();
          }}
        />
      )}
      {editing && (
        <AppForm
          tenantId={tenantId}
          sites={sites}
          app={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(d) => {
            setEditing(null);
            changed();
            if (editing === "new") onOpen(d.id);
          }}
        />
      )}
    </>
  );

  if (appId != null)
    return (
      <>
        <ApplicationDocView
          me={me}
          id={appId}
          reload={docReload}
          onBack={onBack}
          onGo={onGo}
          onManage={() => setSelected(appId)}
          onEdit={() => fetchApp(appId).then(setEditing, setErr)}
        />
        <ErrorBox error={err} onClose={() => setErr(null)} />
        {overlays}
      </>
    );
  if (apps === null) return <Loading>{L("Lade Applikationen…", "Loading applications…")}</Loading>;

  // 0.75.0: Applikationen ohne VMs ausblenden bzw. auswählen und löschen
  const empty = apps.filter((a) => a.vm_count === 0);
  const shown = hideEmpty ? apps.filter((a) => a.vm_count > 0) : apps;
  const toggleHide = () => {
    writeJson(HIDE_KEY, !hideEmpty);
    setHideEmpty(!hideEmpty);
  };
  const pick = (id: number) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const removeChosen = async () => {
    try {
      await deleteApps(tenantId, [...chosen]);
      setChosen(new Set());
      setConfirmBulk(false);
      load();
    } catch (e) {
      setErr(e);
      setConfirmBulk(false);
    }
  };

  return (
    <>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      <div className="card">
        <div className="card-head">
          <h2>{L("Applikationen", "Applications")}</h2>
          <span className="mono faint" style={{ fontSize: 12 }}>
            {shown.length === apps.length ? apps.length : `${shown.length} / ${apps.length}`}
          </span>
          {empty.length > 0 && (
            <button
              className={`toggle${hideEmpty ? " on" : ""}`}
              id="apps-hide-empty"
              title={L(
                "Applikationen ohne VMs (an den Portgruppen ihrer EPGs bzw. hinter ihren Servern) ausblenden",
                "Hide applications without VMs (on the port groups of their EPGs or behind their servers)",
              )}
              onClick={toggleHide}
            >
              {L("ohne VMs ausblenden", "hide without VMs")} ({empty.length})
            </button>
          )}
          <div className="spacer" />
          {me.isOperator && empty.length > 0 && !hideEmpty && (
            <button
              className="btn btn-ghost btn-sm"
              id="apps-select-empty"
              onClick={() => setChosen(new Set(empty.map((a) => a.id)))}
            >
              {L("ohne VMs auswählen", "select without VMs")}
            </button>
          )}
          {me.isOperator && chosen.size > 0 && (
            <button className="btn btn-danger btn-sm" id="apps-delete" onClick={() => setConfirmBulk(true)}>
              {L(`${chosen.size} löschen`, `Delete ${chosen.size}`)}
            </button>
          )}
          {me.isOperator && (
            <button className="btn btn-ghost btn-sm" id="app-import" onClick={() => setImporting(true)}>
              {L("CSV importieren", "Import CSV")}
            </button>
          )}
          {me.isOperator && (
            <button className="btn btn-primary btn-sm" onClick={() => setEditing("new")}>
              <Icon name="plus" size={13} /> {L("Applikation", "Application")}
            </button>
          )}
        </div>
        <div className="card-body scroll">
          {apps.length === 0 ? (
            <Empty title={L("Noch keine Applikationen", "No applications yet")}>
              {suggestions.length > 0
                ? L(
                    "Vorschläge aus der Namenskonvention (unten) übernehmen oder eine Applikation von Hand anlegen.",
                    "Apply suggestions from the naming convention (below) or create an application manually.",
                  )
                : L(
                    "Eine Applikation fasst Application Profiles, EPGs und Server zusammen.",
                    "An application groups Application Profiles, EPGs and servers.",
                  )}
            </Empty>
          ) : (
            <table id="apps-list">
              <thead>
                <tr>
                  {me.isOperator && <th style={{ width: 28 }} />}
                  <th>Name</th>
                  <th>{L("Zweck", "Purpose")}</th>
                  <th>{L("Kürzel", "Code")}</th>
                  <th>{L("Kritikalität", "Criticality")}</th>
                  <th>{L("Verantwortlich", "Owner")}</th>
                  <th>{L("Standort", "Site")}</th>
                  <th className="num">{L("Mitglieder", "Members")}</th>
                  <th className="num">{L("Systeme", "Systems")}</th>
                  <th
                    className="num"
                    title={L(
                      "VMs an den Portgruppen ihrer EPGs bzw. hinter ihren Servern",
                      "VMs on the port groups of their EPGs or behind their servers",
                    )}
                  >
                    VMs
                  </th>
                </tr>
              </thead>
              <tbody>
                {shown.map((a) => (
                  <tr key={a.id} className="clickable" title={L("Doku öffnen", "Open documentation")} data-id={a.id} onClick={() => onOpen(a.id)}>
                    {me.isOperator && (
                      <td
                        onClick={(e) => {
                          e.stopPropagation();
                          pick(a.id);
                        }}
                      >
                        <input type="checkbox" checked={chosen.has(a.id)} readOnly aria-label={L(`${a.name} auswählen`, `Select ${a.name}`)} />
                      </td>
                    )}
                    <td>
                      <b>{a.name}</b>
                      {a.editable && (
                        // feinere Rollen: Kunden-Editor darf diese Applikation pflegen
                        <span
                          className="chip info app-editable"
                          style={{ marginLeft: 6 }}
                          title={L(
                            "Sie dürfen diese Applikation pflegen: Stammdaten, Regeln zuordnen/ausschliessen, Regeln je Kategorie ausblenden",
                            "You may maintain this application: master data, assign/exclude rules, hide rules per category",
                          )}
                        >
                          {L("pflegbar", "editable")}
                        </span>
                      )}
                      {(a.external_name || a.internal_name) && (
                        <div className="faint">{[a.external_name, a.internal_name].filter(Boolean).join(" · ")}</div>
                      )}
                    </td>
                    <td>
                      <PurposeChip value={a.purpose} />
                    </td>
                    <td className="mono">{a.code ?? <Dash />}</td>
                    <td>
                      <CritChip value={a.criticality} />
                    </td>
                    <td>{a.owner ?? <Dash />}</td>
                    <td>{a.site ? siteLabel(a.site) : <Dash />}</td>
                    <td className="num">{a.member_count}</td>
                    <td className="num">{a.system_count}</td>
                    <td className="num">{a.vm_count > 0 ? a.vm_count : <span className="chip na">{L("keine", "none")}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {/* Vorschläge aus der Namenskonvention unter der Liste (Dev-Befund 0.37.0) */}
      {me.isOperator && suggestions.length > 0 && (
        <Suggestions tenantId={tenantId} items={suggestions} onDone={load} onError={setErr} />
      )}

      {overlays}
      {importing && (
        <AppImport
          tenantId={tenantId}
          onClose={() => setImporting(false)}
          onDone={() => {
            setImporting(false);
            load();
          }}
        />
      )}
      {confirmBulk && (
        <Modal
          title={L("Applikationen löschen", "Delete applications")}
          onClose={() => setConfirmBulk(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setConfirmBulk(false)}>
                {L("Abbrechen", "Cancel")}
              </button>
              <button className="btn btn-danger" id="apps-delete-confirm" onClick={removeChosen}>
                {L(`${chosen.size} löschen`, `Delete ${chosen.size}`)}
              </button>
            </>
          }
        >
          <p>
            {L(
              <>
                {chosen.size} Applikation{chosen.size === 1 ? "" : "en"} mit ihren Mitgliedern löschen? Gelesene Daten (Regeln,
                EPGs, Server) bleiben unverändert.
              </>,
              <>
                Delete {chosen.size} application{chosen.size === 1 ? "" : "s"} with their members? Collected data (rules,
                EPGs, servers) remains unchanged.
              </>,
            )}
          </p>
          <div className="objs">
            {apps
              .filter((a) => chosen.has(a.id))
              .map((a) => (
                <span key={a.id} className="mono">
                  {a.name}
                </span>
              ))}
          </div>
        </Modal>
      )}
    </>
  );
}

function Suggestions({
  tenantId,
  items,
  onDone,
  onError,
}: {
  tenantId: number;
  items: AppSuggestion[];
  onDone: () => void;
  onError: (e: unknown) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState(true);

  const accept = async (keys: string[]) => {
    onError(null);
    try {
      for (const key of keys) {
        setBusy(key);
        await acceptAppSuggestion(tenantId, key);
      }
    } catch (e) {
      onError(e);
    } finally {
      setBusy(null);
      onDone();
    }
  };

  return (
    <div className="card" id="app-suggestions" style={{ marginTop: 14 }}>
      <div className="card-head">
        <h2>{L("Vorschläge aus der Namenskonvention", "Suggestions from the naming convention")}</h2>
        <span className="chip info">{items.length}</span>
        <div className="spacer" />
        <button className="btn btn-ghost btn-sm" onClick={() => setOpen((v) => !v)}>
          {open ? L("ausblenden", "hide") : L("anzeigen", "show")}
        </button>
        <button
          className="btn btn-ghost btn-sm"
          disabled={busy != null}
          onClick={() => accept(items.map((s) => s.key))}
        >
          {L("Alle übernehmen", "Apply all")}
        </button>
      </div>
      {open && (
        <>
          <div className="card-body scroll">
            <table>
              <thead>
                <tr>
                  <th>{L("Projekt", "Project")}</th>
                  <th>{L("Applikation", "Application")}</th>
                  <th>{L("Noch nicht zugeordnet", "Not yet assigned")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((s) => (
                  <tr key={s.key} data-key={s.key}>
                    <td className="mono">
                      {s.code}
                      {s.tenant && <div className="faint">{s.tenant}</div>}
                    </td>
                    <td>
                      {s.name}{" "}
                      {s.application_id != null && <span className="chip local">{L("besteht", "exists")}</span>}
                    </td>
                    <td>
                      <div className="objs">
                        {s.members.slice(0, PREVIEW).map((m) => (
                          <span className="mono" key={`${m.kind}-${m.ref}`} title={m.detail ?? undefined}>
                            <span className="faint">{KIND[m.kind]}</span> {m.label}
                          </span>
                        ))}
                        {s.members.length > PREVIEW && (
                          <span className="faint">{L(`+ ${s.members.length - PREVIEW} weitere`, `+ ${s.members.length - PREVIEW} more`)}</span>
                        )}
                      </div>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <button
                        className="btn btn-ghost btn-sm"
                        disabled={busy != null}
                        onClick={() => accept([s.key])}
                      >
                        {busy === s.key ? "…" : s.application_id != null ? L("Ergänzen", "Add to") : L("Übernehmen", "Apply")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card-foot">
            {L(
              <>
                Gruppiert nach der 3-stelligen Projektnummer aus <code>AP_&lt;nr&gt;</code> und Servernamen{" "}
                <code>H_&lt;funktion&gt;-&lt;nr&gt;-…</code> („000“ zählt nicht), je ACI-Tenant getrennt – gleiche Nummern in
                verschiedenen Tenants sind verschiedene Vorschläge. Den Namen liefert das Wörterbuch{" "}
                <code>project</code> des Namensprofils, sonst „Projekt &lt;nr&gt;“.
              </>,
              <>
                Grouped by the 3-digit project number from <code>AP_&lt;nr&gt;</code> and server names{" "}
                <code>H_&lt;function&gt;-&lt;nr&gt;-…</code> (“000” does not count), separately per ACI tenant – equal
                numbers in different tenants are different suggestions. The name comes from the dictionary{" "}
                <code>project</code> of the naming profile, otherwise “Project &lt;nr&gt;”.
              </>,
            )}
          </div>
        </>
      )}
    </div>
  );
}

function AppDrawer({
  me,
  id,
  tenantId,
  onClose,
  onChanged,
  onEdit,
  onDoc,
  onDeleted,
}: {
  me: Me;
  id: number;
  tenantId: number;
  onClose: () => void;
  onChanged: () => void;
  onEdit: (d: ApplicationDetail) => void;
  onDoc?: () => void; // fehlt, wenn die Doku schon offen ist
  onDeleted: () => void;
}) {
  const [d, setD] = useState<ApplicationDetail | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [picking, setPicking] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    setD(null);
    fetchApp(id)
      .then(setD)
      .catch(setErr);
  }, [id]);

  const [saving, setSaving] = useState(false); // 0.74.1: Speichern sichtbar
  const update = async (p: Promise<ApplicationDetail>) => {
    setErr(null);
    setSaving(true);
    try {
      setD(await p);
      onChanged();
    } catch (e) {
      setErr(e);
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    try {
      await deleteApp(id);
      onDeleted();
    } catch (e) {
      setErr(e);
      setConfirmDelete(false);
    }
  };

  return (
    <Drawer
      title={d?.name ?? L("Applikation", "Application")}
      sub={d?.code ? L(`Kürzel ${d.code}`, `Code ${d.code}`) : L("Applikation", "Application")}
      onClose={onClose}
    >
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {!d ? (
        <Loading />
      ) : (
        <>
          <div className="chips" style={{ marginBottom: 12 }}>
            {onDoc && (
              <button className="btn btn-primary btn-sm" onClick={onDoc}>
                <Icon name="apps" size={13} /> {L("Doku", "Documentation")}
              </button>
            )}
            {(me.isOperator || !!d.editable) && (
              <button className="btn btn-ghost btn-sm" onClick={() => onEdit(d)}>
                {L("Bearbeiten", "Edit")}
              </button>
            )}
            {/* Löschen und Mitglieder (AP, EPG, Server) bleiben Betreuern – auch Kunden-Editoren nicht */}
            {me.isOperator && (
              <button className="btn btn-danger btn-sm" onClick={() => setConfirmDelete(true)}>
                {L("Löschen", "Delete")}
              </button>
            )}
          </div>
          <dl className="kv">
            <dt>{L("Externer Name", "External name")}</dt>
            <dd>{d.external_name ?? <Dash />}</dd>
            <dt>{L("Interner Name", "Internal name")}</dt>
            <dd className="mono">{d.internal_name ?? <Dash />}</dd>
            <dt>{L("Zweck", "Purpose")}</dt>
            <dd>
              <PurposeChip value={d.purpose} />
            </dd>
            <dt>{L("Kritikalität", "Criticality")}</dt>
            <dd>
              <CritChip value={d.criticality} />
            </dd>
            <dt>{L("Verantwortlich", "Owner")}</dt>
            <dd>{d.owner ?? <Dash />}</dd>
            <dt>{L("Standort", "Site")}</dt>
            <dd>{d.site ? siteLabel(d.site) : <Dash />}</dd>
            <dt>{L("Beschreibung", "Description")}</dt>
            <dd style={{ whiteSpace: "pre-wrap" }}>{d.description || <Dash />}</dd>
            <dt>{L("Systeme", "Systems")}</dt>
            <dd>{d.system_count}</dd>
          </dl>

          <div className="sub-h" style={{ display: "flex", alignItems: "center" }}>
            {L("Mitglieder", "Members")} ({d.members.length})
            {saving && (
              <span className="faint" id="app-members-saving" style={{ marginLeft: 8, fontWeight: 400 }}>
                {L("speichere…", "saving…")}
              </span>
            )}
            <div style={{ flex: 1 }} />
            {me.isOperator && (
              <button className="btn btn-ghost btn-sm" disabled={saving} onClick={() => setPicking(true)}>
                <Icon name="plus" size={12} /> {L("Mitglied", "Member")}
              </button>
            )}
          </div>
          {d.members.length === 0 ? (
            <div className="faint">
              {L(
                "Noch keine Mitglieder – Application Profile, EPG oder Server hinzufügen.",
                "No members yet – add an Application Profile, EPG or server.",
              )}
            </div>
          ) : (
            <table>
              <tbody>
                {d.members.map((m) => (
                  <tr key={m.id}>
                    <td style={{ width: 60 }}>
                      <span className="chip">{KIND[m.kind]}</span>
                    </td>
                    <td>
                      <span className="mono" style={{ marginRight: 6 }}>
                        {m.label}
                      </span>
                      {!m.exists && <span className="chip warn">{L("nicht mehr gelesen", "no longer read")}</span>}
                      {m.origin === "suggestion" && (
                        <span
                          className="chip na"
                          title={L("aus dem Projektnummer-Vorschlag übernommen", "applied from the project number suggestion")}
                        >
                          {L("Vorschlag", "Suggestion")}
                        </span>
                      )}
                      {m.kind === "ap" && m.systems.length > 0 && (
                        <div className="faint">{m.systems.map((s) => s.split("/").pop()).join(", ")}</div>
                      )}
                    </td>
                    <td style={{ width: 30 }}>
                      {me.isOperator && (
                        <button
                          className="linkbtn danger"
                          title={L("Mitglied entfernen", "Remove member")}
                          onClick={() => update(removeAppMember(d.id, m.id))}
                        >
                          ✕
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
      {picking && d && (
        <MemberPicker
          tenantId={tenantId}
          app={d}
          onClose={() => setPicking(false)}
          onAdd={(members) => {
            setPicking(false);
            return update(addAppMembers(d.id, members));
          }}
        />
      )}
      {confirmDelete && d && (
        <Modal
          title={L("Applikation löschen", "Delete application")}
          onClose={() => setConfirmDelete(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setConfirmDelete(false)}>
                {L("Abbrechen", "Cancel")}
              </button>
              <button className="btn btn-danger" onClick={remove}>
                {L("Löschen", "Delete")}
              </button>
            </>
          }
        >
          <p>
            {L(
              <>
                „{d.name}“ mit {d.members.length} Mitglied{d.members.length === 1 ? "" : "ern"} löschen? Gelesene Daten
                (Regeln, EPGs, Server) bleiben unverändert.
              </>,
              <>
                Delete “{d.name}” with {d.members.length} member{d.members.length === 1 ? "" : "s"}? Collected data
                (rules, EPGs, servers) remains unchanged.
              </>,
            )}
          </p>
        </Modal>
      )}
    </Drawer>
  );
}

function MemberPicker({
  tenantId,
  app,
  onClose,
  onAdd,
}: {
  tenantId: number;
  app: ApplicationDetail;
  onClose: () => void;
  onAdd: (members: { kind: MemberKind; ref: string }[]) => void;
}) {
  const [kind, setKind] = useState<MemberKind | "">("");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<AppCandidate[] | null>(null);
  const [total, setTotal] = useState(0);
  const [picked, setPicked] = useState<Record<string, AppCandidate>>({});
  const [err, setErr] = useState<unknown>(null);
  const have = useMemo(() => new Set(app.members.map((m) => `${m.kind}|${m.ref}`)), [app]);

  useEffect(() => {
    const t = setTimeout(() => {
      fetchAppCandidates(tenantId, { kind, q, limit: 100 })
        .then((p) => {
          setRows(p.rows);
          setTotal(p.total);
        })
        .catch(setErr);
    }, 250);
    return () => clearTimeout(t);
  }, [tenantId, kind, q]);

  const key = (c: AppCandidate) => `${c.kind}|${c.ref}`;
  const toggle = (c: AppCandidate) =>
    setPicked((p) => {
      const n = { ...p };
      if (n[key(c)]) delete n[key(c)];
      else n[key(c)] = c;
      return n;
    });
  const chosen = Object.values(picked);

  return (
    <Modal
      title={L(`Mitglieder für „${app.name}“`, `Members for “${app.name}”`)}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button
            className="btn btn-primary"
            disabled={chosen.length === 0}
            onClick={() => onAdd(chosen.map((c) => ({ kind: c.kind, ref: c.ref })))}
          >
            {L(`${chosen.length} hinzufügen`, `Add ${chosen.length}`)}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="filterbar">
        <select className="sel" value={kind} onChange={(e) => setKind(e.target.value as MemberKind | "")}>
          <option value="">{L("Alle Arten", "All types")}</option>
          <option value="ap">Application Profiles</option>
          <option value="epg">EPGs</option>
          <option value="host">Server</option>
        </select>
        <div className="search">
          <span className="ico">
            <Icon name="search" size={14} />
          </span>
          <input placeholder={L("Name, IP, Tenant …", "Name, IP, tenant …")} value={q} autoFocus onChange={(e) => setQ(e.target.value)} />
        </div>
        <span className="count">{rows ? `${Math.min(rows.length, total)} / ${total}` : "…"}</span>
      </div>
      {rows === null ? (
        <Loading />
      ) : rows.length === 0 ? (
        <div className="faint">{L("Nichts gefunden.", "Nothing found.")}</div>
      ) : (
        <div style={{ maxHeight: 360, overflowY: "auto" }}>
          <table>
            <tbody>
              {rows.map((c) => {
                const already = have.has(key(c));
                return (
                  <tr key={key(c)} className={already ? "" : "clickable"} onClick={() => !already && toggle(c)}>
                    <td style={{ width: 28 }}>
                      <input type="checkbox" checked={already || !!picked[key(c)]} disabled={already} readOnly />
                    </td>
                    <td style={{ width: 60 }}>
                      <span className="chip">{KIND[c.kind]}</span>
                    </td>
                    <td>
                      <span className="mono">{c.label}</span>
                      {c.detail && <span className="faint"> · {c.detail}</span>}
                    </td>
                    <td className="mono faint">{c.project ?? ""}</td>
                    <td>
                      {c.applications.length > 0 && !already && (
                        <span
                          className="chip na"
                          title={L("steht schon in einer anderen Applikation", "already in another application")}
                        >
                          {L("zugeordnet", "assigned")}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {total > (rows?.length ?? 0) && (
        <Note>{L("Suche eingrenzen – es werden die ersten 100 Treffer gezeigt.", "Narrow the search – the first 100 matches are shown.")}</Note>
      )}
    </Modal>
  );
}

function AppForm({
  tenantId,
  sites,
  app,
  onClose,
  onSaved,
}: {
  tenantId: number;
  sites: Site[];
  app: ApplicationDetail | null;
  onClose: () => void;
  onSaved: (d: ApplicationDetail) => void;
}) {
  const [name, setName] = useState(app?.name ?? "");
  const [externalName, setExternalName] = useState(app?.external_name ?? "");
  const [internalName, setInternalName] = useState(app?.internal_name ?? "");
  const [purpose, setPurpose] = useState<Purpose | "">(app?.purpose ?? "");
  const [code, setCode] = useState(app?.code ?? "");
  const [owner, setOwner] = useState(app?.owner ?? "");
  const [crit, setCrit] = useState<Criticality | "">(app?.criticality ?? "");
  const [siteId, setSiteId] = useState<number | "">(app?.site?.id ?? "");
  const [description, setDescription] = useState(app?.description ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setErr(null);
    const body: ApplicationInput = {
      name: name.trim(),
      external_name: externalName.trim() || null,
      internal_name: internalName.trim() || null,
      purpose: purpose || null,
      code: code.trim() || null,
      owner: owner.trim() || null,
      criticality: crit || null,
      site_id: siteId === "" ? null : siteId,
      description: description.trim() || null,
    };
    try {
      onSaved(app ? await patchApp(app.id, body) : await createApp(tenantId, { ...body, name: name.trim() }));
    } catch (e2) {
      setErr(e2);
      setBusy(false);
    }
  };

  return (
    <Modal
      title={app ? L(`Applikation „${app.name}“`, `Application “${app.name}”`) : L("Neue Applikation", "New application")}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-primary" onClick={() => submit()} disabled={busy || !name.trim()}>
            {busy ? L("Speichere…", "Saving…") : app ? L("Speichern", "Save") : L("Anlegen", "Create")}
          </button>
        </>
      }
    >
      <form onSubmit={submit}>
        <ErrorBox error={err} />
        <div className="form-grid">
          <div className="field">
            <label htmlFor="ap-name">Name</label>
            <input
              id="ap-name"
              className="input"
              value={name}
              autoFocus
              maxLength={200}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="ap-purpose">{L("Verwendungszweck", "Purpose")}</label>
            <select
              id="ap-purpose"
              className="sel input"
              value={purpose}
              onChange={(e) => setPurpose(e.target.value as Purpose | "")}
            >
              <option value="">{L("– keiner –", "– none –")}</option>
              {(Object.keys(PURPOSE) as Purpose[]).map((k) => (
                <option key={k} value={k}>
                  {PURPOSE[k][1]}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ap-ext">{L("Externer Name", "External name")}</label>
            <input
              id="ap-ext"
              className="input"
              value={externalName}
              maxLength={200}
              placeholder={L("Name gegen aussen (Fachbereich, Kunde)", "Name toward the outside (business unit, customer)")}
              onChange={(e) => setExternalName(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="ap-int">{L("Interner Name", "Internal name")}</label>
            <input
              id="ap-int"
              className="input mono"
              value={internalName}
              maxLength={200}
              placeholder={L("Name im Betrieb", "Name in operations")}
              onChange={(e) => setInternalName(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="ap-code">{L("Kürzel / Projektnummer", "Code / project number")}</label>
            <input
              id="ap-code"
              className="input mono"
              value={code}
              maxLength={50}
              placeholder={L("z. B. 002", "e.g. 002")}
              onChange={(e) => setCode(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="ap-owner">{L("Verantwortlich", "Owner")}</label>
            <input
              id="ap-owner"
              className="input"
              value={owner}
              maxLength={200}
              placeholder={L("Team oder Person", "Team or person")}
              onChange={(e) => setOwner(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="ap-crit">{L("Kritikalität", "Criticality")}</label>
            <select
              id="ap-crit"
              className="sel input"
              value={crit}
              onChange={(e) => setCrit(e.target.value as Criticality | "")}
            >
              <option value="">{L("– keine –", "– none –")}</option>
              {(Object.keys(CRIT) as Criticality[]).map((k) => (
                <option key={k} value={k}>
                  {CRIT[k][1]}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ap-site">{L("Standort", "Site")}</label>
            <select
              id="ap-site"
              className="sel input"
              value={siteId}
              onChange={(e) => setSiteId(e.target.value ? Number(e.target.value) : "")}
            >
              <option value="">{L("– keiner –", "– none –")}</option>
              <SiteOptions sites={sites} />
            </select>
          </div>
        </div>
        <div className="field">
          <label htmlFor="ap-desc">{L("Beschreibung", "Description")}</label>
          <textarea
            id="ap-desc"
            className="input"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        {!app && (
          <div className="hint faint">
            {L(
              "Mitglieder (APs, EPGs, Server) nach dem Anlegen im Detail hinzufügen.",
              "Add members (APs, EPGs, servers) in the detail view after creating.",
            )}
          </div>
        )}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}
