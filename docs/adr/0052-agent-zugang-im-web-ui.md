# ADR 0052 — Agent-Zugang aus dem Web-UI: Zertifikat, Schlüssel und agent.env je Agent

## Status

**Angenommen** (2026-11-11), umgesetzt in 1.6.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-11, nachdem der Agent mit dem Zertifikat aus dem Web-UI nicht durch das
mTLS-Agent-Gateway kam: „Aber ich habe das Zertifikat auf dem Web UI heruntergeladen, warum geht
das nicht mehr?“ und auf die Erklärung (Ausweis des Agenten nur auf der VM, weil der Schlüssel
der Agent-CA sonst im Orchestrator läge): „ich will das Zertifikat und die .env Config im WebUI
herunterladen können und es muss funktionieren.“

## Kontext

Seit ADR 0013 nimmt das Agent-Gateway nur Agenten mit einem Client-Zertifikat der eigenen
Agent-CA an; im Modus `bind` muss der CN dem Agent-Namen entsprechen. Zertifikate stellte bisher
nur `make-certs.sh agent <Name>` auf der VM aus (Schlüssel der CA nur dort). Die Oberfläche bot
eine `agent.env` (im Browser erzeugt) und das öffentliche Zertifikat der **internen** CA – damit
kann ein Agent nur direkt auf Port 8000 den Server prüfen, aber sich nicht am Gateway ausweisen.
Ergebnis beim Anwender: drei Dateien von Hand, ein Name, der genau stimmen muss, und ein
Download, der nicht hilft.

## Entscheidung

1. **Ein Zugang je Agent aus der Oberfläche** (Admin, `POST /agents/{id}/access`): ein ZIP ohne
   Unterordner mit
   - `agent.env` – Adresse (mit Gateway immer dessen Adresse, sonst die angegebene), **neues**
     Agent-Token, Pfade der Dateien unter `C:\Automato\collector`, Schlüssel für signierte
     Updates (falls gesetzt), Sprache der Oberfläche;
   - mit dem mTLS-Gateway: `agent.crt` (CN = Agent-Name, O = Automato, EC P-256, nur
     clientAuth, 825 Tage bzw. bis zum Ablauf der CA), `agent.key` (je Download neu), `agents-ca.crt`;
   - ohne Gateway mit TLS intern: `automato-ca.crt`;
   - `LIESMICH.txt` (bestehender Agent: Dateien nach `C:\Automato\collector`; neue Installation:
     neben `install.ps1`, das sie übernimmt).
2. **Das Token wird dabei erneuert.** Zertifikat und Token gehören damit immer zusammen, die
   `agent.env` ist vollständig, und ein früherer Download wird wertlos. Schlüssel und Token werden
   nirgends gespeichert (nur der Hash des Tokens); das Audit nennt Agent, Adresse,
   Seriennummer und Ablauf (`agent.access_issued`), nie Token oder Schlüssel.
3. **Die Agent-CA liegt nur lesend im Orchestrator** (`./deploy/agent-gateway/pki` →
   `/etc/automato/agent-ca`). Lesen darf er nur, was `make-certs.sh oberflaeche` freigibt:
   `agents-ca.key` (Besitzer uid 1001, `0400`) und `agents-ca.crt`; der Ordner wird `0711`
   (betretbar, nicht auflistbar), `gateway.key` und von Hand ausgestellte Schlüssel bleiben root.
   `make-certs.sh ca` gibt neue CAs gleich frei, `make-certs.sh oberflaeche aus` sperrt wieder
   (dann nur noch `make-certs.sh agent`).
4. **Namen** für Zertifikate: Buchstaben, Ziffern, Leerzeichen, `. _ - ( )` – so maskiert das
   Gateway nichts im weitergereichten Subject und die Kopfzeile bleibt ASCII. Andere Namen lehnt
   der Zugang mit Grund ab (Token bleibt).
5. Die im Browser erzeugte `agent.env` und das Token-Feld entfallen; die Einrichtung zeigt
   Adresse, Zustand der Agent-CA (freigegeben / fehlt / gesperrt / ungültig) und den Knopf.

## Folgen

- **Risiko:** Wer Admin in der Oberfläche ist (bzw. den Orchestrator übernimmt), kann Agenten-
  Ausweise ausstellen. Vorher brauchte es dafür zusätzlich die VM. Ein Admin kann aber ohnehin
  Agenten und Tokens anlegen und Geräte zuweisen; ein Zertifikat allein nützt ohne das Token des
  Agenten nichts (bind: CN = Agent). Admin-Konten darum mit zweitem Faktor (Keycloak, OTP). Wer das nicht will:
  `make-certs.sh oberflaeche aus`.
- Ein neuer Download macht den alten Zugang über das Token ungültig; das alte Zertifikat bleibt
  bis zum Ablauf gültig (keine CRL, ADR 0013), nützt ohne Token aber nichts.
- Getestet mit dem echten Gateway (Caddy, eigener Port wie `AUTOMATO_AGENT_GATEWAY_PORT`) und dem
  TLS-Code des Agenten: `test_agent_gateway_e2e.py`.
