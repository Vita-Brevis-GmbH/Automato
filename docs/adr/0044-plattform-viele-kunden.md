# ADR 0044 — Plattform für viele Kunden: Startseite, Adressen, Anmeldung je Mandant

## Status

**Entwurf** (2026-11-03) – Entscheide des Anwenders beim Aufsetzen der Produktion; Umsetzung
grösstenteils in Version 2 (neuer Name und Cockpit). **Nachtrag 1.1.0** (2026-11-09): Adressen
mit `-nb`, `-doc`, `-um`, `-cmdb`, interne Seiten unter `*.mgmt.int`, Front-Proxy auf der
Automato-VM hinter einer WAF, NetBox-Host als Backend (unten). Ergänzt ADR 0014 (Cockpit, Entwurf v2) und
ADR 0018 (Kunden innerhalb eines Mandanten); passt die Empfehlungen zu den offenen Entscheiden 2
und 3 von ADR 0014 an.

## Kontext

- Viele kleine Kunden brauchen eine Dokumentation; intern gibt es kein Infoblox – NetBox ist
  IPAM und Dokumentation, DNS-Namen kommen aus NetBox und künftig aus dem AD-DNS.
- Weitere Werkzeuge kommen dazu: ein Lizenz- und Gerätemanagement, später Automatisierung.
  Magister und Keycloak laufen schon getrennt.
- Der Kunde soll sich schnell zurechtfinden; der Betrieb soll nicht für jedes Werkzeug und jeden
  Kunden Namen, DNS-Einträge und Zertifikate einzeln vergeben müssen.
- Der neue Name des Werkzeugs ist offen („Palantir“ verworfen – stark geschützte Marke).

## Entscheid

1. **Startseite je Kunde, Werkzeuge mit Namen nach Schema** (Variante B des Anwenders):

   | Adresse | Inhalt |
   |---|---|
   | `<kunde>.mgmt.example.ch` | Startseite des Kunden (Kacheln zu seinen Werkzeugen) |
   | `<kunde>-netbox.mgmt.example.ch` | NetBox des Kunden |
   | `<kunde>-<werkzeug>.mgmt.example.ch` | weitere Werkzeuge je Kunde (z. B. Lizenzen und Geräte) |
   | `<werkzeug>.mgmt.example.ch` | geteilte Werkzeuge (z. B. dieses, eine Instanz für alle) |
   | `start.mgmt.example.ch` | intern: alle Kunden und die Verwaltung |

   - Flach, weil ein Wildcard-Zertifikat nur eine Ebene abdeckt; ein Wildcard-DNS-Eintrag
     `*.mgmt.example.ch` auf den Proxy – neue Namen entstehen nur im Proxy, nichts einzeln zu
     vergeben.
   - Jedes Werkzeug hat seinen eigenen Ursprung im Browser (keine gemeinsamen Cookies, keine
     Pfad-Unterstützung nötig) – verworfen: alles unter einer Adresse mit Pfaden (Variante A).
   - Die Startseite zuerst statisch aus einer Vorlage (nur Links, keine Daten); in V2 dynamisch
     aus dem Cockpit (App-Starter, ADR 0014), nur mit den Werkzeugen, für die der Benutzer Rechte
     hat.
   - Greifen Kunden von eigenen Rechnern zu, braucht es ein öffentlich gültiges Zertifikat (z. B.
     Let's Encrypt mit DNS-01) oder die Verteilung der eigenen Root.
2. **NetBox je Kunde als eigener Stack** (eigene Datenbank, eigenes Redis, eigener `SECRET_KEY`,
   eigene Sicherung) auf einer **eigenen VM** (Entscheid 2026-11-03) mit gemeinsamem Proxy.
   Umgesetzt in 0.121.0: `deploy/netbox-host/netbox-host.sh` (anlegen, Tokens für Automato,
   Startseiten, sichern, wiederherstellen, aktualisieren, löschen, prüfen) und
   `docs/INSTALLATION-NETBOX-HOST.md`. Anmeldung vorerst lokal je Instanz, über Keycloak in V2.
3. **Eine Instanz des Werkzeugs, ein Mandant je Kunde** (statt einer Instanz je Firma, ADR 0014
   Entscheid 2) – jeder Mandant mit eigenen Quellsystemen, auch seiner NetBox als Referenz und
   Sync-Ziel (heute schon so). Kunden auf geteilter Infrastruktur bleiben Kunden innerhalb eines
   Mandanten (ADR 0018).
4. **Anmeldung je Mandant, Global Admin über alles** (V2, grösserer Umbau – „wird sich lohnen“):
   - je Mandant eine eigene Login-Seite wie bei Magister;
   - der **Global Admin** sieht und verwaltet alle Mandanten: entweder die Sicht eines Kunden
     (gefiltert nach Mandant) oder eine **globale Sicht** über alle Mandanten – Objekte und
     Werkzeuge übergreifend, z. B. um Regeln zwischen zwei Kunden zu erstellen bzw. zu überwachen.
   - **Entschieden 2026-11-03: Realm je Mandant** (eigene Login-Seite, eigener IdP des Kunden)
     und ein **Betriebs-Realm** für Vita Brevis mit Anbindung des lokalen AD (Keycloak-User-
     Federation über LDAP). Der Orchestrator nimmt mehrere Aussteller an und leitet den Mandanten
     aus dem Aussteller ab – ein Token aus dem Realm eines Kunden gilt nur für dessen Mandanten,
     unabhängig von den Claims (fail-closed); Global Admin nur aus dem Betriebs-Realm. Verworfen:
     ein Realm mit Keycloak-Organisationen.
5. **VPN-Verbindungen** in Version 2: IPsec und SSL-VPN der FortiGate lesend (Tunnel, Gegenstellen,
   Phase-2-Selektoren, Routen über Tunnel) in Weg-Suche, Verbindungen und Doku.
6. **DNS ohne Infoblox:** NetBox als IPAM; das AD-DNS als zusätzliche Referenzquelle, nur lesend
   über **Zonentransfer** (Entscheid 2026-11-03; auf dem DC nur für die Automato-VM freigegeben, kein
   AD-Konto); umgesetzt in 0.123.0 (ADR 0045).
7. **Projekt Datacenter:** möglicherweise als Kopie des Projekts weiterführen – Entscheid später.

## Nachtrag 1.1.0 (2026-11-09) – Adressen und Front-Proxy

Entscheid des Anwenders beim Aufsetzen des NetBox-Hosts. Ersetzt die Tabelle in Nr. 1 und die
Rolle des Proxys aus Nr. 2:

| Adresse | Inhalt | Wo | Zugang |
|---|---|---|---|
| `<kunde>.mgmt.example.ch` | Startseite des Kunden | Front-Proxy | über die WAF |
| `<kunde>-nb.mgmt.example.ch` | NetBox des Kunden | NetBox-Host | über die WAF |
| `<kunde>-doc.mgmt.example.ch` | Automato (eine Instanz, Mandant des Kunden) | Automato-VM | über die WAF |
| `<kunde>-um.mgmt.example.ch` | Benutzerverwaltung (Magister) | Magister | über die WAF |
| `<kunde>-cmdb.mgmt.example.ch` | CMDB (geplant) | – | über die WAF |
| `mgmt.int.example.ch` | Verwaltung, neues Cockpit (V2) | Front-Proxy | nur Management-Netz |
| `start.mgmt.int.example.ch` | Portal mit allen Kunden | Front-Proxy | nur Management-Netz |

- **Ein Eingang:** Kunden kommen von aussen über eine **WAF** auf die feste externe Adresse von
  `*.mgmt.example.ch`, und diese zeigt auf den **Front-Proxy der Automato-VM**. Er liefert
  Startseiten und Portal aus und leitet je Werkzeug weiter (`-doc` an die Oberfläche von
  Automato, `-nb` an den NetBox-Host, `-um` an Magister). Die internen Adressen
  (`*.mgmt.int.example.ch`) beantwortet er nur aus dem Management-Netz – das Portal mit den Namen
  aller Kunden ist für Kunden nicht erreichbar (bis 1.0.0 lag die Übersicht unter der Domain der
  Kunden).
- **NetBox-Host als Backend** (umgesetzt in 1.1.0, `deploy/netbox-host/netbox-host.sh`): keine
  Startseiten mehr dort, nur `<kunde>-nb`; erreichbar nur vom Front-Proxy und aus dem
  Management-Netz (`ALLOW`), der Angabe `X-Forwarded-For` des Front-Proxys wird geglaubt
  (`FRONT`). Zertifikat für `*.mgmt.example.ch` dort von der eigenen CA – der private Schlüssel
  des öffentlichen Wildcard-Zertifikats liegt nur auf dem Front-Proxy.
- **Kurznamen** nur `a-z`, `0-9`, `-`; nicht auf `-nb`, `-doc`, `-um`, `-cmdb` endend (sonst
  fiele die Startseite eines Kunden mit einem Werkzeug eines anderen zusammen).
- **Zwei Zertifikate:** öffentliches Wildcard für `*.mgmt.example.ch` (Front-Proxy; im
  Zertifikats-Log erscheint nur der Wildcard, keine Kundennamen) und eines der eigenen CA für
  `mgmt.int.example.ch` und `*.mgmt.int.example.ch`.
- **`-doc`** ist dieselbe Instanz von Automato (Nr. 3); die Adresse wählt in V2 den Realm des
  Mandanten (Nr. 4). Freigeschaltet wird `-doc` für einen Kunden erst, wenn er selbst in die Doku
  soll.
- **Front-Proxy umgesetzt in 1.2.0** (`deploy/front/front.sh`, `docs/INSTALLATION-FRONT.md`): die
  WAF beendet TLS mit dem öffentlichen Wildcard und baut eine neue HTTPS-Verbindung zum Front-Proxy
  auf (eine Adresse, `X-Forwarded-For` an) – nur ihr wird die Angabe geglaubt. Kunden-Adressen nur
  von der WAF und aus dem Management-Netz; Mitarbeitende gehen für Kunden-Adressen ebenfalls über
  die WAF (kein Split-DNS), die internen Adressen gehen direkt auf die Automato-VM. Automato
  erreicht die NetBox der Kunden über die WAF. Werkzeuge erscheinen je Kunde nur mit Freischaltung
  (`--doc`, `--um`, `--cmdb`); `-um` (Magister) wird später umgebaut und bis dahin nicht
  weitergeleitet.

## Folgen

- ROADMAP: Version 2 um Startseite je Kunde, Anmeldung je Mandant mit globaler Sicht, VPN und
  Lizenz-/Gerätemanagement ergänzt; NetBox-Host und AD-DNS als nächste Arbeiten.
- Der Name des Werkzeugs bleibt bis zum Entscheid Automato; Adressen der Produktion werden erst mit
  dem neuen Namen umgestellt.
- Kein neuer Schreibweg: AD-DNS und VPN werden nur gelesen; NetBox bleibt der einzige Schreibweg
  (ADR 0010).
