import { Fragment, useCallback, useEffect, useState } from "react";
import {
  createPortalCustomer,
  Customer,
  deletePortalCustomer,
  fetchCustomers,
  fetchPortal,
  PortalAccess,
  PortalCustomer,
  PortalCustomerIn,
  PortalLink,
  PortalListing,
  Tenant,
  updatePortalCustomer,
} from "./api";
import { L } from "./i18n";
import { Empty, ErrorBox, fmtTime, Loading, Modal, Note, PageHead } from "./ui";

// Kunden-Portal (1.3.0, ADR 0050): Kunden der Plattform – Startseite, Werkzeuge, Kacheln und die
// Anmeldung je Kunde (eigener Keycloak-Realm). Der Host-Agent auf der VM übernimmt die Änderungen
// in den Front-Proxy (front.sh sync); NetBox-Instanz und Realm legt man mit den angezeigten
// Befehlen an.

const ROLES: [string, string][] = [
  ["viewer", L("liest die Doku", "reads the documentation")],
  ["triage", L("quittiert Findings", "triages findings")],
  ["editor", L("pflegt Applikationen", "maintains applications")],
  ["customer_admin", L("ordnet Kunden-Benutzer zu", "assigns customer users")],
];

const EMPTY: PortalCustomerIn = {
  short_name: "",
  name: "",
  doc: false,
  um: false,
  cmdb: false,
  links: [],
  realm: null,
  tenant_id: null,
  customer_ids: [],
  default_role: "viewer",
  access: "ueberall",
  access_nets: [],
};

// Zugriff je Kunde (1.5.0): Text in der Liste
function accessText(c: PortalCustomer): string {
  if (c.access === "intern") return L("nur intern", "internal only");
  if (c.access === "netze") return L(`nur ${c.access_nets.join(", ")}`, `only ${c.access_nets.join(", ")}`);
  return L("überall", "everywhere");
}

function tools(c: PortalCustomer): string {
  return ["NetBox", c.doc && "Doku", c.um && "UM", c.cmdb && "CMDB"].filter(Boolean).join(", ");
}

// Befehle für das, was der Agent (noch) nicht selbst macht: NetBox-Instanz, Keycloak-Realm und
// seit 1.4.0 (ADR 0051) das Client-Secret des Anmelde-Tors je Realm
function setupSteps(c: PortalCustomer, data: PortalListing): { title: string; cmd: string }[] {
  const steps = [
    {
      title: L(
        "NetBox-Host: Instanz anlegen (einmal; bestehende Instanz: Anmeldung über das Tor mit „sso“)",
        "NetBox host: create the instance (once; existing instance: sign-in via the gate with “sso”)",
      ),
      cmd: `sudo deploy/netbox-host/netbox-host.sh neu ${c.short_name}\nsudo deploy/netbox-host/netbox-host.sh sso ${c.short_name}`,
    },
  ];
  const domain = data.domain || "<domain>";
  const doc = c.addresses.doc ?? `https://${c.short_name}-doc.${domain}`;
  const main = data.keycloak.main_realm;
  const callbacks = Object.values(c.addresses).map((u) => `${u}/oauth2/callback`);
  if (!c.realm) {
    steps.push({
      title: L(
        "Anmelde-Tor: ohne Realm ist der Kunde gesperrt – Realm eintragen (notfalls ein Realm mit lokalen Benutzern)",
        "Sign-in gate: without a realm the customer is locked – enter a realm (if need be a realm with local users)",
      ),
      cmd: L("Bearbeiten → Realm", "Edit → Realm"),
    });
    return [...steps, ...webClient(c, main, doc)];
  }
  if (c.realm !== main) {
    steps.push({
      title: L(`Keycloak: Realm „${c.realm}“ anlegen (Automato-VM, dann in der Admin-Konsole importieren)`,
        `Keycloak: create realm “${c.realm}” (Automato VM, then import in the admin console)`),
      cmd: `python3 deploy/keycloak/make_realm.py --realm ${c.realm} --display-name "${c.name.replace(/"/g, "'")}" --ui-url ${doc} --front-kunde ${c.short_name} --front-domain ${domain} > ~/realm-${c.realm}.json`,
    });
  } else {
    steps.push({
      title: L(
        `Keycloak (Realm ${main}, Client automato-front): Valid redirect URIs ergänzen`,
        `Keycloak (realm ${main}, client automato-front): add valid redirect URIs`,
      ),
      cmd: callbacks.join("\n"),
    });
  }
  steps.push({
    title: L(
      `Automato-VM: Client-Secret von automato-front (Realm ${c.realm} → Clients → automato-front → Credentials) hinterlegen – einmal je Realm`,
      `Automato VM: store the client secret of automato-front (realm ${c.realm} → Clients → automato-front → Credentials) – once per realm`,
    ),
    cmd: `sudo deploy/front/front.sh tor ${c.realm}`,
  });
  return c.realm === main ? [...steps, ...webClient(c, main, doc)] : steps;
}

// Doku im Realm von Automato: Rückkehr-Adresse und Origin des Clients automato-web
function webClient(c: PortalCustomer, main: string, doc: string): { title: string; cmd: string }[] {
  if (!c.doc) return [];
  return [
    {
      title: L(
        `Keycloak (Realm ${main || "von Automato"}, Client automato-web): Redirect-URI und Web-Origin ergänzen`,
        `Keycloak (realm ${main || "of Automato"}, client automato-web): add redirect URI and web origin`,
      ),
      cmd: `${doc}/*\n${doc}`,
    },
  ];
}

// Anmelde-Tor (1.4.0, ADR 0051): was der Host-Agent zuletzt gemeldet hat
type Tor = NonNullable<NonNullable<PortalListing["agent"]["status"]>["tor"]>;
function TorStand({ tor }: { tor?: Tor }) {
  if (!tor) return null;
  if (!tor.an)
    return (
      <div className="hint" id="portal-tor" style={{ marginTop: 8 }}>
        {L(
          "Anmelde-Tor aus – Startseiten und Werkzeuge ohne Anmeldung davor (AUTOMATO_FRONT_TOR=an in .env)",
          "Sign-in gate off – start pages and tools without sign-in in front (AUTOMATO_FRONT_TOR=an in .env)",
        )}
      </div>
    );
  return (
    <div id="portal-tor" style={{ marginTop: 8 }}>
      <span className="chip ok">
        {L("Anmelde-Tor an", "Sign-in gate on")}
        {tor.realms_bereit.length > 0 && ` · Realm ${tor.realms_bereit.join(", ")}`}
      </span>
      {tor.realms_ohne_secret.map((r) => (
        <Note warn key={r}>
          {L(`Realm ${r}: Client-Secret fehlt – auf der VM: `, `Realm ${r}: client secret missing – on the VM: `)}
          <span className="mono">sudo deploy/front/front.sh tor {r}</span>
        </Note>
      ))}
      {tor.gesperrt.length > 0 && (
        <div className="hint">
          {L("Gesperrt (kein Realm bzw. kein Client-Secret): ", "Locked (no realm or no client secret): ")}
          <span className="mono">{tor.gesperrt.join(", ")}</span>
        </div>
      )}
    </div>
  );
}

export default function Portal({ tenants }: { tenants: Tenant[] }) {
  const [data, setData] = useState<PortalListing | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);
  const [editing, setEditing] = useState<PortalCustomer | "new" | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  const load = useCallback(() => {
    fetchPortal().then(setData).catch(setErr);
  }, []);
  useEffect(load, [load]);

  const remove = async (c: PortalCustomer) => {
    if (
      !window.confirm(
        L(
          `„${c.short_name}“ aus dem Kunden-Portal entfernen? Startseite und Weiterleitungen fallen weg; die NetBox des Kunden und alle Daten bleiben.`,
          `Remove “${c.short_name}” from the customer portal? Start page and forwarding are removed; the customer's NetBox and all data remain.`,
        ),
      )
    )
      return;
    try {
      const r = await deletePortalCustomer(c.id);
      setNote(r.note);
      load();
    } catch (e) {
      setErr(e);
    }
  };

  const agent = data?.agent;
  const tenantName = (id: number | null) => tenants.find((t) => t.id === id)?.name ?? (id == null ? "–" : `#${id}`);

  return (
    <>
      <PageHead
        title={L("Kunden-Portal", "Customer portal")}
        actions={
          <button className="btn btn-primary btn-sm" onClick={() => setEditing("new")}>
            {L("+ Kunde", "+ Customer")}
          </button>
        }
      >
        {L(
          <>
            Kunden der Plattform: Startseite <span className="mono">&lt;kurzname&gt;.&lt;domain&gt;</span>, NetBox
            (<span className="mono">-nb</span>), Doku (<span className="mono">-doc</span>), Benutzerverwaltung
            (<span className="mono">-um</span>), CMDB (<span className="mono">-cmdb</span>) und weitere Kacheln. Mit einem
            eigenen Realm meldet sich der Kunde unter seiner Doku-Adresse dort an (z. B. Azure oder LDAPS) und sieht nur
            den hier zugeordneten Ausschnitt. Der Host-Agent übernimmt Änderungen in den Front-Proxy.
          </>,
          <>
            Customers of the platform: start page <span className="mono">&lt;short name&gt;.&lt;domain&gt;</span>, NetBox
            (<span className="mono">-nb</span>), documentation (<span className="mono">-doc</span>), user management
            (<span className="mono">-um</span>), CMDB (<span className="mono">-cmdb</span>) and further tiles. With its own
            realm the customer signs in there under its documentation address (e.g. Azure or LDAPS) and sees only the scope
            assigned here. The host agent applies changes to the front proxy.
          </>,
        )}
      </PageHead>
      <ErrorBox error={err} onClose={() => setErr(null)} />
      {note && <Note warn>{note}</Note>}

      {agent && (
        <div className="card" id="portal-agent">
          <div className="card-body">
            {!agent.configured ? (
              <Note warn>
                {L(
                  "Host-Agent nicht eingerichtet (AUTOMATO_FRONT_STATE_DIR leer) – Kunden werden nur gespeichert. Einrichten: sudo deploy/front/front.sh agent",
                  "Host agent not set up (AUTOMATO_FRONT_STATE_DIR empty) – customers are only stored. Set up: sudo deploy/front/front.sh agent",
                )}
              </Note>
            ) : !agent.writable ? (
              <Note warn>
                {L(
                  "Ordner des Host-Agenten nicht beschreibbar – auf der VM: sudo deploy/front/front.sh agent",
                  "Host agent folder not writable – on the VM: sudo deploy/front/front.sh agent",
                )}
              </Note>
            ) : agent.status && !agent.status.ok ? (
              <Note warn>
                {L("Host-Agent meldet einen Fehler", "Host agent reports an error")}: {agent.status.meldung}
                {agent.status.zeit && ` (${fmtTime(agent.status.zeit)})`}
              </Note>
            ) : agent.pending ? (
              <span className="chip warn">
                {L("Änderungen noch nicht im Front-Proxy – der Agent übernimmt sie in Kürze", "Changes not yet in the front proxy – the agent applies them shortly")}
              </span>
            ) : (
              <span className="chip ok">
                {L("Front-Proxy aktuell", "Front proxy up to date")}
                {agent.status?.zeit && ` · ${fmtTime(agent.status.zeit)}`}
              </span>
            )}
            <TorStand tor={agent.status?.tor} />
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-body scroll">
          {data == null ? (
            <Loading />
          ) : data.customers.length === 0 ? (
            <Empty title={L("Noch keine Kunden im Portal", "No customers in the portal yet")}>
              {L("„+ Kunde“ legt Startseite und Weiterleitungen an.", "“+ Customer” creates start page and forwarding.")}
            </Empty>
          ) : (
            <table id="portal-customers">
              <thead>
                <tr>
                  <th>{L("Kurzname", "Short name")}</th>
                  <th>Name</th>
                  <th>{L("Werkzeuge", "Tools")}</th>
                  <th>{L("Anmeldung", "Sign-in")}</th>
                  <th>{L("Ausschnitt", "Scope")}</th>
                  <th>{L("Adressen", "Addresses")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.customers.map((c) => (
                  <Fragment key={c.id}>
                    <tr>
                      <td className="mono">{c.short_name}</td>
                      <td>{c.name}</td>
                      <td>{tools(c)}</td>
                      <td>
                        {c.realm && c.realm !== data.keycloak.main_realm ? (
                          <span className="chip local">
                            Realm {c.realm}
                          </span>
                        ) : (
                          <span className="chip">{L("Automato", "Automato")}</span>
                        )}
                        {c.access && c.access !== "ueberall" && (
                          <span className="chip local" title={L("Zugriff auf alle Adressen des Kunden", "Access to all addresses of the customer")}>
                            {accessText(c)}
                          </span>
                        )}
                        {agent?.status?.tor?.an && agent.status.tor.gesperrt.includes(c.short_name) && (
                          <span className="chip warn" title={L("Anmelde-Tor: kein Realm bzw. kein Client-Secret", "Sign-in gate: no realm or no client secret")}>
                            {L("gesperrt", "locked")}
                          </span>
                        )}
                      </td>
                      <td>
                        {c.tenant_id == null
                          ? "–"
                          : `${tenantName(c.tenant_id)} · ${c.customer_ids.length} ${L("Kunden", "customers")} · ${c.default_role}`}
                      </td>
                      <td>
                        {Object.entries(c.addresses).map(([k, url]) => (
                          <div key={k}>
                            <a href={url} target="_blank" rel="noreferrer" className="mono">
                              {url.replace(/^https:\/\//, "")}
                            </a>
                          </div>
                        ))}
                        {Object.keys(c.addresses).length === 0 && <span className="muted">{L("Domain nicht gesetzt", "domain not set")}</span>}
                      </td>
                      <td className="nowrap">
                        <button className="linkbtn" onClick={() => setOpen(open === c.id ? null : c.id)}>
                          {L("Einrichtung", "Setup")}
                        </button>{" "}
                        <button className="linkbtn" onClick={() => setEditing(c)}>
                          {L("Bearbeiten", "Edit")}
                        </button>{" "}
                        <button className="linkbtn danger" onClick={() => remove(c)}>
                          {L("Entfernen", "Remove")}
                        </button>
                      </td>
                    </tr>
                    {open === c.id && (
                      <tr className="portal-setup">
                        <td colSpan={7}>
                          {setupSteps(c, data).map((s) => (
                            <div key={s.title} style={{ marginBottom: 8 }}>
                              <div className="hint">{s.title}</div>
                              <pre className="mono">{s.cmd}</pre>
                            </div>
                          ))}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {editing && data && (
        <PortalEditor
          existing={editing === "new" ? null : editing}
          tenants={tenants}
          domain={data.domain}
          onClose={() => setEditing(null)}
          onSaved={(n) => {
            setEditing(null);
            setNote(n);
            load();
          }}
        />
      )}
    </>
  );
}

function PortalEditor({
  existing,
  tenants,
  domain,
  onClose,
  onSaved,
}: {
  existing: PortalCustomer | null;
  tenants: Tenant[];
  domain: string;
  onClose: () => void;
  onSaved: (note: string | null) => void;
}) {
  const [f, setF] = useState<PortalCustomerIn>(() =>
    existing
      ? {
          short_name: existing.short_name,
          name: existing.name,
          doc: existing.doc,
          um: existing.um,
          cmdb: existing.cmdb,
          links: existing.links.map((l) => ({ ...l })),
          realm: existing.realm,
          tenant_id: existing.tenant_id,
          customer_ids: [...existing.customer_ids],
          default_role: existing.default_role,
          access: existing.access ?? "ueberall",
          access_nets: [...(existing.access_nets ?? [])],
        }
      : { ...EMPTY, links: [], customer_ids: [], access_nets: [] },
  );
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [netsText, setNetsText] = useState(() => (existing?.access_nets ?? []).join("\n"));
  const set = (patch: Partial<PortalCustomerIn>) => setF((old) => ({ ...old, ...patch }));

  useEffect(() => {
    if (f.tenant_id == null) {
      setCustomers([]);
      return;
    }
    fetchCustomers(f.tenant_id)
      .then((rows) => setCustomers(rows.filter((c) => !c.shared)))
      .catch(() => setCustomers([]));
  }, [f.tenant_id]);

  const setLink = (i: number, patch: Partial<PortalLink>) =>
    set({ links: f.links.map((l, j) => (j === i ? { ...l, ...patch } : l)) });

  const save = async () => {
    setBusy(true);
    setErr(null);
    // Netze: je Zeile bzw. mit Komma/Leerzeichen getrennt; nur bei „nur aus diesen Netzen“
    const nets = f.access === "netze" ? netsText.split(/[\s,;]+/).map((n) => n.trim()).filter(Boolean) : [];
    const body = { ...f, short_name: f.short_name.trim(), name: f.name.trim(), realm: f.realm?.trim() || null, access_nets: nets };
    try {
      const r = existing ? await updatePortalCustomer(existing.id, body) : await createPortalCustomer(body);
      onSaved(r.note ?? null);
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  };

  const k = f.short_name.trim() || "<kurzname>";
  const d = domain || "<domain>";
  return (
    <Modal
      wide
      title={existing ? L(`Kunde „${existing.short_name}“`, `Customer “${existing.short_name}”`) : L("Neuer Kunde im Portal", "New customer in the portal")}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {L("Abbrechen", "Cancel")}
          </button>
          <button className="btn btn-primary" onClick={save} disabled={busy || !f.short_name.trim() || !f.name.trim()}>
            {busy ? L("Speichere…", "Saving…") : L("Speichern", "Save")}
          </button>
        </>
      }
    >
      <ErrorBox error={err} />
      <div className="field">
        <label htmlFor="pc-short">{L("Kurzname", "Short name")}</label>
        <input
          id="pc-short"
          className="input mono"
          value={f.short_name}
          maxLength={42}
          placeholder={L("z. B. kunde-a", "e.g. customer-a")}
          onChange={(e) => set({ short_name: e.target.value.toLowerCase() })}
        />
        <div className="hint">
          {L(
            `Teil der Adressen: ${k}.${d}, ${k}-nb.${d} … – wie auf dem NetBox-Host. Nur a-z, 0-9 und „-“.`,
            `Part of the addresses: ${k}.${d}, ${k}-nb.${d} … – as on the NetBox host. Only a-z, 0-9 and “-”.`,
          )}
        </div>
      </div>
      <div className="field">
        <label htmlFor="pc-name">{L("Anzeigename", "Display name")}</label>
        <input id="pc-name" className="input" value={f.name} maxLength={200} placeholder={L("z. B. Kunde A", "e.g. Customer A")} onChange={(e) => set({ name: e.target.value })} />
      </div>
      <div className="field">
        <span className="label">{L("Werkzeuge (NetBox immer)", "Tools (NetBox always)")}</span>
        <label className="check">
          <input type="checkbox" checked={f.doc} onChange={(e) => set({ doc: e.target.checked })} /> {L("Doku (Automato, -doc)", "Documentation (Automato, -doc)")}
        </label>
        <label className="check">
          <input type="checkbox" checked={f.um} onChange={(e) => set({ um: e.target.checked })} /> {L("Benutzerverwaltung (-um)", "User management (-um)")}
        </label>
        <label className="check">
          <input type="checkbox" checked={f.cmdb} onChange={(e) => set({ cmdb: e.target.checked })} /> CMDB (-cmdb)
        </label>
      </div>

      <div className="field">
        <label htmlFor="pc-access">{L("Zugriff (von wo die Adressen erreichbar sind)", "Access (where the addresses can be reached from)")}</label>
        <select id="pc-access" className="input" value={f.access} onChange={(e) => set({ access: e.target.value as PortalAccess })}>
          <option value="ueberall">{L("Überall – aus dem Internet (über WAF bzw. FortiGate)", "Everywhere – from the internet (via WAF or FortiGate)")}</option>
          <option value="intern">{L("Nur intern – nur aus dem Management-Netz", "Internal only – only from the management network")}</option>
          <option value="netze">{L("Nur aus diesen Netzen", "Only from these networks")}</option>
        </select>
        {f.access === "netze" && (
          <textarea
            id="pc-access-nets"
            className="input mono"
            rows={3}
            value={netsText}
            placeholder={L("z. B. 203.0.113.0/24 (je Zeile ein Netz oder eine Adresse)", "e.g. 203.0.113.0/24 (one network or address per line)")}
            onChange={(e) => setNetsText(e.target.value)}
          />
        )}
        <div className="hint">
          {L(
            "Gilt für Startseite und alle Werkzeuge des Kunden, auch vor der Anmeldung und für die NetBox-API. Von anderswo sieht man nur „Unbekannte Adresse“ – wie bei einem Namen, den es nicht gibt.",
            "Applies to the start page and all tools of the customer, also before sign-in and for the NetBox API. From elsewhere one only sees “Unknown address” – as for a name that does not exist.",
          )}
        </div>
      </div>

      <h3 style={{ marginTop: 12 }}>{L("Anmeldung", "Sign-in")}</h3>
      <div className="field">
        <label htmlFor="pc-realm">{L("Eigener Realm (optional)", "Own realm (optional)")}</label>
        <input id="pc-realm" className="input mono" value={f.realm ?? ""} maxLength={64} placeholder={L("leer = Realm von Automato", "empty = realm of Automato")} onChange={(e) => set({ realm: e.target.value })} />
        <div className="hint">
          {L(
            "Unter der Doku-Adresse meldet sich der Kunde in diesem Realm an (z. B. mit Azure oder LDAPS). Benutzer daraus sind immer Kunden-Benutzer und sehen nur den Ausschnitt unten – was der Realm sonst meldet, zählt nicht.",
            "Under the documentation address the customer signs in to this realm (e.g. with Azure or LDAPS). Its users are always customer users and see only the scope below – anything else the realm reports does not count.",
          )}
        </div>
      </div>
      <div className="field">
        <label htmlFor="pc-tenant">{L("Mandant (Umgebung)", "Tenant (environment)")}</label>
        <select
          id="pc-tenant"
          className="input"
          value={f.tenant_id ?? ""}
          onChange={(e) => set({ tenant_id: e.target.value ? Number(e.target.value) : null, customer_ids: [] })}
        >
          <option value="">{L("– keiner –", "– none –")}</option>
          {tenants.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </div>
      {f.tenant_id != null && (
        <div className="field">
          <span className="label">{L("Kunden (Ausschnitt)", "Customers (scope)")}</span>
          {customers.length === 0 && <div className="hint">{L("Keine Kunden in diesem Mandanten – unter Administration → Kunden anlegen.", "No customers in this tenant – create them under Administration → Customers.")}</div>}
          {customers.map((c) => (
            <label key={c.id} className="check">
              <input
                type="checkbox"
                checked={f.customer_ids.includes(c.id)}
                onChange={(e) =>
                  set({ customer_ids: e.target.checked ? [...f.customer_ids, c.id] : f.customer_ids.filter((x) => x !== c.id) })
                }
              />{" "}
              {c.name}
            </label>
          ))}
        </div>
      )}
      <div className="field">
        <label htmlFor="pc-role">{L("Rolle ohne Angabe im Realm", "Role without one from the realm")}</label>
        <select id="pc-role" className="input" value={f.default_role} onChange={(e) => set({ default_role: e.target.value })}>
          {ROLES.map(([role, text]) => (
            <option key={role} value={role}>
              {role} – {text}
            </option>
          ))}
        </select>
      </div>

      <h3 style={{ marginTop: 12 }}>{L("Weitere Kacheln der Startseite", "Further tiles of the start page")}</h3>
      <table className="compact form-table" id="portal-links">
        <tbody>
          {f.links.map((l, i) => (
            <tr key={i}>
              <td>
                <input className="input" value={l.title} placeholder={L("Titel", "Title")} onChange={(e) => setLink(i, { title: e.target.value })} />
              </td>
              <td>
                <input className="input mono" value={l.url} placeholder="https://…" onChange={(e) => setLink(i, { url: e.target.value })} />
              </td>
              <td>
                <input className="input" value={l.text} placeholder={L("Beschreibung", "Description")} onChange={(e) => setLink(i, { text: e.target.value })} />
              </td>
              <td>
                <button className="linkbtn danger" onClick={() => set({ links: f.links.filter((_, j) => j !== i) })} aria-label={L("Kachel entfernen", "Remove tile")}>
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="btn btn-ghost btn-sm" onClick={() => set({ links: [...f.links, { title: "", url: "", text: "" }] })}>
        {L("+ Kachel", "+ Tile")}
      </button>
    </Modal>
  );
}
