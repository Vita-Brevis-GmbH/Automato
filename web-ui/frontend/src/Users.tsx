import { AuthConfig, Tenant } from "./api";
import { Me, roleLabel } from "./auth";
import { Icon, Note, PageHead } from "./ui";

// Benutzer & Rollen: Konten liegen im dedizierten IdP (Keycloak, ADR 0007). Automato
// zeigt die Rollen, das eigene Konto und verlinkt die Benutzerverwaltung des Realms.
export default function Users({ cfg, me, tenants }: { cfg: AuthConfig; me: Me; tenants: Tenant[] }) {
  const oidc = cfg.auth_mode === "oidc";
  const console = cfg.admin_console_url;
  const realm = cfg.issuer.split("/realms/")[1] ?? "";
  const myRole = me.isAdmin ? "admin" : me.roles.includes("operator") ? "operator" : "viewer";
  const scope =
    me.tenantIds === null
      ? "alle Mandanten"
      : tenants.map((t) => (t.code ? `${t.name} (${t.code})` : t.name)).join(", ") || "keine";

  return (
    <>
      <PageHead
        title="Benutzer & Rollen"
        actions={
          oidc && me.isAdmin && console ? (
            <>
              <a className="btn btn-ghost btn-sm" href={`${console}/users`} target="_blank" rel="noreferrer">
                <Icon name="external" size={13} /> Benutzer verwalten
              </a>
              <a className="btn btn-primary btn-sm" href={`${console}/users/add-user`} target="_blank" rel="noreferrer">
                + Benutzer
              </a>
            </>
          ) : undefined
        }
      >
        Lokale Konten und SAML-Benutzer werden im Auth-Dienst (Keycloak) gepflegt. OTP ist für alle
        interaktiven Konten verpflichtend.
      </PageHead>

      <div className="role-grid">
        <div className={`role-card ${myRole === "viewer" ? "mine" : ""}`}>
          <h3>
            <span className="dot g" />
            Viewer
          </h3>
          <ul>
            <li>Doku lesen (Regeln, NAT, Applikationen)</li>
            <li>Findings einsehen</li>
            <li>nur eigene Mandanten</li>
          </ul>
        </div>
        <div className={`role-card ${myRole === "operator" ? "mine" : ""}`}>
          <h3>
            <span className="dot y" />
            Operator
          </h3>
          <ul>
            <li>wie Viewer</li>
            <li>Läufe auslösen („Jetzt einlesen“)</li>
            <li>Findings triagieren, Systeme verorten</li>
          </ul>
        </div>
        <div className={`role-card ${myRole === "admin" ? "mine" : ""}`}>
          <h3>
            <span className="dot r" />
            Admin
          </h3>
          <ul>
            <li>Mandanten, Standorte, Quellsysteme</li>
            <li>Namensprofile, Collector-Agenten</li>
            <li>mandantsübergreifend</li>
          </ul>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Angemeldet als</h2>
        </div>
        <div className="card-body">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Anmeldung</th>
                <th>Typ</th>
                <th>Rolle</th>
                <th>Mandanten</th>
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
            Passwort und OTP ändern:{" "}
            <a href={`${cfg.issuer}/account`} target="_blank" rel="noreferrer">
              Mein Konto
            </a>
          </div>
        )}
      </div>

      <div style={{ marginTop: 16 }}>
        {oidc ? (
          <Note>
            <div>
              Konten, Rollen und Mandanten-Zuordnung werden in Keycloak gepflegt (Realm <code>{realm}</code>):
              Benutzer anlegen → Rolle <code>viewer</code>/<code>operator</code>/<code>admin</code> unter{" "}
              <i>Role mapping</i> zuweisen → Attribut <code>tenant_ids</code> mit den Mandanten-IDs setzen
              (siehe „Mandanten &amp; Standorte“, Spalte ID; Admin sieht alle). Änderungen gelten ab der
              nächsten Anmeldung.
              {!console && (
                <>
                  {" "}
                  Für den direkten Link hier <code>AUTOMATO_OIDC_ADMIN_CONSOLE_URL</code> setzen.
                </>
              )}
            </div>
          </Note>
        ) : (
          <Note warn>
            Entwicklungsmodus (<code>AUTOMATO_AUTH_MODE=dev</code>): keine Anmeldung, alle Anfragen laufen als
            Standard-Admin. Für echte Benutzer den OIDC-Modus mit Keycloak aktivieren (ADR 0007).
          </Note>
        )}
      </div>
    </>
  );
}
