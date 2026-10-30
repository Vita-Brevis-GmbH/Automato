# Architecture Decision Records (ADR)

Hier werden wichtige Architekturentscheidungen dokumentiert (Kontext, Entscheidung, Konsequenzen).
Format: eine Datei pro Entscheidung, z. B. `0001-monorepo-vs-multirepo.md`.

| ADR | Titel |
|---|---|
| 0001 | Monorepo als Startstruktur |
| 0002 | SoT Gateway Adapter-Pattern, Rule Store, Topology Service |
| 0003 | Grundaufbau – Tech-Stack und Service-Schnitt für UC1 |
| 0004 | Datenmodell UC1 (Ist-Regel-Dokumentation, mandantenfähig) |
| 0005 | Security-Review UC1 & Sicherheitsentscheide (M3) |
| 0006 | UC1-Durchstich: Erkenntnisse & Entscheide aus dem echten Lab (M8) |
| 0007 | AuthN/RBAC via Keycloak (dedizierter IdP, Produktions-Gate) |
| 0008 | Externer/dezentraler Collector (Agent) |
| 0009 | Geräte-Zugangsdaten zentral, Zuweisung Gerät → Agent |
| 0010 | Inventar, Referenz, Abgleich; Schreiben nur nach NetBox |
| 0011 | Selbst-Update der Collector-Agenten |
| 0012 | Applikation als eigenes Objekt (AP/EPG/Server, Vorschläge per Projektnummer) |
| 0013 | Produktions-Gate: mTLS-Agent-Gateway, signierte Updates, Sicherung, Update/Rückfall, R16-Prüfung |
| 0014 | Vita-Brevis-Cockpit für alle Werkzeuge (Automato, Magister, NetBox, Keycloak) zusammen mit Magister – Plan mit Phasen und offenen Entscheiden (Entwurf v2) |
| 0015 | Standorte virtuell (000, 001) und physisch (Ort-Code), n:m; NetBox-Site-Gruppen |
| 0016 | IPv4/IPv6 gleichnamig getrennt, IPv4/IPv6-Regelpaare, abgeleiteter Regelindex (Leistung) |
| 0017 | VMware vCenter lesen (nur lesende vSphere-Methoden), Portgruppe → EPG, VMs nach NetBox Virtualization |
| 0018 | Umgebung und Kunden: einmal lesen, je Kunde ein Ausschnitt über Selektoren (Entwurf) |
| 0019 | Topologie und Pfade: VDOM-Kontext, Routing/HA/L3Out lesen, Regel-Relevanz, Designer (Entwurf) |
| 0020 | Parallele Firewalls (Verbund): Regeln zusammenfassen, Abweichungen als Findings; seit 0.72.0 auch Policy-Routen, Routing und NAT |
| 0021 | Verbindungen Ende zu Ende in der Applikations-Doku: Weg je Verbindung, Regeln je Hop, Abgleich Regel ↔ Weg (Entwurf) |
| 0022 | Aufbewahrung (Fristen für Läufe, Sync-Vorschauen, Audit) und getrennte DB-Rollen (Migration als Eigentümer, Betrieb ohne DDL) (Entwurf) |
| 0023 | TLS intern mit eigener CA: Postgres/Redis nur TLS, Orchestrator und Web-UI per HTTPS, Gegenstellen gegen die CA geprüft (Entwurf) |
| 0024 | Logische Zonen in Automato (Selektoren, längstes Präfix), Zonenmatrix mit Absicht je Paar, Zonenplan an Firewalls/VRFs, Export draw.io/SVG/PDF und Versionen (Entwurf) |
| 0025 | Cisco-Switches strikt lesend über SSH (Exec je Befehl, Positivliste, Host-Schlüssel festhalten) und Verkabelung aus CDP/LLDP mit Befunden (Entwurf) |
| 0026 | Export für Confluence: Word (.docx) und draw.io zum Import von Hand – kein Schreibweg |
| 0027 | VMware NSX Advanced Load Balancer (Avi) als lesende Quelle: Virtual Services als NAT/Load Balancer, Weg über VIP zu den Servern |
| 0028 | Sprache der Oberfläche und der Doku: Deutsch und Englisch (Text neben Text, Sprache je Browser, Server über `Accept-Language`, Fachbegriffe englisch) |
| 0029 | Datenqualität: Bereiche mit Ampel, Ursache und Link, Gesamtnote; Befunde „Stand veraltet“ und „nur teilweise gelesen“ (Entwurf, erste Fassung von Roadmap Schritt 6) |
| 0030 | Verlauf des Routings: Änderungen an Routen, BGP-Nachbarn, HA, Policy-Routen, Kontexten und L3Outs von Lauf zu Lauf (Positivliste der Felder, Ausgangsstand, nicht lesbar ohne Flut, Obergrenze, Frist 180 Tage) (Entwurf) |
| 0031 | Verkabelung und Applikationen nach NetBox: zwei Abschnitte im NetBox-Sync (Switches/Router mit Ports und beidseitig gemeldeten Kabeln, Modus/VLANs nur mit vorhandenen VLANs; Applikations-Tags `automato-app-…` an VMs und IP-Adressen) |
| 0032 | Routing bis an die Firmengrenze: Router und L3-Switches strikt lesend (RIB, BGP-Nachbarn/-Pfade je VRF, Obergrenze, nicht lesbar statt Fehler), als Knoten in Weg und Topologie; Firmengrenze bei eBGP zu fremdem AS, Anbindung Internet/MPLS/Partner bzw. Default-Route zum Provider (Entwurf) |
| 0033 | SNMPv3 lesend als Alternative zu SSH für Switches & Router: Zugang am Quellsystem, nur authPriv (SHA/SHA-2 + AES), nur GET/GETBULK auf eine Positivliste der Standard-MIBs, dieselben DTOs wie per SSH (Entwurf) |
| 0034 | L3Outs: Firewall führend – Firewall hinter jedem L3Out (abgeleitet bzw. von Hand), ihr Routing entscheidet über den L3Out einer Gegenstelle; Contracts zu externen EPGs nur als Hilfs-Contract an der Firewall-Regel; unspezifische Zugriffe nach L3Out (Entwurf) |
| 0035 | Kategorien nach Zonentyp (seit 0.106.0 einfach): Zonentyp = Zone des Grundschemas, Kategorie = Titel + Source-/Dest-Zone (drei feste: innerhalb Applikation, innerhalb Security Tenant, unspezifisch), Geräte je Zone, Ausgänge aus Geräten und Routing, Befund „Netz nicht über den Übergang zur Zone“ (Entwurf) |
| 0036 | Module: Verzeichnis im Code (Kern, Einstellungen, Worker, Gateway, Analyse, Wegsucher, Findings, Doku, Design, Schreibpfad), jede Datei genau einem Modul, Schreibpfad als einziger Nutzer des NetBox-Schreibers (geprüft in der CI), Fachmodule je Mandant schaltbar (Entwurf) |
| 0037 | Doku je Applikation als Stand: Doku und Verbindungen gespeichert, neu nur bei geändertem Fingerabdruck der Eingaben, sofort der letzte Stand und neu im Hintergrund, Befunde immer aktuell, Vorrechnen nach Läufen und Änderungen; Befunde der Zonen beim Speichern im Hintergrund (Entwurf) |
| 0038 | Weg erklären und Findings von Hand: „Warum dieser Weg?“ für Weg-Suche und Verbindungen (alle Routen zum Ziel mit Grund, Policy-Routen, Next-Hop, Ende mit Hinweis, Stand des Routings), „Erwartet über …“ ein Gerät, Findings vom Typ `manual` bleiben nach dem Abgleich (Entwurf) |
| 0039 | Findings als steckbare Prüfungen und Oberfläche je Modul: je Prüfung eine Datei `app/checks/<modul>/` (Modul, Befund-Typen, `run`), vom Abgleich selbst gefunden, je Mandant schaltbar (Admin, Audit) mit Dauer und Anzahl; Ansichten der Oberfläche in `src/modules/<modul>/`, Navigation aus dem Verzeichnis; übrige Dateien ziehen schrittweise um (Entwurf) |
| 0040 | Grosse Netze der Regeln zuordnen (kombiniert): Netz grösser als die Routen (Teile genauer geroutet bzw. privat nur über die Default-Route) – von Hand (Zone, Anbindung, Weg nach dem Routing, nur die Teile), sonst Zone bzw. Netze der Anbindung, sonst Teile nach dem Routing und Befund „Netz der Regel grösser als die Routen“; Netzlisten der Anbindungen einfügen, prüfen, zusammenfassen (Entwurf) |
| 0041 | BGP-Filter und Route Control lesen (Stufe 1): FortiGate `router bgp` mit Prefix-Listen, Route-Maps und Access-Listen (ohne Passwörter), ACI `enforceRtctrl` und import-/export-rtctrl; je Nachbar erwartet/erhalten/angekündigt, Befunde bei Abweichungen, Netze der Filter als Vorschlag für „Netze dahinter“; Stufe 2 (0.114.0): Backup-Wege gegen die Filter, Soll je Nachbar aus den Zonen dahinter mit Abweichungen und CLI-Vorschlag |
| 0042 | Nachkontrolle des NetBox-Syncs: nach „Übernehmen“ NetBox über einen eigenen, rein lesenden Client neu lesen und je geschriebenem Objekt vergleichen (fehlt, andere Werte, nicht gelöscht); Ergebnis am Plan und in der Meldung, Befund `netbox_sync_mismatch`; kein neuer Schreibweg |
