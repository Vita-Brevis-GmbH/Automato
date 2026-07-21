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

## Offene Punkte für spätere ADRs
- Wahl des Secrets-Backends (Ansible Vault vs. HashiCorp Vault) und Migrationszeitpunkt
- Netzwerksegmentierung zwischen Orchestrator, Workern und Zielsystemen
- Konkretes RBAC-Rollenmodell
- Zertifikatsverwaltung für interne mTLS-Kommunikation
