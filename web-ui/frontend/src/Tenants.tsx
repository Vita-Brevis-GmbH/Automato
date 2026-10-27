import { useEffect, useState } from "react";
import {
  Agent,
  checkSystem,
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
  updateSite,
  fetchSystems,
  NamingProfile,
  patchSystem,
  patchTenant,
  profileLabel,
  Site,
  SiteKind,
  System,
  SystemCheck,
  SystemCredentials,
  Tenant,
} from "./api";
import type { Me } from "./auth";
import { L } from "./i18n";
import { PLATFORM_LABEL, REF_ROLE_LABEL, REFERENCE_PLATFORMS } from "./refs";
import { ScheduleField, scheduleLabel } from "./Schedule";
import { SITE_KIND_GROUP, SITE_KIND_LABEL, SiteChip, SiteOptions } from "./sites";
import { CredentialInput, Empty, ErrorBox, fmtTime, Icon, Loading, Modal, Note, PageHead, SecretInput } from "./ui";

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
    if (id == null) return L("default (Standard)", "default");
    const p = profiles.find((x) => x.id === id);
    return p ? profileLabel(p) : `#${id}`;
  };

  return (
    <>
      <PageHead
        title={L("Mandanten & Standorte", "Tenants & sites")}
        actions={
          me.isAdmin && (
            <button className="btn btn-primary btn-sm" onClick={() => setNewOpen(true)}>
              {L("+ Mandant", "+ Tenant")}
            </button>
          )
        }
      >
        {L(
          <>
            Ein Mandant ist eine Gruppengesellschaft (GG) mit Kürzel und einem oder mehreren Standorten.
            Mandanten wählen: Klick auf die Zeile (gilt auch für den Mandanten-Schalter oben).
          </>,
          <>
            A tenant is a group company (GC) with a code and one or more sites. Select a tenant: click the row
            (also applies to the tenant switcher at the top).
          </>,
        )}
      </PageHead>

      <div className="card">
        <div className="card-head">
          <h2>{L("Mandanten", "Tenants")}</h2>
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
              title={L("Noch keine Mandanten", "No tenants yet")}
              action={
                me.isAdmin && (
                  <button className="btn btn-primary btn-sm" onClick={() => setNewOpen(true)}>
                    {L("+ Mandant anlegen", "+ Create tenant")}
                  </button>
                )
              }
            >
              {L("Lege die erste Gruppengesellschaft an.", "Create the first group company.")}
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
                    {t.code && <span className="chip local">{L("GG", "GC")} {t.code}</span>}
                  </span>
                  <span className="s-meta mono" style={{ display: "block" }}>
                    ID {t.id} · SoT: {t.ipam_type} · DNS: {t.dns_type} · {L("Profil", "Profile")}:{" "}
                    {profileName(t.naming_profile_id)}
                  </span>
                </span>
                <span className="s-right">
                  {t.audit_reads && <span className="chip info">{L("Lese-Audit", "Read audit")}</span>}
                  {t.appdoc_foreign_names && (
                    <span
                      className="chip info"
                      title={L(
                        "Applikations-Doku: Gegenstellen mit Namen (nur Betreuer)",
                        "Application documentation: peers with names (staff only)",
                      )}
                    >
                      {L("Namen in der Doku", "Names in docs")}
                    </span>
                  )}
                  {t.id === tenantId && <span className="chip ok">{L("gewählt", "selected")}</span>}
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
          <Note>
            {L(
              "Einen Mandanten anklicken, um Kürzel, Namensprofil, Standorte und Quellsysteme zu pflegen.",
              "Click a tenant to maintain its code, naming profile, sites and source systems.",
            )}
          </Note>
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
      title={L("Neuer Mandant", "New tenant")}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-primary" onClick={() => submit()} disabled={busy || !name.trim()}>
            {busy ? L("Lege an…", "Creating…") : L("Anlegen", "Create")}
          </button>
        </>
      }
    >
      <form onSubmit={submit}>
        <ErrorBox error={err} />
        <div className="field">
          <label htmlFor="nt-name">{L("Name (Gruppengesellschaft)", "Name (group company)")}</label>
          <input
            id="nt-name"
            className="input"
            value={name}
            autoFocus
            maxLength={200}
            placeholder={L("z. B. Kunde A AG", "e.g. Customer A Ltd")}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="nt-code">{L("Kürzel (optional)", "Code (optional)")}</label>
          <input
            id="nt-code"
            className="input mono"
            value={code}
            maxLength={20}
            placeholder={L("z. B. ssgch", "e.g. ssgch")}
            onChange={(e) => setCode(e.target.value)}
          />
          <div className="hint">
            {L(
              "Kennung der GG bei VMs/Objekten. Keine Funktionsrelevanz, jederzeit änderbar.",
              "Identifier of the GC on VMs/objects. No functional relevance, can be changed at any time.",
            )}
          </div>
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
  // 0.95.0: Namen der Gegenstellen in der Applikations-Doku (nur Betreuer, setzt der Admin)
  const [foreignNames, setForeignNames] = useState(!!tenant.appdoc_foreign_names);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  const dirty =
    (code.trim() || null) !== tenant.code ||
    profileId !== tenant.naming_profile_id ||
    auditReads !== tenant.audit_reads ||
    foreignNames !== !!tenant.appdoc_foreign_names;

  const save = async () => {
    setBusy(true);
    setErr(null);
    setSaved(false);
    try {
      await patchTenant(tenant.id, {
        code: code.trim() || null,
        naming_profile_id: profileId,
        audit_reads: auditReads,
        appdoc_foreign_names: foreignNames,
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
          <label htmlFor="ts-code">{L("Kürzel (GG)", "Code (GC)")}</label>
          <input
            id="ts-code"
            className="input mono"
            value={code}
            maxLength={20}
            disabled={ro}
            placeholder={L("z. B. ssgch", "e.g. ssgch")}
            onChange={(e) => setCode(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="ts-profile">{L("Namensprofil", "Naming profile")}</label>
          <select
            id="ts-profile"
            className="sel input"
            value={profileId ?? ""}
            disabled={ro}
            onChange={(e) => setProfileId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">{L("Standard (Profil „default“)", "Default (profile “default”)")}</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {profileLabel(p)}
              </option>
            ))}
          </select>
          <div className="hint">
            {L(
              "Steuert die Präfix-Erkennung beim Einlesen. Ohne Auswahl gilt das Standardprofil „default“.",
              "Controls prefix detection during collection. Without a selection, the default profile “default” applies.",
            )}
          </div>
        </div>
        <div className="field">
          <label className="check">
            <input
              type="checkbox"
              checked={auditReads}
              disabled={ro}
              onChange={(e) => setAuditReads(e.target.checked)}
            />
            {L(
              "Lesezugriffe dieses Mandanten im Audit-Log protokollieren",
              "Record read access of this tenant in the audit log",
            )}
          </label>
        </div>
        <div className="field" id="ts-foreign-names">
          <label className="check">
            <input
              type="checkbox"
              checked={foreignNames}
              disabled={ro}
              onChange={(e) => setForeignNames(e.target.checked)}
            />
            {L(
              "Applikations-Doku: Namen der Gegenstellen zeigen",
              "Application documentation: show the names of peers",
            )}
          </label>
          <div className="hint">
            {L(
              <>
                Kennt eine Firewall-Regel von einer Gegenstelle (z. B. einem System ausserhalb des Tenants) nur die
                Adresse, nennt die Doku nach Kategorien – auch Markdown und Word – den Namen dazu: aus dem IPAM, der VM,
                der EPG bzw. dem Firewall-Objekt. Nur für Betreuer; Kunden-Benutzer sehen weiter nur die Adressen.
              </>,
              <>
                If a firewall rule only knows the address of a peer (e.g. a system outside the tenant), the
                documentation by category – also Markdown and Word – adds its name: from the IPAM, the VM, the EPG or
                the firewall object. Only for staff; customer users keep seeing only the addresses.
              </>,
            )}
          </div>
        </div>
        <div className="field">
          <span className="label">{L("Benutzer-Zuordnung", "User assignment")}</span>
          <div className="hint" style={{ marginTop: 0 }}>
            {L(
              <>
                In Keycloak beim Benutzer das Attribut <code>tenant_ids</code> = <code>{tenant.id}</code> setzen.
              </>,
              <>
                In Keycloak, set the user attribute <code>tenant_ids</code> = <code>{tenant.id}</code>.
              </>,
            )}
          </div>
        </div>
        {!ro && (
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <button className="btn btn-primary btn-sm" onClick={save} disabled={busy || !dirty}>
              {busy ? L("Speichere…", "Saving…") : L("Speichern", "Save")}
            </button>
            {saved && !dirty && <span className="chip ok">{L("gespeichert", "saved")}</span>}
            <div style={{ flex: 1 }} />
            <button className="btn btn-danger btn-sm" onClick={() => setDeleteOpen(true)}>
              {L("Mandant löschen", "Delete tenant")}
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
  rules: L("Regeln", "rules"),
  nat_rules: L("NAT-Einträge", "NAT entries"),
  findings: L("Findings", "findings"),
  references: L("Referenzeinträge (IPAM)", "reference entries (IPAM)"),
  epgs: "EPGs",
  objects: L("Adressobjekte", "address objects"),
  zones: L("Zonen", "zones"),
  sites: L("Standorte", "sites"),
  source_systems: L("Quellsysteme", "source systems"),
  runs: L("Läufe", "runs"),
  agents: L("Collector-Agenten", "collector agents"),
};

function countsText(counts: DeleteCounts): string {
  const parts = Object.entries(counts)
    .filter(([k, n]) => k !== "id" && n > 0)
    .map(([k, n]) => `${n} ${COUNT_LABEL[k] ?? k}`);
  return parts.length ? parts.join(", ") : L("keine weiteren Daten", "no further data");
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
        title={L("Mandant gelöscht", "Tenant deleted")}
        onClose={onDeleted}
        footer={
          <button className="btn btn-primary" onClick={onDeleted}>
            {L("Schliessen", "Close")}
          </button>
        }
      >
        <p>
          {L(
            <>
              „{tenant.name}“ wurde gelöscht, mit: {countsText(done)}.
            </>,
            <>
              “{tenant.name}” was deleted, with: {countsText(done)}.
            </>,
          )}
        </p>
        <Note>
          {L(
            <>
              In Keycloak bei betroffenen Benutzern die Mandanten-ID <code>{tenant.id}</code> aus dem Attribut{" "}
              <code>tenant_ids</code> entfernen.
            </>,
            <>
              In Keycloak, remove the tenant ID <code>{tenant.id}</code> from the attribute{" "}
              <code>tenant_ids</code> of the affected users.
            </>,
          )}
        </Note>
      </Modal>
    );
  }

  return (
    <Modal
      title={L("Mandant löschen", "Delete tenant")}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button
            className="btn btn-danger"
            onClick={submit}
            disabled={busy || typed.trim() !== tenant.name}
          >
            {busy ? L("Lösche…", "Deleting…") : L("Endgültig löschen", "Delete permanently")}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <Note warn>
        {L(
          <>
            Löscht <b>„{tenant.name}“</b> mit <b>allen</b> Daten: Quellsysteme mit ihren Regeln und NAT-Einträgen,
            Adressobjekte, Zonen, EPGs, Findings, Standorte, Läufe und die Collector-Agenten dieses Mandanten. Das
            lässt sich nicht rückgängig machen; das Audit-Log bleibt erhalten.
          </>,
          <>
            Deletes <b>“{tenant.name}”</b> with <b>all</b> data: source systems with their rules and NAT entries,
            address objects, zones, EPGs, findings, sites, runs and the collector agents of this tenant. This cannot
            be undone; the audit log is retained.
          </>,
        )}
      </Note>
      <div className="field">
        <label htmlFor="dt-confirm">
          {L("Zur Bestätigung den Namen des Mandanten eingeben", "Enter the tenant name to confirm")}
        </label>
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
  // Virtuelle (000, 001 …) und physische Standorte (chzrh1 …); ein virtueller kann an
  // mehreren physischen laufen (ADR 0015)
  const [sites, setSites] = useState<Site[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [kind, setKind] = useState<SiteKind>("virtual");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [physical, setPhysical] = useState<number[]>([]);
  const [linking, setLinking] = useState<number | null>(null); // virtueller Standort in Bearbeitung
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
    setLinking(null);
    load();
  }, [tenant.id]);

  const all = sites ?? [];
  const physicalSites = all.filter((s) => s.kind === "physical");
  const codeOf = (id: number) => all.find((s) => s.id === id)?.code ?? `#${id}`;

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      await createSite(tenant.id, {
        code: code.trim(),
        name: name.trim(),
        description: description.trim() || null,
        kind,
        physical_site_ids: kind === "virtual" ? physical : [],
      });
      setCode("");
      setName("");
      setDescription("");
      setPhysical([]);
      setAdding(false);
      await load();
    } catch (ex) {
      setErr(ex);
    }
  };

  const saveLinks = async (s: Site, ids: number[]) => {
    setErr(null);
    try {
      await updateSite(tenant.id, s.id, { physical_site_ids: ids });
      setLinking(null);
      await load();
    } catch (ex) {
      setErr(ex);
    }
  };

  const remove = async (s: Site) => {
    if (!window.confirm(L(`Standort ${s.code} „${s.name}“ löschen?`, `Delete site ${s.code} “${s.name}”?`))) return;
    setErr(null);
    try {
      await deleteSite(tenant.id, s.id);
      await load();
    } catch (ex) {
      setErr(ex);
    }
  };

  const section = (k: SiteKind) => {
    const list = all.filter((s) => s.kind === k);
    return (
      <>
        <div className="sub-h" style={{ padding: "10px 16px 0" }}>
          {SITE_KIND_GROUP[k]} ({list.length})
        </div>
        {list.length === 0 ? (
          <div className="faint" style={{ padding: "4px 16px 8px" }}>
            {k === "virtual"
              ? L(
                  "Keine – virtuelle Standorte sind dreistellige Nummern (z. B. 000 = Stretched DC).",
                  "None – virtual sites are three-digit numbers (e.g. 000 = stretched DC).",
                )
              : L(
                  "Keine – physische Standorte sind Ort-Codes (z. B. chzrh1).",
                  "None – physical sites are location codes (e.g. chzrh1).",
                )}
          </div>
        ) : (
          list.map((s) => (
            <div className="site-card" key={s.id}>
              <span className="site-ico">
                <Icon name="pin" size={20} />
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span className="s-name">
                  {s.name}{" "}
                  <span className={`chip ${k === "physical" ? "info" : "local"}`}>
                    {SITE_KIND_LABEL[k]} {s.code}
                  </span>
                </span>
                {s.description && <span className="s-meta" style={{ display: "block" }}>{s.description}</span>}
                <span className="s-meta" style={{ display: "block" }}>
                  {k === "virtual"
                    ? s.physical_site_ids.length
                      ? L(
                          `läuft an: ${s.physical_site_ids.map(codeOf).join(", ")}`,
                          `runs at: ${s.physical_site_ids.map(codeOf).join(", ")}`,
                        )
                      : L("keinem physischen Standort zugeordnet", "not assigned to a physical site")
                    : s.virtual_site_ids.length
                      ? L(
                          `virtuelle Standorte hier: ${s.virtual_site_ids.map(codeOf).join(", ")}`,
                          `virtual sites here: ${s.virtual_site_ids.map(codeOf).join(", ")}`,
                        )
                      : L("kein virtueller Standort zugeordnet", "no virtual site assigned")}
                </span>
                {linking === s.id && (
                  <LinkPicker
                    options={physicalSites}
                    value={s.physical_site_ids}
                    onCancel={() => setLinking(null)}
                    onSave={(ids) => saveLinks(s, ids)}
                  />
                )}
              </span>
              {me.isAdmin && (
                <span className="s-right">
                  {k === "virtual" && physicalSites.length > 0 && linking !== s.id && (
                    <button className="btn btn-ghost btn-sm" onClick={() => setLinking(s.id)}>
                      {L("Zuordnen", "Assign")}
                    </button>
                  )}
                  <button className="linkbtn danger" title={L("Löschen", "Delete")} onClick={() => remove(s)}>
                    ✕
                  </button>
                </span>
              )}
            </div>
          ))
        )}
      </>
    );
  };

  return (
    <div className="card">
      <div className="card-head">
        <h2>{L("Standorte", "Sites")}</h2>
        <div className="spacer" />
        {me.isAdmin && !adding && (
          <button className="btn btn-ghost btn-sm" onClick={() => setAdding(true)}>
            {L("+ Standort", "+ Site")}
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
              <label htmlFor="st-kind">{L("Art", "Kind")}</label>
              <select
                id="st-kind"
                className="sel input"
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value as SiteKind);
                  setCode("");
                }}
              >
                <option value="virtual">{L("virtuell", "virtual")}</option>
                <option value="physical">{L("physisch", "physical")}</option>
              </select>
            </div>
            <div className="field narrow">
              <label htmlFor="st-code">Code</label>
              <input
                id="st-code"
                className="input mono"
                value={code}
                required
                autoFocus
                pattern={kind === "virtual" ? "[0-9]{3}" : "[A-Za-z][A-Za-z0-9-]{2,15}"}
                maxLength={kind === "virtual" ? 3 : 16}
                title={
                  kind === "virtual"
                    ? L("dreistellige Nummer, z. B. 000 (Stretched DC)", "three-digit number, e.g. 000 (stretched DC)")
                    : L(
                        "Ort-Code, beginnt mit einem Buchstaben, z. B. chzrh1",
                        "location code, starts with a letter, e.g. chzrh1",
                      )
                }
                placeholder={kind === "virtual" ? "000" : "chzrh1"}
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
                placeholder={
                  kind === "virtual" ? L("z. B. Stretched DC", "e.g. Stretched DC") : L("z. B. Zürich RZ1", "e.g. Zurich DC1")
                }
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="st-desc">{L("Beschreibung", "Description")}</label>
              <input
                id="st-desc"
                className="input"
                value={description}
                placeholder={L("optional", "optional")}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            {kind === "virtual" && physicalSites.length > 0 && (
              <div className="field">
                <span className="label">{L("läuft an", "runs at")}</span>
                <span className="chips">
                  {physicalSites.map((p) => (
                    <label key={p.id} className="chip" style={{ cursor: "pointer" }}>
                      <input
                        type="checkbox"
                        checked={physical.includes(p.id)}
                        onChange={(e) =>
                          setPhysical(e.target.checked ? [...physical, p.id] : physical.filter((x) => x !== p.id))
                        }
                      />{" "}
                      {p.code}
                    </label>
                  ))}
                </span>
              </div>
            )}
            <button className="btn btn-primary btn-sm" type="submit">
              {L("Hinzufügen", "Add")}
            </button>
            <button className="btn btn-ghost btn-sm" type="button" onClick={() => setAdding(false)}>
              {L("Abbrechen", "Cancel")}
            </button>
          </form>
        )}
        {sites === null ? (
          <Loading />
        ) : sites.length === 0 && !adding ? (
          <Empty title={L("Noch keine Standorte", "No sites yet")}>
            {L(
              <>
                Virtuelle Standorte sind dreistellige Nummern (000, 001 …), physische Standorte Ort-Codes (z. B.
                chzrh1). Ein virtueller Standort kann an mehreren physischen laufen.
              </>,
              <>
                Virtual sites are three-digit numbers (000, 001 …), physical sites are location codes (e.g. chzrh1). A
                virtual site can run at several physical sites.
              </>,
            )}
          </Empty>
        ) : (
          <>
            {section("virtual")}
            {section("physical")}
          </>
        )}
      </div>
    </div>
  );
}

// Physische Standorte eines virtuellen auswählen
function LinkPicker({
  options,
  value,
  onSave,
  onCancel,
}: {
  options: Site[];
  value: number[];
  onSave: (ids: number[]) => void;
  onCancel: () => void;
}) {
  const [ids, setIds] = useState<number[]>(value);
  return (
    <span className="chips" style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
      {options.map((p) => (
        <label key={p.id} className="chip" style={{ cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={ids.includes(p.id)}
            onChange={(e) => setIds(e.target.checked ? [...ids, p.id] : ids.filter((x) => x !== p.id))}
          />{" "}
          {p.code} · {p.name}
        </label>
      ))}
      <button className="btn btn-primary btn-sm" type="button" onClick={() => onSave(ids)}>
        {L("Speichern", "Save")}
      </button>
      <button className="btn btn-ghost btn-sm" type="button" onClick={onCancel}>
        {L("Abbrechen", "Cancel")}
      </button>
    </span>
  );
}

function SourceSystems({ me, tenant }: { me: Me; tenant: Tenant }) {
  const [systems, setSystems] = useState<System[] | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [editing, setEditing] = useState<System | "new" | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [toDelete, setToDelete] = useState<System | null>(null);
  // Verbindung prüfen (zentral gelesene Systeme, vom Orchestrator aus)
  const [check, setCheck] = useState<SystemCheck | "busy" | null>(null);
  const runCheck = (s: System) => {
    setCheck("busy");
    checkSystem(s.id)
      .then(setCheck)
      .catch((e) => {
        setCheck(null);
        setErr(e);
      });
  };

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
        <h2>{L(<>Quellsysteme von „{tenant.name}“</>, <>Source systems of “{tenant.name}”</>)}</h2>
        <span className="faint" style={{ fontSize: 12 }}>
          {L(
            "Firewalls, Controller und Referenzquellen (IPAM), aus denen gelesen wird",
            "Firewalls, controllers and reference sources (IPAM) that are read",
          )}
        </span>
        <div className="spacer" />
        {me.isAdmin && (
          <button className="btn btn-ghost btn-sm" onClick={() => setEditing("new")}>
            {L("+ Quellsystem", "+ Source system")}
          </button>
        )}
      </div>
      <div className="card-body scroll">
        {err ? (
          <div style={{ padding: "10px 16px 0" }}>
            <ErrorBox error={err} onClose={() => setErr(null)} />
          </div>
        ) : null}
        {check === "busy" && <Note>{L("Verbindung wird geprüft…", "Checking connection…")}</Note>}
        {check && check !== "busy" && (
          <div style={{ padding: "10px 16px 0" }} id="system-check">
            <Note warn={!check.ok}>
              <b>
                {check.name}: {check.ok ? L("erreichbar", "reachable") : L("nicht erreichbar", "not reachable")}
              </b>{" "}
              {L("(geprüft vom Orchestrator)", "(checked by the orchestrator)")}
              <ul style={{ margin: "4px 0" }}>
                {check.steps.map((st, i) => (
                  <li key={i}>
                    {st.ok ? "✓" : "✗"}{" "}
                    {{ dns: "Name", tcp: L("Verbindung", "Connection"), http: "NetBox-API" }[st.step]}:{" "}
                    {st.detail}
                  </li>
                ))}
              </ul>
              {check.hint && <div>{check.hint}</div>}
              <button className="linkbtn" onClick={() => setCheck(null)}>
                {L("schliessen", "close")}
              </button>
            </Note>
          </div>
        )}
        {systems === null ? (
          <Loading />
        ) : systems.length === 0 ? (
          <Empty title={L("Noch keine Quellsysteme", "No source systems yet")}>
            {L(
              <>
                Gerät (FortiGate, APIC) oder Referenzquelle (Infoblox DDI, NetBox) mit Adresse und read-only
                Zugangsdaten anlegen und einem Collector-Agenten zuweisen, dann unter „Übersicht → Jetzt einlesen“ einen
                Lauf starten.
              </>,
              <>
                Create a device (FortiGate, APIC) or reference source (Infoblox DDI, NetBox) with address and read-only
                credentials, assign it to a collector agent, then start a run under “Overview → Collect now”.
              </>,
            )}
          </Empty>
        ) : (
          <SystemTable
            systems={systems}
            agentName={agentName}
            actions={
              me.isAdmin
                ? (s) => (
                    <>
                      {s.agent_id == null && s.base_url && (
                        <button
                          className="btn btn-ghost btn-sm"
                          disabled={check === "busy"}
                          title={L(
                            "Erreichbarkeit vom Orchestrator aus prüfen (Name, Verbindung, bei NetBox die API)",
                            "Check reachability from the orchestrator (name, connection, for NetBox the API)",
                          )}
                          onClick={() => runCheck(s)}
                        >
                          {L("Prüfen", "Check")}
                        </button>
                      )}
                      <button className="btn btn-ghost btn-sm" onClick={() => setEditing(s)}>
                        {L("Bearbeiten", "Edit")}
                      </button>
                      <button className="linkbtn danger" title={L("Löschen", "Delete")} onClick={() => setToDelete(s)}>
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
        <Icon name="lock" size={12} />{" "}
        {L(
          <>
            Zugangsdaten (Tokens, Benutzer/Passwort) werden verschlüsselt im Orchestrator
            gespeichert, nie wieder angezeigt und nur dem zugewiesenen Agenten für dessen Läufe übergeben (ADR 0009). Alle
            Quellsysteme werden nur gelesen; einzige Ausnahme ist das optionale NetBox-Schreib-Token für den NetBox-Sync – es
            bleibt im Orchestrator und geht nie an einen Agenten (ADR 0010).
          </>,
          <>
            Credentials (tokens, user/password) are stored encrypted in the orchestrator, never shown again and only
            handed to the assigned agent for its runs (ADR 0009). All source systems are only read; the only
            exception is the optional NetBox write token for the NetBox sync – it stays in the orchestrator and never
            goes to an agent (ADR 0010).
          </>,
        )}
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
  // Zugangsdaten (nur schreibbar): FortiGate/NetBox = API-Token, Cisco ACI/Infoblox = Benutzer + Passwort,
  // NetBox optional Schreib-Token (nur NetBox-Sync, bleibt im Orchestrator)
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [writeToken, setWriteToken] = useState("");
  const [clearCreds, setClearCreds] = useState(false);
  const [clearWrite, setClearWrite] = useState(false);
  const [readScope, setReadScope] = useState(system?.read_scope ?? "");
  const [refRole, setRefRole] = useState(system?.ref_role ?? "");
  const [siteId, setSiteId] = useState<number | null>(system?.site_id ?? null);
  const [schedule, setSchedule] = useState(system?.schedule ?? "");
  const [parallelGroup, setParallelGroup] = useState(system?.parallel_group ?? "");
  const [sites, setSites] = useState<Site[]>([]);
  // ohne Standort nutzt der NetBox-Sync den einzigen physischen bzw. den einzigen Standort
  const physicalOnly = sites.filter((s) => s.kind === "physical");
  const fallbackSite = physicalOnly.length === 1 ? physicalOnly[0] : sites.length === 1 ? sites[0] : null;
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const hasCreds = system?.has_credentials ?? false;
  const hasWrite = system?.has_write_token ?? false;
  const isAci = platform === "cisco_aci";
  const isInfoblox = platform === "infoblox";
  const isNetbox = platform === "netbox";
  const isVmware = platform === "vmware";
  const isSwitch = platform === "cisco_switch";
  const isAvi = platform === "avi";
  const isRef = REFERENCE_PLATFORMS.includes(platform);
  const userPass = isAci || isInfoblox || isVmware || isSwitch || isAvi;
  // Switches (0.62.0): Host-Schlüssel vergessen (beim nächsten Lauf neu lernen)
  const [forget, setForget] = useState<string[]>([]);

  useEffect(() => {
    fetchSites(tenant.id)
      .then(setSites)
      .catch(() => setSites([]));
  }, [tenant.id]);
  const kept = L("hinterlegt – leer lassen = unverändert", "stored – leave empty = unchanged");
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
      ...(isAci || isSwitch || isAvi ? { read_scope: readScope.trim() } : {}),
      ...(isRef && refRole ? { ref_role: refRole } : {}),
      ...(isRef ? {} : { site_id: siteId }),
      schedule: schedule.trim(),
    };
    // Nur geänderte Felder senden; leer gelassen = unverändert, "" = entfernen
    const creds: SystemCredentials = {};
    if (clearCreds) {
      if (userPass) Object.assign(creds, { username: "", password: "" });
      else creds.token = "";
    } else if (userPass) {
      if (username.trim()) creds.username = username.trim();
      if (password) creds.password = password;
    } else if (token.trim()) {
      creds.token = token.trim();
    }
    if (isNetbox) {
      if (clearCreds || clearWrite) creds.write_token = "";
      else if (writeToken.trim()) creds.write_token = writeToken.trim();
    }
    try {
      if (system === null) {
        await createSystem({ tenant_id: tenant.id, platform, ...common, ...creds });
      } else {
        const group = platform === "fortigate" ? { parallel_group: parallelGroup.trim() } : {};
        const keys = isSwitch && forget.length ? { forget_host_keys: forget } : {};
        await patchSystem(system.id, { ...common, enabled, ...group, ...keys, ...creds });
      }
      await onSaved();
    } catch (ex) {
      setErr(ex);
      setBusy(false);
    }
  };

  return (
    <Modal
      title={
        system
          ? L(`Quellsystem „${system.name}“`, `Source system “${system.name}”`)
          : L("Neues Quellsystem", "New source system")
      }
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-primary" onClick={() => submit()} disabled={busy || !name.trim()}>
            {busy ? L("Speichere…", "Saving…") : system ? L("Speichern", "Save") : L("Anlegen", "Create")}
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
            placeholder={L("z. B. fw-01", "e.g. fw-01")}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="ss-platform">{L("Plattform", "Platform")}</label>
          <select
            id="ss-platform"
            className="sel input"
            value={platform}
            disabled={system !== null}
            onChange={(e) => setPlatform(e.target.value)}
          >
            <option value="fortigate">FortiGate</option>
            <option value="cisco_aci">Cisco ACI</option>
            <option value="vmware">VMware vCenter</option>
            <option value="cisco_switch">
              {L(
                "Switches & Router (SSH: Cisco Catalyst/Nexus, Juniper, Arista, Aruba CX)",
                "Switches & routers (SSH: Cisco Catalyst/Nexus, Juniper, Arista, Aruba CX)",
              )}
            </option>
            <option value="avi">VMware NSX ALB (Avi, Load Balancer)</option>
            <option value="infoblox">{L("Infoblox DDI (Referenzquelle)", "Infoblox DDI (reference source)")}</option>
            <option value="netbox">{L("NetBox (Referenzquelle)", "NetBox (reference source)")}</option>
          </select>
          {isRef && (
            <div className="hint">
              {L(
                <>
                  Referenzquelle für den Abgleich: wird komplett gelesen (Netze, Ranges, IPs, DNS-Einträge, VLANs) und
                  mit den Firewall-/ACI-Daten verglichen.
                </>,
                <>
                  Reference source for the reconciliation: read completely (networks, ranges, IPs, DNS records, VLANs)
                  and compared with the firewall/ACI data.
                </>,
              )}
            </div>
          )}
        </div>
        {isRef && (
          <div className="field">
            <label htmlFor="ss-role">{L("Rolle", "Role")}</label>
            <select id="ss-role" className="sel input" value={refRole} onChange={(e) => setRefRole(e.target.value)}>
              {system === null && (
                <option value="">{L("automatisch (DDI führend, sonst NetBox)", "automatic (DDI primary, otherwise NetBox)")}</option>
              )}
              <option value="primary">
                {REF_ROLE_LABEL.primary}{" "}
                {L("– Quelle der Wahrheit für Netze, IPs, DNS", "– source of truth for networks, IPs, DNS")}
              </option>
              <option value="enrichment">
                {REF_ROLE_LABEL.enrichment}{" "}
                {L("– zusätzliche Daten (Standort, Tenant, Links)", "– additional data (site, tenant, links)")}
              </option>
            </select>
            <div className="hint">
              {L(
                "Je Mandant gibt es genau ein führendes IPAM; ein neues führendes stuft das bisherige zur Anreicherung herab.",
                "Each tenant has exactly one leading IPAM; a new leading one demotes the previous one to enrichment.",
              )}
            </div>
          </div>
        )}
        {isSwitch && (
          <div className="field">
            <label htmlFor="ss-switches">{L("Switches und Router", "Switches and routers")}</label>
            <textarea
              id="ss-switches"
              className="input mono"
              rows={5}
              value={readScope}
              placeholder={"10.0.0.11\nsw-acc-02.kunde.lan\n10.0.0.2:2222"}
              onChange={(e) => setReadScope(e.target.value)}
            />
            <div className="hint">
              {L(
                <>
                  Eine Adresse je Zeile (bzw. kommagetrennt), optional mit SSH-Port. Alle Geräte teilen das Konto unten; der
                  Agent liest sie parallel und meldet, welche nicht erreichbar waren. Die Plattform erkennt Automato an{" "}
                  <code>show version</code>:
                </>,
                <>
                  One address per line (or comma-separated), optionally with SSH port. All devices share the account
                  below; the agent reads them in parallel and reports which were not reachable. Automato detects the
                  platform from <code>show version</code>:
                </>,
              )}
              <ul className="ss-products" id="ss-switch-products">
                <li>Cisco Catalyst 9200L, 9300, 3650, 4500 – IOS/IOS-XE</li>
                <li>{L("Cisco Nexus 9000 – NX-OS (mit vPC)", "Cisco Nexus 9000 – NX-OS (with vPC)")}</li>
                <li>
                  {L(
                    "Cisco Catalyst 1200/1300 – Business-CLI, über eine begrenzte Sitzung (kein Exec)",
                    "Cisco Catalyst 1200/1300 – business CLI, via a restricted session (no exec)",
                  )}
                </li>
                <li>
                  {L(
                    "Juniper MX (Router), EX/QFX – Junos, strukturierte Ausgabe (JSON)",
                    "Juniper MX (router), EX/QFX – Junos, structured output (JSON)",
                  )}
                </li>
                <li>{L("Arista 7000er-Serie – EOS, strukturierte Ausgabe (JSON)", "Arista 7000 series – EOS, structured output (JSON)")}</li>
                <li>
                  {L(
                    "HPE Aruba CX 6000/8000 – AOS-CX (nicht ArubaOS-Switch/ProCurve)",
                    "HPE Aruba CX 6000/8000 – AOS-CX (not ArubaOS-Switch/ProCurve)",
                  )}
                </li>
              </ul>
              {L(
                <>
                  Gelesen werden je Gerät Ports, VLANs, Nachbarn (CDP/LLDP), MAC-Tabelle, ARP und die IPv6-Nachbarn (NDP); fehlt ein
                  Befehl auf älterer Software, bleibt der Teil leer.
                </>,
                <>
                  Read per device: ports, VLANs, neighbors (CDP/LLDP), MAC table, ARP and the IPv6 neighbors (NDP); if a command
                  is missing on older software, that part stays empty.
                </>,
              )}
            </div>
          </div>
        )}
        {system !== null && isSwitch && (system.host_keys ?? []).length > 0 && (
          <div className="field" id="ss-hostkeys">
            <label>{L("SSH-Host-Schlüssel (beim ersten Lauf festgehalten)", "SSH host keys (pinned on the first run)")}</label>
            <table className="compact">
              <tbody>
                {(system.host_keys ?? []).map((k) => (
                  <tr key={k.host}>
                    <td className="mono">{k.host}</td>
                    <td className="mono faint">{k.fingerprint}</td>
                    <td>
                      <label className="check">
                        <input
                          type="checkbox"
                          checked={forget.includes(k.host)}
                          onChange={(e) => setForget((f) => (e.target.checked ? [...f, k.host] : f.filter((h) => h !== k.host)))}
                        />
                        {L("vergessen", "forget")}
                      </label>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="hint">
              {L(
                <>
                  Weist ein Switch einen anderen Schlüssel vor, bricht die Verbindung vor der Anmeldung ab. Nach einem
                  geplanten Wechsel (neues Gerät, neuer Schlüssel) hier „vergessen“ – der nächste Lauf lernt ihn neu.
                </>,
                <>
                  If a switch presents a different key, the connection is aborted before login. After a planned change
                  (new device, new key) “forget” it here – the next run learns it again.
                </>,
              )}
            </div>
          </div>
        )}
        {!isSwitch && (
        <div className="field">
          <label htmlFor="ss-url">{L("Adresse", "Address")}</label>
          <input
            id="ss-url"
            className="input mono"
            value={baseUrl}
            maxLength={500}
            placeholder={
              isAci
                ? "https://apic-01.kunde.lan"
                : isVmware
                  ? "https://vcenter.kunde.lan"
                  : isAvi
                  ? "https://avi-controller.kunde.lan"
                  : isInfoblox
                  ? "https://ddi.kunde.lan"
                  : isNetbox
                    ? "https://netbox.kunde.lan"
                    : "https://fw-01.kunde.lan"
            }
            onChange={(e) => setBaseUrl(e.target.value)}
          />
          <div className="hint">
            {L(
              "So, wie der Agent das Gerät erreicht (vom Agent-Rechner aus gesehen).",
              "As the agent reaches the device (seen from the agent host).",
            )}
          </div>
        </div>
        )}
        {!isRef && (
          <div className="field">
            <label htmlFor="ss-site">{L("Standort", "Site")}</label>
            <select
              id="ss-site"
              className="sel input"
              value={siteId ?? ""}
              onChange={(e) => setSiteId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">
                {fallbackSite
                  ? L(
                      `nicht gesetzt (einziger Standort ${fallbackSite.code} wird genutzt)`,
                      `not set (only site ${fallbackSite.code} is used)`,
                    )
                  : L("nicht gesetzt", "not set")}
              </option>
              <SiteOptions sites={sites} />
            </select>
            <div className="hint">
              {L(
                <>
                  Dort legt der NetBox-Sync das Gerät mit seinen Ports an – meist der physische Standort, bei einem
                  Stretched-Cluster der virtuelle (z. B. 000).
                </>,
                <>
                  The NetBox sync creates the device with its ports there – usually the physical site, for a stretched
                  cluster the virtual one (e.g. 000).
                </>,
              )}
              {system?.site_hint && siteId == null && (
                <>
                  {" "}
                  {L("Aus dem Namen:", "From the name:")} <SiteChip site={system.site_hint} from="name" />
                  {system.site_hint.known && system.site_hint.id != null && (
                    <button
                      type="button"
                      className="linkbtn"
                      onClick={() => setSiteId(system.site_hint!.id ?? null)}
                      style={{ marginLeft: 6 }}
                    >
                      {L("übernehmen", "apply")}
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        )}
        <ScheduleField value={schedule} onChange={setSchedule} />
        {system !== null && platform === "fortigate" && (
          <div className="field">
            <label htmlFor="ss-parallel">{L("Verbund (parallele Firewalls)", "Parallel group (parallel firewalls)")}</label>
            <input
              id="ss-parallel"
              className="input"
              placeholder={L("z. B. Cluster Nord – leer = keiner", "e.g. Cluster North – empty = none")}
              value={parallelGroup}
              onChange={(e) => setParallelGroup(e.target.value)}
            />
            <div className="hint">
              {L(
                <>
                  Firewalls mit demselben Verbund tragen dieselben Regeln (z. B. zwei Cluster mit Session-Sync): gleiche
                  Policies erscheinen einmal, Abweichungen als Findings.
                </>,
                <>
                  Firewalls in the same parallel group carry the same rules (e.g. two clusters with session sync):
                  identical policies appear once, deviations as findings.
                </>,
              )}
            </div>
          </div>
        )}
        <div className="field">
          <label htmlFor="ss-agent">{L("Verwaltet von", "Managed by")}</label>
          <select
            id="ss-agent"
            className="sel input"
            value={agentId ?? ""}
            onChange={(e) => setAgentId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">{L("kein Agent – zentraler Worker", "no agent – central worker")}</option>
            {options.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
                {a.tenant_id == null ? L(" (alle Mandanten)", " (all tenants)") : ""}
                {a.enabled ? "" : L(" – deaktiviert", " – disabled")}
              </option>
            ))}
          </select>
          <div className="hint">
            {agents.length === 0
              ? L(
                  "Noch kein Agent für diesen Mandanten – unter „Collector-Agenten“ anlegen.",
                  "No agent for this tenant yet – create one under “Collector agents”.",
                )
              : L(
                  "Der Agent holt die Läufe dieses Geräts ab und bekommt dafür die Zugangsdaten. Jederzeit einem anderen Agenten zuweisbar.",
                  "The agent picks up the runs of this device and receives the credentials for them. Can be assigned to another agent at any time.",
                )}
          </div>
          {selected && !selected.enabled && (
            <Note warn>
              {L(
                "Dieser Agent ist deaktiviert – Läufe des Geräts werden nicht abgeholt.",
                "This agent is disabled – runs of the device are not picked up.",
              )}
            </Note>
          )}
        </div>
        {isInfoblox ? (
          <>
            <div className="field">
              <label htmlFor="ss-user">{L("Infoblox-Benutzer (read-only)", "Infoblox user (read-only)")}</label>
              <CredentialInput
                id="ss-user"
                value={username}
                maxLength={200}
                disabled={clearCreds}
                placeholder={hasCreds ? kept : L("z. B. ro-automato", "e.g. ro-automato")}
                onChange={setUsername}
              />
            </div>
            <div className="field">
              <label htmlFor="ss-password">{L("Passwort", "Password")}</label>
              <SecretInput
                id="ss-password"
                value={password}
                disabled={clearCreds}
                onChange={setPassword}
                placeholder={hasCreds ? kept : L("Passwort des Infoblox-Benutzers", "Password of the Infoblox user")}
              />
              <div className="hint">
                {L(
                  <>
                    WAPI-Benutzer einer Admin-Gruppe mit API-Zugriff und nur Lese-Rechten (DNS, IPAM, DHCP). Automato liest
                    ausschliesslich (nur GET) – Schreibzugriffe sind im Code gesperrt.
                  </>,
                  <>
                    WAPI user of an admin group with API access and read-only permissions (DNS, IPAM, DHCP). Automato
                    only reads (GET only) – write access is blocked in the code.
                  </>,
                )}
              </div>
            </div>
          </>
        ) : isNetbox ? (
          <>
            <div className="field">
              <label htmlFor="ss-token">{L("API-Token (lesen)", "API token (read)")}</label>
              <SecretInput
                id="ss-token"
                value={token}
                disabled={clearCreds}
                onChange={setToken}
                placeholder={hasCreds ? kept : L("NetBox-Token ohne Schreibrecht", "NetBox token without write permission")}
              />
              <div className="hint">
                {L(
                  "Für das Einlesen (Agent/Worker). NetBox: Token mit „Write enabled“ aus.",
                  "For collecting (agent/worker). NetBox: token with “Write enabled” off.",
                )}
              </div>
            </div>
            <div className="field">
              <label htmlFor="ss-wtoken">
                {L("Schreib-Token (optional, nur NetBox-Sync)", "Write token (optional, NetBox sync only)")}
              </label>
              <SecretInput
                id="ss-wtoken"
                value={writeToken}
                disabled={clearCreds || clearWrite}
                onChange={setWriteToken}
                placeholder={hasWrite ? kept : L("leer = NetBox wird nur gelesen", "empty = NetBox is only read")}
              />
              <div className="hint">
                {L(
                  <>
                    Nur für den NetBox-Sync (Vorschau → Übernehmen durch einen Admin). Bleibt im Orchestrator und wird nie
                    an einen Agenten übergeben.
                  </>,
                  <>
                    Only for the NetBox sync (preview → apply by an admin). Stays in the orchestrator and is never handed
                    to an agent.
                  </>,
                )}
              </div>
              {hasWrite && (
                <label className="check">
                  <input type="checkbox" checked={clearWrite} onChange={(e) => setClearWrite(e.target.checked)} />
                  {L("Schreib-Token entfernen", "Remove write token")}
                </label>
              )}
            </div>
          </>
        ) : isVmware ? (
          <>
            <div className="field">
              <label htmlFor="ss-user">{L("vCenter-Benutzer (Read-only)", "vCenter user (read-only)")}</label>
              <CredentialInput
                id="ss-user"
                value={username}
                maxLength={200}
                disabled={clearCreds}
                placeholder={hasCreds ? kept : L("z. B. ro-automato@vsphere.local", "e.g. ro-automato@vsphere.local")}
                onChange={setUsername}
              />
            </div>
            <div className="field">
              <label htmlFor="ss-password">{L("vCenter-Passwort", "vCenter password")}</label>
              <SecretInput
                id="ss-password"
                value={password}
                disabled={clearCreds}
                onChange={setPassword}
                placeholder={hasCreds ? kept : L("Passwort des vCenter-Benutzers", "Password of the vCenter user")}
              />
              <div className="hint">
                {L(
                  <>
                    Konto mit der Rolle <b>Read-only</b> auf dem vCenter-Objekt (oberste Ebene) mit „An untergeordnete Objekte
                    weitergeben“ – oder als globale Berechtigung, ebenfalls mit Weitergabe. Automato liest VMs, Hosts, Cluster
                    und Portgruppen; schreibende vSphere-Aufrufe sind im Code gesperrt. Wird verschlüsselt gespeichert.
                  </>,
                  <>
                    Account with the role <b>Read-only</b> on the vCenter object (top level) with “Propagate to
                    children” – or as a global permission, also with propagation. Automato reads VMs, hosts, clusters
                    and port groups; writing vSphere calls are blocked in the code. Stored encrypted.
                  </>,
                )}
              </div>
            </div>
          </>
        ) : isSwitch ? (
          <>
            <div className="field">
              <label htmlFor="ss-user">{L("SSH-Benutzer (nur Leserechte)", "SSH user (read-only permissions)")}</label>
              <CredentialInput
                id="ss-user"
                value={username}
                maxLength={200}
                disabled={clearCreds}
                placeholder={hasCreds ? kept : L("z. B. ro-automato", "e.g. ro-automato")}
                onChange={setUsername}
              />
            </div>
            <div className="field">
              <label htmlFor="ss-password">{L("SSH-Passwort", "SSH password")}</label>
              <SecretInput
                id="ss-password"
                value={password}
                disabled={clearCreds}
                onChange={setPassword}
                placeholder={hasCreds ? kept : L("Passwort des SSH-Benutzers", "Password of the SSH user")}
              />
              <div className="hint">
                {L(
                  <>
                    Konto nur mit Leserechten (IOS/IOS-XE privilege 1 bzw. eine View mit den show-Befehlen, NX-OS-Rolle{" "}
                    <code>network-operator</code>, Junos-Klasse <code>read-only</code>, Catalyst 1200/1300 Stufe 1, Arista-Rolle{" "}
                    <code>network-operator</code>, AOS-CX-Gruppe <code>operators</code>). Automato
                    führt nur feste <code>show</code>-Befehle aus (je Befehl ein SSH-Aufruf, kein Konfigurationsmodus); nur Geräte
                    ohne Exec (Catalyst 1200/1300, je nach Version Aruba CX) werden über eine Sitzung gelesen, in der
                    ausschliesslich dieselben Befehle und das Abschalten des Seitenumbruchs für die Sitzung gesendet werden. Wird
                    verschlüsselt gespeichert.
                  </>,
                  <>
                    Account with read-only permissions only (IOS/IOS-XE privilege 1 or a view with the show commands,
                    NX-OS role <code>network-operator</code>, Junos class <code>read-only</code>, Catalyst 1200/1300
                    level 1, Arista role <code>network-operator</code>, AOS-CX group <code>operators</code>). Automato
                    only runs fixed <code>show</code> commands (one SSH call per command, no configuration mode); only
                    devices without exec (Catalyst 1200/1300, depending on the version Aruba CX) are read via a session
                    in which only the same commands and disabling paging for the session are sent. Stored encrypted.
                  </>,
                )}
              </div>
            </div>
          </>
        ) : isAvi ? (
          <>
            <div className="field">
              <label htmlFor="ss-user">{L("Avi-Benutzer (nur Leserechte)", "Avi user (read-only permissions)")}</label>
              <CredentialInput
                id="ss-user"
                value={username}
                maxLength={200}
                disabled={clearCreds}
                placeholder={hasCreds ? kept : L("z. B. ro-automato", "e.g. ro-automato")}
                onChange={setUsername}
              />
            </div>
            <div className="field">
              <label htmlFor="ss-password">{L("Avi-Passwort", "Avi password")}</label>
              <SecretInput
                id="ss-password"
                value={password}
                disabled={clearCreds}
                onChange={setPassword}
                placeholder={hasCreds ? kept : L("Passwort des Avi-Benutzers", "Password of the Avi user")}
              />
              <div className="hint">
                {L(
                  <>
                    Konto mit einer eigenen Rolle nur mit Leserechten (Virtual Service, VS VIP, Pool, Pool Group, VRF Context,
                    Application Profile, Tenant). Automato meldet sich an und ab und liest sonst nur (GET); Zertifikate,
                    Schlüssel und Benutzer liest es nicht. Wird verschlüsselt gespeichert und nie wieder angezeigt.
                  </>,
                  <>
                    Account with a custom role with read-only permissions only (Virtual Service, VS VIP, Pool, Pool
                    Group, VRF Context, Application Profile, Tenant). Automato logs in and out and otherwise only reads
                    (GET); it does not read certificates, keys or users. Stored encrypted and never shown again.
                  </>,
                )}
              </div>
            </div>
            <div className="field">
              <label htmlFor="ss-scope">Avi-Tenants</label>
              <input
                id="ss-scope"
                className="input mono"
                value={readScope}
                maxLength={500}
                placeholder={L("z. B. admin, kunde-a", "e.g. admin, customer-a")}
                onChange={(e) => setReadScope(e.target.value)}
              />
              <div className="hint">
                {L(
                  "Kommagetrennt. Leer = alle Tenants, die das Konto sieht.",
                  "Comma-separated. Empty = all tenants the account can see.",
                )}
              </div>
            </div>
          </>
        ) : isAci ? (
          <>
            <div className="field">
              <label htmlFor="ss-user">{L("APIC-Benutzer (read-only)", "APIC user (read-only)")}</label>
              <CredentialInput
                id="ss-user"
                value={username}
                maxLength={200}
                disabled={clearCreds}
                placeholder={hasCreds ? kept : L("z. B. ro-automato", "e.g. ro-automato")}
                onChange={setUsername}
              />
            </div>
            <div className="field">
              <label htmlFor="ss-password">{L("APIC-Passwort", "APIC password")}</label>
              <SecretInput
                id="ss-password"
                value={password}
                disabled={clearCreds}
                onChange={setPassword}
                placeholder={hasCreds ? kept : L("Passwort des APIC-Benutzers", "Password of the APIC user")}
              />
              <div className="hint">
                {L(
                  <>
                    Lokaler APIC-Benutzer mit Nur-Lese-Rolle (z. B. read-all, Security-Domain all); Remote-Benutzer als{" "}
                    <code>apic#Domäne\Benutzer</code>. Wird verschlüsselt gespeichert und nie wieder angezeigt.
                  </>,
                  <>
                    Local APIC user with a read-only role (e.g. read-all, security domain all); remote users as{" "}
                    <code>apic#domain\user</code>. Stored encrypted and never shown again.
                  </>,
                )}
              </div>
            </div>
            <div className="field">
              <label htmlFor="ss-scope">ACI-Tenants</label>
              <input
                id="ss-scope"
                className="input mono"
                value={readScope}
                maxLength={500}
                placeholder={L("z. B. TE_share, TE_app", "e.g. TE_share, TE_app")}
                onChange={(e) => setReadScope(e.target.value)}
              />
              <div className="hint">
                {L(
                  "Kommagetrennt. Leer = alle Tenants ausser infra und mgmt.",
                  "Comma-separated. Empty = all tenants except infra and mgmt.",
                )}
              </div>
            </div>
          </>
        ) : (
          <div className="field">
            <label htmlFor="ss-token">{L("API-Token (read-only)", "API token (read-only)")}</label>
            <SecretInput
              id="ss-token"
              value={token}
              disabled={clearCreds}
              onChange={setToken}
              placeholder={hasCreds ? kept : L("REST-API-Token der FortiGate", "REST API token of the FortiGate")}
            />
            <div className="hint">
              {L(
                <>
                  FortiGate: System → Administratoren → REST API Admin mit Nur-Lese-Profil; Trusted Hosts = Agent-Rechner.
                  Wird verschlüsselt gespeichert und nie wieder angezeigt.
                </>,
                <>
                  FortiGate: System → Administrators → REST API Admin with read-only profile; trusted hosts = agent
                  host. Stored encrypted and never shown again.
                </>,
              )}
            </div>
          </div>
        )}
        {(hasCreds || hasWrite) && (
          <div className="field">
            <label className="check">
              <input type="checkbox" checked={clearCreds} onChange={(e) => setClearCreds(e.target.checked)} />
              {isNetbox
                ? L("Alle hinterlegten Tokens entfernen", "Remove all stored tokens")
                : L("Hinterlegte Zugangsdaten entfernen", "Remove stored credentials")}
            </label>
          </div>
        )}
        <div className="field">
          <label className="check">
            <input type="checkbox" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} />
            {L(
              "TLS-Zertifikat des Geräts prüfen (nur im Lab mit selbstsigniertem Zertifikat ausschalten)",
              "Verify the device's TLS certificate (only turn off in the lab with a self-signed certificate)",
            )}
          </label>
        </div>
        {system && (
          <div className="field">
            <label className="check">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              {L(
                "Aktiv – inaktive Geräte werden nicht eingelesen und nicht geprüft",
                "Active – inactive devices are not collected and not checked",
              )}
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
          <th>{L("Plattform", "Platform")}</th>
          <th>{L("Adresse", "Address")}</th>
          <th>{L("Verwaltet von", "Managed by")}</th>
          <th>{L("Zugangsdaten", "Credentials")}</th>
          <th>{L("Daten", "Data")}</th>
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
              {s.ref_role && (
                <div style={{ marginTop: 3 }}>
                  <span className={`chip ${s.ref_role === "primary" ? "local" : "info"}`}>
                    {REF_ROLE_LABEL[s.ref_role] ?? s.ref_role}
                  </span>
                </div>
              )}
            </td>
            <td className="mono nowrap">
              {s.base_url || <span className="faint">–</span>}
              {s.read_scope && s.platform === "cisco_switch" ? (
                <div className="faint" style={{ fontSize: 11 }} title={s.read_scope}>
                  {s.read_scope.split(/\s+/).length} Switches
                </div>
              ) : (
                s.read_scope && (
                  <div className="faint" style={{ fontSize: 11 }} title={L("gelesene ACI-Tenants", "ACI tenants read")}>
                    Tenants: {s.read_scope}
                  </div>
                )
              )}
            </td>
            <td className="nowrap">
              {s.agent_id != null ? (
                <span className="chip info">
                  <Icon name="agents" size={12} /> {agentName(s.agent_id)}
                </span>
              ) : (
                <span className="faint">{L("zentraler Worker", "central worker")}</span>
              )}
            </td>
            <td className="nowrap">
              {s.has_credentials ? (
                <span className="chip ok">
                  <Icon name="lock" size={12} /> {L("hinterlegt", "stored")}
                </span>
              ) : s.has_write_token ? (
                <span
                  className="chip warn"
                  title={L("Zum Einlesen wird das Lese-Token gebraucht", "The read token is needed for collecting")}
                >
                  {L("Lese-Token fehlt", "Read token missing")}
                </span>
              ) : (
                <span className={`chip ${s.agent_id != null || REFERENCE_PLATFORMS.includes(s.platform) ? "warn" : "na"}`}>
                  {L("fehlen", "missing")}
                </span>
              )}
              {s.has_write_token && (
                <span
                  className="chip local"
                  title={L(
                    "Schreib-Token für den NetBox-Sync (bleibt im Orchestrator)",
                    "Write token for the NetBox sync (stays in the orchestrator)",
                  )}
                >
                  {L("Schreib-Token", "Write token")}
                </span>
              )}
              {!s.verify_tls && (
                <span
                  className="chip warn"
                  title={L("TLS-Zertifikat des Geräts wird nicht geprüft", "The device's TLS certificate is not verified")}
                >
                  {L("TLS aus", "TLS off")}
                </span>
              )}
            </td>
            <td className="nowrap">
              {REFERENCE_PLATFORMS.includes(s.platform) ? (
                s.reference_at ? (
                  <>
                    <span className="mono">{s.reference_count}</span> {L("Einträge", "entries")}
                    <div className="faint" style={{ fontSize: 11 }}>
                      {L("Stand", "As of")} {fmtTime(s.reference_at)}
                    </div>
                  </>
                ) : (
                  <span className="faint">{L("noch nicht eingelesen", "not collected yet")}</span>
                )
              ) : (
                <>
                  <span className="mono">{s.rule_count}</span> {L("Regeln", "rules")} ·{" "}
                  <span className="mono">{s.nat_count}</span> NAT
                </>
              )}
            </td>
            <td className="nowrap">
              {s.enabled ? (
                <span className="chip ok">{L("aktiv", "active")}</span>
              ) : (
                <span className="chip na">{L("inaktiv", "inactive")}</span>
              )}
              {s.parallel_group && (
                <div
                  className="faint"
                  style={{ fontSize: 11, marginTop: 3 }}
                  title={L("Verbund paralleler Firewalls", "Parallel group of parallel firewalls")}
                >
                  {L("Verbund", "Parallel group")} {s.parallel_group}
                </div>
              )}
              {s.schedule && (
                <div
                  className="faint"
                  style={{ fontSize: 11, marginTop: 3 }}
                  title={L(`Zeitplan: ${s.schedule}`, `Schedule: ${s.schedule}`)}
                >
                  {scheduleLabel(s.schedule)}
                  {s.next_run_at && L(` · nächster ${fmtTime(s.next_run_at)}`, ` · next ${fmtTime(s.next_run_at)}`)}
                </div>
              )}
            </td>
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
  const isRef = REFERENCE_PLATFORMS.includes(system.platform);
  return (
    <Modal
      title={L("Quellsystem löschen", "Delete source system")}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-danger" onClick={submit} disabled={busy}>
            {busy ? L("Lösche…", "Deleting…") : L("Löschen", "Delete")}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <p>
        {L(
          <>
            Quellsystem <b>„{system.name}“</b> löschen?
          </>,
          <>
            Delete source system <b>“{system.name}”</b>?
          </>,
        )}
      </p>
      {isRef ? (
        system.reference_count > 0 ? (
          <Note warn>
            {L(
              <>
                Mit gelöscht wird der eingelesene Stand (<b>{system.reference_count} Einträge</b>); der Abgleich wird ohne
                diese Quelle neu berechnet. In der Quelle selbst wird nichts geändert.
              </>,
              <>
                The collected data is deleted as well (<b>{system.reference_count} entries</b>); the reconciliation is
                recalculated without this source. Nothing is changed in the source itself.
              </>,
            )}
          </Note>
        ) : (
          <Note>{L("Aus dieser Quelle wurden noch keine Daten gelesen.", "No data has been read from this source yet.")}</Note>
        )
      ) : data > 0 ? (
        <Note warn>
          {L(
            <>
              Mit gelöscht werden die daraus gelesenen <b>{system.rule_count} Regeln</b> und{" "}
              <b>{system.nat_count} NAT-Einträge</b> samt ihrer Findings. Bisherige Läufe bleiben als Historie
              erhalten.
            </>,
            <>
              The <b>{system.rule_count} rules</b> and <b>{system.nat_count} NAT entries</b> read from it are deleted
              as well, including their findings. Previous runs are kept as history.
            </>,
          )}
        </Note>
      ) : (
        <Note>{L("Aus diesem System wurden noch keine Daten gelesen.", "No data has been read from this system yet.")}</Note>
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
        <h2>{L("Nicht zugeordnete Quellsysteme", "Unassigned source systems")}</h2>
        <span className="chip warn">{systems.length}</span>
      </div>
      <div className="card-body">
        <div style={{ padding: "10px 16px 0" }}>
          <ErrorBox error={err} onClose={() => setErr(null)} />
          <Note warn>
            {L(
              <>
                Diese Quellsysteme stammen aus der Zeit vor der Mandanten-Zuordnung. Bitte einem Mandanten zuordnen
                (oder löschen) – erst dann lassen sie sich wieder einlesen.
              </>,
              <>
                These source systems date from before tenant assignment. Please assign them to a tenant (or delete
                them) – only then can they be collected again.
              </>,
            )}
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
                {L("Zuordnen", "Assign")}
              </button>
              <button className="linkbtn danger" title={L("Löschen", "Delete")} onClick={() => setToDelete(s)}>
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
