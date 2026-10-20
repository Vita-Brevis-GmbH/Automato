# Signierte Agent-Updates

Agenten aktualisieren sich selbst über den Agent-Kanal (ADR 0011). Damit ein kompromittierter
Orchestrator keine fremden Pakete verteilen kann, signiert ein Admin jedes Agent-Paket
**offline** mit Ed25519 (ADR 0013). Agenten mit hinterlegtem öffentlichem Schlüssel
installieren nur Updates mit gültiger Signatur; sonst bleiben sie bei ihrer Version.

Signiert wird die Nachricht aus Paket-Version und SHA-256 je Datei
(`automato-agent-update/v1`). Der private Schlüssel kommt nie auf den Orchestrator.

## Einmalig

```bash
# auf einem Rechner ohne Netz zum Orchestrator (Python mit cryptography)
python3 scripts/agent_signing.py keygen agent-signing.key
# -> AUTOMATO_AGENT_SIGNING_PUBKEY=<Base64>; agent-signing.key offline verwahren
```

- Orchestrator: `AUTOMATO_AGENT_SIGNING_PUBKEY=<Base64>` in `.env` (prüft eingespielte
  Signaturen, zeigt den Zustand auf der System-Seite).
- Agenten: dieselbe Zeile in `agent.env`. Bei der Erstinstallation (`install.ps1`) gibt es noch
  keine Signaturprüfung – das Paket direkt aus dem Web-UI laden.

## Je Release (nach `deploy/update.sh`)

```bash
docker compose exec orchestrator automato agent-package manifest > manifest.txt
# offline:
python3 scripts/agent_signing.py sign agent-signing.key manifest.txt > agent-package.sig
# zurück auf der VM:
docker compose cp agent-package.sig orchestrator:/tmp/agent-package.sig
docker compose exec orchestrator automato agent-package install-signature /tmp/agent-package.sig
```

Die Signatur liegt im Daten-Volume (`/var/lib/automato/agent-package.sig`) und gilt nur für
genau diese Paket-Version. System-Seite: „signiert, Signatur gültig“. Ein neues Image ohne neue
Signatur zeigt „nicht signiert“; Agenten mit Schlüssel aktualisieren dann nicht.

## Schlüssel wechseln

Neues Paar erzeugen, neuen öffentlichen Schlüssel an Orchestrator **und** Agenten verteilen
(`agent.env`), danach mit dem neuen Schlüssel signieren. Bis dahin bleiben Agenten mit dem alten
Schlüssel auf ihrer Version.
