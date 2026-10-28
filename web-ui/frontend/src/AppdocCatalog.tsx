// Kategorien der Applikations-Doku im Namenskonzept (0.104.0, seit 0.106.0 einfach – Anwender: „Titel, Titel English,
// Source Zone, Dest Zone“ und „einfacher ist besser, in allem“). Die Vorgaben stehen im Orchestrator
// (`store/appdoc_catalog.py`) als Richtungen auf der Vorlage des Grundschemas; je Namensprofil lassen sich Titel und
// Richtung ändern, Kategorien entfernen und eigene dazunehmen. Source- und Dest-Zone sind Zonentypen (Zonen des
// Grundschemas, `*` = jeder), je Seite einer oder mehrere. Fest (nur der Titel änderbar): innerhalb der Applikation,
// innerhalb des Security Tenants und unspezifisch. Speichern und zurücksetzen = Admin; gilt für die Mandanten mit
// diesem Profil.
import { useEffect, useState } from "react";
import { AppdocCatalog, AppdocCatalogIn, fetchProfileAppdocCatalog, resetProfileAppdocCatalog, saveProfileAppdocCatalog } from "./api";
import { ErrorBox, Loading } from "./ui";
import { L } from "./i18n";

type CatDraft = AppdocCatalogIn["categories"][number] & { builtin: boolean; role: string };

const ROLE: Record<string, string> = {
  aci_ap: L("Sonderfall: innerhalb der Applikation (Server intern, ACI im selben Application Profile)", "special case: within the application (server internal, ACI in the same application profile)"),
  aci_tenant: L("Sonderfall: innerhalb des Security Tenants (ACI im selben Tenant bzw. in der eigenen Zone)", "special case: within the security tenant (ACI in the same tenant or in the own zone)"),
  fallback: L("unspezifisch: keine Richtung passt", "unspecific: no direction matches"),
};
const ANY = "*";

function toDraft(c: Pick<AppdocCatalog, "categories">): CatDraft[] {
  return c.categories.map((x) => ({ index: x.index, title: x.title, title_en: x.title_en, src: [...x.src], dst: [...x.dst], builtin: x.builtin, role: x.role }));
}

function toBody(d: CatDraft[]): AppdocCatalogIn {
  return { categories: d.map(({ builtin: _b, role: _r, ...c }) => ({ ...c, title: c.title.trim(), title_en: c.title_en.trim() })) };
}

// Zonentypen einer Seite: Chips mit ✕, dazu eine Auswahl der Zonen des Grundschemas (bzw. `*` = jeder)
function TypePicker({ value, types, label, disabled, onChange }: { value: string[]; types: string[]; label: string; disabled: boolean; onChange: (v: string[]) => void }) {
  const known = new Set(types.map((t) => t.toLowerCase()));
  const left = [ANY, ...types].filter((t) => !value.includes(t));
  return (
    <div className="chips" style={{ alignItems: "center" }}>
      {value.map((t) => (
        <span key={t} className={`chip ${t !== ANY && known.size > 0 && !known.has(t.toLowerCase()) ? "warn" : "info"}`} title={t !== ANY && !known.has(t.toLowerCase()) ? L("nicht im Grundschema", "not in the base schema") : undefined}>
          {t === ANY ? L("* jeder", "* any") : t}
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
          {left.map((t) => (
            <option key={t} value={t}>
              {t === ANY ? L("* jeder", "* any") : t}
            </option>
          ))}
        </select>
      )}
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
    patch([...draft, { index, title: "", title_en: "", src: [], dst: [], builtin: false, role: "" }]);
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
            "Je Kategorie die Richtung: Zonentyp der Quelle → Zonentyp des Ziels (Zonen des Grundschemas, * = jeder). Den Zonentyp jeder Zone setzt das Netzwerkdesign → Kategorien.",
            "Per category the direction: zone type of the source → zone type of the destination (zones of the base schema, * = any). Network design → Categories sets the zone type of each zone.",
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
                    </td>
                  ) : (
                    <>
                      <td>
                        <TypePicker value={c.src} types={types} label={L(`Source-Zone der Kategorie ${c.index}`, `Source zone of category ${c.index}`)} disabled={!editable} onChange={(v) => setCat(i, { src: v })} />
                      </td>
                      <td>
                        <TypePicker value={c.dst} types={types} label={L(`Dest-Zone der Kategorie ${c.index}`, `Destination zone of category ${c.index}`)} disabled={!editable} onChange={(v) => setCat(i, { dst: v })} />
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
            " · Reihenfolge: innerhalb der Applikation, innerhalb des Security Tenants, dann die genaueste Richtung (ohne * vor *), bei Gleichstand die kleinere Nummer; sonst unspezifisch.",
            " · Order: within the application, within the security tenant, then the most specific direction (without * before *), on a tie the lower number; otherwise unspecific.",
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
