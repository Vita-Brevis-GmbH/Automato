# Sicherheitskonzept

## Grundprinzipien

1. **Least Privilege**: Jeder Worker erhält nur Credentials/Berechtigungen für seine eigene Zielplattform.
   Kein Worker kann auf Systeme einer anderen Plattform zugreifen.
2. **Strikte Kommunikationswege**: Web UI ↔ Orchestrator ↔ Worker. Kein Bypass, keine Direktverbindung
   Web UI → Worker oder Web UI → Zielsystem.
3. **Verschlüsselung überall**: TLS auch für interne Kommunikation (Orchestrator ↔ Worker),
   nicht nur an der Außengrenze.
4. **Secrets nie im Code/Repo**: Start mit Ansible Vault, Wachstumspfad zu HashiCorp Vault.
   Keine Klartext-Credentials in Playbooks, Inventories oder Konfigurationsdateien.
5. **Lückenloses Audit-Log**: Jeder ausgelöste Job wird protokolliert – wer, wann, was, welches Ergebnis.
   Read-only-Zugriffe (Documentation Engine) ebenfalls protokollieren.
6. **RBAC**: Rollenbasierte Zugriffskontrolle auf Web-UI- und API-Ebene (z. B. wer darf welche Wizards/
   welche Plattformen bedienen).
7. **Trennung Lesen/Schreiben**: Die Documentation Engine agiert rein lesend gegenüber Zielsystemen.
   Schreibender Zugriff läuft ausschließlich über Orchestrator → Worker → Ansible.
8. **Signierte/validierte Jobs**: Jobs vom Orchestrator an Worker sollten authentifiziert/signiert sein,
   um Job-Spoofing zu verhindern.
9. **Kunden-Benutzer fail-closed** (ADR 0018, 0.33.0): Kunden-Benutzer (Keycloak-Rolle `customer`
   bzw. `customer_ids`) erreichen nur die Endpunkte einer Freigabeliste (`auth/customer_routes.py`,
   neue Endpunkte sind automatisch gesperrt, ein Test prüft jeden Endpunkt). Jede freigegebene
   Lese-API schränkt serverseitig auf den Ausschnitt ihrer Kunden ein, Details ausserhalb sind
   nicht auffindbar (404), Kunden-Benutzer sind nie Admin und werden immer auditiert. Kunden-IDs
   kommen bei SAML-Brokern fest aus der IdP-Konfiguration, nie aus der Assertion des Kunden.

## Offene Punkte für spätere ADRs
- Wahl des Secrets-Backends (Ansible Vault vs. HashiCorp Vault) und Migrationszeitpunkt
- Netzwerksegmentierung zwischen Orchestrator, Workern und Zielsystemen
- Konkretes RBAC-Rollenmodell
- Zertifikatsverwaltung für interne mTLS-Kommunikation
