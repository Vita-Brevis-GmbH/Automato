# Betriebs- & Anwenderhandbuch (UC1: Firewall-Regel-Dokumentation)

Für Model 2 (FortiGate + NetBox). Setup-Details: `lab/README.md`. Architektur/Entscheide:
`ARCHITECTURE.md`, `docs/adr/`.

## Überblick

```
FortiGate ──(read-only REST)──▶ Collector ─▶ Orchestrator ─▶ NetBox (Matching, SoT)
                                                 │
                                   Web-UI ◀──────┘  (Regeln, NAT/VIPs, Applikationen, Findings)
```

- **Collector** liest rein lesend von der FortiGate (Policies, Adress-/Service-Objekte, VIPs).
- **Orchestrator** persistiert, matcht Adressen gegen NetBox, erzeugt Findings, stellt die API.
- **NetBox** ist die **Source of Truth** fürs IPAM (nach dem Erst-Import gepflegt).
- **Web-UI** zeigt alles read-only mit Filtern.

## 1. Inbetriebnahme

Einmalig auf der VM (siehe `lab/README.md`):

```bash
sudo bash lab/install-prereqs.sh && newgrp docker
FORTIGATE_URL=https://<fortigate-ip> FORTIGATE_TOKEN='<readonly>' bash lab/bootstrap.sh
```

`bootstrap.sh` fährt NetBox + Automato hoch, importiert die FortiGate-Objekte als
IPAM-Ausgangsbasis nach NetBox (Tag `automato-import`) und löst einen ersten Lauf aus.

## 2. Betrieb

**Regeln erneut einlesen** (nach FortiGate-Änderungen):
```bash
docker compose exec orchestrator automato collect --platform fortigate --tenant lab --source fw-01
docker compose exec orchestrator automato status --run-id <id>
```
Der Lauf **ersetzt** die Regeln/NAT des Quellsystems und baut die Findings neu auf.

**NetBox pflegen (SoT):** Die Firewall-Objekte sind nur der Startpunkt. Netze/IPs/Zonen
in NetBox kuratieren; Zonen hängen als Tag `zone-<name>` am Prefix. Nicht gematchte
Objekte erscheinen als Findings → in NetBox ergänzen oder als extern bestätigen.

**Zyklisch:** Für regelmässige Läufe den Collect per Scheduler/Cron gegen die CLI bzw.
`POST /jobs/collect` auslösen (Celery-Beat ist im Stack vorhanden).

**Stoppen / Zurücksetzen:**
```bash
docker compose --profile core --profile collectors down          # stoppen
docker compose --profile core --profile collectors down -v       # inkl. Daten
```

## 3. Bedienung (Web-UI, `http://<host>:5173`)

- **Regeln:** Tabelle mit Aktion, System, Quell-/Ziel-Zone und Zählern. Filter oben:
  Freitext/IP-Suche (sucht auch in Objektwerten → „Netzwerke"), System, Zone, Aktion,
  „nur mit Findings". Klick auf eine Regel → Detail mit aufgelösten Quellen/Zielen
  (Wert, Match-Status, Zone) und Diensten; bei VIP-Zielen zusätzlich „→ internes Ziel".
- **Applikationen:** je publiziertem Dienst (VIP) extern→intern + die exponierenden
  Regeln; Button **Export (Markdown)** erzeugt ein Applikations-Dokument.
- **NAT / VIPs:** alle VIPs mit externer IP → internem Ziel und Port-Forward.
- **Findings:** Datenqualität (nicht aufgelöste Objekte). Leere Liste = alles aufgelöst.

## 4. API (read-only)

| Endpunkt | Zweck |
|---|---|
| `GET /health` | Status (API/DB) |
| `GET /rules` (`?q=`,`?tenant_id=`) | Regelliste |
| `GET /rules/{id}` | Regel-Detail (Endpunkte, Services, NAT) |
| `GET /findings` | Datenqualitäts-Befunde |
| `GET /nat` | VIP/DNAT-Zuordnungen |
| `GET /applications`, `/applications/{name}` | Applikationen (publizierte Dienste) |
| `GET /systems` | Quellsysteme |
| `POST /jobs/collect` | Lauf auslösen (intern über die CLI) |

## 5. Sicherheit (Stand UC1 Model 2)

- Alle Zielsystem-Zugänge **read-only** (Least Privilege). `.env` wird nie committet.
- Die **öffentliche Read-API ist noch nicht authentifiziert** (AuthN/RBAC = Folge-ADR).
  Ausserhalb `AUTOMATO_ENVIRONMENT=dev` verweigert der Orchestrator den Start, solange
  `AUTOMATO_ALLOW_UNAUTHENTICATED_READ_API` nicht bewusst gesetzt ist — bis RBAC steht,
  die API nur in einem vertrauenswürdigen Netz / hinter einem authentifizierenden
  Reverse-Proxy betreiben. Details: `docs/usecases/uc1-firewall-regel-dokumentation.md`
  („M9 – Abschluss") und ADR 0005.

## 6. Fehlerbehebung

- NetBox startet nicht / Token-Fehler: `source-of-truth/netbox/README.md` (Abschnitt Fehlerbehebung).
- Stack-/Lab-Probleme: `lab/README.md`.
- Logs: `docker compose logs -f orchestrator collector`.
