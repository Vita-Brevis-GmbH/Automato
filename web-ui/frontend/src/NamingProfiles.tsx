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
import { Empty, ErrorBox, Loading, Modal, Note, PageHead } from "./ui";
import ZoneSchemaCard from "./ZoneSchema";

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
  host: "Host (intern)",
  host_external: "Host extern (expected_external)",
  network: "Netzwerk",
  network_external: "Netzwerk extern",
  transfer: "Transfernetz",
  vip: "VIP",
  vip_dnat: "VIP / DNAT",
  snat_pool: "SNAT IP-Pool",
  range: "Adressbereich",
  service: "Service / ACI-Filter",
};

const KINDS: Record<string, KindMeta> = {
  object_prefix: {
    title: "Objekt-Prefixe → Typ",
    cols: ["Prefix", "Objekttyp"],
    add: "Prefix",
    note:
      "Wird beim Einlesen ausgewertet (längstes Prefix gewinnt). Typ „Service / ACI-Filter“ (FT_) gilt für die Namensprüfung der Services und Filter. Leer = eingebaute Standard-Konvention.",
    values: OBJECT_CLASSES,
  },
  utm_prefix: { title: "UTM-Schutzprofil-Prefixe", cols: ["Prefix", "Mechanismus"], add: "Prefix" },
  color_code: { title: "FortiGate-Farbcodes", cols: ["Code", "Klasse"], add: "Farbe" },
  l3out: { title: "L3Out-Zonen (ACI)", cols: ["L3Out", "Bedeutung"], add: "L3Out" },
  gg_code: { title: "Gruppengesellschaften (Kap. 3.1)", cols: ["Kürzel", "Tenant-Nr."], add: "GG" },
  function_code: { title: "Funktions-/Gerätecodes", cols: ["Code", "Bedeutung"], add: "Code" },
  site_code: {
    title: "Standort-Codes (3-stellig)",
    cols: ["Code", "Standort"],
    add: "Standort",
    note: "DC-/Standort-Kennung im Namen (z. B. 000).",
  },
  service: {
    title: "Standard-Services (FT_)",
    cols: ["Name", "Port"],
    add: "Service",
    note: "Sprechende Namen mit Soll-Port (z. B. FT_https = tcp/443); sonst gilt das Schema FT_<proto><port>.",
  },
};

// nicht mehr Teil der Namenskonvention (0.36.0): bestehende Einträge bleiben gespeichert, werden
// aber nicht mehr gezeigt; das Grundschema der Zonen (0.55.0) hat eine eigene Karte
const HIDDEN_KINDS = ["project_code", "zone_schema", "zone_schema_link", "zone_schema_meta"];

const KIND_ORDER = Object.keys(KINDS);

function metaFor(kind: string): KindMeta {
  return KINDS[kind] ?? { title: kind, cols: ["Schlüssel", "Wert"], add: "Eintrag" };
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
      <PageHead title="Namensprofile & Wörterbücher">
        Pro Kunde pflegbar und <b>erweiterbar ohne Deployment</b>. Treibt Parser/Matching (Prefix → Typ,
        Farbcode, Codes). <b>Offen für neue Namenskonventionsvorgaben</b> – zusätzliche Wörterbuch-Arten lassen
        sich jederzeit anlegen. Das Profil <b>„default“</b> ist der Standard und gilt für alle Mandanten
        ohne eigenes Profil.
      </PageHead>

      <div className="filterbar">
        <select
          className="sel"
          value={profileId ?? ""}
          onChange={(e) => setProfileId(e.target.value ? Number(e.target.value) : null)}
          disabled={!profiles?.length}
        >
          {!profiles?.length && <option value="">Noch kein Profil</option>}
          {profiles?.map((p) => (
            <option key={p.id} value={p.id}>
              Profil: {profileLabel(p)}
              {isDefaultProfile(p) ? " (Standard)" : ""}
            </option>
          ))}
        </select>
        {me.isAdmin && (
          <>
            <button className="btn btn-ghost btn-sm" onClick={() => setNewProfile(true)}>
              + Neues Profil
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => setNewKind(true)} disabled={!profile}>
              + Neue Namenskonvention
            </button>
          </>
        )}
        <div className="grow" />
        {profile && (
          <span className="faint" style={{ fontSize: 12.5 }}>
            {usedBy.length
              ? `Gilt für: ${usedBy.map((t) => t.name).join(", ")}`
              : isDefault
                ? "Standard für alle Mandanten ohne eigenes Profil"
                : "Noch keinem Mandanten zugeordnet (unter „Mandanten & Standorte“)"}
          </span>
        )}
      </div>

      <ErrorBox error={err} onClose={() => setErr(null)} />

      {profiles === null ? (
        <Loading />
      ) : profiles.length === 0 ? (
        <div className="card">
          <Empty
            title="Noch kein Namensprofil"
            action={
              me.isAdmin && (
                <button className="btn btn-primary btn-sm" onClick={() => setNewProfile(true)}>
                  + Neues Profil
                </button>
              )
            }
          >
            Ein Profil bündelt die Wörterbücher eines Kunden. Optional mit den Werten des Standardprofils
            „default“ vorbefüllen.
          </Empty>
        </div>
      ) : entries === null ? (
        <Loading />
      ) : (
        <div className="stack">
          <ZoneSchemaCard key={`schema-${profileId}`} profileId={profileId!} editable={me.isAdmin} tenants={tenants} />
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
    if (!window.confirm(`Eintrag „${e.key}“ aus „${meta.title}“ löschen?`)) return;
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
              Speichern
            </button>
            <button className="btn btn-ghost btn-sm" type="button" onClick={cancel}>
              Abbrechen
            </button>
          </form>
        )}
        {entries.length === 0 && !adding ? (
          <div className="loading">Noch keine Einträge.</div>
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
                          Speichern
                        </button>
                        <button className="btn btn-ghost btn-sm" type="button" onClick={cancel}>
                          Abbrechen
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
                          unbekannter Typ
                        </span>
                      )}
                    </td>
                    <td className="actions">
                      {editable && (
                        <>
                          <button className="linkbtn" title="Bearbeiten" onClick={() => startEdit(e)}>
                            ✎
                          </button>
                          <button className="linkbtn danger" title="Löschen" onClick={() => remove(e)}>
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
      title="Neues Namensprofil"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={busy || !ok}>
            {busy ? "Lege an…" : "Anlegen"}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="field">
        <label htmlFor="np-template">Vorlage</label>
        <select
          id="np-template"
          className="sel input"
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
        >
          {profiles.map((p) => (
            <option key={p.id} value={`copy:${p.id}`}>
              Kopie von „{profileLabel(p)}“{isDefaultProfile(p) ? " (Standard)" : ""}
            </option>
          ))}
          {!profiles.some(isDefaultProfile) && <option value="defaults">Eingebaute Standardwerte</option>}
          <option value="empty">Leer beginnen</option>
        </select>
        <div className="hint">
          Alle Wörterbücher der Vorlage werden übernommen und sind danach unabhängig änderbar.
        </div>
      </div>
      <div className="field">
        <label htmlFor="np-customer">Kunde / Umgebung</label>
        <input
          id="np-customer"
          className="input"
          value={customer}
          autoFocus
          placeholder="z. B. Kunde A"
          onChange={(e) => setCustomer(e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="np-name">Profilname</label>
        <input
          id="np-name"
          className="input"
          value={name}
          placeholder="z. B. Standard"
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
      title="Neue Namenskonvention"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Abbrechen
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={!clean || dup || busy}>
            {busy ? "Kopiere…" : srcKind ? "Anlegen & Einträge kopieren" : "Anlegen"}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="field">
        <label htmlFor="nk-kind">Art (technischer Schlüssel)</label>
        <input
          id="nk-kind"
          className="input mono"
          value={kind}
          autoFocus
          placeholder="z. B. vm_prefix"
          onChange={(e) => setKind(e.target.value)}
        />
        <div className="hint">
          {dup ? "Diese Art gibt es bereits." : clean ? `Wird angelegt als „${clean}“.` : "Kleinbuchstaben, Ziffern, _"}
        </div>
      </div>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="nk-src-profile">Einträge kopieren aus Profil</label>
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
          <label htmlFor="nk-src-kind">Namenskonvention</label>
          <select
            id="nk-src-kind"
            className="sel input"
            value={srcKind}
            onChange={(e) => setSrcKind(e.target.value)}
          >
            <option value="">– leer beginnen –</option>
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
          ? "Die Einträge werden als Ausgangspunkt kopiert und sind danach unabhängig änderbar."
          : "Die neue Art erscheint als eigene Karte; sie wird gespeichert, sobald der erste Eintrag hinzugefügt ist."}
      </Note>
    </Modal>
  );
}
