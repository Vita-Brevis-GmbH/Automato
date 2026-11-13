# Zusätzliche CAs für Orchestrator und Worker (1.10.0, ADR 0053)

Jede Datei `*.crt`, `*.pem` oder `*.cer` (PEM oder DER) in diesem Ordner vertrauen Orchestrator
und zentraler Worker **zusätzlich** zu den öffentlichen CAs – etwa die interne CA, die das
Zertifikat des Front-Proxys bzw. der NetBox eines Kunden ausgestellt hat. Nur das öffentliche
Zertifikat der CA, nie einen Schlüssel.

```bash
sudo install -D -m 644 /root/ca.crt deploy/trust/interne-ca.crt
sudo docker compose up -d --force-recreate orchestrator collector
```

Der Ordner ist im Container nur lesend eingebunden (`/etc/automato/trust`); beim Start baut jeder
Prozess daraus ein Bündel und zeigt `SSL_CERT_FILE` darauf (`sot_gateway/trust.py`). Ohne Dateien
ändert sich nichts. Ersetzt die `docker-compose.override.yml` mit `SSL_CERT_FILE` aus
`docs/INSTALLATION-PROD.md` 6.3 (die bleibt möglich und wird ergänzt, nicht ersetzt).
