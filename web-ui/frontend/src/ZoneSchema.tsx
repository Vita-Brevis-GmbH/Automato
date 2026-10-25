// Grundschema der Zonen (0.55.0, ADR 0024 Nr. 11): die möglichen Zonen und die Absicht zwischen
// ihnen als draw.io-Zeichnung in der Namenskonvention. Hochladen mit Vorschau und Unterschied,
// Zeichnung bzw. Vorlage herunterladen, entfernen (Admin). Die Zonen-Vorschläge im Netzwerkdesign
// richten sich danach; ohne Schema gilt die eingebaute Vorlage (seit 0.56.0 nach Ebenen: DC, Edge,
// Transfer, Core, Distribution, Access, Public – mit festen Farben). Seit 0.57.0 trägt ein
// Übergang mit `automato_services` die erlaubten Dienste („eingeschränkt“).
import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteZoneSchema,
  fetchZoneSchema,
  previewZoneSchema,
  saveZoneSchema,
  SchemaLink,
  SchemaZone,
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
          <span className="mono">automato_code</span> (Kurzname) und <span className="mono">automato_match</span> (Erkennungsmuster,
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
          {editable && schema && (
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
              </div>
            )}
            <SchemaView schema={schema} />
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
