import { useCallback, useEffect, useMemo, useState } from "react";
import {
  copyDictionary,
  createNamingProfile,
  deleteEntry,
  DictionaryEntry,
  fetchDictionary,
  fetchNamingProfiles,
  isDefaultProfile,
  NamingProfile,
  profileLabel,
  Tenant,
  upsertEntry,
} from "./api";
import type { Me } from "./auth";
import { L } from "./i18n";
import { Empty, ErrorBox, Loading, Modal, Note, PageHead } from "./ui";
import ZoneSchemaCard from "./ZoneSchema";
import AppdocCatalogCard from "./AppdocCatalog";

// Namensprofile & Wörterbücher: pro Kunde pflegbar und erweiterbar ohne Deployment.
// `kind` ist offen - neue Namenskonventionsvorgaben = neue Wörterbuch-Art. Die Daten
// sind Laufzeitdaten ohne Funktionszwang (Profil „default" gilt für Mandanten ohne eigenes
// Profil; leer -> eingebaute Standard-Konvention).

interface KindMeta {
  title: string;
  cols: [string, string];
  add: string;
  note?: string;
  values?: Record<string, string>; // feste Auswahl für den Wert (vom Parser ausgewertet)
}

const OBJECT_CLASSES: Record<string, string> = {
  host: L("Host (intern)", "Host (internal)"),
  host_external: L("Host extern (expected_external)", "Host external (expected_external)"),
  network: L("Netzwerk", "Network"),
  network_external: L("Netzwerk extern", "Network external"),
  transfer: L("Transfernetz", "Transfer network"),
  vip: "VIP",
  vip_dnat: "VIP / DNAT",
  virtual_server: "Virtual Server",
  snat_pool: L("SNAT IP-Pool", "SNAT IP pool"),
  range: L("Adressbereich", "Address range"),
  service: L("Service / ACI-Filter", "Service / ACI filter"),
};

const KINDS: Record<string, KindMeta> = {
  object_prefix: {
    title: L("Objekt-Prefixe → Typ", "Object prefixes → type"),
    cols: ["Prefix", L("Objekttyp", "Object type")],
    add: "Prefix",
    note: L(
      "Wird beim Einlesen ausgewertet (längstes Prefix gewinnt). Typ „Service / ACI-Filter“ (FT_) gilt für die Namensprüfung der Services und Filter, „VIP“ bzw. „VIP / DNAT“ (V-EXT_, VIP_) für VIPs, „Virtual Server“ für Virtual Server (ohne Eintrag wie VIPs) und „SNAT IP-Pool“ (IPP_) für IP-Pools – ohne Prefix eines Typs keine Prüfung dieser Namen. Leer = eingebaute Standard-Konvention.",
      "Evaluated during collection (longest prefix wins). Type “Service / ACI filter” (FT_) applies to the name check of services and filters, “VIP” or “VIP / DNAT” (V-EXT_, VIP_) to VIPs, “Virtual Server” to virtual servers (without an entry like VIPs) and “SNAT IP pool” (IPP_) to IP pools – without a prefix of a type, these names are not checked. Empty = built-in default convention.",
    ),
    values: OBJECT_CLASSES,
  },
  utm_prefix: {
    title: L("UTM-Schutzprofil-Prefixe", "UTM security profile prefixes"),
    cols: ["Prefix", L("Mechanismus", "Mechanism")],
    add: "Prefix",
  },
  color_code: {
    title: L("FortiGate-Farbcodes", "FortiGate color codes"),
    cols: ["Code", L("Klasse", "Class")],
    add: L("Farbe", "Color"),
  },
  l3out: { title: L("L3Out-Zonen (ACI)", "L3Out zones (ACI)"), cols: ["L3Out", L("Bedeutung", "Meaning")], add: "L3Out" },
  gg_code: {
    title: L("Gruppengesellschaften (Kap. 3.1)", "Group companies (ch. 3.1)"),
    cols: [L("Kürzel", "Code"), L("Tenant-Nr.", "Tenant no.")],
    add: "GG",
  },
  function_code: {
    title: L("Funktions-/Gerätecodes", "Function/device codes"),
    cols: ["Code", L("Bedeutung", "Meaning")],
    add: "Code",
  },
  site_code: {
    title: L("Standort-Codes (3-stellig)", "Site codes (3 digits)"),
    cols: ["Code", L("Standort", "Site")],
    add: L("Standort", "Site"),
    note: L("DC-/Standort-Kennung im Namen (z. B. 000).", "DC/site identifier in the name (e.g. 000)."),
  },
  service: {
    title: L("Standard-Services (FT_)", "Standard services (FT_)"),
    cols: ["Name", "Port"],
    add: "Service",
    note: L(
      "Sprechende Namen mit Soll-Port (z. B. FT_https = tcp/443); sonst gilt das Schema FT_<proto><port>.",
      "Descriptive names with expected port (e.g. FT_https = tcp/443); otherwise the schema FT_<proto><port> applies.",
    ),
  },
};

// nicht mehr Teil der Namenskonvention (0.36.0): bestehende Einträge bleiben gespeichert, werden
// aber nicht mehr gezeigt; das Grundschema der Zonen (0.55.0) hat eine eigene Karte
// bzw. die Kategorien der Applikations-Doku (0.104.0)
const HIDDEN_KINDS = ["project_code", "zone_schema", "zone_schema_link", "zone_schema_meta", "appdoc_category", "appdoc_area", "appdoc_option"];

const KIND_ORDER = Object.keys(KINDS);

function metaFor(kind: string): KindMeta {
  return KINDS[kind] ?? { title: kind, cols: [L("Schlüssel", "Key"), L("Wert", "Value")], add: L("Eintrag", "Entry") };
}

export default function NamingProfiles({ me, tenants }: { me: Me; tenants: Tenant[] }) {
  const [profiles, setProfiles] = useState<NamingProfile[] | null>(null);
  const [profileId, setProfileId] = useState<number | null>(null);
  const [entries, setEntries] = useState<DictionaryEntry[] | null>(null);
  const [extraKinds, setExtraKinds] = useState<string[]>([]);
  const [err, setErr] = useState<unknown>(null);
  const [newProfile, setNewProfile] = useState(false);
  const [newKind, setNewKind] = useState(false);

  const loadProfiles = useCallback(async (select?: number) => {
    try {
      const list = await fetchNamingProfiles();
      setProfiles(list);
      const fallback = (list.find(isDefaultProfile) ?? list[0])?.id ?? null;
      setProfileId((cur) => select ?? (list.some((p) => p.id === cur) ? cur : fallback));
    } catch (e) {
      setErr(e);
      setProfiles([]);
    }
  }, []);

  useEffect(() => {
    loadProfiles();
  }, [loadProfiles]);

  const loadEntries = useCallback(() => {
    if (profileId == null) {
      setEntries([]);
      return;
    }
    fetchDictionary(profileId)
      .then(setEntries)
      .catch((e) => {
        setErr(e);
        setEntries([]);
      });
  }, [profileId]);

  useEffect(() => {
    setEntries(null);
    setExtraKinds([]);
    loadEntries();
  }, [loadEntries]);

  const kinds = useMemo(() => {
    const present = new Set((entries ?? []).map((e) => e.kind));
    const custom = [...present, ...extraKinds].filter((k) => !KINDS[k] && !HIDDEN_KINDS.includes(k));
    return [...KIND_ORDER, ...[...new Set(custom)].sort()];
  }, [entries, extraKinds]);

  const profile = profiles?.find((p) => p.id === profileId) ?? null;
  const isDefault = profile ? isDefaultProfile(profile) : false;
  const usedBy = tenants.filter(
    (t) => t.naming_profile_id === profileId || (isDefault && t.naming_profile_id == null),
  );

  return (
    <>
      <PageHead title={L("Namensprofile & Wörterbücher", "Naming profiles & dictionaries")}>
        {L(
          <>
            Pro Kunde pflegbar und <b>erweiterbar ohne Deployment</b>. Treibt Parser/Matching (Prefix → Typ,
            Farbcode, Codes). <b>Offen für neue Namenskonventionsvorgaben</b> – zusätzliche Wörterbuch-Arten lassen
            sich jederzeit anlegen. Das Profil <b>„default“</b> ist der Standard und gilt für alle Mandanten
            ohne eigenes Profil.
          </>,
          <>
            Maintainable per customer and <b>extensible without deployment</b>. Drives parser/matching (prefix → type,
            color code, codes). <b>Open for new naming convention rules</b> – additional dictionary types can be
            created at any time. The profile <b>“default”</b> is the default and applies to all tenants without
            their own profile.
          </>,
        )}
      </PageHead>

      <div className="filterbar">
        <select
          className="sel"
          value={profileId ?? ""}
          onChange={(e) => setProfileId(e.target.value ? Number(e.target.value) : null)}
          disabled={!profiles?.length}
        >
          {!profiles?.length && <option value="">{L("Noch kein Profil", "No profile yet")}</option>}
          {profiles?.map((p) => (
            <option key={p.id} value={p.id}>
              {L("Profil:", "Profile:")} {profileLabel(p)}
              {isDefaultProfile(p) ? L(" (Standard)", " (default)") : ""}
            </option>
          ))}
        </select>
        {me.isAdmin && (
          <>
            <button className="btn btn-ghost btn-sm" onClick={() => setNewProfile(true)}>
              {L("+ Neues Profil", "+ New profile")}
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => setNewKind(true)} disabled={!profile}>
              {L("+ Neue Namenskonvention", "+ New naming convention")}
            </button>
          </>
        )}
        <div className="grow" />
        {profile && (
          <span className="faint" style={{ fontSize: 12.5 }}>
            {usedBy.length
              ? L(
                  `Gilt für: ${usedBy.map((t) => t.name).join(", ")}`,
                  `Applies to: ${usedBy.map((t) => t.name).join(", ")}`,
                )
              : isDefault
                ? L(
                    "Standard für alle Mandanten ohne eigenes Profil",
                    "Default for all tenants without their own profile",
                  )
                : L(
                    "Noch keinem Mandanten zugeordnet (unter „Mandanten & Standorte“)",
                    "Not yet assigned to any tenant (under “Tenants & sites”)",
                  )}
          </span>
        )}
      </div>

      <ErrorBox error={err} onClose={() => setErr(null)} />

      {profiles === null ? (
        <Loading />
      ) : profiles.length === 0 ? (
        <div className="card">
          <Empty
            title={L("Noch kein Namensprofil", "No naming profile yet")}
            action={
              me.isAdmin && (
                <button className="btn btn-primary btn-sm" onClick={() => setNewProfile(true)}>
                  {L("+ Neues Profil", "+ New profile")}
                </button>
              )
            }
          >
            {L(
              <>
                Ein Profil bündelt die Wörterbücher eines Kunden. Optional mit den Werten des Standardprofils
                „default“ vorbefüllen.
              </>,
              <>
                A profile bundles the dictionaries of a customer. Optionally prefill it with the values of the default
                profile “default”.
              </>,
            )}
          </Empty>
        </div>
      ) : entries === null ? (
        <Loading />
      ) : (
        <div className="stack">
          <ZoneSchemaCard key={`schema-${profileId}`} profileId={profileId!} editable={me.isAdmin} tenants={tenants} />
          <AppdocCatalogCard key={`appdoc-${profileId}`} profileId={profileId!} editable={me.isAdmin} />
          {kinds.map((k) => (
            <DictCard
              key={`${profileId}-${k}`}
              kind={k}
              meta={metaFor(k)}
              entries={entries.filter((e) => e.kind === k)}
              profileId={profileId!}
              editable={me.isAdmin}
              onChanged={loadEntries}
              onError={setErr}
            />
          ))}
        </div>
      )}

      {newProfile && (
        <NewProfileDialog
          profiles={profiles ?? []}
          onClose={() => setNewProfile(false)}
          onCreated={async (p) => {
            setNewProfile(false);
            await loadProfiles(p.id);
          }}
        />
      )}
      {newKind && profileId != null && (
        <NewKindDialog
          existing={kinds}
          profiles={profiles ?? []}
          profileId={profileId}
          onClose={() => setNewKind(false)}
          onCreated={(k, copied) => {
            setNewKind(false);
            if (copied) loadEntries();
            else setExtraKinds((prev) => [...prev, k]);
          }}
        />
      )}
    </>
  );
}

function DictCard({
  kind,
  meta,
  entries,
  profileId,
  editable,
  onChanged,
  onError,
}: {
  kind: string;
  meta: KindMeta;
  entries: DictionaryEntry[];
  profileId: number;
  editable: boolean;
  onChanged: () => void;
  onError: (e: unknown) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");

  const startAdd = () => {
    setEditId(null);
    setKey("");
    setValue(meta.values ? Object.keys(meta.values)[0] : "");
    setAdding(true);
  };

  const startEdit = (e: DictionaryEntry) => {
    setAdding(false);
    setEditId(e.id);
    setKey(e.key);
    setValue(e.value ?? "");
  };

  const cancel = () => {
    setAdding(false);
    setEditId(null);
  };

  const save = async (ev: React.FormEvent, original?: DictionaryEntry) => {
    ev.preventDefault();
    const k = key.trim();
    if (!k) return;
    try {
      await upsertEntry(profileId, { kind, key: k, value: value.trim() || null });
      // Schlüssel umbenannt -> alten Eintrag entfernen (Schlüssel ist die Identität).
      if (original && original.key !== k) await deleteEntry(original.id);
      cancel();
      onChanged();
    } catch (e) {
      onError(e);
    }
  };

  const remove = async (e: DictionaryEntry) => {
    const question = L(`Eintrag „${e.key}“ aus „${meta.title}“ löschen?`, `Delete entry “${e.key}” from “${meta.title}”?`);
    if (!window.confirm(question)) return;
    try {
      await deleteEntry(e.id);
      onChanged();
    } catch (ex) {
      onError(ex);
    }
  };

  const valueInput = (
    <ValueInput meta={meta} value={value} onChange={setValue} />
  );

  return (
    <div className="card">
      <div className="card-head">
        <h2>{meta.title}</h2>
        {!KINDS[kind] && <span className="chip local mono">{kind}</span>}
        <div className="spacer" />
        <span className="faint mono" style={{ fontSize: 12 }}>
          {entries.length}
        </span>
        {editable && !adding && (
          <button className="btn btn-ghost btn-sm" onClick={startAdd}>
            + {meta.add}
          </button>
        )}
      </div>
      <div className="card-body scroll">
        {adding && (
          <form className="inline-form" onSubmit={(e) => save(e)}>
            <div className="field narrow" style={{ flexBasis: 180 }}>
              <label>{meta.cols[0]}</label>
              <input className="input mono" value={key} autoFocus required onChange={(e) => setKey(e.target.value)} />
            </div>
            <div className="field">
              <label>{meta.cols[1]}</label>
              {valueInput}
            </div>
            <button className="btn btn-primary btn-sm" type="submit">
              {L("Speichern", "Save")}
            </button>
            <button className="btn btn-ghost btn-sm" type="button" onClick={cancel}>
              {L("Abbrechen", "Cancel")}
            </button>
          </form>
        )}
        {entries.length === 0 && !adding ? (
          <div className="loading">{L("Noch keine Einträge.", "No entries yet.")}</div>
        ) : entries.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>{meta.cols[0]}</th>
                <th>{meta.cols[1]}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) =>
                editId === e.id ? (
                  <tr key={e.id}>
                    <td colSpan={3} style={{ padding: 0 }}>
                      <form className="inline-form" style={{ borderBottom: "none" }} onSubmit={(ev) => save(ev, e)}>
                        <div className="field narrow" style={{ flexBasis: 180 }}>
                          <input
                            className="input mono"
                            value={key}
                            required
                            aria-label={meta.cols[0]}
                            onChange={(ev) => setKey(ev.target.value)}
                          />
                        </div>
                        <div className="field">{valueInput}</div>
                        <button className="btn btn-primary btn-sm" type="submit">
                          {L("Speichern", "Save")}
                        </button>
                        <button className="btn btn-ghost btn-sm" type="button" onClick={cancel}>
                          {L("Abbrechen", "Cancel")}
                        </button>
                      </form>
                    </td>
                  </tr>
                ) : (
                  <tr key={e.id}>
                    <td className="mono">{e.key}</td>
                    <td>
                      {meta.values?.[e.value ?? ""] ?? e.value ?? <span className="faint">–</span>}
                      {meta.values && e.value && !meta.values[e.value] && (
                        <span className="chip warn" style={{ marginLeft: 6 }}>
                          {L("unbekannter Typ", "unknown type")}
                        </span>
                      )}
                    </td>
                    <td className="actions">
                      {editable && (
                        <>
                          <button className="linkbtn" title={L("Bearbeiten", "Edit")} onClick={() => startEdit(e)}>
                            ✎
                          </button>
                          <button className="linkbtn danger" title={L("Löschen", "Delete")} onClick={() => remove(e)}>
                            ✕
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        ) : null}
        {meta.note && <div className="card-foot">{meta.note}</div>}
      </div>
    </div>
  );
}

function ValueInput({
  meta,
  value,
  onChange,
}: {
  meta: KindMeta;
  value: string;
  onChange: (v: string) => void;
}) {
  if (meta.values) {
    return (
      <select className="sel input" value={value} aria-label={meta.cols[1]} onChange={(e) => onChange(e.target.value)}>
        {!meta.values[value] && value && <option value={value}>{value}</option>}
        {Object.entries(meta.values).map(([k, label]) => (
          <option key={k} value={k}>
            {label}
          </option>
        ))}
      </select>
    );
  }
  return <input className="input" value={value} aria-label={meta.cols[1]} onChange={(e) => onChange(e.target.value)} />;
}

function NewProfileDialog({
  profiles,
  onClose,
  onCreated,
}: {
  profiles: NamingProfile[];
  onClose: () => void;
  onCreated: (p: NamingProfile) => void;
}) {
  const fallback = profiles.find(isDefaultProfile) ?? profiles[0];
  const [customer, setCustomer] = useState("");
  const [name, setName] = useState("");
  // Vorlage: "copy:<id>" = bestehendes Profil kopieren, "defaults" = eingebaute Standardwerte
  const [template, setTemplate] = useState(fallback ? `copy:${fallback.id}` : "defaults");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const copyFrom = template.startsWith("copy:") ? Number(template.slice(5)) : null;
      onCreated(
        await createNamingProfile({
          customer: customer.trim(),
          name: name.trim(),
          copy_from_profile_id: copyFrom,
          with_defaults: template === "defaults",
        }),
      );
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  };

  const ok = customer.trim() && name.trim();
  return (
    <Modal
      title={L("Neues Namensprofil", "New naming profile")}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={busy || !ok}>
            {busy ? L("Lege an…", "Creating…") : L("Anlegen", "Create")}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="field">
        <label htmlFor="np-template">{L("Vorlage", "Template")}</label>
        <select
          id="np-template"
          className="sel input"
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
        >
          {profiles.map((p) => (
            <option key={p.id} value={`copy:${p.id}`}>
              {L(
                <>
                  Kopie von „{profileLabel(p)}“{isDefaultProfile(p) ? " (Standard)" : ""}
                </>,
                <>
                  Copy of “{profileLabel(p)}”{isDefaultProfile(p) ? " (default)" : ""}
                </>,
              )}
            </option>
          ))}
          {!profiles.some(isDefaultProfile) && (
            <option value="defaults">{L("Eingebaute Standardwerte", "Built-in default values")}</option>
          )}
          <option value="empty">{L("Leer beginnen", "Start empty")}</option>
        </select>
        <div className="hint">
          {L(
            "Alle Wörterbücher der Vorlage werden übernommen und sind danach unabhängig änderbar.",
            "All dictionaries of the template are copied and can be changed independently afterwards.",
          )}
        </div>
      </div>
      <div className="field">
        <label htmlFor="np-customer">{L("Kunde / Umgebung", "Customer / environment")}</label>
        <input
          id="np-customer"
          className="input"
          value={customer}
          autoFocus
          placeholder={L("z. B. Kunde A", "e.g. Customer A")}
          onChange={(e) => setCustomer(e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="np-name">{L("Profilname", "Profile name")}</label>
        <input
          id="np-name"
          className="input"
          value={name}
          placeholder={L("z. B. Standard", "e.g. Default")}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
    </Modal>
  );
}

function NewKindDialog({
  existing,
  profiles,
  profileId,
  onClose,
  onCreated,
}: {
  existing: string[];
  profiles: NamingProfile[];
  profileId: number;
  onClose: () => void;
  onCreated: (kind: string, copied: boolean) => void;
}) {
  const [kind, setKind] = useState("");
  const [srcProfile, setSrcProfile] = useState(profileId);
  const [srcKinds, setSrcKinds] = useState<[string, number][]>([]);
  const [srcKind, setSrcKind] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const clean = kind.trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_");
  const dup = existing.includes(clean);

  // Wörterbuch-Arten des Quellprofils (mit Anzahl Einträge) als Kopiervorlage
  useEffect(() => {
    setSrcKind("");
    fetchDictionary(srcProfile)
      .then((entries) => {
        const counts = new Map<string, number>();
        entries.forEach((e) => counts.set(e.kind, (counts.get(e.kind) ?? 0) + 1));
        setSrcKinds([...counts.entries()].sort((a, b) => a[0].localeCompare(b[0])));
      })
      .catch(() => setSrcKinds([]));
  }, [srcProfile]);

  const submit = async () => {
    if (!srcKind) {
      onCreated(clean, false);
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await copyDictionary(profileId, { source_profile_id: srcProfile, source_kind: srcKind, kind: clean });
      onCreated(clean, true);
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  };

  return (
    <Modal
      title={L("Neue Namenskonvention", "New naming convention")}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={!clean || dup || busy}>
            {busy
              ? L("Kopiere…", "Copying…")
              : srcKind
                ? L("Anlegen & Einträge kopieren", "Create & copy entries")
                : L("Anlegen", "Create")}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="field">
        <label htmlFor="nk-kind">{L("Art (technischer Schlüssel)", "Type (technical key)")}</label>
        <input
          id="nk-kind"
          className="input mono"
          value={kind}
          autoFocus
          placeholder={L("z. B. vm_prefix", "e.g. vm_prefix")}
          onChange={(e) => setKind(e.target.value)}
        />
        <div className="hint">
          {dup
            ? L("Diese Art gibt es bereits.", "This type already exists.")
            : clean
              ? L(`Wird angelegt als „${clean}“.`, `Will be created as “${clean}”.`)
              : L("Kleinbuchstaben, Ziffern, _", "Lowercase letters, digits, _")}
        </div>
      </div>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="nk-src-profile">{L("Einträge kopieren aus Profil", "Copy entries from profile")}</label>
          <select
            id="nk-src-profile"
            className="sel input"
            value={srcProfile}
            onChange={(e) => setSrcProfile(Number(e.target.value))}
          >
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {profileLabel(p)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="nk-src-kind">{L("Namenskonvention", "Naming convention")}</label>
          <select
            id="nk-src-kind"
            className="sel input"
            value={srcKind}
            onChange={(e) => setSrcKind(e.target.value)}
          >
            <option value="">{L("– leer beginnen –", "– start empty –")}</option>
            {srcKinds.map(([k, n]) => (
              <option key={k} value={k}>
                {metaFor(k).title} ({n})
              </option>
            ))}
          </select>
        </div>
      </div>
      <Note>
        {srcKind
          ? L(
              "Die Einträge werden als Ausgangspunkt kopiert und sind danach unabhängig änderbar.",
              "The entries are copied as a starting point and can be changed independently afterwards.",
            )
          : L(
              "Die neue Art erscheint als eigene Karte; sie wird gespeichert, sobald der erste Eintrag hinzugefügt ist.",
              "The new type appears as its own card; it is saved as soon as the first entry has been added.",
            )}
      </Note>
    </Modal>
  );
}
