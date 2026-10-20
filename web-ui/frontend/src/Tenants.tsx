import { useEffect, useState } from "react";
import {
  Agent,
  createSite,
  createSystem,
  createTenant,
  DeleteCounts,
  deleteSite,
  deleteSystem,
  deleteTenant,
  fetchAgents,
  fetchNamingProfiles,
  fetchSites,
  fetchSystems,
  NamingProfile,
  patchSystem,
  patchTenant,
  profileLabel,
  Site,
  System,
  Tenant,
} from "./api";
import type { Me } from "./auth";
import { Empty, ErrorBox, Icon, Loading, Modal, Note, PageHead, SecretInput } from "./ui";

const PLATFORM_LABEL: Record<string, string> = { fortigate: "FortiGate", cisco_aci: "Cisco ACI" };

// Mandanten (= Gruppengesellschaft) mit Kürzel, Namensprofil, Standorten und Quellsystemen.
// Lesen = viewer, Schreiben/Löschen = admin (serverseitig erzwungen).
export default function Tenants({
  me,
  tenants,
  tenantId,
  onSelectTenant,
  onTenantsChanged,
}: {
  me: Me;
  tenants: Tenant[] | null;
  tenantId: number | null;
  onSelectTenant: (id: number | null) => void;
  onTenantsChanged: () => Promise<Tenant[]>;
}) {
  const [profiles, setProfiles] = useState<NamingProfile[]>([]);
  const [newOpen, setNewOpen] = useState(false);
  const [systemsVersion, setSystemsVersion] = useState(0); // neu laden nach Zuordnung

  useEffect(() => {
    fetchNamingProfiles()
      .then(setProfiles)
      .catch(() => setProfiles([]));
  }, []);

  const selected = tenants?.find((t) => t.id === tenantId) ?? null;
  const profileName = (id: number | null) => {
    if (id == null) return "default (Standard)";
    const p = profiles.find((x) => x.id === id);
    return p ? profileLabel(p) : `#${id}`;
  };

  return (
    <>
      <PageHead
        title="Mandanten & Standorte"
        actions={
          me.isAdmin && (
            <button className="btn btn-primary btn-sm" onClick={() => setNewOpen(true)}>
              + Mandant
            </button>
          )
        }
      >
        Ein Mandant ist eine Gruppengesellschaft (GG) mit Kürzel und einem oder mehreren Standorten.
        Mandanten wählen: Klick auf die Zeile (gilt auch für den Mandanten-Schalter oben).
      </PageHead>

      <div className="card">
        <div className="card-head">
          <h2>Mandanten</h2>
          <div className="spacer" />
          <span className="faint mono" style={{ fontSize: 12 }}>
            {tenants?.length ?? 0}
          </span>
        </div>
        <div className="card-body">
          {tenants === null ? (
            <Loading />
          ) : tenants.length === 0 ? (
            <Empty
              title="Noch keine Mandanten"
              action={
                me.isAdmin && (
                  <button className="btn btn-primary btn-sm" onClick={() => setNewOpen(true)}>
                    + Mandant anlegen
                  </button>
                )
              }
            >
              Lege die erste Gruppengesellschaft an.
            </Empty>
          ) : (
            tenants.map((t) => (
              <button
                key={t.id}
                className={`site-card ${t.id === tenantId ? "selected" : ""}`}
                onClick={() => onSelectTenant(t.id)}
              >
                <span className="site-ico">
                  <Icon name="tenants" size={20} />
                </span>
                <span>
                  <span className="s-name">
                    {t.name}
                    {t.code && <span className="chip local">GG {t.code}</span>}
                  </span>
                  <span className="s-meta mono" style={{ display: "block" }}>
                    ID {t.id} · SoT: {t.ipam_type} · DNS: {t.dns_type} · Profil: {profileName(t.naming_profile_id)}
                  </span>
                </span>
                <span className="s-right">
                  {t.audit_reads && <span className="chip info">Lese-Audit</span>}
                  {t.id === tenantId && <span className="chip ok">gewählt</span>}
                </span>
              </button>
            ))
          )}
        </div>
      </div>

      {selected && (
        <div className="two-col" style={{ marginTop: 16 }}>
          <TenantSettings
            key={selected.id}
            me={me}
            tenant={selected}
            profiles={profiles}
            onSaved={onTenantsChanged}
            onDeleted={async () => {
              onSelectTenant(null);
              await onTenantsChanged();
            }}
          />
          <Sites me={me} tenant={selected} />
        </div>
      )}
      {selected && (
        <div style={{ marginTop: 16 }}>
          <SourceSystems key={`${selected.id}-${systemsVersion}`} me={me} tenant={selected} />
        </div>
      )}
      {!selected && tenants && tenants.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <Note>Einen Mandanten anklicken, um Kürzel, Namensprofil, Standorte und Quellsysteme zu pflegen.</Note>
        </div>
      )}
      {me.isAdmin && tenants && tenants.length > 0 && (
        <UnassignedSystems tenants={tenants} onAssigned={() => setSystemsVersion((v) => v + 1)} />
      )}

      {newOpen && (
        <NewTenantDialog
          onClose={() => setNewOpen(false)}
          onCreated={async (t) => {
            await onTenantsChanged();
            onSelectTenant(t.id);
            setNewOpen(false);
          }}
        />
      )}
    </>
  );
}

function NewTenantDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (t: Tenant) => void }) {
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      onCreated(await createTenant({ name: name.trim(), code: code.trim() || null }));
    } catch (ex) {
      setErr(ex);
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Neuer Mandant"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button className="btn btn-primary" onClick={() => submit()} disabled={busy || !name.trim()}>
            {busy ? "Lege an…" : "Anlegen"}
          </button>
        </>
      }
    >
      <form onSubmit={submit}>
        <ErrorBox error={err} />
        <div className="field">
          <label htmlFor="nt-name">Name (Gruppengesellschaft)</label>
          <input
            id="nt-name"
            className="input"
            value={name}
            autoFocus
            maxLength={200}
            placeholder="z. B. Kunde A AG"
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="nt-code">Kürzel (optional)</label>
          <input
            id="nt-code"
            className="input mono"
            value={code}
            maxLength={20}
            placeholder="z. B. ssgch"
            onChange={(e) => setCode(e.target.value)}
          />
          <div className="hint">Kennung der GG bei VMs/Objekten. Keine Funktionsrelevanz, jederzeit änderbar.</div>
        </div>
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

function TenantSettings({
  me,
  tenant,
  profiles,
  onSaved,
  onDeleted,
}: {
  me: Me;
  tenant: Tenant;
  profiles: NamingProfile[];
  onSaved: () => Promise<Tenant[]>;
  onDeleted: () => Promise<void>;
}) {
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [code, setCode] = useState(tenant.code ?? "");
  const [profileId, setProfileId] = useState<number | null>(tenant.naming_profile_id);
  const [auditReads, setAuditReads] = useState(tenant.audit_reads);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  const dirty =
    (code.trim() || null) !== tenant.code ||
    profileId !== tenant.naming_profile_id ||
    auditReads !== tenant.audit_reads;

  const save = async () => {
    setBusy(true);
    setErr(null);
    setSaved(false);
    try {
      await patchTenant(tenant.id, {
        code: code.trim() || null,
        naming_profile_id: profileId,
        audit_reads: auditReads,
      });
      await onSaved();
      setSaved(true);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  const ro = !me.isAdmin;
  return (
    <div className="card">
      <div className="card-head">
        <h2>{tenant.name}</h2>
        <span className="chip mono">ID {tenant.id}</span>
      </div>
      <div className="card-body pad">
        <ErrorBox error={err} />
        <div className="field">
          <label htmlFor="ts-code">Kürzel (GG)</label>
          <input
            id="ts-code"
            className="input mono"
            value={code}
            maxLength={20}
            disabled={ro}
            placeholder="z. B. ssgch"
            onChange={(e) => setCode(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="ts-profile">Namensprofil</label>
          <select
            id="ts-profile"
            className="sel input"
            value={profileId ?? ""}
            disabled={ro}
            onChange={(e) => setProfileId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Standard (Profil „default“)</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {profileLabel(p)}
              </option>
            ))}
          </select>
          <div className="hint">Steuert die Präfix-Erkennung beim Einlesen. Ohne Auswahl gilt das Standardprofil „default“.</div>
        </div>
        <div className="field">
          <label className="check">
            <input
              type="checkbox"
              checked={auditReads}
              disabled={ro}
              onChange={(e) => setAuditReads(e.target.checked)}
            />
            Lesezugriffe dieses Mandanten im Audit-Log protokollieren
          </label>
        </div>
        <div className="field">
          <span className="label">Benutzer-Zuordnung</span>
          <div className="hint" style={{ marginTop: 0 }}>
            In Keycloak beim Benutzer das Attribut <code>tenant_ids</code> = <code>{tenant.id}</code> setzen.
          </div>
        </div>
        {!ro && (
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <button className="btn btn-primary btn-sm" onClick={save} disabled={busy || !dirty}>
              {busy ? "Speichere…" : "Speichern"}
            </button>
            {saved && !dirty && <span className="chip ok">gespeichert</span>}
            <div style={{ flex: 1 }} />
            <button className="btn btn-danger btn-sm" onClick={() => setDeleteOpen(true)}>
              Mandant löschen
            </button>
          </div>
        )}
      </div>
      {deleteOpen && (
        <DeleteTenantDialog
          tenant={tenant}
          onClose={() => setDeleteOpen(false)}
          onDeleted={async () => {
            setDeleteOpen(false);
            await onDeleted();
          }}
        />
      )}
    </div>
  );
}

const COUNT_LABEL: Record<string, string> = {
  rules: "Regeln",
  nat_rules: "NAT-Einträge",
  findings: "Findings",
  epgs: "EPGs",
  objects: "Adressobjekte",
  zones: "Zonen",
  sites: "Standorte",
  source_systems: "Quellsysteme",
  runs: "Läufe",
  agents: "Collector-Agenten",
};

function countsText(counts: DeleteCounts): string {
  const parts = Object.entries(counts)
    .filter(([k, n]) => k !== "id" && n > 0)
    .map(([k, n]) => `${n} ${COUNT_LABEL[k] ?? k}`);
  return parts.length ? parts.join(", ") : "keine weiteren Daten";
}

function DeleteTenantDialog({
  tenant,
  onClose,
  onDeleted,
}: {
  tenant: Tenant;
  onClose: () => void;
  onDeleted: () => Promise<void>;
}) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [done, setDone] = useState<DeleteCounts | null>(null);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      setDone(await deleteTenant(tenant.id, typed.trim()));
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Modal
        title="Mandant gelöscht"
        onClose={onDeleted}
        footer={
          <button className="btn btn-primary" onClick={onDeleted}>
            Schliessen
          </button>
        }
      >
        <p>
          „{tenant.name}“ wurde gelöscht, mit: {countsText(done)}.
        </p>
        <Note>
          In Keycloak bei betroffenen Benutzern die Mandanten-ID <code>{tenant.id}</code> aus dem Attribut{" "}
          <code>tenant_ids</code> entfernen.
        </Note>
      </Modal>
    );
  }

  return (
    <Modal
      title="Mandant löschen"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button
            className="btn btn-danger"
            onClick={submit}
            disabled={busy || typed.trim() !== tenant.name}
          >
            {busy ? "Lösche…" : "Endgültig löschen"}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <Note warn>
        Löscht <b>„{tenant.name}“</b> mit <b>allen</b> Daten: Quellsysteme mit ihren Regeln und NAT-Einträgen,
        Adressobjekte, Zonen, EPGs, Findings, Standorte, Läufe und die Collector-Agenten dieses Mandanten. Das
        lässt sich nicht rückgängig machen; das Audit-Log bleibt erhalten.
      </Note>
      <div className="field">
        <label htmlFor="dt-confirm">Zur Bestätigung den Namen des Mandanten eingeben</label>
        <input
          id="dt-confirm"
          className="input"
          value={typed}
          autoFocus
          placeholder={tenant.name}
          onChange={(e) => setTyped(e.target.value)}
        />
      </div>
    </Modal>
  );
}

function Sites({ me, tenant }: { me: Me; tenant: Tenant }) {
  const [sites, setSites] = useState<Site[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [err, setErr] = useState<unknown>(null);

  const load = () =>
    fetchSites(tenant.id)
      .then(setSites)
      .catch((e) => {
        setErr(e);
        setSites([]);
      });

  useEffect(() => {
    setSites(null);
    setErr(null);
    setAdding(false);
    load();
  }, [tenant.id]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      await createSite(tenant.id, {
        code: code.trim(),
        name: name.trim(),
        description: description.trim() || null,
      });
      setCode("");
      setName("");
      setDescription("");
      setAdding(false);
      await load();
    } catch (ex) {
      setErr(ex);
    }
  };

  const remove = async (s: Site) => {
    if (!window.confirm(`Standort ${s.code} „${s.name}“ löschen?`)) return;
    setErr(null);
    try {
      await deleteSite(tenant.id, s.id);
      await load();
    } catch (ex) {
      setErr(ex);
    }
  };

  return (
    <div className="card">
      <div className="card-head">
        <h2>Standorte</h2>
        <div className="spacer" />
        {me.isAdmin && !adding && (
          <button className="btn btn-ghost btn-sm" onClick={() => setAdding(true)}>
            + Standort
          </button>
        )}
      </div>
      <div className="card-body">
        {err ? (
          <div style={{ padding: "10px 16px 0" }}>
            <ErrorBox error={err} onClose={() => setErr(null)} />
          </div>
        ) : null}
        {adding && (
          <form className="inline-form" onSubmit={add}>
            <div className="field narrow">
              <label htmlFor="st-code">Code</label>
              <input
                id="st-code"
                className="input mono"
                value={code}
                required
                autoFocus
                pattern="[0-9A-Za-z]{3}"
                maxLength={3}
                title="dreistelliger Standort-Code, z. B. 000"
                placeholder="000"
                onChange={(e) => setCode(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="st-name">Name</label>
              <input
                id="st-name"
                className="input"
                value={name}
                required
                placeholder="z. B. RZ01 Hauptstandort"
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="st-desc">Beschreibung</label>
              <input
                id="st-desc"
                className="input"
                value={description}
                placeholder="optional"
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            <button className="btn btn-primary btn-sm" type="submit">
              Hinzufügen
            </button>
            <button className="btn btn-ghost btn-sm" type="button" onClick={() => setAdding(false)}>
              Abbrechen
            </button>
          </form>
        )}
        {sites === null ? (
          <Loading />
        ) : sites.length === 0 ? (
          <Empty title="Noch keine Standorte">Jeder Standort hat einen dreistelligen Code (z. B. 000).</Empty>
        ) : (
          sites.map((s) => (
            <div className="site-card" key={s.id}>
              <span className="site-ico">
                <Icon name="pin" size={20} />
              </span>
              <span>
                <span className="s-name">
                  {s.name} <span className="chip local">Standort {s.code}</span>
                </span>
                {s.description && <span className="s-meta" style={{ display: "block" }}>{s.description}</span>}
              </span>
              {me.isAdmin && (
                <span className="s-right">
                  <button className="linkbtn danger" title="Löschen" onClick={() => remove(s)}>
                    ✕
                  </button>
                </span>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function SourceSystems({ me, tenant }: { me: Me; tenant: Tenant }) {
  const [systems, setSystems] = useState<System[] | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [editing, setEditing] = useState<System | "new" | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [toDelete, setToDelete] = useState<System | null>(null);

  const load = () =>
    fetchSystems(tenant.id)
      .then(setSystems)
      .catch((e) => {
        setErr(e);
        setSystems([]);
      });

  useEffect(() => {
    load();
    // Agenten-Liste ist Admin-Sache; Viewer sehen nur "Agent #id"
    if (me.isAdmin) fetchAgents().then(setAgents).catch(() => setAgents([]));
  }, []);

  // Geräte dieses Mandanten dürfen eigene oder mandantsübergreifende Agenten verwalten
  const usable = agents.filter((a) => a.tenant_id == null || a.tenant_id === tenant.id);
  const agentName = (id: number) => agents.find((a) => a.id === id)?.name ?? `Agent #${id}`;

  return (
    <div className="card">
      <div className="card-head">
        <h2>Quellsysteme von „{tenant.name}“</h2>
        <span className="faint" style={{ fontSize: 12 }}>
          Geräte (Firewalls/Controller), aus denen gelesen wird
        </span>
        <div className="spacer" />
        {me.isAdmin && (
          <button className="btn btn-ghost btn-sm" onClick={() => setEditing("new")}>
            + Quellsystem
          </button>
        )}
      </div>
      <div className="card-body scroll">
        {err ? (
          <div style={{ padding: "10px 16px 0" }}>
            <ErrorBox error={err} onClose={() => setErr(null)} />
          </div>
        ) : null}
        {systems === null ? (
          <Loading />
        ) : systems.length === 0 ? (
          <Empty title="Noch keine Quellsysteme">
            Gerät (FortiGate oder APIC) mit Adresse und read-only Zugangsdaten anlegen und einem Collector-Agenten
            zuweisen, dann unter
            „Übersicht → Jetzt einlesen“ einen Lauf starten.
          </Empty>
        ) : (
          <SystemTable
            systems={systems}
            agentName={agentName}
            actions={
              me.isAdmin
                ? (s) => (
                    <>
                      <button className="btn btn-ghost btn-sm" onClick={() => setEditing(s)}>
                        Bearbeiten
                      </button>
                      <button className="linkbtn danger" title="Löschen" onClick={() => setToDelete(s)}>
                        ✕
                      </button>
                    </>
                  )
                : undefined
            }
          />
        )}
      </div>
      <div className="card-foot">
        <Icon name="lock" size={12} /> Zugangsdaten (FortiGate-Token, APIC-Benutzer/Passwort) werden verschlüsselt
        im Orchestrator gespeichert, nie wieder angezeigt und nur dem zugewiesenen Agenten für dessen Läufe übergeben
        (ADR 0009).
      </div>
      {editing && (
        <SystemDialog
          tenant={tenant}
          system={editing === "new" ? null : editing}
          agents={usable}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await load();
            if (me.isAdmin) fetchAgents().then(setAgents).catch(() => undefined);
          }}
        />
      )}
      {toDelete && (
        <DeleteSystemDialog
          system={toDelete}
          onClose={() => setToDelete(null)}
          onDeleted={async () => {
            setToDelete(null);
            await load();
          }}
        />
      )}
    </div>
  );
}

// Gerät anlegen/bearbeiten: Adresse, verwaltender Agent, read-only API-Token (nur schreibbar).
function SystemDialog({
  tenant,
  system,
  agents,
  onClose,
  onSaved,
}: {
  tenant: Tenant;
  system: System | null; // null = neu
  agents: Agent[]; // für diesen Mandanten zulässige Agenten
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const own = agents.find((a) => a.tenant_id === tenant.id);
  const [name, setName] = useState(system?.name ?? "");
  const [platform, setPlatform] = useState(system?.platform ?? "fortigate");
  const [baseUrl, setBaseUrl] = useState(system?.base_url ?? "");
  const [agentId, setAgentId] = useState<number | null>(system ? system.agent_id : (own?.id ?? null));
  const [verifyTls, setVerifyTls] = useState(system?.verify_tls ?? true);
  const [enabled, setEnabled] = useState(system?.enabled ?? true);
  // Zugangsdaten (nur schreibbar): FortiGate = API-Token, Cisco ACI = APIC-Benutzer + Passwort
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [clearCreds, setClearCreds] = useState(false);
  const [readScope, setReadScope] = useState(system?.read_scope ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const hasCreds = system?.has_credentials ?? false;
  const isAci = platform === "cisco_aci";
  const kept = "hinterlegt – leer lassen = unverändert";
  // Zuweisung bleibt sichtbar, auch wenn der Agent nicht (mehr) in der Liste steht
  const options =
    agentId != null && !agents.some((a) => a.id === agentId)
      ? [...agents, { id: agentId, name: `Agent #${agentId}`, tenant_id: null, enabled: true } as Agent]
      : agents;
  const selected = options.find((a) => a.id === agentId);

  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setErr(null);
    const common = {
      name: name.trim(),
      base_url: baseUrl.trim(),
      agent_id: agentId,
      verify_tls: verifyTls,
      ...(isAci ? { read_scope: readScope.trim() } : {}),
    };
    // Nur geänderte Felder senden; leer gelassen = unverändert, "" = entfernen
    const creds: { token?: string; username?: string; password?: string } = {};
    if (clearCreds) {
      if (isAci) Object.assign(creds, { username: "", password: "" });
      else creds.token = "";
    } else if (isAci) {
      if (username.trim()) creds.username = username.trim();
      if (password) creds.password = password;
    } else if (token.trim()) {
      creds.token = token.trim();
    }
    try {
      if (system === null) {
        await createSystem({ tenant_id: tenant.id, platform, ...common, ...creds });
      } else {
        await patchSystem(system.id, { ...common, enabled, ...creds });
      }
      await onSaved();
    } catch (ex) {
      setErr(ex);
      setBusy(false);
    }
  };

  return (
    <Modal
      title={system ? `Quellsystem „${system.name}“` : "Neues Quellsystem"}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button className="btn btn-primary" onClick={() => submit()} disabled={busy || !name.trim()}>
            {busy ? "Speichere…" : system ? "Speichern" : "Anlegen"}
          </button>
        </>
      }
    >
      <form onSubmit={submit}>
        <ErrorBox error={err} />
        <div className="field">
          <label htmlFor="ss-name">Name</label>
          <input
            id="ss-name"
            className="input"
            value={name}
            autoFocus
            maxLength={200}
            placeholder="z. B. fw-01"
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="ss-platform">Plattform</label>
          <select
            id="ss-platform"
            className="sel input"
            value={platform}
            disabled={system !== null}
            onChange={(e) => setPlatform(e.target.value)}
          >
            <option value="fortigate">FortiGate</option>
            <option value="cisco_aci">Cisco ACI</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="ss-url">Adresse</label>
          <input
            id="ss-url"
            className="input mono"
            value={baseUrl}
            maxLength={500}
            placeholder={isAci ? "https://apic-01.kunde.lan" : "https://fw-01.kunde.lan"}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
          <div className="hint">So, wie der Agent das Gerät erreicht (vom Agent-Rechner aus gesehen).</div>
        </div>
        <div className="field">
          <label htmlFor="ss-agent">Verwaltet von</label>
          <select
            id="ss-agent"
            className="sel input"
            value={agentId ?? ""}
            onChange={(e) => setAgentId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">kein Agent – zentraler Worker</option>
            {options.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
                {a.tenant_id == null ? " (alle Mandanten)" : ""}
                {a.enabled ? "" : " – deaktiviert"}
              </option>
            ))}
          </select>
          <div className="hint">
            {agents.length === 0
              ? "Noch kein Agent für diesen Mandanten – unter „Collector-Agenten“ anlegen."
              : "Der Agent holt die Läufe dieses Geräts ab und bekommt dafür die Zugangsdaten. Jederzeit einem anderen Agenten zuweisbar."}
          </div>
          {selected && !selected.enabled && (
            <Note warn>Dieser Agent ist deaktiviert – Läufe des Geräts werden nicht abgeholt.</Note>
          )}
        </div>
        {isAci ? (
          <>
            <div className="field">
              <label htmlFor="ss-user">APIC-Benutzer (read-only)</label>
              <input
                id="ss-user"
                className="input mono"
                value={username}
                autoComplete="off"
                maxLength={200}
                disabled={clearCreds}
                placeholder={hasCreds ? kept : "z. B. ro-automato"}
                onChange={(e) => setUsername(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="ss-password">APIC-Passwort</label>
              <SecretInput
                id="ss-password"
                value={password}
                disabled={clearCreds}
                onChange={setPassword}
                placeholder={hasCreds ? kept : "Passwort des APIC-Benutzers"}
              />
              <div className="hint">
                Lokaler APIC-Benutzer mit Nur-Lese-Rolle (z. B. read-all, Security-Domain all); Remote-Benutzer als{" "}
                <code>apic#Domäne\Benutzer</code>. Wird verschlüsselt gespeichert und nie wieder angezeigt.
              </div>
            </div>
            <div className="field">
              <label htmlFor="ss-scope">ACI-Tenants</label>
              <input
                id="ss-scope"
                className="input mono"
                value={readScope}
                maxLength={500}
                placeholder="z. B. TE_share, TE_app"
                onChange={(e) => setReadScope(e.target.value)}
              />
              <div className="hint">Kommagetrennt. Leer = alle Tenants ausser infra und mgmt.</div>
            </div>
          </>
        ) : (
          <div className="field">
            <label htmlFor="ss-token">API-Token (read-only)</label>
            <SecretInput
              id="ss-token"
              value={token}
              disabled={clearCreds}
              onChange={setToken}
              placeholder={hasCreds ? kept : "REST-API-Token der FortiGate"}
            />
            <div className="hint">
              FortiGate: System → Administratoren → REST API Admin mit Nur-Lese-Profil; Trusted Hosts = Agent-Rechner.
              Wird verschlüsselt gespeichert und nie wieder angezeigt.
            </div>
          </div>
        )}
        {hasCreds && (
          <div className="field">
            <label className="check">
              <input type="checkbox" checked={clearCreds} onChange={(e) => setClearCreds(e.target.checked)} />
              Hinterlegte Zugangsdaten entfernen
            </label>
          </div>
        )}
        <div className="field">
          <label className="check">
            <input type="checkbox" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} />
            TLS-Zertifikat des Geräts prüfen (nur im Lab mit selbstsigniertem Zertifikat ausschalten)
          </label>
        </div>
        {system && (
          <div className="field">
            <label className="check">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              Aktiv – inaktive Geräte werden nicht eingelesen und nicht geprüft
            </label>
          </div>
        )}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

function SystemTable({
  systems,
  actions,
  agentName = (id) => `Agent #${id}`,
}: {
  systems: System[];
  actions?: (s: System) => React.ReactNode;
  agentName?: (id: number) => string;
}) {
  return (
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>Plattform</th>
          <th>Adresse</th>
          <th>Verwaltet von</th>
          <th>Zugangsdaten</th>
          <th className="num">Regeln</th>
          <th className="num">NAT</th>
          <th>Status</th>
          {actions && <th></th>}
        </tr>
      </thead>
      <tbody>
        {systems.map((s) => (
          <tr key={s.id}>
            <td className="nowrap">
              <b>{s.name}</b>
            </td>
            <td>
              <span className="chip">{PLATFORM_LABEL[s.platform] ?? s.platform}</span>
            </td>
            <td className="mono nowrap">
              {s.base_url || <span className="faint">–</span>}
              {s.read_scope && (
                <div className="faint" style={{ fontSize: 11 }} title="gelesene ACI-Tenants">
                  Tenants: {s.read_scope}
                </div>
              )}
            </td>
            <td className="nowrap">
              {s.agent_id != null ? (
                <span className="chip info">
                  <Icon name="agents" size={12} /> {agentName(s.agent_id)}
                </span>
              ) : (
                <span className="faint">zentraler Worker</span>
              )}
            </td>
            <td className="nowrap">
              {s.has_credentials ? (
                <span className="chip ok">
                  <Icon name="lock" size={12} /> hinterlegt
                </span>
              ) : (
                <span className={`chip ${s.agent_id != null ? "warn" : "na"}`}>fehlen</span>
              )}
              {!s.verify_tls && (
                <span className="chip warn" title="TLS-Zertifikat des Geräts wird nicht geprüft">
                  TLS aus
                </span>
              )}
            </td>
            <td className="num">{s.rule_count}</td>
            <td className="num">{s.nat_count}</td>
            <td>{s.enabled ? <span className="chip ok">aktiv</span> : <span className="chip na">inaktiv</span>}</td>
            {actions && <td className="actions">{actions(s)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DeleteSystemDialog({
  system,
  onClose,
  onDeleted,
}: {
  system: System;
  onClose: () => void;
  onDeleted: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await deleteSystem(system.id);
      await onDeleted();
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  };

  const data = system.rule_count + system.nat_count;
  return (
    <Modal
      title="Quellsystem löschen"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button className="btn btn-danger" onClick={submit} disabled={busy}>
            {busy ? "Lösche…" : "Löschen"}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <p>
        Quellsystem <b>„{system.name}“</b> löschen?
      </p>
      {data > 0 ? (
        <Note warn>
          Mit gelöscht werden die daraus gelesenen <b>{system.rule_count} Regeln</b> und{" "}
          <b>{system.nat_count} NAT-Einträge</b> samt ihrer Findings. Bisherige Läufe bleiben als Historie
          erhalten.
        </Note>
      ) : (
        <Note>Aus diesem System wurden noch keine Daten gelesen.</Note>
      )}
    </Modal>
  );
}

// Altbestand vor der Mandanten-Zuordnung: Quellsysteme ohne Mandant zuordnen oder löschen.
function UnassignedSystems({ tenants, onAssigned }: { tenants: Tenant[]; onAssigned: () => void }) {
  const [systems, setSystems] = useState<System[]>([]);
  const [target, setTarget] = useState<Record<number, number>>({});
  const [err, setErr] = useState<unknown>(null);
  const [toDelete, setToDelete] = useState<System | null>(null);

  const load = () =>
    fetchSystems()
      .then((all) => setSystems(all.filter((s) => s.tenant_id == null)))
      .catch(setErr);

  useEffect(() => {
    load();
  }, []);

  if (systems.length === 0 && !err) return null;

  const assign = async (s: System) => {
    const tid = target[s.id] ?? tenants[0]?.id;
    if (tid == null) return;
    setErr(null);
    try {
      await patchSystem(s.id, { tenant_id: tid });
      await load();
      onAssigned();
    } catch (e) {
      setErr(e);
    }
  };

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-head">
        <h2>Nicht zugeordnete Quellsysteme</h2>
        <span className="chip warn">{systems.length}</span>
      </div>
      <div className="card-body">
        <div style={{ padding: "10px 16px 0" }}>
          <ErrorBox error={err} onClose={() => setErr(null)} />
          <Note warn>
            Diese Quellsysteme stammen aus der Zeit vor der Mandanten-Zuordnung. Bitte einem Mandanten zuordnen
            (oder löschen) – erst dann lassen sie sich wieder einlesen.
          </Note>
        </div>
        <SystemTable
          systems={systems}
          actions={(s) => (
            <>
              <select
                className="sel"
                value={target[s.id] ?? tenants[0]?.id ?? ""}
                onChange={(e) => setTarget({ ...target, [s.id]: Number(e.target.value) })}
              >
                {tenants.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>{" "}
              <button className="btn btn-ghost btn-sm" onClick={() => assign(s)}>
                Zuordnen
              </button>
              <button className="linkbtn danger" title="Löschen" onClick={() => setToDelete(s)}>
                ✕
              </button>
            </>
          )}
        />
      </div>
      {toDelete && (
        <DeleteSystemDialog
          system={toDelete}
          onClose={() => setToDelete(null)}
          onDeleted={async () => {
            setToDelete(null);
            await load();
          }}
        />
      )}
    </div>
  );
}
