// Grundschema der Zonen (0.55.0, ADR 0024 Nr. 11): die möglichen Zonen und die Absicht zwischen
// ihnen als draw.io-Zeichnung in der Namenskonvention. Hochladen mit Vorschau und Unterschied,
// Zeichnung bzw. Vorlage herunterladen, entfernen (Admin). Die Zonen-Vorschläge im Netzwerkdesign
// richten sich danach; ohne Schema gilt die eingebaute Vorlage (seit 0.56.0 nach Ebenen: DC, Edge,
// Transfer, Core, Distribution, Access, Public – mit festen Farben). Seit 0.57.0 trägt ein
// Übergang mit `automato_services` die erlaubten Dienste („eingeschränkt“). Seit 0.70.0 lässt sich
// das Schema nach dem Hochladen auch hier bearbeiten (Namen, Art, Kurzname, Farbe, Erkennung,
// Reihenfolge, Übergänge mit Absicht und Diensten); die Zeichnung zum Herunterladen folgt dem Stand.
import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteZoneSchema,
  editZoneSchema,
  fetchZoneSchema,
  previewZoneSchema,
  saveZoneSchema,
  SchemaLink,
  SchemaLinkEdit,
  SchemaZone,
  SchemaZoneEdit,
  Tenant,
  ZoneIntent,
  ZoneKind,
  ZoneSchemaData,
  ZoneSchemaDiff,
  ZoneSchemaState,
  zoneSchemaDrawing,
  zoneSchemaTemplate,
} from "./api";
import { download, ErrorBox, fmtTime, Loading, Modal } from "./ui";

const KIND: Record<ZoneKind, string> = {
  dc: "Datacenter",
  edge: "Edge",
  transfer: "Transfer",
  core: "Core",
  distribution: "Distribution",
  access: "Access",
  external: "Public / extern",
  internal: "intern",
  dmz: "DMZ",
  management: "Management",
  shared: "geteilt",
};
// Farbe der Art, wenn die Zeichnung keine nennt (wie `zones.KIND_COLOR` im Orchestrator)
const KIND_COLOR: Record<ZoneKind, string> = {
  dc: "#3b82c4",
  edge: "#d9822b",
  transfer: "#8a93a3",
  core: "#2f9e44",
  distribution: "#1098ad",
  access: "#74b816",
  external: "#cc3333",
  internal: "#3b82c4",
  dmz: "#d9822b",
  management: "#7a5cc4",
  shared: "#2f9e8f",
};
const INTENT: Record<ZoneIntent, [string, string]> = {
  allow: ["ok", "erlaubt"],
  limited: ["warn", "eingeschränkt"],
  deny: ["crit", "gesperrt"],
};
const LINE: Record<ZoneIntent, string> = { allow: "#2d7d46", limited: "#d79b00", deny: "#b85450" };
const MAX_BYTES = 2_000_000;

export default function ZoneSchemaCard({
  profileId,
  editable,
  tenants,
}: {
  profileId: number;
  editable: boolean;
  tenants: Tenant[];
}) {
  const [state, setState] = useState<ZoneSchemaState | null>(null);
  const [upload, setUpload] = useState<{ content: string; filename: string; schema: ZoneSchemaData; diff: ZoneSchemaDiff } | null>(null);
  const [fromTenant, setFromTenant] = useState<number | "">("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const file = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    fetchZoneSchema(profileId)
      .then(setState)
      .catch((e) => {
        setErr(e);
        setState({ profile_id: profileId, schema: null, drawing: null });
      });
  }, [profileId]);
  useEffect(() => {
    setState(null);
    load();
  }, [load]);

  const pick = async (f: File | undefined) => {
    if (!f) return;
    setErr(null);
    if (f.size > MAX_BYTES) {
      setErr(new Error("Zeichnung zu gross (höchstens 2 MB)"));
      return;
    }
    try {
      const content = await f.text();
      const p = await previewZoneSchema(profileId, content, f.name);
      setUpload({ content, filename: f.name, ...p });
    } catch (e) {
      setErr(e);
    } finally {
      if (file.current) file.current.value = "";
    }
  };
  const save = async () => {
    if (!upload) return;
    setBusy(true);
    try {
      setState(await saveZoneSchema(profileId, upload.content, upload.filename));
      setUpload(null);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const saveDraft = async () => {
    if (!draft) return;
    setBusy(true);
    setErr(null);
    try {
      setState(await editZoneSchema(profileId, fromDraft(draft)));
      setDraft(null);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!window.confirm("Grundschema der Zonen entfernen? Zonen und Absichten im Netzwerkdesign bleiben.")) return;
    try {
      await deleteZoneSchema(profileId);
      load();
    } catch (e) {
      setErr(e);
    }
  };
  const get = async (f: () => Promise<{ blob: Blob; filename: string }>) => {
    try {
      const { blob, filename } = await f();
      download(filename, blob);
    } catch (e) {
      setErr(e);
    }
  };

  const schema = state?.schema ?? null;
  return (
    <div className="card" id="zone-schema">
      <div className="card-head">
        <h2>Grundschema der Zonen</h2>
        <span className="chip local mono">draw.io</span>
        <div className="spacer" />
        {schema && (
          <span className="faint" style={{ fontSize: 12.5 }}>
            {schema.zones.length} Zonen, {schema.links.length} Übergänge
          </span>
        )}
      </div>
      <div className="card-body pad">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        <p className="faint" style={{ marginTop: 0 }}>
          Die möglichen Zonen als Zeichnung (draw.io / diagrams.net): jede Form ist eine Zone (erste Zeile = Name), jeder Pfeil ein
          Übergang mit Absicht (Beschriftung erlaubt, eingeschränkt, gesperrt; gestrichelt = eingeschränkt, rot = gesperrt). Unter
          „Daten bearbeiten“: <span className="mono">automato_kind</span> (intern, DMZ, extern, Management, geteilt, Transfer),{" "}
          <span className="mono">automato_code</span> (Kurzname), <span className="mono">automato_customer</span> („je Kunde“ = Vorlage für
          eine Zone je Kunde) und <span className="mono">automato_match</span> (Erkennungsmuster,
          z. B. <span className="mono">dmz*, srv*</span>). Die Zonen-Vorschläge im Netzwerkdesign übernehmen Name, Art und Farbe aus
          dem Schema; die Übergänge lassen sich als Absichten in die Zonenmatrix übernehmen. Ohne Schema arbeitet alles wie bisher.
        </p>
        <div className="row-actions" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {editable && (
            <>
              <input
                ref={file}
                type="file"
                accept=".drawio,.xml,application/xml,text/xml"
                hidden
                aria-label="Zeichnung"
                onChange={(e) => pick(e.target.files?.[0])}
              />
              <button className="btn btn-primary btn-sm" id="zone-schema-upload" onClick={() => file.current?.click()}>
                {schema ? "Neue Zeichnung hochladen…" : "Zeichnung hochladen…"}
              </button>
            </>
          )}
          {schema && (
            <button className="btn btn-sm" onClick={() => get(() => zoneSchemaDrawing(profileId))}>
              Zeichnung herunterladen
            </button>
          )}
          <button className="btn btn-sm btn-ghost" onClick={() => get(() => zoneSchemaTemplate())}>
            Vorlage (draw.io)
          </button>
          {tenants.length > 0 && (
            <>
              <select className="sel" aria-label="Mandant" value={fromTenant} onChange={(e) => setFromTenant(e.target.value ? Number(e.target.value) : "")}>
                <option value="">aus den Zonen von …</option>
                {tenants.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
              <button className="btn btn-sm btn-ghost" disabled={fromTenant === ""} onClick={() => fromTenant !== "" && get(() => zoneSchemaTemplate(fromTenant))}>
                als Zeichnung
              </button>
            </>
          )}
          {editable && schema && !draft && (
            <button className="btn btn-sm" id="zone-schema-edit" onClick={() => setDraft(toDraft(schema))}>
              Bearbeiten
            </button>
          )}
          {editable && schema && !draft && (
            <button className="btn btn-sm btn-ghost" style={{ marginLeft: "auto" }} onClick={remove}>
              Schema entfernen
            </button>
          )}
        </div>
        {state == null ? (
          <Loading />
        ) : schema ? (
          <>
            {state.drawing && (
              <div className="faint" style={{ margin: "10px 0" }}>
                {state.drawing.filename ?? "Zeichnung"}
                {schema.page && ` · Seite „${schema.page}“`}
                {state.drawing.uploaded_at && ` · ${fmtTime(state.drawing.uploaded_at)}`}
                {state.drawing.uploaded_by && ` · ${state.drawing.uploaded_by}`}
                {state.drawing.edited_at && (
                  <span id="zone-schema-edited">
                    {" "}
                    · in der Oberfläche bearbeitet {fmtTime(state.drawing.edited_at)}
                    {state.drawing.edited_by ? ` von ${state.drawing.edited_by}` : ""}
                  </span>
                )}
              </div>
            )}
            {draft ? (
              <SchemaEditor draft={draft} onChange={setDraft} onSave={saveDraft} onCancel={() => setDraft(null)} busy={busy} />
            ) : (
              <SchemaView schema={schema} />
            )}
          </>
        ) : (
          <div className="loading" style={{ marginTop: 10 }}>
            Noch kein Grundschema – Vorlage herunterladen, in draw.io anpassen und hochladen.
          </div>
        )}
      </div>
      {upload && (
        <Modal
          wide
          title={`Zeichnung „${upload.filename}“`}
          onClose={() => setUpload(null)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setUpload(null)}>
                Abbrechen
              </button>
              <button className="btn btn-primary" id="zone-schema-save" disabled={busy} onClick={save}>
                {busy ? "Speichere…" : "Als Grundschema speichern"}
              </button>
            </>
          }
        >
          <DiffView diff={upload.diff} />
          <SchemaView schema={upload.schema} />
        </Modal>
      )}
    </div>
  );
}

function DiffView({ diff }: { diff: ZoneSchemaDiff }) {
  const parts: string[] = [];
  if (diff.zones.added.length) parts.push(`neu: ${diff.zones.added.join(", ")}`);
  if (diff.zones.removed.length) parts.push(`entfernt: ${diff.zones.removed.join(", ")}`);
  for (const c of diff.zones.changed) parts.push(`${c.name}: ${c.fields.join(", ")} geändert`);
  const links = diff.links.added.length + diff.links.removed.length + diff.links.changed.length;
  return (
    <div id="zone-schema-diff" className="note" style={{ marginBottom: 10 }}>
      <b>Gegenüber dem gespeicherten Schema:</b> {parts.length ? parts.join(" · ") : "Zonen unverändert"}
      {links > 0 && (
        <>
          {" "}
          · Übergänge: {diff.links.added.length} neu, {diff.links.removed.length} entfernt
          {diff.links.changed.map((x) => `, ${x.src} → ${x.dst}: ${INTENT[x.before][1]} → ${INTENT[x.intent][1]}`).join("")}
        </>
      )}
    </div>
  );
}

function SchemaView({ schema }: { schema: ZoneSchemaData }) {
  return (
    <>
      {schema.warnings.length > 0 && (
        <ul className="zone-schema-warnings">
          {schema.warnings.map((w) => (
            <li key={w} className="faint">
              ⚠ {w}
            </li>
          ))}
        </ul>
      )}
      <SchemaDrawing zones={schema.zones} links={schema.links} />
      <table className="compact" id="zone-schema-zones" style={{ marginTop: 10 }}>
        <thead>
          <tr>
            <th>Zone</th>
            <th>Art</th>
            <th>Kurzname</th>
            <th>Erkennung</th>
            <th>Gruppe</th>
          </tr>
        </thead>
        <tbody>
          {schema.zones.map((z) => (
            <tr key={z.key}>
              <td>
                <span className="zone-swatch" style={{ background: z.color ?? KIND_COLOR[z.kind] }} /> <b>{z.name}</b>
                {z.per_customer && (
                  <span className="chip info schema-per-customer" title="Vorlage: je Kunde eine eigene Zone „Name Kunde“ (automato_customer)">
                    je Kunde
                  </span>
                )}
                {z.description && <div className="faint">{z.description}</div>}
              </td>
              <td>
                {KIND[z.kind] ?? z.kind}
                <div className="faint">{z.kind_reason}</div>
              </td>
              <td className="mono">{z.code ?? <span className="faint">–</span>}</td>
              <td>
                <div className="objs">
                  {z.match.map((m) => (
                    <span key={m} className="chip mono">
                      {m}
                    </span>
                  ))}
                  {z.match.length === 0 && <span className="faint">Name{z.code ? ", Kurzname" : ""}</span>}
                </div>
              </td>
              <td>{z.group ?? <span className="faint">–</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {schema.links.length > 0 && (
        <table className="compact" id="zone-schema-links" style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>Von</th>
              <th>Nach</th>
              <th>Absicht</th>
              <th>Woher</th>
            </tr>
          </thead>
          <tbody>
            {schema.links.map((x) => (
              <tr key={`${x.src}>${x.dst}`}>
                <td>{x.src}</td>
                <td>{x.dst}</td>
                <td>
                  <span className={`chip ${INTENT[x.intent][0]}`}>{INTENT[x.intent][1]}</span>
                  {x.services && x.services.length > 0 && <span className="mono"> nur {x.services.join(", ")}</span>}
                  {x.note && <span className="faint"> {x.note}</span>}
                </td>
                <td className="faint">{x.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

// Vorschau der Zeichnung: Formen an ihrer Position (bzw. im Raster), Pfeile mit der Absicht
function SchemaDrawing({ zones, links }: { zones: SchemaZone[]; links: SchemaLink[] }) {
  const boxes = zones.map((z, i) => {
    const g = z.geometry ?? [40 + (i % 5) * 200, 40 + Math.floor(i / 5) * 110, 160, 60];
    return { z, x: g[0], y: g[1], w: Math.max(g[2], 60), h: Math.max(g[3], 30) };
  });
  if (boxes.length === 0) return null;
  const minX = Math.min(...boxes.map((b) => b.x)) - 20;
  const minY = Math.min(...boxes.map((b) => b.y)) - 20;
  const maxX = Math.max(...boxes.map((b) => b.x + b.w)) + 20;
  const maxY = Math.max(...boxes.map((b) => b.y + b.h)) + 20;
  const at = new Map(boxes.map((b) => [b.z.name, b]));
  const edge = (a: (typeof boxes)[number], b: (typeof boxes)[number]) => {
    const [ax, ay, bx, by] = [a.x + a.w / 2, a.y + a.h / 2, b.x + b.w / 2, b.y + b.h / 2];
    const clip = (box: typeof a, dx: number, dy: number) => {
      const tx = dx ? box.w / 2 / Math.abs(dx) : Infinity;
      const ty = dy ? box.h / 2 / Math.abs(dy) : Infinity;
      return Math.min(tx, ty);
    };
    const dx = bx - ax;
    const dy = by - ay;
    const s = clip(a, dx, dy);
    const t = clip(b, dx, dy);
    return [ax + dx * s, ay + dy * s, bx - dx * t, by - dy * t];
  };
  return (
    <svg
      id="zone-schema-drawing"
      className="zone-schema-drawing"
      viewBox={`${minX} ${minY} ${maxX - minX} ${maxY - minY}`}
      style={{ width: "100%", maxHeight: 420, border: "1px solid var(--line)", borderRadius: 8, background: "var(--surface)" }}
    >
      <defs>
        {(Object.keys(LINE) as ZoneIntent[]).map((k) => (
          <marker key={k} id={`zs-arrow-${k}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill={LINE[k]} />
          </marker>
        ))}
      </defs>
      {links.map((x) => {
        const a = at.get(x.src);
        const b = at.get(x.dst);
        if (!a || !b) return null;
        const [x1, y1, x2, y2] = edge(a, b);
        return (
          <line
            key={`${x.src}>${x.dst}`}
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
            stroke={LINE[x.intent]}
            strokeWidth={2}
            strokeDasharray={x.intent === "limited" ? "6 4" : undefined}
            markerEnd={`url(#zs-arrow-${x.intent})`}
          >
            <title>
              {x.src} → {x.dst}: {INTENT[x.intent][1]}
            </title>
          </line>
        );
      })}
      {boxes.map((b) => (
        <g key={b.z.key}>
          <rect x={b.x} y={b.y} width={b.w} height={b.h} rx={8} fill={b.z.color ?? KIND_COLOR[b.z.kind]} fillOpacity={0.3} stroke={b.z.color ?? KIND_COLOR[b.z.kind]} />
          <text x={b.x + b.w / 2} y={b.y + b.h / 2} textAnchor="middle" dominantBaseline="middle" fontSize={13} fontWeight={600} fill="var(--fg)">
            {b.z.name}
          </text>
        </g>
      ))}
    </svg>
  );
}

// -- Bearbeiten (0.70.0) ------------------------------------------------------------------------
// Zonen tragen eine feste `uid`, die Übergänge verweisen darauf - Umbenennen zieht die Übergänge mit.
type DraftZone = Omit<SchemaZoneEdit, "match"> & { uid: string; match: string };
type DraftLink = { uid: string; src: string; dst: string; intent: ZoneIntent; services: string; note: string };
type Draft = { zones: DraftZone[]; links: DraftLink[] };

let uidSeq = 0;
const uid = () => `u${++uidSeq}`;
const list = (text: string) =>
  text
    .split(/[,;\n]+/)
    .map((x) => x.trim())
    .filter(Boolean);

function toDraft(schema: ZoneSchemaData): Draft {
  const zones = schema.zones.map((z) => ({
    uid: uid(),
    name: z.name,
    kind: z.kind,
    code: z.code,
    color: z.color,
    match: z.match.join(", "),
    group: z.group,
    description: z.description,
    geometry: z.geometry,
    previous: z.name,
    per_customer: !!z.per_customer,
  }));
  const byName = new Map(zones.map((z) => [z.name, z.uid]));
  const links = schema.links
    .filter((x) => byName.has(x.src) && byName.has(x.dst))
    .map((x) => ({
      uid: uid(),
      src: byName.get(x.src)!,
      dst: byName.get(x.dst)!,
      intent: x.intent,
      services: (x.services ?? []).join(", "),
      note: x.note ?? "",
    }));
  return { zones, links };
}

function fromDraft(d: Draft): { zones: SchemaZoneEdit[]; links: SchemaLinkEdit[] } {
  const name = new Map(d.zones.map((z) => [z.uid, z.name.trim()]));
  return {
    zones: d.zones.map(({ uid: _u, match, ...z }) => ({
      ...z,
      name: z.name.trim(),
      code: z.code?.trim() || null,
      color: z.color?.trim() || null,
      group: z.group?.trim() || null,
      description: z.description?.trim() || null,
      match: list(match),
    })),
    links: d.links.map((x) => ({
      src: name.get(x.src) ?? "",
      dst: name.get(x.dst) ?? "",
      intent: x.intent,
      services: x.intent === "limited" ? list(x.services) : [],
      note: x.note.trim() || null,
    })),
  };
}

function SchemaEditor({ draft, onChange, onSave, onCancel, busy }: {
  draft: Draft;
  onChange: (d: Draft) => void;
  onSave: () => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const setZone = (i: number, patch: Partial<DraftZone>) =>
    onChange({ ...draft, zones: draft.zones.map((z, j) => (j === i ? { ...z, ...patch } : z)) });
  const move = (i: number, by: number) => {
    const zones = [...draft.zones];
    const [z] = zones.splice(i, 1);
    zones.splice(Math.max(0, Math.min(zones.length, i + by)), 0, z);
    onChange({ ...draft, zones });
  };
  const dropZone = (i: number) => {
    const gone = draft.zones[i].uid;
    onChange({ zones: draft.zones.filter((_, j) => j !== i), links: draft.links.filter((x) => x.src !== gone && x.dst !== gone) });
  };
  const addZone = () =>
    onChange({
      ...draft,
      zones: [
        ...draft.zones,
        {
          uid: uid(),
          name: "",
          kind: "internal",
          code: null,
          color: null,
          match: "",
          group: null,
          description: null,
          geometry: null,
          previous: null,
          per_customer: false,
        },
      ],
    });
  const setLink = (i: number, patch: Partial<DraftLink>) =>
    onChange({ ...draft, links: draft.links.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  const addLink = () => {
    const [a, b] = draft.zones;
    if (!a || !b) return;
    onChange({ ...draft, links: [...draft.links, { uid: uid(), src: a.uid, dst: b.uid, intent: "allow", services: "", note: "" }] });
  };
  const names = new Map(draft.zones.map((z) => [z.uid, z.name]));
  const preview = fromDraft(draft);
  const keys = preview.zones.map((z) => z.name.toLowerCase());
  const dupes = new Set(keys.filter((k, i) => k && keys.indexOf(k) !== i));
  const empty = preview.zones.some((z) => !z.name);
  const pairs = draft.links.map((x) => `${x.src}>${x.dst}`);
  const badLink = draft.links.some((x, i) => x.src === x.dst || pairs.indexOf(pairs[i]) !== i);
  const shown: SchemaZone[] = preview.zones.map((z, i) => ({
    ...z,
    key: `${i}:${z.name}`,
    kind_reason: "",
    position: (i + 1) * 10,
    color: /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(z.color ?? "") ? z.color : null,
  }));
  const shownLinks: SchemaLink[] = preview.links.map((x) => ({ ...x, reason: "", note: x.note }));
  return (
    <div id="zone-schema-editor">
      <SchemaDrawing zones={shown} links={shownLinks} />
      <table className="compact zone-schema-edit" id="zone-schema-edit-zones" style={{ marginTop: 10 }}>
        <thead>
          <tr>
            <th />
            <th>Zone</th>
            <th>Art</th>
            <th>Kurzname</th>
            <th>Farbe</th>
            <th title="Erkennungsmuster, mit Komma getrennt (* und ?)">Erkennung</th>
            <th>Gruppe</th>
            <th>Beschreibung</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {draft.zones.map((z, i) => (
            <tr key={z.uid} data-zone={z.name}>
              <td className="nowrap">
                <button className="linkbtn" title="nach oben" disabled={i === 0} onClick={() => move(i, -1)}>
                  ↑
                </button>
                <button className="linkbtn" title="nach unten" disabled={i === draft.zones.length - 1} onClick={() => move(i, 1)}>
                  ↓
                </button>
              </td>
              <td>
                <input
                  className={`input${!z.name.trim() || dupes.has(z.name.trim().toLowerCase()) ? " invalid" : ""}`}
                  aria-label="Name der Zone"
                  value={z.name}
                  onChange={(e) => setZone(i, { name: e.target.value })}
                />
                {z.previous && z.previous !== z.name.trim() && <div className="faint">bisher {z.previous}</div>}
              </td>
              <td>
                <select className="sel" aria-label="Art" value={z.kind} onChange={(e) => setZone(i, { kind: e.target.value as ZoneKind })}>
                  {(Object.keys(KIND) as ZoneKind[]).map((k) => (
                    <option key={k} value={k}>
                      {KIND[k]}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <input className="input mono" style={{ width: 90 }} aria-label="Kurzname" value={z.code ?? ""} onChange={(e) => setZone(i, { code: e.target.value })} />
              </td>
              <td className="nowrap">
                <input
                  type="color"
                  aria-label="Farbe"
                  value={/^#[0-9a-f]{6}$/i.test(z.color ?? "") ? z.color! : KIND_COLOR[z.kind]}
                  onChange={(e) => setZone(i, { color: e.target.value })}
                />
                {z.color && (
                  <button className="linkbtn" title="Farbe der Art" onClick={() => setZone(i, { color: null })}>
                    ✕
                  </button>
                )}
              </td>
              <td>
                <input className="input mono" aria-label="Erkennung" placeholder="dmz*, srv*" value={z.match} onChange={(e) => setZone(i, { match: e.target.value })} />
              </td>
              <td>
                <input className="input" style={{ width: 110 }} aria-label="Gruppe" value={z.group ?? ""} onChange={(e) => setZone(i, { group: e.target.value })} />
                <label className="check" title="Vorlage: je Kunde eine eigene Zone „Name Kunde“ vorschlagen (0.82.0)">
                  <input type="checkbox" aria-label="je Kunde" checked={!!z.per_customer} onChange={(e) => setZone(i, { per_customer: e.target.checked })} /> je Kunde
                </label>
              </td>
              <td>
                <input className="input" aria-label="Beschreibung" value={z.description ?? ""} onChange={(e) => setZone(i, { description: e.target.value })} />
              </td>
              <td>
                <button className="linkbtn danger" title="Zone entfernen (mit ihren Übergängen)" onClick={() => dropZone(i)}>
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="btn btn-sm btn-ghost" id="zone-schema-add-zone" style={{ marginTop: 6 }} onClick={addZone}>
        + Zone
      </button>
      <table className="compact zone-schema-edit" id="zone-schema-edit-links" style={{ marginTop: 12 }}>
        <thead>
          <tr>
            <th>Von</th>
            <th>Nach</th>
            <th>Absicht</th>
            <th title="bei „eingeschränkt“: erlaubte Dienste, mit Komma getrennt (tcp/443, FT_https)">Dienste</th>
            <th>Notiz</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {draft.links.map((x, i) => (
            <tr key={x.uid} data-link={`${names.get(x.src)}>${names.get(x.dst)}`} className={x.src === x.dst || pairs.indexOf(pairs[i]) !== i ? "row-crit" : undefined}>
              {(["src", "dst"] as const).map((end) => (
                <td key={end}>
                  <select className="sel" aria-label={end === "src" ? "Von" : "Nach"} value={x[end]} onChange={(e) => setLink(i, { [end]: e.target.value })}>
                    {draft.zones.map((z) => (
                      <option key={z.uid} value={z.uid}>
                        {z.name || "(ohne Namen)"}
                      </option>
                    ))}
                  </select>
                </td>
              ))}
              <td>
                <select className="sel" aria-label="Absicht" value={x.intent} onChange={(e) => setLink(i, { intent: e.target.value as ZoneIntent })}>
                  {(Object.keys(INTENT) as ZoneIntent[]).map((k) => (
                    <option key={k} value={k}>
                      {INTENT[k][1]}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <input
                  className="input mono"
                  aria-label="Dienste"
                  placeholder={x.intent === "limited" ? "tcp/443, FT_https" : "nur bei eingeschränkt"}
                  disabled={x.intent !== "limited"}
                  value={x.services}
                  onChange={(e) => setLink(i, { services: e.target.value })}
                />
              </td>
              <td>
                <input className="input" aria-label="Notiz" value={x.note} onChange={(e) => setLink(i, { note: e.target.value })} />
              </td>
              <td>
                <button className="linkbtn danger" title="Übergang entfernen" onClick={() => onChange({ ...draft, links: draft.links.filter((_, j) => j !== i) })}>
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="btn btn-sm btn-ghost" id="zone-schema-add-link" style={{ marginTop: 6 }} disabled={draft.zones.length < 2} onClick={addLink}>
        + Übergang
      </button>
      <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
        <button className="btn btn-primary btn-sm" id="zone-schema-edit-save" disabled={busy || empty || dupes.size > 0 || badLink || draft.zones.length === 0} onClick={onSave}>
          {busy ? "Speichere…" : "Speichern"}
        </button>
        <button className="btn btn-sm btn-ghost" onClick={onCancel}>
          Abbrechen
        </button>
        <span className="faint">
          {empty
            ? "Jede Zone braucht einen Namen."
            : dupes.size > 0
              ? `Name doppelt: ${[...dupes].join(", ")}`
              : badLink
                ? "Übergang doppelt bzw. von einer Zone zu sich selbst."
                : "Die Zonen-Vorschläge folgen dem gespeicherten Schema; Zonen und Absichten im Netzwerkdesign bleiben unverändert."}
        </span>
      </div>
    </div>
  );
}
