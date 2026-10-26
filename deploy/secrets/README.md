# Secrets-Provider (E1.5, ADR 0007 D4)

Secrets (Tokens, Passwörter, Schlüssel) gehören **nie** im Klartext ins Repo. Automato
löst secret-tragende Konfigurationswerte beim Start über einen austauschbaren
Secrets-Provider auf (Adapter-Pattern wie beim SoT-Gateway). Code: `orchestrator/app/secrets/`.

## Referenz-Schema

Jeder secret-tragende Wert darf entweder **Klartext** sein (Lab/Dev, `.env`) oder eine
**Referenz** `schema:locator`, die beim App-Start aufgelöst wird:

| Referenz | Herkunft | Beispiel |
|----------|----------|----------|
| `env:NAME` | Umgebungsvariable | `AUTOMATO_NETBOX_TOKEN=env:NETBOX_PAT` |
| `file:NAME` / `file:/pfad` | Datei (Docker-/K8s-Secret, SOPS/age-entschlüsselt) | `AUTOMATO_NETBOX_TOKEN=file:netbox_token` |
| `openbao:FELD` / `openbao:pfad#feld` | OpenBao/Vault (KV v2) | `AUTOMATO_NETBOX_TOKEN=openbao:netbox_token` |
| `literal:WERT` | Klartext mit `:` (Escape-Hatch) | `literal:a:b:c` |

Werte **ohne** bekanntes Schema bleiben unverändert → `.env`/Dev verhält sich wie bisher.
Ist ein Backend nicht erreichbar oder ein Secret nicht auffindbar, startet die App
**nicht** (fail-closed) — in Produktion ohne Secrets soll sie nicht laufen.

Aufgelöste Felder: `internal_api_token`, `netbox_token`, `infoblox_username`,
`infoblox_password`, `audit_encryption_key` (siehe `app.secrets.SECRET_FIELDS`).

## Lab / Dev

Nichts zu tun: Werte stehen als Klartext in `.env`. (Default-Provider = Klartext.)

## Produktion A — Datei-Secrets (SOPS/age oder Docker-/K8s-Secrets)

Verzeichnis mit je einer Datei pro Secret (Dateiname = Locator):

```bash
# SOPS/age: einmal entschlüsselt an einen tmpfs-Pfad schreiben
sops -d secrets.enc.yaml > /dev/null   # Beispiel; echte Entschlüsselung je Setup
install -m600 netbox_token /run/secrets/netbox_token
```

Konfiguration:

```ini
AUTOMATO_SECRETS_FILE_DIR=/run/secrets         # Default
AUTOMATO_NETBOX_TOKEN=file:netbox_token
AUTOMATO_INTERNAL_API_TOKEN=file:internal_api_token
```

Mit Docker Compose: `secrets:` definieren, als `/run/secrets/<name>` eingehängt.

## Produktion B — OpenBao / Vault (KV v2)

OpenBao ist der MPL-lizenzierte Vault-Fork (Open-Source-Grundsatz). Ein KV-v2-Dokument
hält die App-Secrets; Automato liest Felder daraus.

```bash
# Secret-Dokument anlegen (Pfad = AUTOMATO_OPENBAO_PATH unter dem Mount)
bao kv put secret/automato \
  netbox_token=... internal_api_token=... audit_encryption_key=...
```

Konfiguration:

```ini
AUTOMATO_OPENBAO_ADDR=https://bao.intern:8200
AUTOMATO_OPENBAO_TOKEN=file:openbao_token   # Boot-Token selbst als Datei-Secret
AUTOMATO_OPENBAO_KV_MOUNT=secret            # Default
AUTOMATO_OPENBAO_PATH=automato              # Default
AUTOMATO_NETBOX_TOKEN=openbao:netbox_token
AUTOMATO_AUDIT_ENCRYPTION_KEY=openbao:audit_encryption_key
```

Der HTTP-Zugriff nutzt nur die Python-Standardbibliothek (kein Zusatz-Dependency); für
ein **internes** OpenBao `no_proxy`/`NO_PROXY` passend setzen.

## Dezentral / verteilt (später)

Das Interface erlaubt pro Instanz einen eigenen Provider (eigener OpenBao-Namespace,
eigenes Secret-Verzeichnis) — passend zur geplanten verteilten Bauweise. Keine Änderung
am Rest der Anwendung nötig; nur die Referenzen/den Provider umkonfigurieren.
