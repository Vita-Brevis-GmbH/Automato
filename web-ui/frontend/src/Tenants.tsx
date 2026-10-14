import { useEffect, useState } from "react";
import { createSite, deleteSite, fetchSites, patchTenantCode, Site, Tenant } from "./api";

// Mandant-/Standorte-Verwaltung: GG-Kürzel pflegen + Standorte (3-stelliger Code) listen,
// anlegen, löschen. Schreiben erfordert serverseitig die Admin-Rolle (sonst 403 sichtbar).
export default function Tenants({
  tenantId,
  tenants,
  onTenantsChanged,
}: {
  tenantId: number | null;
  tenants: Tenant[];
  onTenantsChanged: () => void;
}) {
  const tenant = tenants.find((t) => t.id === tenantId) ?? null;

  const [code, setCode] = useState("");
  const [sites, setSites] = useState<Site[] | null>(null);
  const [newCode, setNewCode] = useState("");
  const [newName, setNewName] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setCode(tenant?.code ?? "");
  }, [tenant?.id, tenant?.code]);

  useEffect(() => {
    setErr(null);
    setSites(null);
    if (tenantId == null) return;
    fetchSites(tenantId)
      .then(setSites)
      .catch((e) => setErr(`Standorte laden fehlgeschlagen: ${e}`));
  }, [tenantId]);

  if (tenantId == null || tenant == null) {
    return <p className="notice">Bitte oben einen Mandanten wählen, um Kürzel und Standorte zu verwalten.</p>;
  }

  const reloadSites = () =>
    fetchSites(tenantId)
      .then(setSites)
      .catch((e) => setErr(`Standorte laden fehlgeschlagen: ${e}`));

  const saveCode = async () => {
    setErr(null);
    try {
      await patchTenantCode(tenantId, code.trim() || null);
      onTenantsChanged();
    } catch (e) {
      setErr(`Kürzel speichern fehlgeschlagen: ${e} (nur Admin)`);
    }
  };

  const addSite = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      await createSite(tenantId, { code: newCode.trim(), name: newName.trim() });
      setNewCode("");
      setNewName("");
      await reloadSites();
    } catch (e) {
      setErr(`Standort anlegen fehlgeschlagen: ${e} (Code = 3 Zeichen, eindeutig; nur Admin)`);
    }
  };

  const removeSite = async (siteId: number) => {
    setErr(null);
    try {
      await deleteSite(tenantId, siteId);
      await reloadSites();
    } catch (e) {
      setErr(`Standort löschen fehlgeschlagen: ${e} (nur Admin)`);
    }
  };

  return (
    <div>
      {err && <p className="err">{err}</p>}

      <div className="panel" style={{ padding: ".8rem", marginBottom: "1rem" }}>
        <div className="section-title">Mandant (Gruppengesellschaft)</div>
        <div className="form-row">
          <strong>{tenant.name}</strong>
          <span className="muted">Kürzel</span>
          <input
            value={code}
            maxLength={20}
            placeholder="z. B. ssgch"
            onChange={(e) => setCode(e.target.value)}
          />
          <button className="btn primary" onClick={saveCode}>
            Speichern
          </button>
        </div>
      </div>

      <div className="section-title" style={{ padding: "0 .2rem" }}>
        Standorte
      </div>
      <form className="form-row" onSubmit={addSite}>
        <input
          value={newCode}
          placeholder="Code (3-stellig)"
          pattern="[0-9A-Za-z]{3}"
          title="dreistelliger Standort-Code, z. B. 000"
          required
          style={{ width: "9rem" }}
          onChange={(e) => setNewCode(e.target.value)}
        />
        <input
          value={newName}
          placeholder="Name (z. B. RZ01 Hauptstandort)"
          required
          style={{ minWidth: "16rem" }}
          onChange={(e) => setNewName(e.target.value)}
        />
        <button className="btn primary" type="submit">
          Standort hinzufügen
        </button>
      </form>

      {sites === null ? (
        <p className="notice">Lade Standorte…</p>
      ) : sites.length === 0 ? (
        <p className="notice">Noch keine Standorte für diesen Mandanten.</p>
      ) : (
        <div className="panel">
          <table>
            <thead>
              <tr>
                <th>Code</th>
                <th>Name</th>
                <th>Beschreibung</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {sites.map((s) => (
                <tr key={s.id}>
                  <td>
                    <code>{s.code}</code>
                  </td>
                  <td>{s.name}</td>
                  <td className="muted">{s.description ?? "–"}</td>
                  <td>
                    <button className="btn danger" onClick={() => removeSite(s.id)}>
                      Löschen
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
