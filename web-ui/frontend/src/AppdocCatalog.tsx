// Kategorien der Applikations-Doku im Namenskonzept (0.104.0, seit 0.106.0 einfach – Anwender: „Titel, Titel English,
// Source Zone, Dest Zone“ und „einfacher ist besser, in allem“). Die Vorgaben stehen im Orchestrator
// (`store/appdoc_catalog.py`) als Richtungen auf der Vorlage des Grundschemas; je Namensprofil lassen sich Titel und
// Richtung ändern, Kategorien entfernen und eigene dazunehmen. Source- und Dest-Zone sind Zonentypen (Zonen des
// Grundschemas, `*` = jeder), je Seite einer oder mehrere. Fest (nur der Titel änderbar): innerhalb der Applikation,
// innerhalb des Security Tenants und unspezifisch. Speichern und zurücksetzen = Admin; gilt für die Mandanten mit
// diesem Profil.
// Seit 0.125.0 (ADR 0046, Anwender 2026-11-03): Zonentypen mit Zusatz „(Applikation)“ = Seite der Systeme der
// Applikation bzw. „(andere)“ = Gegenstelle (z. B. DC (Applikation) → DC (andere)) und je Seite die Auswahl
// spezifisch/unspezifisch (auch bei den festen) - abgewählte Zeilen stehen nur unter „Regeln & Contracts“.
import { useEffect, useState } from "react";
import { AppdocCatalog, AppdocCatalogIn, AppdocScope, fetchProfileAppdocCatalog, resetProfileAppdocCatalog, saveProfileAppdocCatalog } from "./api";
import { ErrorBox, Loading } from "./ui";
import { L } from "./i18n";

type CatDraft = AppdocCatalogIn["categories"][number] & { builtin: boolean; role: string };

const ROLE: Record<string, string> = {
  aci_ap: L("Sonderfall: innerhalb der Applikation (Server intern, ACI im selben Application Profile)", "special case: within the application (server internal, ACI in the same application profile)"),
  aci_tenant: L("Sonderfall: innerhalb des Security Tenants (ACI im selben Tenant bzw. in der eigenen Zone)", "special case: within the security tenant (ACI in the same tenant or in the own zone)"),
  fallback: L("unspezifisch: keine Richtung passt", "unspecific: no direction matches"),
};
const ANY = "*";
const SCOPES: AppdocScope[] = ["specific", "unspecific"];
// Zusatz am Zonentyp (gespeichert deutsch, angezeigt in der Sprache der Oberfläche)
const APP_SIDE = "Applikation";
const OTHER_SIDE = "andere";

function splitType(t: string): [string, string | null] {
  const m = /^(.*\S)\s*\((Applikation|andere)\)$/i.exec(t.trim());
  return m ? [m[1], m[2].toLowerCase() === "andere" ? OTHER_SIDE : APP_SIDE] : [t, null];
}

function typeLabel(t: string): string {
  if (t === ANY) return L("* jeder", "* any");
  const [base, side] = splitType(t);
  if (side === APP_SIDE) return `${base} ${L("(Applikation)", "(application)")}`;
  if (side === OTHER_SIDE) return `${base} ${L("(andere)", "(other)")}`;
  return t;
}

function toDraft(c: Pick<AppdocCatalog, "categories">): CatDraft[] {
  return c.categories.map((x) => ({
    index: x.index,
    title: x.title,
    title_en: x.title_en,
    src: [...x.src],
    dst: [...x.dst],
    src_scope: [...(x.src_scope ?? SCOPES)],
    dst_scope: [...(x.dst_scope ?? SCOPES)],
    builtin: x.builtin,
    role: x.role,
  }));
}

function toBody(d: CatDraft[]): AppdocCatalogIn {
  return { categories: d.map(({ builtin: _b, role: _r, ...c }) => ({ ...c, title: c.title.trim(), title_en: c.title_en.trim() })) };
}

// Zonentypen einer Seite: Chips mit ✕, dazu eine Auswahl der Zonen des Grundschemas (bzw. `*` = jeder), je Zonentyp
// auch mit Zusatz „(Applikation)“ bzw. „(andere)“ (0.125.0)
function TypePicker({ value, types, label, disabled, onChange }: { value: string[]; types: string[]; label: string; disabled: boolean; onChange: (v: string[]) => void }) {
  const known = new Set(types.map((t) => t.toLowerCase()));
  const unknown = (t: string) => t !== ANY && known.size > 0 && !known.has(splitType(t)[0].toLowerCase());
  const free = (list: string[]) => list.filter((t) => !value.includes(t));
  const groups: [string, string[]][] = [
    [L("Zonentyp (beide Seiten)", "zone type (both sides)"), free(types)],
    [L("Seite der Applikation", "side of the application"), free(types.map((t) => `${t} (${APP_SIDE})`))],
    [L("Gegenstelle (andere)", "peer (other)"), free(types.map((t) => `${t} (${OTHER_SIDE})`))],
  ];
  return (
    <div className="chips" style={{ alignItems: "center" }}>
      {value.map((t) => (
        <span key={t} className={`chip ${unknown(t) ? "warn" : "info"}`} title={unknown(t) ? L("nicht im Grundschema", "not in the base schema") : undefined}>
          {typeLabel(t)}
          {!disabled && (
            <button className="chip-x" aria-label={L(`${t} entfernen`, `Remove ${t}`)} onClick={() => onChange(value.filter((x) => x !== t))}>
              ✕
            </button>
          )}
        </span>
      ))}
      {!disabled && value[0] !== ANY && (
        <select className="sel" style={{ fontSize: 12 }} aria-label={label} value="" onChange={(e) => e.target.value && onChange(e.target.value === ANY ? [ANY] : [...value, e.target.value])}>
          <option value="">{L("+ Zonentyp", "+ zone type")}</option>
          <option value={ANY}>{L("* jeder", "* any")}</option>
          {groups
            .filter(([, list]) => list.length > 0)
            .map(([name, list]) => (
              <optgroup key={name} label={name}>
                {list.map((t) => (
                  <option key={t} value={t}>
                    {typeLabel(t)}
                  </option>
                ))}
              </optgroup>
            ))}
        </select>
      )}
    </div>
  );
}

// Auswahl einer Seite (0.125.0): spezifisch und/oder unspezifisch - mindestens eines bleibt
function ScopePicker({ id, value, side, disabled, onChange }: { id: string; value: AppdocScope[]; side: "src" | "dst"; disabled: boolean; onChange: (v: AppdocScope[]) => void }) {
  const names: Record<AppdocScope, string> = { specific: L("spezifisch", "specific"), unspecific: L("unspezifisch", "unspecific") };
  const hint: Record<AppdocScope, string> = {
    specific: L("Host bzw. Netz in einer Zone; auf der Seite der Applikation das System direkt", "host or network in one zone; on the side of the application the system itself"),
    unspecific: L("any, Netz über mehrere Zonen; bei der Applikation Subnetz, any, vzAny, Preferred Group", "any, network across zones; for the application subnet, any, vzAny, preferred group"),
  };
  return (
    <div className="scope-pick" style={{ display: "flex", gap: 10, fontSize: 11, marginTop: 4, flexWrap: "wrap" }}>
      {side === "src" ? L("Quellen:", "Sources:") : L("Ziele:", "Destinations:")}
      {SCOPES.map((s) => {
        const on = value.includes(s);
        return (
          <label key={s} title={hint[s]} style={{ display: "inline-flex", gap: 3, alignItems: "center" }}>
            <input
              type="checkbox"
              id={`${id}-${s}`}
              checked={on}
              disabled={disabled || (on && value.length === 1)}
              onChange={() => onChange(SCOPES.filter((x) => (x === s ? !on : value.includes(x))))}
            />
            {names[s]}
          </label>
        );
      })}
    </div>
  );
}

export default function AppdocCatalogCard({ profileId, editable }: { profileId: number; editable: boolean }) {
  const [catalog, setCatalog] = useState<AppdocCatalog | null>(null);
  const [draft, setDraft] = useState<CatDraft[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  useEffect(() => {
    let live = true;
    fetchProfileAppdocCatalog(profileId)
      .then((c) => {
        if (!live) return;
        setCatalog(c);
        setDraft(toDraft(c));
      })
      .catch((e) => live && setErr(e));
    return () => {
      live = false;
    };
  }, [profileId]);

  if (!catalog || !draft) {
    return (
      <div className="card" id="appdoc-catalog">
        <ErrorBox error={err} onClose={() => setErr(null)} />
        <Loading />
      </div>
    );
  }

  const types = catalog.zone_types ?? [];
  const base = catalog.default ? toDraft(catalog.default) : [];
  const dirty = JSON.stringify(toBody(draft)) !== JSON.stringify(toBody(toDraft(catalog)));
  const empty = draft.some((c) => !c.title.trim() || (!c.role && (c.src.length === 0 || c.dst.length === 0)));
  const patch = (next: CatDraft[]) => {
    setSaved(false);
    setDraft(next);
  };
  const setCat = (i: number, p: Partial<CatDraft>) => patch(draft.map((c, j) => (j === i ? { ...c, ...p } : c)));
  const addCategory = () => {
    const index = Math.max(0, ...draft.map((c) => c.index)) + 1;
    patch([...draft, { index, title: "", title_en: "", src: [], dst: [], src_scope: [...SCOPES], dst_scope: [...SCOPES], builtin: false, role: "" }]);
  };
  const run = async (call: () => Promise<AppdocCatalog>) => {
    setBusy(true);
    setErr(null);
    try {
      const c = await call();
      setCatalog(c);
      setDraft(toDraft(c));
      setSaved(true);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const reset = () => {
    if (window.confirm(L("Kategorien dieses Profils auf die Vorgaben zurücksetzen?", "Reset the categories of this profile to the defaults?"))) void run(() => resetProfileAppdocCatalog(profileId));
  };

  return (
    <div className="card" id="appdoc-catalog">
      <div className="card-head">
        <h2>{L("Kategorien der Applikations-Doku", "Categories of the application documentation")}</h2>
        <span className="faint">
          {L(
            "Je Kategorie die Richtung: Zonentyp der Quelle → Zonentyp des Ziels (Zonen des Grundschemas, * = jeder; „(Applikation)“ = Seite der Applikation, „(andere)“ = Gegenstelle) und je Seite, ob spezifische und/oder unspezifische Zugriffe erscheinen – abgewählte stehen nur unter „Regeln & Contracts“. Den Zonentyp jeder Zone setzt das Netzwerkdesign → Kategorien.",
            "Per category the direction: zone type of the source → zone type of the destination (zones of the base schema, * = any; “(application)” = side of the application, “(other)” = peer) and per side whether specific and/or unspecific accesses appear – deselected ones are only listed under “Rules & contracts”. Network design → Categories sets the zone type of each zone.",
          )}
        </span>
        {editable && (
          <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <button className="btn btn-sm btn-ghost" id="appdoc-catalog-reset" disabled={busy} onClick={reset}>
              {L("Auf Vorgaben zurücksetzen", "Reset to defaults")}
            </button>
            <button className="btn btn-primary btn-sm" id="appdoc-catalog-save" disabled={busy || !dirty || empty} onClick={() => void run(() => saveProfileAppdocCatalog(profileId, toBody(draft)))}>
              {busy ? L("Speichere…", "Saving…") : L("Speichern", "Save")}
            </button>
          </div>
        )}
      </div>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {saved && !dirty && (
        <div className="faint" id="appdoc-catalog-saved" style={{ padding: "0 14px", fontSize: 12 }}>
          {L("Gespeichert – die Applikations-Doku ordnet beim nächsten Laden neu.", "Saved – the application documentation re-sorts on the next load.")}
        </div>
      )}
      <div className="card-body pad">
        <table className="compact form-table" id="appdoc-catalog-categories">
          <colgroup>
            <col style={{ width: 44 }} />
            <col style={{ width: "22%" }} />
            <col style={{ width: "20%" }} />
            <col style={{ width: "24%" }} />
            <col style={{ width: "24%" }} />
            <col style={{ width: 40 }} />
          </colgroup>
          <thead>
            <tr>
              <th>{L("Nr.", "No.")}</th>
              <th>{L("Titel", "Title")}</th>
              <th>{L("Titel englisch", "Title English")}</th>
              <th title={L("Zonentyp der Quelle (eingehend die Gegenstelle, ausgehend das System)", "zone type of the source (inbound the peer, outbound the system)")}>{L("Source-Zone", "Source zone")}</th>
              <th title={L("Zonentyp des Ziels", "zone type of the destination")}>{L("Dest-Zone", "Destination zone")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {draft.map((c, i) => {
              const d = base.find((x) => x.index === c.index);
              const changed = d !== undefined && JSON.stringify(d) !== JSON.stringify(c);
              return (
                <tr key={c.index}>
                  <td>
                    <b>{c.index}</b>
                    {!c.builtin ? <div className="faint" style={{ fontSize: 11 }}>{L("eigen", "own")}</div> : changed ? <div className="faint" style={{ fontSize: 11 }}>{L("angepasst", "adjusted")}</div> : null}
                  </td>
                  <td>
                    <input className="input" aria-label={L(`Titel der Kategorie ${c.index}`, `Title of category ${c.index}`)} value={c.title} disabled={!editable} onChange={(e) => setCat(i, { title: e.target.value })} />
                  </td>
                  <td>
                    <input className="input" aria-label={L(`Titel englisch der Kategorie ${c.index}`, `English title of category ${c.index}`)} value={c.title_en} disabled={!editable} onChange={(e) => setCat(i, { title_en: e.target.value })} />
                  </td>
                  {c.role ? (
                    <td colSpan={2}>
                      <span className="faint" style={{ fontSize: 12 }}>
                        {ROLE[c.role] ?? c.role}
                      </span>
                      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                        <ScopePicker id={`cat-${c.index}-src`} side="src" value={c.src_scope} disabled={!editable} onChange={(v) => setCat(i, { src_scope: v })} />
                        <ScopePicker id={`cat-${c.index}-dst`} side="dst" value={c.dst_scope} disabled={!editable} onChange={(v) => setCat(i, { dst_scope: v })} />
                      </div>
                    </td>
                  ) : (
                    <>
                      <td>
                        <TypePicker value={c.src} types={types} label={L(`Source-Zone der Kategorie ${c.index}`, `Source zone of category ${c.index}`)} disabled={!editable} onChange={(v) => setCat(i, { src: v })} />
                        <ScopePicker id={`cat-${c.index}-src`} side="src" value={c.src_scope} disabled={!editable} onChange={(v) => setCat(i, { src_scope: v })} />
                      </td>
                      <td>
                        <TypePicker value={c.dst} types={types} label={L(`Dest-Zone der Kategorie ${c.index}`, `Destination zone of category ${c.index}`)} disabled={!editable} onChange={(v) => setCat(i, { dst: v })} />
                        <ScopePicker id={`cat-${c.index}-dst`} side="dst" value={c.dst_scope} disabled={!editable} onChange={(v) => setCat(i, { dst_scope: v })} />
                      </td>
                    </>
                  )}
                  <td>
                    {!c.role && editable && (
                      <button className="btn btn-sm btn-ghost" title={L("Kategorie entfernen", "Remove category")} aria-label={L(`Kategorie ${c.index} entfernen`, `Remove category ${c.index}`)} onClick={() => patch(draft.filter((_, j) => j !== i))}>
                        ✕
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {editable && (
          <button className="btn btn-sm btn-ghost" id="appdoc-catalog-add-category" style={{ marginTop: 6 }} onClick={addCategory}>
            {L("+ Kategorie", "+ Category")}
          </button>
        )}
        <div className="faint" id="appdoc-catalog-types" style={{ fontSize: 12, marginTop: 6 }}>
          {L("Zonentypen im Grundschema: ", "Zone types in the base schema: ")}
          {types.join(", ") || "–"}
          {L(
            " · Reihenfolge: innerhalb der Applikation, innerhalb des Security Tenants, dann die genaueste Richtung (mit Zusatz vor ohne, ohne * vor *), bei Gleichstand die kleinere Nummer; sonst unspezifisch. Eine Regel mit Gegenstellen in mehreren Zonen steht je Zone in ihrer Kategorie.",
            " · Order: within the application, within the security tenant, then the most specific direction (with qualifier before without, without * before *), on a tie the lower number; otherwise unspecific. A rule with peers in several zones appears per zone in its category.",
          )}
        </div>
        {empty && editable && (
          <div className="faint" style={{ color: "var(--warn)", fontSize: 12, marginTop: 4 }}>
            {L("Jede Kategorie braucht einen Titel sowie Source- und Dest-Zone.", "Every category needs a title as well as a source and destination zone.")}
          </div>
        )}
      </div>
    </div>
  );
}
