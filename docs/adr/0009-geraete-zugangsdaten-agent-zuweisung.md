# ADR 0009 — Geräte-Zugangsdaten zentral, Zuweisung Gerät → Agent

## Status

Angenommen (2026-10-20). Umgesetzt (Alembic `0016`, `0017`); ergänzt ADR 0007 (Secrets) und
ADR 0008 (externer Collector).

## Kontext

Bisher (ADR 0008) lagen URL und API-Token jedes Zielsystems in der `agent.env` des
Collector-Agenten; das Quellsystem im Web-UI war nur Dokumentation. Im ersten Dev-Test
zeigte sich:

- **Kein Überblick:** Welcher Agent welches Gerät liest, war nur auf der Station sichtbar.
- **Umverteilen teuer:** Ein Gerät einem anderen Agenten zuzuordnen hiess, zwei
  `agent.env` auf zwei Stationen zu ändern. Ein Agent konnte nur **ein** FortiGate lesen.
- **Zu viele Secrets beim Agenten:** neben dem Agent-Token auch das interne Service-Token
  des Orchestrators (`AUTOMATO_INTERNAL_API_TOKEN`, Ergebnis-Upload) – ein geteiltes
  Geheimnis, das auf jeder Kundenstation lag.

## Entscheidung

1. **Gerät = Quellsystem beim Mandanten** – FortiGate und Cisco ACI (APIC) gleich behandelt.
   Adresse, read-only Zugangsdaten, TLS-Prüfung und **verwaltender Agent**
   (`source_system.agent_id`, nullable) werden im Web-UI beim Mandanten erfasst; bei ACI
   zusätzlich die zu lesenden **ACI-Tenants** (`source_system.read_scope`, kommagetrennt,
   leer = alle ausser `infra`/`mgmt`). Der Agent muss zum Mandanten des Geräts gehören oder
   mandantsübergreifend sein (sonst 422). Ohne Agent liest der zentrale Worker.
2. **Zugangsdaten nur schreibbar, verschlüsselt gespeichert.** `source_system.credentials`
   hält ein Fernet-Token (AES-128-CBC + HMAC-SHA256) des JSON je Plattform: FortiGate
   `{"token"}`, Cisco ACI `{"username", "password"}` (APIC-Login, read-only Rolle). Ein Gerät
   hat alle Felder seiner Plattform oder keine (sonst 422); fremde Felder → 422. PATCH ändert
   nur angegebene Felder (z. B. nur das Passwort), `""` entfernt ein Feld. Die API gibt nur
   `has_credentials` aus; Audit protokolliert nur `credentials_set` (E1.4-Allowlist).
3. **Auslieferung nur an den Berechtigten.**
   - `GET /internal/agent/next-run`: Ein Agent holt nur Läufe von Geräten, die **ihm**
     zugewiesen sind (oder – Altbestand – gar keinem Agenten). Der Lauf merkt sich den
     abholenden Agenten (`collector_run.agent_id`); das `target` enthält das Token **nur**,
     wenn das Gerät genau diesem Agenten zugewiesen ist.
   - `GET /internal/agent/targets`: zugewiesene, aktive Geräte inkl. Token – für
     `automato-collector --check` (alle Geräte prüfen).
   - `GET /internal/collector-runs/{id}/target` (internes Token): Ziel für den zentralen
     Worker, nur für `inprocess`-Läufe (sonst 403).
   - Ein Gerät mit Agent wird immer `external` eingelesen (`POST /jobs/collect` erzwingt es).
4. **Ergebnis-Upload mit dem Agent-Token.** `POST /internal/collector-runs/{id}/result`
   akzeptiert `X-Agent-Token`, wenn genau dieser Agent den Lauf abgeholt hat (sonst 403),
   oder wie bisher `X-Internal-Token` (zentraler Worker). Agenten brauchen das interne
   Token damit **nicht mehr**; die `agent.env` enthält nur Orchestrator-Adresse +
   Agent-Token (+ optional mTLS).
5. **Schlüssel.** `AUTOMATO_CREDENTIALS_KEY` (Passphrase → SHA-256 → Fernet-Key; auch als
   Secret-Referenz `openbao:`/`file:`/`env:` über den Secrets-Provider) oder – leer – eine
   beim ersten Start erzeugte Schlüsseldatei `/var/lib/automato/credentials.key` (0600,
   Volume `orchestrator-data`). Kein Schlüssel / falscher Schlüssel = fail-closed: Speichern
   → 503, Abholen → Lauf `failed` mit Meldung, nie ein Lauf mit leeren Zugangsdaten.
6. **Kein Beispieldaten-Rückfall beim Agenten.** Ein Agent ohne Zugangsdaten für das Gerät
   bricht den Lauf mit klarer Meldung ab („im Web-UI beim Quellsystem hinterlegen“).
   Beispieldaten bleiben nur dem zentralen Worker/Lab vorbehalten. Lokale
   `AUTOMATO_FORTIGATE_URL/_TOKEN` in einer alten `agent.env` gelten als Rückfall,
   die Angaben am Quellsystem haben Vorrang.

## Begründung / Alternativen

- **Secrets nur beim Agenten (ADR 0008, bisher):** maximal dezentral, aber ohne Überblick,
  ohne Umverteilen und mit einem Gerät je Agent. Verworfen für den Normalfall.
- **Vault/OpenBao als Pflicht-Ablage:** sauberste Trennung, aber neue Pflicht-Abhängigkeit
  für jede Installation. Stattdessen app-seitige Verschlüsselung (wie Audit, E1.4) mit dem
  Schlüssel optional aus OpenBao – gleiches Muster, kein Zwang.
- **pgcrypto:** Schlüssel läge DB-nah; app-seitig bleibt die DB allein wertlos und die Tests
  DB-agnostisch.

## Konsequenzen

- Der Orchestrator wird **Verwahrer** read-only Geräte-Tokens. Schutz: Verschlüsselung at
  rest, Schlüssel ausserhalb der DB, Klartext nur über den authentisierten Agent-Kanal
  (TLS/mTLS, ADR 0008), nie über die Web-API/Logs/Audit. Tokens bleiben **read-only**
  (FortiGate-Profil nur Lesen, Trusted Hosts = Agent-Rechner) – ein Leck erlaubt Lesen,
  nicht Ändern.
- **Schlüssel sichern:** Verlust oder Wechsel des Schlüssels macht gespeicherte Tokens
  unlesbar – dann im Web-UI neu hinterlegen (keine Datenverluste sonst). Rotation:
  neuen Schlüssel setzen, Tokens neu erfassen (Re-Encrypt-Werkzeug bei Bedarf später).
- Agent-Kompromittierung legt nur die Tokens der **ihm** zugewiesenen Geräte offen;
  deaktivierter Agent authentisiert sofort nicht mehr (ADR 0008).
- Geräte lassen sich per UI jederzeit einem anderen Agenten zuweisen; an der Station ändert
  sich nichts. Ein Agent kann beliebig viele Geräte lesen.
- **APIC-REST-Client** (`collectors/cisco_aci/client.py`): `aaaLogin` mit den Geräte-
  Zugangsdaten → Session-Cookie, Tenant-Liste (`/api/class/fvTenant.json`), je Tenant der
  verschachtelte MO-Baum (`/api/mo/uni/tn-<TE>.json?rsp-subtree=full`, gleiches Format wie
  der Tenant-Export, inkl. `fvCEp`/`fvIp`), `aaaLogout`. `--check` = Login + Tenant-Liste.
  Weitere Plattformen ergänzen nur ihre Felder in `credentials.FIELDS`.
