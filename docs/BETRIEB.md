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

**Zeitgesteuert:** Mit einem Zeitplan am Quellsystem legt der Orchestrator zum Termin selbst
einen Lauf an (`AUTOMATO_SCHEDULER_ENABLED=true`, in docker-compose Standard). Wie „Jetzt
einlesen“: Gerät mit Agent → der Agent holt den Lauf ab. Wartet oder läuft für das
Quellsystem noch ein Lauf (z. B. Agent offline), fällt der Termin aus; nach einem Ausfall des
Orchestrators wird nur der letzte verpasste Termin nachgeholt. Mehrere Orchestrator-Prozesse
lösen einen Termin nur einmal aus. „Letzte Läufe“ kennzeichnet solche Läufe mit „Zeitplan“.

**NetBox pflegen (SoT):** Die Firewall-Objekte sind nur der Startpunkt. Netze/IPs/Zonen
in NetBox kuratieren; Zonen hängen als Tag `zone-<name>` am Prefix. Nicht gematchte
Objekte erscheinen als Findings → in NetBox ergänzen oder als extern bestätigen.

**Alle auf einmal:** Übersicht → **Alle einlesen** bzw. `POST /jobs/collect-all` legt für jedes
aktive Quellsystem des Mandanten einen Lauf an (Referenzquellen zuerst).

**Stoppen / Zurücksetzen:**
```bash
docker compose --profile core --profile collectors down          # stoppen
docker compose --profile core --profile collectors down -v       # inkl. Daten
```

## 3. Bedienung (Web-UI, `http://<host>:5173` bzw. über den TLS-Proxy `https://<host>`)

Aufbau nach dem UI-Mockup: Sidebar links (Dokumentation / Administration, Zähler für
Regeln, NAT und offene Findings), Topbar mit **Mandanten-Schalter**, HTTPS-Anzeige,
Hell/Dunkel und Benutzermenü (Rolle, Mandanten, Mein Konto, Abmelden). Im OIDC-Modus
wird das Token still erneuert; läuft die Keycloak-Sitzung ab, folgt die Anmeldung erneut.
Fehler zeigt das UI mit der Meldung des Orchestrators (z. B. „Keine Berechtigung“).

**Dokumentation**
- **Übersicht:** KPIs (Regeln, NAT/VIPs, offene Findings, Abdeckung gegen die SoT),
  **Letzte Läufe** und **Jetzt einlesen** (Operator): Mandant + Quellsystem wählen,
  Ausführung *Extern (Agent)* oder *Zentral (Worker)*. **Alle einlesen** legt für jedes aktive
  Quellsystem des Mandanten einen Lauf an (IPAM-Quellen zuerst; läuft/wartet schon einer oder
  fehlt der Lese-Zugang, wird das System mit Grund übersprungen, z. B. „Lauf läuft seit
  21.10. 09:12 (Agent „agent-01“)“). Laufende Läufe aktualisieren sich; ein Agent holt den
  Lauf im Wach-Modus innert rund 20 s ab.
  - **Hängende Läufe** (Agent beendet bzw. neu gestartet, Ergebnis-Upload verloren) beendet
    Automato selbst: laufend ohne Ergebnis nach `AUTOMATO_RUN_TIMEOUT_MINUTES` (Standard 120),
    nicht abgeholt nach `AUTOMATO_RUN_QUEUE_TIMEOUT_HOURS` (Standard 24).
  - Sofort geht es mit **Abbrechen** (Letzte Läufe) bzw. **Abbrechen & neu starten** im
    Dialog „Alle einlesen“ (Operator). Liefert der Agent danach doch noch ab, wird das
    Ergebnis eingespielt.
- **Firewall-Regeln:** Quelle/Ziel/Dienste mit den **Objektnamen wie auf der Firewall**;
  NAT-Kennzeichen je Regel (**SNAT** mit Pool bzw. Interface-IP, **VIP**; Klick → NAT-Detail).
  Quelle/Ziel umfassen auch Internet Services (ISDB), Benutzergruppen und ZTNA-Tags;
  **„nicht (negiert)“** markiert negierte Felder, **„kein Consumer · nicht wirksam“** einen
  ACI-Contract ohne Consumer bzw. Provider (auch als Befund „Regel nicht wirksam“).
  Die **Suche findet alles**, was zur Regel gehört: Regelname/-ID, Kommentar, Zonen, Aktion,
  Schutzprofile, Objekte **inkl. verschachtelter Gruppenmitglieder**, Services, Werte und IPs
  (eine IP trifft auch, wenn sie in einem Netz/Bereich der Regel liegt – „any“ ausgenommen),
  **DNS-Namen aus dem IPAM** (dort ohne Präfix, z. B. `web01.example.ch` für `H_web01`),
  EPG/AP/Tenant/BD/VRF, **VM-Namen** aus dem vCenter und NAT (VIP-/Pool-Name, externe/interne
  IP, Port). Mehrere Wörter =
  alle müssen vorkommen. Filter System, Quell-/Ziel-Zone, Aktion, **Familie** (IPv4, IPv6,
  IPv4+IPv6), „nur mit Findings“ (über alle Regeln, nicht nur die geladene Seite); Links
  `#regeln?object=<Name>` bzw. `#regeln?nat=<Name>` zeigen alle Regeln mit diesem Objekt (auch
  über Gruppen, beide Familien) bzw. NAT, `#regeln?object_id=<ID>` genau ein Objekt.
  **Regeln je Subnetz:**
  - Objekte → Subnetze → „Regeln im Subnetz“ (`#regeln?net=<CIDR>`) zeigt Regeln, deren
    Quelle oder Ziel sich mit dem Subnetz überschneidet, mit Schalter „inkl. any“.
  - Ein Netz im Suchfeld (z. B. `10.20.0.0/25`) trifft genauso (ohne any).

  **IPv4/IPv6-Paare** (ADR 0016): Je Regel die Familie, und „↔ IPv6 #…“ bzw. „↔ IPv4 #…“
  markiert das Gegenstück auf derselben FortiGate.
  - Grün: gleiche Objektnamen, Dienste und Interfaces.
  - Gelb „ähnlich“: Abweichungen im Tooltip.
  - Klick öffnet das Gegenstück; beim Überfahren wird es hervorgehoben. Beide bleiben eigene
    Regeln.
  Klick → Detail: je Objekt Typ, Kommentar, Gruppenmitglieder (Objektnamen verlinkt) und je
  Wert die **Referenzdaten** aus dem IPAM (DNS-Name, Netz, VLAN, Standort; aufklappbar mit
  allen Feldern und Link in NetBox), die **VM** dahinter (vCenter: Name, Status, Portgruppe,
  EPG), dazu Dienste, Schutzprofile, Kommentar und **NAT** (VIPs/SNAT-Pools mit IPs, Ports und
  DNS-Namen).
- **Objekte & Inventar:** Adressobjekte (inkl. VIPs/IP-Pools), Gruppen, **Services**
  (FortiGate-Services und ACI-Filter `<Tenant>/FT_…` mit Protokoll/Ports, Quellports, ICMP,
  Kategorie, Timer, Ziel-Beschränkung bzw. Filter-Einträgen; Prüfung nach FT_-Schema) – mit
  Verwendung in Regeln/Gruppen und Befunden. **Klick auf ein Objekt** öffnet das Detail wie
  bei NAT: Attribute, Werte, Mitglieder, Befunde und die Firewall-Regeln, die es nutzen (auch
  über Gruppen), mit „In Regeln anzeigen“. Dazu das Netzinventar aus FortiGate/APIC:
  **Subnetze** (mit Abgleich gegen führendes IPAM und Anreicherung, VLAN-Abweichung
  markiert), **VLANs** (inkl. EPGs), **Ports** (Modus, untagged/tagged VLANs, LAG/Parent,
  IPs, Zone/Policy-Gruppe, LLDP/CDP-Nachbar, EPGs), **Geräte**, **VRFs**. Filter Quelle,
  Suche (auch Mitglieder und DNS-Namen der Werte aus dem IPAM), „nur Abweichungen“.
  - Die **Regel-Zahl** je Objekt zählt die Regeln direkt und über (verschachtelte) Gruppen.
    Klick → genau diese Regeln.
  - **IPv4/IPv6:** Gleichnamige Objekte stehen zweimal da, mit Chip „IPv4“ bzw. „IPv6“,
    eigenen Werten, Gruppen und Befunden, wie auf der FortiGate.
  - **VMs** (vCenter, ADR 0017): Status, Netzwerk je NIC (Portgruppe mit VLAN → **EPG**, IPs
    mit DNS-Namen, „nicht im IPAM“), Cluster/Host/Datacenter, vCPU/RAM/Disk, Gast-OS und
    „Regeln der VM“. **Portgruppen** mit Switch, VLAN bzw. Trunk, Anzahl VMs und der
    zugeordneten EPG.
  - Zuordnung Portgruppe → EPG: Name nach ACI-VMM-Schema `Tenant|AP|EPG`
    (z. B. `TE_x|AP_007|EG_dmz02`), auch ohne Präfixe (`EG_db01` = `EPG_db01`); sonst das
    VLAN, wenn es genau einer EPG gehört. Der Chip sagt, ob über Name oder VLAN.
- **NAT / VIPs:** VIPs (extern → intern, Port-Forward), **Virtual Server** und SNAT-Pools.
  - Virtual Server (Lastverteilung) zeigen alle Real Server mit Status (aktiv/Standby/aus),
    Gewicht, Firewall-Objekt, DNS-Name und Health-Check. Im Detail stehen zusätzlich
    Server-Typ, Lastverteilung, Persistenz und die Health-Checks.
  - Alle Einträge mit **DNS-Namen**
  beider IPs aus DDI/NetBox, **Server** (Firewall-Objekt der internen IP), Interface,
  Kommentar und **Anzahl Regeln** (Klick → Regel-Liste). Suche über Name, IP (auch im
  Pool-Bereich), Port, DNS-Name, Server, Interface, Kommentar. Klick → Detail mit
  Einstellungen, IPAM-Sicht (Netz, VLAN, Standort) und den Firewall-Regeln, die den Eintrag
  nutzen.
- **Applikationen** (ADR 0012): *Applikationen* mit Kürzel, Verantwortlichen, Kritikalität,
  Standort und Mitgliedern (Application Profiles, EPGs, Server); **Vorschläge** nach der
  Projektnummer aus `AP_<nr>`/`H_…-<nr>-…` mit einem Klick übernehmen (Operator); **Doku**
  je Applikation über alle Systeme mit **Export (Markdown)** und **Drucken / PDF**. Dazu
  *Doku je System* (10 Kategorien, Standort verorten) und *Publizierte Dienste* (VIPs mit
  Regeln). Die Doku erfasst **alle** Regeln eines Systems. Die Spalte **„Bezug“** sagt, wie
  die Regel greift:
  - direkt,
  - über ein Netz, einen Bereich oder eine Gruppe (Objektname),
  - any,
  - vzAny der VRF, ESG, oder die EPG des Servers.

  Für EPGs zählen ihre Subnetze und Endpunkte. Der Kopf nennt die **VM** des Servers bzw.
  die VMs an den Portgruppen der EPG; ein Server bringt auch die Contracts der EPG seiner
  Portgruppe mit (ohne gelernten Endpunkt).
- **Findings:** oben eine **Übersicht nach Kategorien** (IPAM-Abgleich, Namenskonvention,
  Bereinigung, ACI / VMware) mit offenen/gesamten Befunden je Typ (Klick filtert). Neu mit
  dem vCenter: *VM-IP fehlt im IPAM* und *Portgruppe nach ACI-Schema ohne EPG*. Je Befund ein Satz,
  **was genau nicht stimmt**, und ein Hinweis, was zu tun wäre – z. B. „172.16.5.9 ist im IPAM
  weder als IP noch in einem bekannten Netz erfasst. Den Server db01.example.ch kennt das
  IPAM unter 10.30.0.9. Firewall-Objekt H_db01 …, verwendet in 1 Regel“. Aufklappen zeigt alle
  Angaben und Links auf die betroffenen Regeln. Filter Status/Typ, Suche; Status setzen
  (Triage) als Operator – bleibt über spätere Läufe erhalten (Findings werden nach jedem Lauf
  neu berechnet). Der Abgleich berücksichtigt, dass das IPAM den Server **ohne Präfix**
  führt: `H_web01` passt zu `web01` bzw. `web01.example.ch` (Kurzname nur, wenn eindeutig).
- **Abgleich:** Referenzquellen des Mandanten (führend/Anreicherung, eingelesen?), Kacheln je
  Befund-Typ (Klick → Findings vorgefiltert): nicht im IPAM, Name ≠ DNS-Name, Subnetz fehlt
  im IPAM, VLAN weicht ab, Namenskonvention, Präfix/Typ, doppelte Werte, unbenutzte
  Objekte. Darunter **Vergleich DDI ↔ NetBox** (fehlt in NetBox / abweichend / nur in
  NetBox) mit Suche. **Jetzt abgleichen** (Operator) rechnet neu, ohne etwas zu lesen oder
  zu schreiben.
- **Netzwerkdesign:** geplant (Platzhalter).

**Administration**
- **Benutzer & Rollen:** Rollen viewer/operator/admin, eigenes Konto, Links in die
  Keycloak-Benutzerverwaltung (`AUTOMATO_OIDC_ADMIN_CONSOLE_URL`, sonst aus dem Issuer
  abgeleitet). Benutzer selbst werden in Keycloak gepflegt (`tenant_ids` = Mandanten-ID).
- **Mandanten & Standorte (Admin schreibt):** **+ Mandant**, Kürzel, Namensprofil,
  Lese-Audit, **Standorte** (ADR 0015): *virtuell* (dreistellige Nummer, z. B. `000` =
  Stretched DC) und *physisch* (Ort-Code, z. B. `chzrh1`); **+ Standort** mit Art, Code, Name
  und – bei virtuellen – den physischen Standorten, an denen er läuft (**Zuordnen** ändert das
  später). Die Standort-Auswahl in Quellsystem, Doku je System und Applikation ist nach Art
  gruppiert. Den Standort aus dem Namen (Netz `N_<tenant>-000-…`, Cluster
  `fwc11-000-<tenant>`, Host `evh01-chzrh1-<tenant>`) zeigen Objekte, Inventar (Geräte,
  Subnetze) und der Quellsystem-Dialog als Vorschlag („?“ = Code nicht erfasst). Weiter: die **Quellsysteme des
  Mandanten** (**+ Quellsystem** / **Bearbeiten**: FortiGate/Cisco ACI/Infoblox DDI/NetBox/VMware,
  Adresse, **Standort** des Geräts (für den NetBox-Sync), **Verwaltet von** = Collector-Agent
  oder zentraler Worker, read-only Zugangsdaten – FortiGate: **API-Token**, Cisco ACI:
  **APIC-Benutzer/Passwort** + **ACI-Tenants** (leer = alle ausser infra/mgmt), Infoblox:
  **WAPI-Benutzer/Passwort**, VMware: **vCenter-Benutzer/Passwort** (Konto mit der Rolle
  *Read-only* auf dem vCenter-Objekt bzw. als globale Berechtigung, jeweils mit „An
  untergeordnete Objekte weitergeben“), NetBox: **Lese-Token** + optional **Schreib-Token** (nur
  NetBox-Sync, geht nie an einen Agenten) – nur schreibbar, verschlüsselt gespeichert, Anzeige
  nur „hinterlegt"; bei Infoblox/NetBox die **Rolle** *IPAM führend* oder *Anreicherung*
  (genau ein führendes IPAM je Mandant); TLS-Prüfung, aktiv; **Zeitplan** (stündlich,
  täglich 02:00, werktags 06:00, wöchentlich oder eigener Cron-Ausdruck; Zeitzone
  `AUTOMATO_SCHEDULER_TIMEZONE`, Standard Europe/Zurich – die Tabelle zeigt den nächsten
  Termin); Name je Mandant eindeutig; ADR 0009/0010). **Löschen:** Quellsystem (✕, Rückfrage nennt die mit
  gelöschten Regeln/NAT-Einträge) und **Mandant löschen** (nur nach Eintippen des Namens;
  löscht alle Daten des Mandanten inkl. seiner Collector-Agenten; Audit-Log bleibt).
  Altbestände ohne Mandant erscheinen unter „Nicht zugeordnete Quellsysteme“ zum Zuordnen.
- **Namensprofile:** Standardprofil **„default"** (per Migration angelegt, gilt für alle
  Mandanten ohne eigenes Profil, frei pflegbar), **+ Neues Profil** (Vorlage: Kopie eines
  bestehenden Profils, z. B. „default", oder leer), **+ Neue Namenskonvention** (optional die
  Einträge einer bestehenden Konvention – auch aus einem anderen Profil – kopieren),
  Einträge anlegen/bearbeiten/löschen.
- **Collector-Agenten (nur Admin):** **+ Agent** erzeugt das Token einmalig; Spalte
  **Geräte** (zugewiesene Quellsysteme); Status online/keine Meldung aus dem Heartbeat;
  Aktivieren/Deaktivieren. **Einrichtung Schritt für Schritt** direkt in der Ansicht: Geräte
  zuweisen, `agent.env`-Generator (nur Orchestrator-Adresse + Agent-Token, bleibt im Browser), Download des
  Agent-Pakets (`GET /agents/package`, ZIP ohne Zugangsdaten), PowerShell-Befehle zum
  Kopieren, Test und erster Lauf, Fehlerbehebung. Die geplante Aufgabe startet den Agenten
  alle 15 min (inkl. Selbst-Update-Prüfung); dazwischen bleibt er wach und fragt alle 20 s
  nach Läufen (`run-agent.ps1 -WatchSeconds`, von Hand einmal: `-Once`). Spalte **Version** mit Schalter
  **Auto-Update** (ADR 0011): Agenten aktualisieren sich vor jedem Lauf selbst, sobald das
  Orchestrator-Image ein neues Agent-Paket enthält („Update beim nächsten Lauf“ → „aktuell“);
  ist Auto-Update aus, zeigt die Liste „veraltet“ und das Paket wird manuell installiert.
- **System (nur Admin, ADR 0013):** Version, Migrationsstand DB/Code, Agent-Paket mit
  Signaturzustand und Anzahl aktueller Agenten, Anmeldung, Agent-Kanal (mTLS-Modus),
  Verschlüsselung von Audit-Log und Zugangsdaten, Zeitplan mit nächstem Termin, letzte
  Sicherung. Rein lesend, zeigt nie Schlüssel.
- **NetBox-Sync (nur Admin, ADR 0010):** einziger Schreibweg von Automato. Standorte werden
  NetBox-Sites in den Standort-Gruppen *Virtuelle Standorte* bzw. *Physische Standorte*, der
  Kommentar nennt die Zuordnung (ADR 0015; das Schreib-Token braucht dafür auch
  *DCIM › Site Group*). Ziel-NetBox
  wählen (Quellsystem mit Schreib-Token), Umfang *Netzinventar* (Geräte mit Ports, VLANs je
  Quellsystem, reale Subnetze inkl. ACI BD/EPG, VRFs), *DDI-Spiegel* (Netze, Ranges,
  IPs mit DNS-Namen aus dem führenden IPAM) und/oder *Virtualisierung* (ADR 0017: Cluster-Typ
  „VMware vSphere“, Cluster-Gruppe je Datacenter, Cluster, VMs mit Status, vCPU, RAM, Disk und
  Kommentar, VM-Interfaces mit MAC, Portgruppe, VLAN und EPG, IPs am VM-Interface; ohne
  Vorlagen), optional *Löschen zulassen*. Von Hand gepflegte IPs (NetBox als IPAM) bleiben
  unverändert; fehlt nur die Zuordnung zur VM, steht das als Konflikt da. Ohne den Abschnitt
  *Virtualisierung* bleiben VMs in NetBox unangetastet. Für diesen Abschnitt braucht der
  Sync-Benutzer die Rechte auf `virtualization.*` – `source-of-truth/netbox/sync-user.sh`
  nach dem Update erneut ausführen (idempotent). **Vorschau
  erstellen** liest NetBox nur und zeigt je Art: anlegen / ändern / unverändert / Konflikt
  (von Hand gepflegtes Objekt weicht ab – bleibt unverändert) / löschen / veraltet, mit
  Aktionsliste. **Übernehmen** (Bestätigung) berechnet neu, schreibt nur bei unveränderter
  Lage, auditiert und liest NetBox danach neu ein. Geändert/gelöscht werden nur Objekte mit
  Tag `automato`. Geräte ohne Standort werden übersprungen (Hinweis) – Standort am
  Quellsystem setzen (bei genau einem Standort des Mandanten wird dieser genutzt).

## 4. API

Lesen = `viewer`, Läufe/Triage = `operator`, Verwaltung = `admin` (mandantsgescoped).

| Endpunkt | Zweck |
|---|---|
| `GET /health`, `GET /auth/config` | Status (API/DB), öffentliche Login-Konfiguration |
| `GET /rules` (`?q=`,`?object=`,`?nat=`,`?tenant_id=`,`?domain=firewall\|aci_contract`,`?source_system_id=`,`?src_zone=`,`?dst_zone=`,`?action=`,`?limit=`,`?offset=`), `GET /rules/{id}` | Regeln und ACI-Contracts seitenweise (Standard 500, Gesamtzahl im Header `X-Total-Count`; Suche über alles, was zur Regel gehört; `object` = Regeln mit diesem Objekt auch über Gruppen, `nat` = mit diesem VIP/SNAT-Pool; je Regel `nat`) / Detail (Objektnamen, `refs` mit Referenzdaten, `aci`, `comment`, `nat_entries`) |
| `GET /rules/summary` (`?tenant_id=`) | Kennzahlen aller Regeln für die Übersicht (je Art, Endpunkte, nicht aufgelöst, Quell-/Ziel-Zonen, Aktionen) |
| `GET /objects?tenant_id=` (`category`, `source_system_id`, `q`, `issues_only`), `GET /objects/{id}` | Firewall-Objekte mit IPAM-Abgleich und `attrs` / Detail mit Mitgliedern und Regeln (`rules`, `rule_count`) |
| `GET /inventory/{devices\|interfaces\|vlans\|subnets\|vrfs}?tenant_id=` | Netzinventar (Subnetze mit IPAM-Abgleich) |
| `GET /vms?tenant_id=` (`q`, `source_system_id`, `epg`, `limit`, `offset`), `GET /portgroups?tenant_id=` (`q`) | VMware-Inventar (ADR 0017): VMs mit NICs, IPs (DNS, im IPAM?) und EPG der Portgruppe, Seite mit `total`; Portgruppen mit EPG und Art der Zuordnung |
| `GET /reconcile/summary`, `GET /reconcile/ipam`, `POST /reconcile` | Abgleich: Befunde je Typ, DDI ↔ NetBox, neu berechnen (operator) |
| `GET /findings` (`?status=`,`?type=`,`?q=`,`?limit=`,`?offset=`, `X-Total-Count`), `GET /findings/summary`, `PATCH /findings/{id}` | Datenqualität seitenweise (je Befund `message` = was nicht stimmt, `hint` = was tun), Zähler je Status/Typ, Triage |
| `GET /nat` (`?nat_type=`,`?q=`,`?limit=`,`?offset=`, `X-Total-Count`), `GET /nat/{id}`, `GET /published-services[/{name}]`, `GET /appdoc/systems[/{name}]` | Doku: NAT mit DNS-Namen, Server und Regelzahl / Detail mit IPAM-Sicht und Regeln, publizierte Dienste (VIPs), Doku je System |
| `GET/POST /applications`, `GET/PATCH/DELETE /applications/{id}`, `POST …/{id}/members`, `DELETE …/{id}/members/{mid}`, `GET …/{id}/doc` | Applikationen (ADR 0012; Schreiben = operator): Stammdaten, Mitglieder AP/EPG/Server, Doku über alle Systeme |
| `GET /applications/candidates` (`?kind=`,`?q=`), `GET /applications/suggestions`, `POST /applications/suggestions/{nr}/accept` | Mögliche Mitglieder; Vorschläge nach Projektnummer übernehmen (operator) |
| `GET/POST /tenants`, `PATCH /tenants/{id}`, `DELETE /tenants/{id}?confirm=<Name>` | Mandanten |
| `GET/POST /tenants/{id}/sites`, `PATCH/DELETE /tenants/{id}/sites/{sid}` | Standorte (`kind` virtual/physical, `physical_site_ids` bzw. `virtual_site_ids`) |
| `GET /systems?tenant_id=`, `POST /systems`, `PATCH/DELETE /systems/{id}` | Quellsysteme (je Mandant; `agent_id`, `site_id`, `read_scope`, `ref_role`, `schedule` → `next_run_at`; `token` bzw. `username`/`password`, NetBox `write_token` nur schreibbar → `has_credentials`/`has_write_token`; Referenzquellen mit `reference_count`/`reference_at`) |
| `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/collect`, `POST /jobs/collect-all`, `POST /jobs/{id}/cancel` | Läufe (Gerät mit Agent → immer `external`, `started_at` = abgeholt); `collect-all` = alle aktiven Quellsysteme des Mandanten (operator, Antwort je System `run_id` bzw. `skipped` mit `blocking_run_id`); `cancel` beendet einen offenen Lauf (operator) |
| `GET/POST /agents`, `PATCH /agents/{id}`, `GET /agents/package` | Collector-Agenten (inkl. zugewiesener Geräte), Agent-Paket |
| `GET /internal/agent/next-run`, `…/targets`, `POST …/heartbeat` | Agent-Kanal (`X-Agent-Token`): Lauf + Zugangsdaten nur für zugewiesene Geräte |
| `POST /internal/collector-runs/{id}/result`, `GET …/{id}/target` | Ergebnis (Agent-Token des abholenden Agenten oder internes Token; Antwort = Quittung `status`/`message`); Ziel für den zentralen Worker |
| `POST /internal/collector-runs/{id}/references` | Teil-Upload einer Referenzquelle (Infoblox/NetBox, je bis 2000 Einträge; das Ergebnis meldet `reference_count`, sonst wird der Lauf verworfen) |
| `GET /netbox-sync/targets`, `GET/POST /netbox-sync/plans`, `GET …/plans/{id}[/items]`, `POST …/plans/{id}/apply` | NetBox-Sync (admin): Vorschau, Aktionen, Übernahme |
| `GET/POST /naming-profiles`, `…/{id}/dictionary`, `DELETE …/dictionary/{id}` | Namensprofile |
| `GET /system/status` | Betriebsstand (admin, siehe System-Seite) |

Agent-Aufrufe (`/internal/agent/*`, Ergebnis-/Referenz-Upload mit `X-Agent-Token`) nimmt der
Orchestrator mit `AUTOMATO_INTERNAL_MTLS_MODE=require|bind` nur über das mTLS-Agent-Gateway
an (`deploy/agent-gateway`, Port 8443).

## 4b. Versionen und Updates

- **Eine Version für alles** (Datei `VERSION`, SemVer): Orchestrator (`/health` → `version`,
  Fusszeile der Web-UI), Collector-Agent, SoT-Gateway, Web-UI. Neue Version:
  `python3 scripts/version.py set X.Y.Z`, Änderungen in `CHANGELOG.md`.
- **Orchestrator aktualisieren:** `deploy/update.sh` (bzw. `deploy/update.sh <tag>`):
  verschlüsselte Sicherung → Code holen → Images bauen → starten → `/health`. Migrationen
  laufen beim Start (`alembic upgrade head`). Schlägt etwas fehl: `deploy/update.sh --rollback`
  setzt Code **und** Datenbank auf den Stand vor dem Update zurück (ADR 0013).
- **Sicherung:** täglich `deploy/backup/backup.sh` (age-verschlüsselt: Datenbank, Schlüssel der
  Zugangsdaten, Agent-Signatur), Wiederherstellung `deploy/backup/restore.sh` – Details und
  Verschlüsselung at rest in `deploy/backup/README.md`.
- **Regelindex** (ADR 0016): Er wird beim Einlesen je Quellsystem berechnet, nach einem
  IPAM-Lauf und nach „neu abgleichen“ für den ganzen Mandanten. Fehlt er nach einem Update,
  baut ihn die erste Anfrage einmalig auf; bei grossen Mandanten dauert diese Anfrage einige
  Sekunden.
- **Agenten** ziehen beim nächsten Lauf selbst nach (ADR 0011). Die Agentenliste zeigt je Agent
  „0.2.0 · Build …“ und „aktuell“ bzw. „Update beim nächsten Lauf“. Manuell (`install.ps1`) nur
  für die Erstinstallation oder wenn Auto-Update aus ist. Produktiv das Paket nach jedem Update
  **signieren** (`deploy/agent-signing/README.md`); Agenten mit
  `AUTOMATO_AGENT_SIGNING_PUBKEY` installieren nur signierte Updates.

## 5. Sicherheit

- Alle Zielsystem-Zugänge **read-only** (Least Privilege). `.env` wird nie committet.
- **AuthN/RBAC** über Keycloak (OIDC, `AUTOMATO_AUTH_MODE=oidc`, ADR 0007); im
  `dev`-Modus laufen alle Anfragen als Standard-Admin — nur im Lab verwenden.
- Ausserhalb `AUTOMATO_ENVIRONMENT=dev` verweigert der Orchestrator ohne RBAC den Start,
  solange `AUTOMATO_ALLOW_UNAUTHENTICATED_READ_API` nicht bewusst gesetzt ist (ADR 0005).
- Agent-Tokens werden nur gehasht gespeichert und einmalig angezeigt. Produktiv sprechen
  Agenten nur über das **mTLS-Agent-Gateway** (`deploy/agent-gateway`, Modus `bind`:
  Zertifikat und Token gehören zum selben Agenten); Port 8000 nicht in Agent-Netze freigeben.
- **FortiGate-Token prüfen (R16):** `automato-collector --check --strict` testet, ob die
  FortiGate ein ungültiges Token ablehnt. Opt-in, weil der Versuch als fehlgeschlagene
  Anmeldung zählt (admin-lockout-threshold).
- **Geräte-Zugangsdaten** (ADR 0009) liegen verschlüsselt in der DB; der Schlüssel kommt aus
  `AUTOMATO_CREDENTIALS_KEY` (Passphrase oder Secret-Referenz `openbao:`/`file:`) oder aus der
  beim ersten Start erzeugten Datei `/var/lib/automato/credentials.key` (Volume
  `orchestrator-data`). **Schlüssel bzw. Volume mitsichern** – geht er verloren, sind die
  Tokens unlesbar (Läufe brechen mit „Zugangsdaten nicht lesbar“ ab) und müssen im UI neu
  hinterlegt werden. Ein Agent bekommt nur die Tokens der ihm zugewiesenen Geräte.
- **Nur lesen, ausser NetBox (ADR 0010):** Die Lese-Clients für FortiGate, APIC, Infoblox und
  NetBox lassen technisch nur GET zu (APIC zusätzlich Login/Logout) – ein Schreibversuch
  bricht vor dem Senden ab. Der vCenter-Client (SOAP) lässt nur lesende vSphere-Methoden zu
  (Anmelden, Container-View, Eigenschaften abrufen; ADR 0017). Einziger Schreibweg ist der
  NetBox-Sync im Orchestrator mit dem Schreib-Token des NetBox-Quellsystems; er schreibt nur
  die synchronisierten Objekttypen (Tags, Tenants, Standort-Gruppen, Sites, Hersteller,
  Geräterollen/-typen, Geräte, Interfaces, VRFs, VLAN-Gruppen, VLANs, Prefixe, Ranges,
  IP-Adressen, Cluster-Typen/-Gruppen, Cluster, VMs, VM-Interfaces) und nur an diese NetBox.
  Das Schreib-Token in NetBox auf genau diese Objekttypen begrenzen (`sync-user.sh`).

## 6. Fehlerbehebung

- **„Anmeldung nicht möglich: Der Orchestrator lehnt das Anmelde-Token ab …“** (UI) —
  der Orchestrator nennt den Grund und loggt ihn (`docker compose logs orchestrator |
  grep OIDC`): `token audience invalid` → im Realm fehlt der Audience-Mapper
  `automato-api` (Client `automato-web` → Client scopes → dedicated → Mapper);
  `token issuer invalid` → `AUTOMATO_OIDC_ISSUER` passt nicht zum Realm (exakt wie
  `iss`, z. B. `https://auth.vitabrevis.ch/realms/automato-dev`); `Signaturschlüssel
  nicht in der JWKS` → `AUTOMATO_OIDC_JWKS_URL` zeigt auf einen anderen Realm. HTTP 503
  `JWKS vom Orchestrator nicht erreichbar` → der Orchestrator-Container erreicht Keycloak
  nicht (DNS/Firewall/WAF); Lösung „Backchannel direkt zur Keycloak-VM“ per
  `docker-compose.override.yml` in `deploy/keycloak/PROD-INSTALL.md` Abschnitt 9c.
  `token not yet valid` (bzw. `token expired` direkt nach der Anmeldung) → die Uhren von
  Keycloak-VM und Automato-VM weichen ab: auf beiden `timedatectl` prüfen („System clock
  synchronized: yes“), sonst NTP aktivieren (`sudo timedatectl set-ntp true`, intern ggf.
  den DC als NTP-Server eintragen). Toleranz: `AUTOMATO_OIDC_CLOCK_SKEW_SECONDS` (Default 60). Nach Änderungen an `.env`: `docker compose … up -d` (neu
  erstellt den Container).

- **FortiGate „antwortet auf <Pfad> nicht mit JSON (HTTP 200, …)“** (bis 0.11.1: „Expecting
  value: line 1 column 1 (char 0)“) — die FortiGate liefert für diese Tabelle eine leere bzw.
  HTML-Antwort. Optionale Tabellen (`policy6`, `vip6`, `ldb-monitor`, Systemstatus) überspringt
  der Collector seit 0.11.2. Bei einer Pflicht-Tabelle: im Browser bzw. mit `curl` und dem
  Read-only-Token `https://<fortigate>/api/v2/cmdb/<Pfad>` abfragen. Häufige Ursachen sind
  ein Admin-Profil ohne Leserecht für diesen Bereich, ein VDOM-Zugriff oder ein Proxy
  dazwischen.

- **Lauf „Fehler“ mit „Einspielen fehlgeschlagen: …“** — das Gerät wurde gelesen, aber das
  Ergebnis liess sich nicht in den Store schreiben. Der Ingest wird dann vollständig verworfen
  (vorheriger Stand bleibt), der Agent meldet `failed` mit derselben Meldung; Details inkl.
  Traceback: `docker compose logs orchestrator | grep -A30 "Einspielen von Lauf"`.

- **vCenter „Keine Berechtigung im vCenter: dem Konto fehlt System.View auf
  vim.Folder:group-d1 (oberste Ebene des Inventars)“** (bis 0.11.0 als mehrzeiliges
  `vim.fault.NoPermission`) — die Anmeldung klappt, aber das Konto darf das Inventar nicht
  lesen. Die Rolle *Read-only* enthält `System.View`; sie ist nur am falschen Ort oder ohne
  Weitergabe zugewiesen. Lösung im vSphere Client:
  - oberstes Objekt (der vCenter Server selbst) → *Berechtigungen* → *Hinzufügen*: Konto,
    Rolle *Read-only*, **„An untergeordnete Objekte weitergeben“** anhaken; oder
  - *Verwaltung → Zugriffssteuerung → Globale Berechtigungen*: ebenfalls mit Weitergabe.
    Eine globale Berechtigung ohne Weitergabe gilt nicht für das Inventar.
  - Ist das Konto über eine SSO- bzw. AD-Gruppe berechtigt: Mitgliedschaft prüfen.

  Danach `automato-collector --check` auf dem Agenten: er meldet „… N VMs sichtbar“. Steht
  dort „keine VM sichtbar“, reicht die Berechtigung nicht bis zu den VMs (Weitergabe fehlt
  oder liegt nur auf einem Teil des Inventars).

- **„Alle einlesen“ überspringt ein System, obwohl nichts läuft** (bis 0.11.2) — ein früherer
  Lauf wurde abgeholt, aber nie abgeschlossen (Agent beendet, Upload verloren), und steht
  ausserhalb der letzten acht Läufe noch auf „läuft“. Seit 0.11.3 nennt der Dialog den Lauf
  mit Zeit und Agent, bietet „Abbrechen & neu starten“ und beendet hängende Läufe nach der
  Frist selbst. Scheitert nur der Ergebnis-Upload, meldet der Agent den Lauf jetzt mit
  „Ergebnis-Upload fehlgeschlagen: …“ (z. B. HTTP 413, wenn ein Proxy das Ergebnis zu gross
  findet).

- **Infoblox/NetBox „Teil-Upload unvollstaendig (N von M)“** — bis 0.11.1 konnte der letzte
  Teil-Upload noch nicht gespeichert sein, wenn das Ergebnis eintraf. Behoben in 0.11.2
  (Orchestrator aktualisieren; der Agent muss nichts tun). Tritt die Meldung danach noch auf,
  ist ein Teil-Upload tatsächlich abgewiesen worden: Agent-Log prüfen.

- **NetBox-Sync „Vorschau/Übernahme fehlgeschlagen“** — die Meldung von NetBox steht am
  Plan (z. B. fehlendes Schreibrecht → HTTP 403, Validierungsfehler → HTTP 400 mit Feld).
  „NetBox nicht erreichbar (host)“: der Orchestrator (nicht der Agent) schreibt – NetBox muss
  vom Orchestrator-Container aus erreichbar sein. „Seit der Vorschau hat sich … geändert“:
  nichts wurde geschrieben, neue Vorschau erstellen. Nach einem Abbruch liest Automato
  NetBox neu ein; die nächste Vorschau setzt am Teilstand an.
- **Referenzquelle „unvollständig“** — der Lauf hat weniger Einträge hochgeladen als gemeldet
  (Abbruch/Netz); der vorherige Stand bleibt, Lauf wiederholen.
- NetBox startet nicht / Token-Fehler: `source-of-truth/netbox/README.md` (Abschnitt Fehlerbehebung).
- Stack-/Lab-Probleme: `lab/README.md`.
- Logs: `docker compose logs -f orchestrator collector`.
