# Sicherung, Wiederherstellung, Verschlüsselung at rest

## Sicherung (`backup.sh`)

```bash
age-keygen -o ~/automato-backup.agekey        # einmalig, Identität getrennt aufbewahren
export AUTOMATO_BACKUP_AGE_RECIPIENT=$(age-keygen -y ~/automato-backup.agekey)
deploy/backup/backup.sh                        # Standard /var/backups/automato, 14 Tage
```

Gesichert werden, je mit [age](https://age-encryption.org) verschlüsselt:

- die Datenbank (`pg_dump -Fc`) – Regeln, Objekte, Applikationen, Audit-Log, verschlüsselte
  Geräte-Zugangsdaten,
- der **Schlüssel der Geräte-Zugangsdaten** (`credentials.key` aus dem Volume
  `orchestrator-data`) – ohne ihn sind die gespeicherten Zugangsdaten nach einer
  Wiederherstellung unlesbar. Kommt der Schlüssel aus `AUTOMATO_CREDENTIALS_KEY`/dem
  Secret-Provider, gibt es keine Datei; dann den Wert dort sichern,
- die eingespielte Agent-Signatur.

Ohne age-Empfänger bricht das Skript ab (unverschlüsselt nur bewusst mit
`AUTOMATO_BACKUP_PLAIN=1`). Den Zeitpunkt der letzten Sicherung zeigt die System-Seite. Täglich
per cron, z. B. `15 1 * * * cd /opt/automato && AUTOMATO_BACKUP_AGE_RECIPIENT=age1… deploy/backup/backup.sh`.
Sicherungen zusätzlich auf ein anderes System kopieren (die Dateien sind verschlüsselt).

## Wiederherstellung (`restore.sh`)

```bash
AUTOMATO_BACKUP_AGE_IDENTITY=~/automato-backup.agekey \
  deploy/backup/restore.sh /var/backups/automato/automato-<zeit>.dump.age \
                           /var/backups/automato/credentials-<zeit>.key.age
```

Prüft zuerst die Entschlüsselung, stoppt den Orchestrator, ersetzt die Datenbank
(`pg_restore --clean`), spielt den Schlüssel zurück und startet den Orchestrator wieder
(Migrationen laufen beim Start). Fragt vorher nach (`AUTOMATO_RESTORE_YES=1` für Skripte).
Restore regelmässig auf einer Test-VM üben.

## Update mit Rückfall

`deploy/update.sh` sichert vor jedem Update, holt den Code (`git pull --ff-only` bzw. einen
angegebenen Tag), baut und startet und prüft `/health`. `deploy/update.sh --rollback` setzt
Code **und** Datenbank auf den Stand vor dem letzten Update zurück (Daten seit dem Update gehen
verloren). Nach einem Rückfall ist kein Branch ausgecheckt – das nächste Update mit Stand:
`deploy/update.sh <tag|branch>`.

## Verschlüsselung at rest

| Was | Schutz |
|---|---|
| Geräte-Zugangsdaten (Token, Passwörter) | app-seitig mit Fernet (`credentials.key` bzw. `AUTOMATO_CREDENTIALS_KEY`), nie im Klartext in der DB (ADR 0009) |
| Audit-Log `detail` | optional Fernet (`AUTOMATO_AUDIT_ENCRYPTION_KEY`), dazu Allowlist – Secrets landen strukturell nie im Log (E1.4) |
| Sicherungen | age (siehe oben) |
| Datenbank-Volume, Docker-Volumes | **Festplattenverschlüsselung der VM** (LUKS/dm-crypt bzw. verschlüsselter Datastore im Hypervisor) – Postgres selbst verschlüsselt nicht |
| Agent-Station | BitLocker; `agent.env` und Zertifikats-Schlüssel nur für das Dienstkonto lesbar |

Produktiv: VM-Disk verschlüsselt, Schlüssel der Zugangsdaten und des Audit-Logs über den
Secret-Provider (OpenBao, `deploy/secrets/README.md`) statt als Datei im Volume.
