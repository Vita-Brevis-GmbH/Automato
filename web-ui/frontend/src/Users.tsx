import { AuthConfig, Tenant } from "./api";
import { Me, roleLabel } from "./auth";
import { Icon, Note, PageHead } from "./ui";
import { L } from "./i18n";

// Benutzer & Rollen: Konten liegen im dedizierten IdP (Keycloak, ADR 0007). Automato
// zeigt die Rollen, das eigene Konto und verlinkt die Benutzerverwaltung des Realms.
export default function Users({ cfg, me, tenants }: { cfg: AuthConfig; me: Me; tenants: Tenant[] }) {
  const oidc = cfg.auth_mode === "oidc";
  const console = cfg.admin_console_url;
  const realm = cfg.issuer.split("/realms/")[1] ?? "";
  const myRole = me.isAdmin ? "admin" : me.roles.includes("operator") ? "operator" : "viewer";
  const scope =
    me.tenantIds === null
      ? L("alle Mandanten", "all tenants")
      : tenants.map((t) => (t.code ? `${t.name} (${t.code})` : t.name)).join(", ") || L("keine", "none");

  return (
    <>
      <PageHead
        title={L("Benutzer & Rollen", "Users & roles")}
        actions={
          oidc && me.isAdmin && console ? (
            <>
              <a className="btn btn-ghost btn-sm" href={`${console}/users`} target="_blank" rel="noreferrer">
                <Icon name="external" size={13} /> {L("Benutzer verwalten", "Manage users")}
              </a>
              <a className="btn btn-primary btn-sm" href={`${console}/users/add-user`} target="_blank" rel="noreferrer">
                {L("+ Benutzer", "+ User")}
              </a>
            </>
          ) : undefined
        }
      >
        {L(
          "Lokale Konten und SAML-Benutzer werden im Auth-Dienst (Keycloak) gepflegt. OTP ist für alle interaktiven Konten verpflichtend.",
          "Local accounts and SAML users are maintained in the auth service (Keycloak). OTP is mandatory for all interactive accounts.",
        )}
      </PageHead>

      <div className="role-grid">
        <div className={`role-card ${myRole === "viewer" ? "mine" : ""}`}>
          <h3>
            <span className="dot g" />
            Viewer
          </h3>
          <ul>
            <li>{L("Doku lesen (Regeln, NAT, Applikationen)", "Read documentation (rules, NAT, applications)")}</li>
            <li>{L("Findings einsehen", "View findings")}</li>
            <li>{L("nur eigene Mandanten", "own tenants only")}</li>
          </ul>
        </div>
        <div className={`role-card ${myRole === "operator" ? "mine" : ""}`}>
          <h3>
            <span className="dot y" />
            Operator
          </h3>
          <ul>
            <li>{L("wie Viewer", "like Viewer")}</li>
            <li>{L("Läufe auslösen („Jetzt einlesen“)", "Start runs (“Collect now”)")}</li>
            <li>{L("Findings triagieren, Systeme verorten", "Triage findings, assign systems")}</li>
          </ul>
        </div>
        <div className={`role-card ${myRole === "admin" ? "mine" : ""}`}>
          <h3>
            <span className="dot r" />
            Admin
          </h3>
          <ul>
            <li>{L("Mandanten, Standorte, Quellsysteme", "Tenants, sites, source systems")}</li>
            <li>{L("Namensprofile, Collector-Agenten", "Naming profiles, collector agents")}</li>
            <li>{L("mandantsübergreifend", "across tenants")}</li>
          </ul>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>{L("Angemeldet als", "Signed in as")}</h2>
        </div>
        <div className="card-body">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>{L("Anmeldung", "Login")}</th>
                <th>{L("Typ", "Type")}</th>
                <th>{L("Rolle", "Role")}</th>
                <th>{L("Mandanten", "Tenants")}</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <b>{me.name}</b>
                </td>
                <td className="mono">{me.username}</td>
                <td>{oidc ? <span className="chip local">Keycloak</span> : <span className="chip warn">dev</span>}</td>
                <td>{roleLabel(me)}</td>
                <td>{scope}</td>
              </tr>
            </tbody>
          </table>
        </div>
        {oidc && (
          <div className="card-foot">
            {L("Passwort und OTP ändern:", "Change password and OTP:")}{" "}
            <a href={`${cfg.issuer}/account`} target="_blank" rel="noreferrer">
              {L("Mein Konto", "My account")}
            </a>
          </div>
        )}
      </div>

      <div style={{ marginTop: 16 }}>
        {oidc ? (
          <Note>
            <div>
              {L(
                <>
                  Konten, Rollen und Mandanten-Zuordnung werden in Keycloak gepflegt (Realm <code>{realm}</code>):
                  Benutzer anlegen → Rolle <code>viewer</code>/<code>operator</code>/<code>admin</code> unter{" "}
                  <i>Role mapping</i> zuweisen → Attribut <code>tenant_ids</code> mit den Mandanten-IDs setzen
                  (siehe „Mandanten &amp; Standorte“, Spalte ID; Admin sieht alle). Änderungen gelten ab der
                  nächsten Anmeldung.
                </>,
                <>
                  Accounts, roles and tenant assignment are maintained in Keycloak (realm <code>{realm}</code>):
                  create the user → assign role <code>viewer</code>/<code>operator</code>/<code>admin</code> under{" "}
                  <i>Role mapping</i> → set attribute <code>tenant_ids</code> to the tenant IDs (see “Tenants &amp;
                  sites”, column ID; admin sees all). Changes take effect at the next login.
                </>,
              )}
              {!console && (
                <>
                  {" "}
                  {L(
                    <>
                      Für den direkten Link hier <code>AUTOMATO_OIDC_ADMIN_CONSOLE_URL</code> setzen.
                    </>,
                    <>
                      Set <code>AUTOMATO_OIDC_ADMIN_CONSOLE_URL</code> for a direct link here.
                    </>,
                  )}
                </>
              )}
            </div>
          </Note>
        ) : (
          <Note warn>
            {L(
              <>
                Entwicklungsmodus (<code>AUTOMATO_AUTH_MODE=dev</code>): keine Anmeldung, alle Anfragen laufen als
                Standard-Admin. Für echte Benutzer den OIDC-Modus mit Keycloak aktivieren (ADR 0007).
              </>,
              <>
                Development mode (<code>AUTOMATO_AUTH_MODE=dev</code>): no login, all requests run as the default
                admin. Enable OIDC mode with Keycloak for real users (ADR 0007).
              </>,
            )}
          </Note>
        )}
      </div>
    </>
  );
}
