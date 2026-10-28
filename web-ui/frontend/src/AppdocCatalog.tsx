// Kategorien der Applikations-Doku im Namenskonzept (0.104.0, Anwender: „die Kategorien-Auswahl anpassen oder
// erweitern können – im Namenskonzept“; Konfiguration statt Code). Die Vorgaben stehen im Orchestrator
// (`store/appdoc_catalog.py`); je Namensprofil lassen sich Titel, Spalten und „nur mit Zone“ der Kategorien
// anpassen, eigene Kategorien dazunehmen und die Auswahl je Zone (Bereiche → Kategorien eingehend/ausgehend,
// Merkmal im Namen der Firewall-Zone, Muster für den Vorschlag) ändern bzw. erweitern. Eingebaute Einträge
// bleiben (Bereiche lassen sich ausschalten), feste Rollen (ACI intern, unspezifisch) nicht änderbar.
// Speichern und zurücksetzen = Admin; gilt für die Mandanten mit diesem Profil.
// Seit 0.105.0 (ADR 0035) mit **Richtungen** je Kategorie: Zonentyp Quelle → Ziel (Zonen des Grundschemas, `*` = jeder),
// eine je Zeile. Sind Richtungen gepflegt, gelten sie statt der Bereiche (Auswahl je Zone) – die bleiben eingeklappt.
import { useEffect, useState } from "react";
import {
  AppdocCatalog,
  AppdocCatalogIn,
  AppdocCategory,
  AppdocOptions,
  fetchProfileAppdocCatalog,
  resetProfileAppdocCatalog,
  saveProfileAppdocCatalog,
} from "./api";
import { ErrorBox, Loading } from "./ui";
import { L } from "./i18n";

type CatDraft = Omit<AppdocCatalogIn["categories"][number], "flows"> & {
  flows: string; // eine Richtung je Zeile: „Quelle → Ziel“
  builtin: boolean;
  role: string;
  enforcement: string;
};
type AreaDraft = {
  key: string;
  label: string;
  label_en: string;
  inbound: number;
  outbound: number;
  markers: string; // Komma-getrennt
  match: string;
  off: boolean;
  builtin: boolean;
};
type Draft = { categories: CatDraft[]; areas: AreaDraft[]; options: AppdocOptions };

const LAYOUTS: [AppdocCategory["layout"], string][] = [
  ["common", L("Standard", "Standard")],
  ["internet_out", L("Internet ausgehend", "Internet outbound")],
  ["internet_in", L("Internet eingehend", "Internet inbound")],
];
const ROLE: Record<string, string> = {
  aci_ap: L("ACI im Applikationsprofil", "ACI within the application profile"),
  aci_tenant: L("ACI im Security Tenant", "ACI within the security tenant"),
  aci_other: L("ACI zu anderen Security Tenants", "ACI to other security tenants"),
  fallback: L("unspezifisch (Rückfall)", "unspecific (fallback)"),
};

const list = (text: string) =>
  text
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

// „Edge Office → DC“ (auch „->“ bzw. „>“), eine je Zeile
export function parseFlows(text: string): [string, string][] {
  const out: [string, string][] = [];
  for (const line of text.split("\n")) {
    const parts = line.split(/→|->|>/).map((x) => x.trim());
    if (parts.length === 2 && parts[0] && parts[1] && !out.some(([a, b]) => a === parts[0] && b === parts[1])) out.push([parts[0], parts[1]]);
  }
  return out;
}

function toDraft(c: Omit<AppdocCatalog, "default" | "suggestions">): Draft {
  return {
    categories: c.categories.map((x) => ({
      index: x.index,
      title: x.title,
      title_en: x.title_en,
      zone_title: x.zone_title,
      zone_title_en: x.zone_title_en,
      layout: x.layout,
      only_with_zones: x.only_with_zones,
      flows: (x.flows ?? []).map(([a, b]) => `${a} → ${b}`).join("\n"),
      builtin: x.builtin,
      role: x.role,
      enforcement: x.enforcement,
    })),
    areas: c.areas.map((a) => ({
      key: a.key,
      label: a.label,
      label_en: a.label_en,
      inbound: a.inbound,
      outbound: a.outbound,
      markers: a.markers.join(", "),
      match: a.match.join(", "),
      off: a.off,
      builtin: a.builtin,
    })),
    options: { ...c.options },
  };
}

function toBody(d: Draft): AppdocCatalogIn {
  return {
    categories: d.categories.map(({ builtin: _b, role: _r, enforcement: _e, flows, ...c }) => ({
      ...c,
      title: c.title.trim(),
      flows: parseFlows(flows),
    })),
    areas: d.areas.map((a) => ({
      key: a.key.trim().toLowerCase(),
      label: a.label.trim(),
      label_en: a.label_en.trim(),
      inbound: a.inbound,
      outbound: a.outbound,
      markers: list(a.markers),
      match: list(a.match),
      off: a.off,
    })),
    options: d.options,
  };
}

export default function AppdocCatalogCard({ profileId, editable }: { profileId: number; editable: boolean }) {
  const [catalog, setCatalog] = useState<AppdocCatalog | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
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

  const base = catalog.default ? toDraft(catalog.default) : null;
  const dirty = JSON.stringify(toBody(draft)) !== JSON.stringify(toBody(toDraft(catalog)));
  const firewallCats = draft.categories.filter((c) => c.enforcement === "firewall");
  const hasFlows = draft.categories.some((c) => parseFlows(c.flows).length > 0);
  const known = new Set((catalog.zone_types ?? []).map((t) => t.toLowerCase()));
  const unknownTypes = (text: string) =>
    [...new Set(parseFlows(text).flat())].filter((t) => t !== "*" && known.size > 0 && !known.has(t.toLowerCase()));
  const keys = draft.areas.map((a) => a.key.trim().toLowerCase());
  const badKey = keys.some((k, i) => !/^[a-z0-9_-]{1,8}$/.test(k) || keys.indexOf(k) !== i);
  const empty = draft.categories.some((c) => !c.title.trim()) || draft.areas.some((a) => !a.label.trim());
  const differs = <T,>(items: T[] | undefined, pick: (x: T) => boolean, value: unknown) => {
    const d = items?.find(pick);
    return d === undefined || JSON.stringify(d) !== JSON.stringify(value);
  };

  const patch = (next: Partial<Draft>) => {
    setSaved(false);
    setDraft((d) => (d ? { ...d, ...next } : d));
  };
  const setCat = (i: number, p: Partial<CatDraft>) => patch({ categories: draft.categories.map((c, j) => (j === i ? { ...c, ...p } : c)) });
  const setArea = (i: number, p: Partial<AreaDraft>) => patch({ areas: draft.areas.map((a, j) => (j === i ? { ...a, ...p } : a)) });
  const addCategory = () => {
    const index = Math.max(0, ...draft.categories.map((c) => c.index)) + 1;
    patch({
      categories: [
        ...draft.categories,
        {
          index,
          title: "",
          title_en: "",
          zone_title: "Zugriff aus {z}", // gespeicherter Text deutsch, englisch daneben
          zone_title_en: "Access from {z}",
          layout: "common",
          only_with_zones: true,
          flows: "",
          builtin: false,
          role: "",
          enforcement: "firewall",
        },
      ],
    });
  };
  const addArea = () => {
    const own = draft.categories.filter((c) => !c.builtin);
    const target = own[own.length - 1]?.index ?? firewallCats[0]?.index ?? 1;
    patch({
      areas: [...draft.areas, { key: "", label: "", label_en: "", inbound: target, outbound: target, markers: "", match: "", off: false, builtin: false }],
    });
  };
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const c = await saveProfileAppdocCatalog(profileId, toBody(draft));
      setCatalog(c);
      setDraft(toDraft(c));
      setSaved(true);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const reset = async () => {
    if (!window.confirm(L("Kategorien dieses Profils auf die Vorgaben zurücksetzen?", "Reset the categories of this profile to the defaults?"))) return;
    setBusy(true);
    setErr(null);
    try {
      const c = await resetProfileAppdocCatalog(profileId);
      setCatalog(c);
      setDraft(toDraft(c));
      setSaved(true);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const catOption = (c: CatDraft) => (
    <option key={c.index} value={c.index}>
      {c.index} – {L(c.title || "…", c.title_en || c.title || "…")}
    </option>
  );

  return (
    <div className="card" id="appdoc-catalog">
      <div className="card-head">
        <h2>{L("Kategorien der Applikations-Doku", "Categories of the application documentation")}</h2>
        <span className="faint">
          {L(
            "Vorgaben im Code – hier anpassen bzw. erweitern; gilt für die Mandanten mit diesem Profil. Die Zonen ordnet das Netzwerkdesign → Kategorien zu.",
            "Defaults in the code – adjust or extend them here; applies to the tenants with this profile. Network design → Categories assigns the zones.",
          )}
        </span>
        {editable && (
          <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <button className="btn btn-sm btn-ghost" id="appdoc-catalog-reset" disabled={busy} onClick={() => void reset()}>
              {L("Auf Vorgaben zurücksetzen", "Reset to defaults")}
            </button>
            <button className="btn btn-primary btn-sm" id="appdoc-catalog-save" disabled={busy || !dirty || badKey || empty} onClick={() => void save()}>
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
        <h3 style={{ margin: "4px 0 6px" }}>{L("Kategorien", "Categories")}</h3>
        <table className="compact form-table" id="appdoc-catalog-categories">
          <colgroup>
            <col style={{ width: 44 }} />
            <col style={{ width: "15%" }} />
            <col style={{ width: "13%" }} />
            <col style={{ width: "13%" }} />
            <col style={{ width: "11%" }} />
            <col style={{ width: "20%" }} />
            <col style={{ width: "11%" }} />
            <col style={{ width: 70 }} />
            <col style={{ width: 40 }} />
          </colgroup>
          <thead>
            <tr>
              <th>{L("Nr.", "No.")}</th>
              <th>{L("Titel", "Title")}</th>
              <th>{L("Titel englisch", "Title English")}</th>
              <th title={L("{z} = Namen der Zonen der Kategorie; leer = Titel bleibt", "{z} = names of the zones of the category; empty = title stays")}>
                {L("Titel mit Zonen", "Title with zones")}
              </th>
              <th>{L("… englisch", "… English")}</th>
              <th title={L("je Zeile „Zonentyp Quelle → Zonentyp Ziel“, * = jeder", "per line “source zone type → destination zone type”, * = any")}>
                {L("Richtungen", "Directions")}
              </th>
              <th>{L("Spalten / Rolle", "Columns / role")}</th>
              <th title={L("nur mit einer Zone dieser Kategorie bzw. mit Zeilen", "only with a zone of this category or with rows")}>{L("nur mit Zone", "only with zone")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {draft.categories.map((c, i) => {
              const changed = c.builtin && differs(base?.categories, (x) => x.index === c.index, c);
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
                  <td>
                    <input className="input" placeholder="Zugriff aus {z}" value={c.zone_title} disabled={!editable} onChange={(e) => setCat(i, { zone_title: e.target.value })} />
                  </td>
                  <td>
                    <input className="input" placeholder="Access from {z}" value={c.zone_title_en} disabled={!editable} onChange={(e) => setCat(i, { zone_title_en: e.target.value })} />
                  </td>
                  <td>
                    {c.role === "aci_ap" || c.role === "aci_tenant" ? (
                      <span className="faint" style={{ fontSize: 11 }}>
                        {c.role === "aci_ap"
                          ? L("Sonderfall: innerhalb der Applikation", "special case: within the application")
                          : L("Sonderfall: innerhalb der eigenen Zone", "special case: within the own zone")}
                      </span>
                    ) : (
                      <textarea
                        className="input mono"
                        rows={Math.min(4, Math.max(1, c.flows.split("\n").length))}
                        aria-label={L(`Richtungen der Kategorie ${c.index}`, `Directions of category ${c.index}`)}
                        placeholder="Edge Internet → DC"
                        value={c.flows}
                        disabled={!editable}
                        onChange={(e) => setCat(i, { flows: e.target.value })}
                      />
                    )}
                    {unknownTypes(c.flows).length > 0 && (
                      <div style={{ color: "var(--warn)", fontSize: 11 }}>
                        {L(`nicht im Grundschema: ${unknownTypes(c.flows).join(", ")}`, `not in the base schema: ${unknownTypes(c.flows).join(", ")}`)}
                      </div>
                    )}
                  </td>
                  <td>
                    {c.role ? (
                      <span className="faint" style={{ fontSize: 12 }}>{ROLE[c.role] ?? c.role}</span>
                    ) : (
                      <select className="sel" value={c.layout} disabled={!editable} onChange={(e) => setCat(i, { layout: e.target.value as AppdocCategory["layout"] })}>
                        {LAYOUTS.map(([k, label]) => (
                          <option key={k} value={k}>
                            {label}
                          </option>
                        ))}
                      </select>
                    )}
                  </td>
                  <td>
                    <input type="checkbox" checked={c.only_with_zones} disabled={!editable} onChange={(e) => setCat(i, { only_with_zones: e.target.checked })} />
                  </td>
                  <td>
                    {!c.builtin && editable && (
                      <button
                        className="btn btn-sm btn-ghost"
                        title={L("Kategorie entfernen", "Remove category")}
                        onClick={() => patch({ categories: draft.categories.filter((_, j) => j !== i) })}
                      >
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
          {(catalog.zone_types ?? []).join(", ") || "–"}
          {L(
            " · Reihenfolge: innerhalb der Applikation (7), innerhalb der eigenen Zone (8), dann die genaueste Richtung; was keine trifft, wie ohne Richtungen.",
            " · Order: within the application (7), within the own zone (8), then the most specific direction; anything matching none as without directions.",
          )}
        </div>

        <details open={!hasFlows} id="appdoc-catalog-areas-fold" style={{ marginTop: 14 }}>
        <summary>
        <h3 style={{ margin: "0 0 6px", display: "inline" }}>{L("Auswahl je Zone (Bereiche)", "Choice per zone (areas)")}</h3>
        {hasFlows && <span className="faint"> {L(" – gilt nur ohne Richtungen", " – only applies without directions")}</span>}
        </summary>
        <div className="faint" style={{ fontSize: 12, marginBottom: 6 }}>
          {L(
            "Im Netzwerkdesign → Kategorien bekommt jede Zone einen Bereich; Zugriffe aus der Zone stehen in der Kategorie „eingehend“, Zugriffe des Systems in die Zone in „ausgehend“. Ohne Bereich an der Zone zählt das Merkmal im Namen der Firewall-Zone (z. B. L3O_inet); die Muster schlagen den Bereich nach dem Zonennamen vor (* = beliebig).",
            "In Network design → Categories every zone gets an area; accesses from the zone go into the “inbound” category, accesses of the system into the zone into “outbound”. Without an area on the zone, the marker in the firewall zone name counts (e.g. L3O_inet); the patterns suggest the area by the zone name (* = any).",
          )}
        </div>
        <table className="compact form-table" id="appdoc-catalog-areas">
          <colgroup>
            <col style={{ width: 80 }} />
            <col style={{ width: "16%" }} />
            <col style={{ width: "13%" }} />
            <col style={{ width: "16%" }} />
            <col style={{ width: "16%" }} />
            <col style={{ width: "10%" }} />
            <col style={{ width: "15%" }} />
            <col style={{ width: 60 }} />
            <col style={{ width: 40 }} />
          </colgroup>
          <thead>
            <tr>
              <th>{L("Kürzel", "Key")}</th>
              <th>{L("Bezeichnung", "Label")}</th>
              <th>{L("… englisch", "… English")}</th>
              <th>{L("Kategorie eingehend", "Category inbound")}</th>
              <th>{L("Kategorie ausgehend", "Category outbound")}</th>
              <th title={L("Teil im Namen der Firewall-Zone, Komma-getrennt", "part of the firewall zone name, comma-separated")}>{L("Merkmal", "Marker")}</th>
              <th title={L("Muster für den Vorschlag nach dem Zonennamen, Komma-getrennt", "patterns for the suggestion by the zone name, comma-separated")}>
                {L("Vorschlag bei", "Suggest for")}
              </th>
              <th>{L("angeboten", "offered")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {draft.areas.map((a, i) => {
              const changed = a.builtin && differs(base?.areas, (x) => x.key === a.key, a);
              return (
                <tr key={i}>
                  <td>
                    {a.builtin ? (
                      <b className="mono">{a.key}</b>
                    ) : (
                      <input
                        className="input mono"
                        aria-label={L(`Kürzel des Bereichs ${i + 1}`, `Key of area ${i + 1}`)}
                        placeholder="part"
                        maxLength={8}
                        value={a.key}
                        disabled={!editable}
                        onChange={(e) => setArea(i, { key: e.target.value })}
                      />
                    )}
                    {!a.builtin ? <div className="faint" style={{ fontSize: 11 }}>{L("eigen", "own")}</div> : changed ? <div className="faint" style={{ fontSize: 11 }}>{L("angepasst", "adjusted")}</div> : null}
                  </td>
                  <td>
                    <input className="input" aria-label={L(`Bezeichnung des Bereichs ${a.key || i + 1}`, `Label of area ${a.key || i + 1}`)} value={a.label} disabled={!editable} onChange={(e) => setArea(i, { label: e.target.value })} />
                  </td>
                  <td>
                    <input className="input" value={a.label_en} disabled={!editable} onChange={(e) => setArea(i, { label_en: e.target.value })} />
                  </td>
                  <td>
                    <select className="sel" aria-label={L(`Kategorie eingehend des Bereichs ${a.key || i + 1}`, `Inbound category of area ${a.key || i + 1}`)} value={a.inbound} disabled={!editable} onChange={(e) => setArea(i, { inbound: Number(e.target.value) })}>
                      {firewallCats.map(catOption)}
                    </select>
                  </td>
                  <td>
                    <select className="sel" aria-label={L(`Kategorie ausgehend des Bereichs ${a.key || i + 1}`, `Outbound category of area ${a.key || i + 1}`)} value={a.outbound} disabled={!editable} onChange={(e) => setArea(i, { outbound: Number(e.target.value) })}>
                      {firewallCats.map(catOption)}
                    </select>
                  </td>
                  <td>
                    <input className="input mono" placeholder="inet" value={a.markers} disabled={!editable} onChange={(e) => setArea(i, { markers: e.target.value })} />
                  </td>
                  <td>
                    <input
                      className="input mono"
                      aria-label={L(`Vorschlag des Bereichs ${a.key || i + 1}`, `Suggestion of area ${a.key || i + 1}`)}
                      placeholder="*partner*"
                      value={a.match}
                      disabled={!editable}
                      onChange={(e) => setArea(i, { match: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={L(`Bereich ${a.key || i + 1} angeboten`, `Area ${a.key || i + 1} offered`)}
                      checked={!a.off}
                      disabled={!editable}
                      onChange={(e) => setArea(i, { off: !e.target.checked })}
                    />
                  </td>
                  <td>
                    {!a.builtin && editable && (
                      <button className="btn btn-sm btn-ghost" title={L("Bereich entfernen", "Remove area")} onClick={() => patch({ areas: draft.areas.filter((_, j) => j !== i) })}>
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
          <button className="btn btn-sm btn-ghost" id="appdoc-catalog-add-area" style={{ marginTop: 6 }} onClick={addArea}>
            {L("+ Bereich", "+ Area")}
          </button>
        )}
        {badKey && (
          <div className="faint" style={{ color: "var(--crit)", fontSize: 12, marginTop: 4 }}>
            {L("Kürzel: 1–8 Kleinbuchstaben, Ziffern, - oder _, jedes nur einmal", "Key: 1–8 lowercase letters, digits, - or _, each only once")}
          </div>
        )}
        </details>

        <h3 style={{ margin: "14px 0 6px" }}>{L("Optionen", "Options")}</h3>
        <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}>
          <input
            type="checkbox"
            id="appdoc-option-outbound-broad"
            checked={draft.options.outbound_broad_hidden}
            disabled={!editable}
            onChange={(e) => patch({ options: { ...draft.options, outbound_broad_hidden: e.target.checked } })}
          />
          {L(
            "Ausgehende Firewall-Regeln ausblenden, die das System nur über eine breite Quelle (z. B. 10.0.0.0/8) erfassen",
            "Hide outbound firewall rules that cover the system only via a broad source (e.g. 10.0.0.0/8)",
          )}
        </label>
        <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13, marginTop: 4 }}>
          <input
            type="checkbox"
            id="appdoc-option-fallback-inbound"
            checked={draft.options.fallback_inbound_only}
            disabled={!editable}
            onChange={(e) => patch({ options: { ...draft.options, fallback_inbound_only: e.target.checked } })}
          />
          {L("Unspezifische Kategorie nur mit Zugriffen auf die Applikation (eingehend)", "Unspecific category only with accesses to the application (inbound)")}
        </label>
      </div>
    </div>
  );
}
