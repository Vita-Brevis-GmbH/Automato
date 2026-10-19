import { useEffect, useState } from "react";
import {
  createSite,
  createSystem,
  createTenant,
  deleteSite,
  fetchNamingProfiles,
  fetchSites,
  fetchSystems,
  NamingProfile,
  patchTenant,
  profileLabel,
  Site,
  System,
  Tenant,
} from "./api";
import type { Me } from "./auth";
import { Empty, ErrorBox, Icon, Loading, Modal, Note, PageHead } from "./ui";

const PLATFORM_LABEL: Record<string, string> = { fortigate: "FortiGate", cisco_aci: "Cisco ACI" };

// Mandanten (= Gruppengesellschaft) mit Kürzel, Namensprofil und Standorten; dazu die
// Quellsysteme. Lesen = viewer, Schreiben = admin (serverseitig erzwungen).
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
          />
          <Sites me={me} tenant={selected} />
        </div>
      )}
      {!selected && tenants && tenants.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <Note>Einen Mandanten anklicken, um Kürzel, Namensprofil und Standorte zu pflegen.</Note>
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        <SourceSystems me={me} />
      </div>

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
}: {
  me: Me;
  tenant: Tenant;
  profiles: NamingProfile[];
  onSaved: () => Promise<Tenant[]>;
}) {
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
          </div>
        )}
      </div>
    </div>
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

function SourceSystems({ me }: { me: Me }) {
  const [systems, setSystems] = useState<System[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [platform, setPlatform] = useState("fortigate");
  const [baseUrl, setBaseUrl] = useState("");
  const [err, setErr] = useState<unknown>(null);

  const load = () =>
    fetchSystems()
      .then(setSystems)
      .catch((e) => {
        setErr(e);
        setSystems([]);
      });

  useEffect(() => {
    load();
  }, []);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      await createSystem({ name: name.trim(), platform, base_url: baseUrl.trim() });
      setName("");
      setBaseUrl("");
      setAdding(false);
      await load();
    } catch (ex) {
      setErr(ex);
    }
  };

  return (
    <div className="card">
      <div className="card-head">
        <h2>Quellsysteme</h2>
        <span className="faint" style={{ fontSize: 12 }}>
          Firewalls/Controller, aus denen gelesen wird
        </span>
        <div className="spacer" />
        {me.isAdmin && !adding && (
          <button className="btn btn-ghost btn-sm" onClick={() => setAdding(true)}>
            + Quellsystem
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
            <div className="field">
              <label htmlFor="ss-name">Name</label>
              <input
                id="ss-name"
                className="input"
                value={name}
                required
                autoFocus
                placeholder="z. B. fw-01"
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="field narrow" style={{ flexBasis: 150 }}>
              <label htmlFor="ss-platform">Plattform</label>
              <select
                id="ss-platform"
                className="sel input"
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
              >
                <option value="fortigate">FortiGate</option>
                <option value="cisco_aci">Cisco ACI</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="ss-url">Adresse (nur Doku)</label>
              <input
                id="ss-url"
                className="input mono"
                value={baseUrl}
                placeholder="https://fw-01.kunde.lan"
                onChange={(e) => setBaseUrl(e.target.value)}
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
        {systems === null ? (
          <Loading />
        ) : systems.length === 0 ? (
          <Empty title="Noch keine Quellsysteme">
            Ein Quellsystem anlegen, dann unter „Übersicht → Jetzt einlesen“ einen Lauf starten.
          </Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Plattform</th>
                <th>Adresse</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {systems.map((s) => (
                <tr key={s.id}>
                  <td className="mono">{s.id}</td>
                  <td>
                    <b>{s.name}</b>
                  </td>
                  <td>
                    <span className="chip">{PLATFORM_LABEL[s.platform] ?? s.platform}</span>
                  </td>
                  <td className="mono">{s.base_url || <span className="faint">–</span>}</td>
                  <td>{s.enabled ? <span className="chip ok">aktiv</span> : <span className="chip na">inaktiv</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="card-foot">
        Zugangsdaten werden hier nie gespeichert – sie liegen beim Collector bzw. Agenten (ADR 0007/0008). Die
        Zuordnung zum Mandanten erfolgt beim Lauf.
      </div>
    </div>
  );
}
