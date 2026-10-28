# Glossar Deutsch – Englisch

Begriffe der Oberfläche in beiden Sprachen (seit 0.86.0 umschaltbar, Benutzermenü → Sprache).
Neue Texte halten sich an diese Liste, damit derselbe Begriff überall gleich heisst.

**Grundsätze**

- Deutsch ist die Standardsprache; der deutsche Text steht im Code, der englische daneben
  (`L("Speichern", "Save")`, `web-ui/frontend/src/i18n.ts`).
- Englisch in US-Schreibweise (*color*, *behavior*), Datum/Uhrzeit nach `en-GB` (Tag zuerst, 24 h).
- Alle Benutzer können umschalten (auch Kunden-Benutzer); die Wahl gilt je Browser.
- Fachbegriffe bleiben in **beiden** Sprachen englisch, nichts eindeutschen (Entscheid des
  Anwenders 2026-10-27, „das würde verwirren“). Fachbegriffe der Hersteller bleiben wie im Gerät: Policy, Contract, Filter, Subject, EPG, ESG,
  BD, VRF, L3Out, vzAny, Taboo, Preferred Group, VDOM, VIP, IP-Pool, SNAT/DNAT, Virtual Server,
  Virtual Service, Pool, Service Engine, Load Balancer, Next-Hop, ECMP, BGP, RIB, FGSP, HA, vPC,
  Port-Channel, LLDP/CDP, Spanning Tree, Portgruppe → *port group*, Tenant (ACI).
- Titel und Knöpfe im Englischen mit grossem Anfangsbuchstaben nur am Anfang (*Save changes*,
  nicht *Save Changes*).

## Plattform und Betrieb

| Deutsch | Englisch |
|---|---|
| Mandant | Tenant |
| Umgebung | Environment |
| Kunde, Kunden-Benutzer | Customer, customer user |
| Kunden-Rolle (Lesen, Triage, Editor, Kunden-Admin) | Customer role (read, triage, editor, customer admin) |
| pflegbar (Applikation) | editable |
| Ausschnitt (eines Kunden) | Scope |
| geteilt | shared |
| Selektor | Selector |
| Quellsystem | Source system |
| Gerät | Device |
| Zugangsdaten | Credentials |
| Zugang (SSH / SNMPv3) | Access (SSH / SNMPv3) |
| Lauf, Läufe | Run, runs |
| einlesen / Alle einlesen | collect / Collect all |
| einspielen (Ergebnis) | ingest |
| Zeitplan | Schedule |
| Collector-Agent | Collector agent |
| Betriebszustand | Health |
| Verbindungen (System) | Connections |
| Aufbewahrung | Retention |
| Sicherung / Wiederherstellung | Backup / restore |
| Benutzer & Rollen | Users & roles |
| Namensprofil | Naming profile |
| Wörterbuch | Dictionary |
| Standort (virtuell/physisch) | Site (virtual/physical) |
| Referenzstand (IPAM) | Reference data |
| Vorschau → Übernehmen | Preview → Apply |
| Abgleich | Reconciliation |
| Datenqualität, Gesamtnote, Bereich | Data quality, overall score, area |
| Stand veraltet / nur teilweise gelesen | data outdated / only partially collected |
| Stichprobe | Sample |
| Befund, Findings | Finding, findings |
| Hinweis / Warnung / Fehler | Note / warning / error |
| offen / bestätigt / ignoriert | open / acknowledged / ignored |
| Kennzahlen | Key figures |
| Übersicht | Overview |

## Regeln, Objekte, NAT

| Deutsch | Englisch |
|---|---|
| Regel, Regeln & Contracts | Rule, Rules & contracts |
| Objekte & Inventar | Objects & inventory |
| Adressobjekt, Gruppe | Address object, group |
| Dienst | Service |
| Standardobjekt (Hersteller) | Predefined object |
| Zwilling (paralleler Firewalls) | Twin |
| IPv4/IPv6-Paar | IPv4/IPv6 pair |
| erlauben / sperren (Aktion) | accept / deny |
| Quelle / Ziel | Source / destination |
| Eingang / Ausgang (Interface) | Incoming / outgoing interface |
| eingehend / ausgehend | inbound / outbound |
| beide Richtungen | both directions |
| Rückrichtung, Rückweg | Reverse direction, return path |
| Bereich (Adressen) | Range |

## Routing, Weg, Topologie

| Deutsch | Englisch |
|---|---|
| Weg-Suche | Path search |
| Weg, Hop | Path, hop |
| erlaubt / gesperrt / abhängig | allowed / blocked / depends |
| möglich / unmöglich / unbekannt | possible / impossible / unknown |
| implizit gesperrt | implicitly denied |
| aktiv / Backup | active / backup |
| Kontext (VDOM/VRF) | Context |
| nicht lesbar / wieder lesbar | not readable / readable again |
| Verlauf (des Routings) | History (routing history) |
| Ausgangsstand | Baseline |
| hinzugefügt / entfernt / geändert | added / removed / changed |
| Vermerk, Obergrenze (je Lauf) | Note, limit (per run) |
| Anbindung (nach aussen) | Exit |
| erkannte Anbindung | Detected exit |
| ausblenden / einblenden | hide / show |
| parallele Firewalls, Verbund | Parallel firewalls, parallel group |
| Transfernetz | Transfer network |
| Topologie | Topology |
| Relevanz | Relevance |
| Gegenstelle | Peer |

## Zonen und Netzwerkdesign

| Deutsch | Englisch |
|---|---|
| Netzwerkdesign | Network design |
| Zone, logische Zone | Zone, logical zone |
| Zonenmatrix | Zone matrix |
| Zonenplan | Zone plan |
| Grundschema | Base schema |
| Vorlage | Template |
| Absicht (erlaubt/eingeschränkt/gesperrt) | Intent (allow/limited/deny) |
| Übergang | Transition |
| Ebene | Layer |
| Vorschläge | Suggestions |
| Version (des Plans) | Version |
| Anordnung | Arrangement |
| Kommunikation | Communication |

## Applikationen

| Deutsch | Englisch |
|---|---|
| Applikation | Application |
| Applikations-Doku | Application documentation |
| Verbindungen Ende zu Ende | End-to-end connections |
| Netzwerkplan | Network diagram |
| Kategorie | Category |
| Kategorien (Reiter im Netzwerkdesign, 0.102.0) | Categories |
| Ausschlüsse („ohne“ Netz einer Zone) | Exclusions (“without”) |
| Zugriff Management (Kategorie 11) | Management access |
| Kategorien der Applikations-Doku (Namensprofile, 0.104.0) | Categories of the application documentation |
| Auf Vorgaben zurücksetzen | Reset to defaults |
| Zonentyp (Zone des Grundschemas, 0.105.0) | Zone type |
| Source-Zone / Dest-Zone (Richtung einer Kategorie, 0.106.0) | Source zone / Destination zone |
| Sonderfall: innerhalb der Applikation / des Security Tenants | special case: within the application / the security tenant |
| Geräte (einer Zone: Firewall/VDOM, ACI-Tenant/-VRF, Anbindung) | Devices |
| Netz nicht über den Übergang zur Zone (Befund) | Network not via the transition to the zone |
| Zweck (Dev/Test/Prod) | Purpose |
| Kritikalität | Criticality |
| Verantwortlich | Owner |
| Abhängigkeiten | Dependencies |
| direkt zugeordnet / ausgeschlossen | assigned directly / excluded |
| spezifisch / unspezifisch | specific / unspecific |
| Bezug | Reference |
| Abweichung | Deviation |

## Verkabelung

| Deutsch | Englisch |
|---|---|
| Verkabelung | Cabling |
| Verbindung (Kabel) | Link |
| einseitig | one-sided |
| Endpunkte | Endpoints |
| Wo hängt …? | Where is … connected? |
| Ausfall | Failure |
| Patchungen | Patching |
| Uplink / Anschluss | Uplink / access port |
| IPv6-Nachbarn (NDP) | IPv6 neighbors (NDP) |
| Link-local (IPv6) | link-local |

## Texte vom Server (seit 0.87.0)

| Deutsch | Englisch |
|---|---|
| Startpunkt, weiter über | start point, onward via |
| Ziel erreicht, Weg endet | Destination reached, Path ends |
| implizit gesperrt | implicitly denied |
| nicht prüfbar | not verifiable |
| Rückrichtung, beide Richtungen | reverse direction, both directions |
| nur Antworten | replies only |
| ohne Zone, ohne EPG | no zone, without EPG |
| Art (der Zone), Kurzname | Kind, Short name |
| Knoten | Node |
| Stand (Datum) | As of |
| Anmeldung | Sign-in |
| Lebenszeichen | sign of life |
| Kategorie: Zugriff aus dem Internet | Internet access |
| Schutzmechanismen | Protection |

## Allgemeine Knöpfe und Meldungen

| Deutsch | Englisch |
|---|---|
| Speichern, Abbrechen, Löschen | Save, Cancel, Delete |
| Bearbeiten, Neu, Hinzufügen, Entfernen | Edit, New, Add, Remove |
| Schliessen, Öffnen, Zurück, Weiter | Close, Open, Back, Next |
| Aktualisieren, Neu laden | Refresh, Reload |
| Suchen, Filter, alle, keine | Search, Filter, all, none |
| Export, Drucken, Herunterladen | Export, Print, Download |
| wird geladen… | Loading… |
| keine Einträge | No entries |
| rückgängig | undo |
| Modul (Kern, Einstellungen, Worker, Gateway, Analyse, Wegsucher, Findings, Doku, Design, Schreibpfad) | Module (core, settings, worker, gateway, analysis, path finder, findings, documentation, design, write path) |
| Regel nie gebraucht | Rule never needed |
