# ADR 0044 — Plattform für viele Kunden: Startseite, Adressen, Anmeldung je Mandant

## Status

**Entwurf** (2026-11-03) – Entscheide des Anwenders beim Aufsetzen der Produktion; Umsetzung
grösstenteils in Version 2 (neuer Name und Cockpit). Ergänzt ADR 0014 (Cockpit, Entwurf v2) und
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

## Folgen

- ROADMAP: Version 2 um Startseite je Kunde, Anmeldung je Mandant mit globaler Sicht, VPN und
  Lizenz-/Gerätemanagement ergänzt; NetBox-Host und AD-DNS als nächste Arbeiten.
- Der Name des Werkzeugs bleibt bis zum Entscheid Automato; Adressen der Produktion werden erst mit
  dem neuen Namen umgestellt.
- Kein neuer Schreibweg: AD-DNS und VPN werden nur gelesen; NetBox bleibt der einzige Schreibweg
  (ADR 0010).
