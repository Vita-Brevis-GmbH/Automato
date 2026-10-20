// Applikationen (ADR 0012): fachliche Klammer über Application Profiles, EPGs und Server.
// Vorschläge kommen aus der Projektnummer der Namenskonvention (AP_<nr>, H_…-<nr>-…);
// die Doku je Applikation fasst die Tabellen aller ihrer Systeme zusammen.
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  acceptAppSuggestion,
  addAppMembers,
  AppCandidate,
  Application,
  ApplicationDetail,
  ApplicationDoc,
  ApplicationInput,
  AppSuggestion,
  createApp,
  Criticality,
  deleteApp,
  fetchApp,
  fetchAppCandidates,
  fetchAppDoc,
  fetchApps,
  fetchAppSuggestions,
  fetchSites,
  MemberKind,
  patchApp,
  removeAppMember,
  Site,
} from "./api";
import { DocTableView, tablesMarkdown } from "./AppDoc";
import type { Me } from "./auth";
import { Dash, download, Drawer, Empty, ErrorBox, Icon, Loading, Modal, Note } from "./ui";

const CRIT: Record<Criticality, [string, string]> = {
  low: ["", "niedrig"],
  medium: ["info", "mittel"],
  high: ["warn", "hoch"],
  critical: ["crit", "kritisch"],
};
const KIND: Record<MemberKind, string> = { ap: "AP", epg: "EPG", host: "Server" };
const PREVIEW = 6;

function CritChip({ value }: { value: Criticality | null }) {
  if (!value) return <Dash />;
  const [cls, label] = CRIT[value];
  return <span className={`chip ${cls}`}>{label}</span>;
}

export default function Apps({ me, tenantId }: { me: Me; tenantId: number | null }) {
  const [apps, setApps] = useState<Application[] | null>(null);
  const [suggestions, setSuggestions] = useState<AppSuggestion[]>([]);
  const [sites, setSites] = useState<Site[]>([]);
  const [err, setErr] = useState<unknown>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [docId, setDocId] = useState<number | null>(null);
  const [editing, setEditing] = useState<ApplicationDetail | "new" | null>(null);

  const load = useCallback(() => {
    if (tenantId == null) return;
    fetchApps(tenantId)
      .then(setApps)
      .catch((e) => {
        setErr(e);
        setApps([]);
      });
    fetchAppSuggestions(tenantId)
      .then(setSuggestions)
      .catch(() => setSuggestions([]));
  }, [tenantId]);

  useEffect(() => {
    setApps(null);
    setSelected(null);
    setDocId(null);
    load();
    if (tenantId != null)
      fetchSites(tenantId)
        .then(setSites)
        .catch(() => setSites([]));
  }, [tenantId, load]);

  if (tenantId == null)
    return (
      <div className="card">
        <Empty title="Mandant wählen">Applikationen gehören zu einem Mandanten – oben einen wählen.</Empty>
      </div>
    );
  if (docId != null) return <AppDocView id={docId} onBack={() => setDocId(null)} />;
  if (apps === null) return <Loading>Lade Applikationen…</Loading>;

  return (
    <>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {me.isOperator && suggestions.length > 0 && (
        <Suggestions tenantId={tenantId} items={suggestions} onDone={load} onError={setErr} />
      )}
      <div className="card">
        <div className="card-head">
          <h2>Applikationen</h2>
          <span className="mono faint" style={{ fontSize: 12 }}>
            {apps.length}
          </span>
          <div className="spacer" />
          {me.isOperator && (
            <button className="btn btn-primary btn-sm" onClick={() => setEditing("new")}>
              <Icon name="plus" size={13} /> Applikation
            </button>
          )}
        </div>
        <div className="card-body scroll">
          {apps.length === 0 ? (
            <Empty title="Noch keine Applikationen">
              {suggestions.length > 0
                ? "Vorschläge aus der Namenskonvention übernehmen oder eine Applikation von Hand anlegen."
                : "Eine Applikation fasst Application Profiles, EPGs und Server zusammen."}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Kürzel</th>
                  <th>Kritikalität</th>
                  <th>Verantwortlich</th>
                  <th>Standort</th>
                  <th className="num">Mitglieder</th>
                  <th className="num">Systeme</th>
                </tr>
              </thead>
              <tbody>
                {apps.map((a) => (
                  <tr key={a.id} className="clickable" onClick={() => setSelected(a.id)}>
                    <td>
                      <b>{a.name}</b>
                    </td>
                    <td className="mono">{a.code ?? <Dash />}</td>
                    <td>
                      <CritChip value={a.criticality} />
                    </td>
                    <td>{a.owner ?? <Dash />}</td>
                    <td>{a.site ? `${a.site.code} ${a.site.name}` : <Dash />}</td>
                    <td className="num">{a.member_count}</td>
                    <td className="num">{a.system_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {selected != null && (
        <AppDrawer
          me={me}
          id={selected}
          tenantId={tenantId}
          onClose={() => setSelected(null)}
          onChanged={load}
          onEdit={(d) => setEditing(d)}
          onDoc={() => {
            setDocId(selected);
            setSelected(null);
          }}
          onDeleted={() => {
            setSelected(null);
            load();
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
            load();
            setSelected(d.id);
          }}
        />
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

  const accept = async (codes: string[]) => {
    onError(null);
    try {
      for (const code of codes) {
        setBusy(code);
        await acceptAppSuggestion(tenantId, code);
      }
    } catch (e) {
      onError(e);
    } finally {
      setBusy(null);
      onDone();
    }
  };

  return (
    <div className="card">
      <div className="card-head">
        <h2>Vorschläge aus der Namenskonvention</h2>
        <span className="chip info">{items.length}</span>
        <div className="spacer" />
        <button className="btn btn-ghost btn-sm" onClick={() => setOpen((v) => !v)}>
          {open ? "ausblenden" : "anzeigen"}
        </button>
        <button
          className="btn btn-ghost btn-sm"
          disabled={busy != null}
          onClick={() => accept(items.map((s) => s.code))}
        >
          Alle übernehmen
        </button>
      </div>
      {open && (
        <>
          <div className="card-body scroll">
            <table>
              <thead>
                <tr>
                  <th>Projekt</th>
                  <th>Applikation</th>
                  <th>Noch nicht zugeordnet</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((s) => (
                  <tr key={s.code}>
                    <td className="mono">{s.code}</td>
                    <td>
                      {s.name}{" "}
                      {s.application_id != null && <span className="chip local">besteht</span>}
                    </td>
                    <td>
                      <div className="objs">
                        {s.members.slice(0, PREVIEW).map((m) => (
                          <span className="mono" key={`${m.kind}-${m.ref}`} title={m.detail ?? undefined}>
                            <span className="faint">{KIND[m.kind]}</span> {m.label}
                          </span>
                        ))}
                        {s.members.length > PREVIEW && (
                          <span className="faint">+ {s.members.length - PREVIEW} weitere</span>
                        )}
                      </div>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <button
                        className="btn btn-ghost btn-sm"
                        disabled={busy != null}
                        onClick={() => accept([s.code])}
                      >
                        {busy === s.code ? "…" : s.application_id != null ? "Ergänzen" : "Übernehmen"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card-foot">
            Gruppiert nach der 3-stelligen Projektnummer aus <code>AP_&lt;nr&gt;</code> und Servernamen{" "}
            <code>H_&lt;funktion&gt;-&lt;nr&gt;-…</code> („000“ zählt nicht). Den Namen liefert das Wörterbuch{" "}
            <code>project</code> des Namensprofils, sonst „Projekt &lt;nr&gt;“.
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
  onDoc: () => void;
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

  const update = async (p: Promise<ApplicationDetail>) => {
    setErr(null);
    try {
      setD(await p);
      onChanged();
    } catch (e) {
      setErr(e);
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
    <Drawer title={d?.name ?? "Applikation"} sub={d?.code ? `Kürzel ${d.code}` : "Applikation"} onClose={onClose}>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {!d ? (
        <Loading />
      ) : (
        <>
          <div className="chips" style={{ marginBottom: 12 }}>
            <button className="btn btn-primary btn-sm" onClick={onDoc}>
              <Icon name="apps" size={13} /> Doku
            </button>
            {me.isOperator && (
              <>
                <button className="btn btn-ghost btn-sm" onClick={() => onEdit(d)}>
                  Bearbeiten
                </button>
                <button className="btn btn-danger btn-sm" onClick={() => setConfirmDelete(true)}>
                  Löschen
                </button>
              </>
            )}
          </div>
          <dl className="kv">
            <dt>Kritikalität</dt>
            <dd>
              <CritChip value={d.criticality} />
            </dd>
            <dt>Verantwortlich</dt>
            <dd>{d.owner ?? <Dash />}</dd>
            <dt>Standort</dt>
            <dd>{d.site ? `${d.site.code} · ${d.site.name}` : <Dash />}</dd>
            <dt>Beschreibung</dt>
            <dd style={{ whiteSpace: "pre-wrap" }}>{d.description || <Dash />}</dd>
            <dt>Systeme</dt>
            <dd>{d.system_count}</dd>
          </dl>

          <div className="sub-h" style={{ display: "flex", alignItems: "center" }}>
            Mitglieder ({d.members.length})
            <div style={{ flex: 1 }} />
            {me.isOperator && (
              <button className="btn btn-ghost btn-sm" onClick={() => setPicking(true)}>
                <Icon name="plus" size={12} /> Mitglied
              </button>
            )}
          </div>
          {d.members.length === 0 ? (
            <div className="faint">Noch keine Mitglieder – Application Profile, EPG oder Server hinzufügen.</div>
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
                      {!m.exists && <span className="chip warn">nicht mehr gelesen</span>}
                      {m.origin === "suggestion" && (
                        <span className="chip na" title="aus dem Projektnummer-Vorschlag übernommen">
                          Vorschlag
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
                          title="Mitglied entfernen"
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
          title="Applikation löschen"
          onClose={() => setConfirmDelete(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setConfirmDelete(false)}>
                Abbrechen
              </button>
              <button className="btn btn-danger" onClick={remove}>
                Löschen
              </button>
            </>
          }
        >
          <p>
            „{d.name}“ mit {d.members.length} Mitglied{d.members.length === 1 ? "" : "ern"} löschen? Gelesene Daten
            (Regeln, EPGs, Server) bleiben unverändert.
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
      title={`Mitglieder für „${app.name}“`}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button
            className="btn btn-primary"
            disabled={chosen.length === 0}
            onClick={() => onAdd(chosen.map((c) => ({ kind: c.kind, ref: c.ref })))}
          >
            {chosen.length} hinzufügen
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="filterbar">
        <select className="sel" value={kind} onChange={(e) => setKind(e.target.value as MemberKind | "")}>
          <option value="">Alle Arten</option>
          <option value="ap">Application Profiles</option>
          <option value="epg">EPGs</option>
          <option value="host">Server</option>
        </select>
        <div className="search">
          <span className="ico">
            <Icon name="search" size={14} />
          </span>
          <input placeholder="Name, IP, Tenant …" value={q} autoFocus onChange={(e) => setQ(e.target.value)} />
        </div>
        <span className="count">{rows ? `${Math.min(rows.length, total)} / ${total}` : "…"}</span>
      </div>
      {rows === null ? (
        <Loading />
      ) : rows.length === 0 ? (
        <div className="faint">Nichts gefunden.</div>
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
                        <span className="chip na" title="steht schon in einer anderen Applikation">
                          zugeordnet
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
      {total > (rows?.length ?? 0) && <Note>Suche eingrenzen – es werden die ersten 100 Treffer gezeigt.</Note>}
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
      title={app ? `Applikation „${app.name}“` : "Neue Applikation"}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button className="btn btn-primary" onClick={() => submit()} disabled={busy || !name.trim()}>
            {busy ? "Speichere…" : app ? "Speichern" : "Anlegen"}
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
            <label htmlFor="ap-code">Kürzel / Projektnummer</label>
            <input
              id="ap-code"
              className="input mono"
              value={code}
              maxLength={50}
              placeholder="z. B. 002"
              onChange={(e) => setCode(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="ap-owner">Verantwortlich</label>
            <input
              id="ap-owner"
              className="input"
              value={owner}
              maxLength={200}
              placeholder="Team oder Person"
              onChange={(e) => setOwner(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="ap-crit">Kritikalität</label>
            <select
              id="ap-crit"
              className="sel input"
              value={crit}
              onChange={(e) => setCrit(e.target.value as Criticality | "")}
            >
              <option value="">– keine –</option>
              {(Object.keys(CRIT) as Criticality[]).map((k) => (
                <option key={k} value={k}>
                  {CRIT[k][1]}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ap-site">Standort</label>
            <select
              id="ap-site"
              className="sel input"
              value={siteId}
              onChange={(e) => setSiteId(e.target.value ? Number(e.target.value) : "")}
            >
              <option value="">– keiner –</option>
              {sites.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.code} {s.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="field">
          <label htmlFor="ap-desc">Beschreibung</label>
          <textarea
            id="ap-desc"
            className="input"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        {!app && (
          <div className="hint faint">Mitglieder (APs, EPGs, Server) nach dem Anlegen im Detail hinzufügen.</div>
        )}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

function docMarkdown(doc: ApplicationDoc): string {
  const a = doc.application;
  const lines = [`# Applikation ${a.name}`, ""];
  const meta = [
    a.code && `Kürzel: ${a.code}`,
    doc.tenant_code && `GG: ${doc.tenant_code}`,
    a.criticality && `Kritikalität: ${CRIT[a.criticality][1]}`,
    a.owner && `Verantwortlich: ${a.owner}`,
    a.site && `Standort: ${a.site.code} ${a.site.name}`,
  ].filter(Boolean);
  if (meta.length) lines.push(meta.join(" · "), "");
  if (a.description) lines.push(a.description, "");
  lines.push("## Systeme", "");
  for (const s of doc.systems) {
    lines.push(`- ${s.kind === "epg" ? "EPG" : "Server"} ${s.name}${s.ip ? ` (${s.ip})` : ""}`);
  }
  lines.push("");
  return lines.concat(tablesMarkdown(doc.tables)).join("\n");
}

function AppDocView({ id, onBack }: { id: number; onBack: () => void }) {
  const [doc, setDoc] = useState<ApplicationDoc | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [showEmpty, setShowEmpty] = useState(false);

  useEffect(() => {
    fetchAppDoc(id)
      .then(setDoc)
      .catch(setErr);
  }, [id]);

  if (err) return <ErrorBox error={err} prefix="Doku laden" />;
  if (!doc) return <Loading>Erzeuge Doku…</Loading>;
  const a = doc.application;
  const tables = doc.tables.filter((t) => showEmpty || t.rows.length > 0);

  return (
    <div className="printable">
      <div className="filterbar no-print">
        <button className="btn btn-ghost btn-sm" onClick={onBack}>
          ← Applikationen
        </button>
        <button className={`toggle ${showEmpty ? "on" : ""}`} onClick={() => setShowEmpty((v) => !v)}>
          leere Kategorien zeigen
        </button>
        <div className="grow" />
        <button className="btn btn-ghost btn-sm" onClick={() => download(`${a.name}.md`, docMarkdown(doc))}>
          <Icon name="download" size={14} /> Export (Markdown)
        </button>
        <button className="btn btn-ghost btn-sm" onClick={() => window.print()} title="Drucken oder als PDF sichern">
          Drucken / PDF
        </button>
      </div>

      <h1 className="print-title">Applikation {a.name}</h1>
      <div className="chips" style={{ marginBottom: 10 }}>
        {a.code && <span className="chip local">Kürzel {a.code}</span>}
        {doc.tenant_code && <span className="chip">GG {doc.tenant_code}</span>}
        {a.criticality && <CritChip value={a.criticality} />}
        {a.owner && <span className="chip">Verantwortlich: {a.owner}</span>}
        {a.site ? (
          <span className="chip">
            Standort {a.site.code} · {a.site.name}
          </span>
        ) : (
          <span className="chip na">ohne Standort</span>
        )}
      </div>
      {a.description && <p style={{ whiteSpace: "pre-wrap" }}>{a.description}</p>}

      <div className="card">
        <div className="card-head">
          <h2>Systeme</h2>
          <span className="mono faint" style={{ fontSize: 12 }}>
            {doc.systems.length}
          </span>
        </div>
        <div className="card-body">
          {doc.systems.length === 0 ? (
            <div className="loading">Keine Systeme – Mitglieder hinzufügen.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Art</th>
                  <th>System</th>
                  <th>IP</th>
                  <th>Standort</th>
                </tr>
              </thead>
              <tbody>
                {doc.systems.map((s) => (
                  <tr key={`${s.kind}-${s.name}`}>
                    <td>
                      <span className="chip">{s.kind === "epg" ? "EPG" : "Server"}</span>
                    </td>
                    <td className="mono">{s.name}</td>
                    <td className="mono">{s.ip ?? <Dash />}</td>
                    <td>{s.site ? `${s.site.code} ${s.site.name}` : <Dash />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="stack" style={{ marginTop: 14 }}>
        {tables.length === 0 ? (
          <div className="card">
            <Empty title="Noch keine Zugriffe kategorisiert">Die Systeme dieser Applikation haben keine Regeln.</Empty>
          </div>
        ) : (
          tables.map((t) => <DocTableView key={t.index} table={t} />)
        )}
      </div>
    </div>
  );
}
