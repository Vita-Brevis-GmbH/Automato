# ADR 0024 — Logische Zonen, Zonenmatrix und Zonenplan

## Status

Entwurf (2026-10-24), umgesetzt in 0.51.0 (Zonen, Matrix, Plan), 0.52.0 (Export, Versionen),
0.53.0 (Kunden auf Zonen, ADR 0018 Schritt 4) und 0.54.0 (Zonen erkennen und vorschlagen).
Baut auf dem Topologie-Modell (ADR 0019, 0.50.0) und den Selektoren der Kunden (ADR 0018) auf.

## Kontext

Die Doku zeigt Regeln je Gerät. Ob eine Regel zum Sicherheitskonzept passt, sieht man so
nicht: Dafür braucht es Zonen über die Geräte hinweg („Server“, „DMZ“, „Internet“,
„Kunde A“) und eine Matrix, was zwischen ihnen erlaubt sein soll. Die Firewall-Zonen der
FortiGate gelten nur je Gerät und VDOM. Das ACI kennt Tenants, VRFs und EPGs, aber keine Zonen.

Entscheide des Anwenders (2026-10-23):

- Logische Zonen werden **in Automato gepflegt**, nicht aus einem Quellsystem gelesen.
- Der Plan wird im Tool gezeigt und später exportiert (draw.io, SVG, PDF).
- Ein Kunde besitzt Zonen; geteilte Zonen gehören keinem.

## Entscheidung

1. **Zone mit Selektoren** (`logical_zone`, `logical_zone_selector`, Migration 0048):
   - Name, Kurzname, Art (intern, DMZ, extern, Management, geteilt, Transfer), Farbe,
     Reihenfolge, Beschreibung, optional ein Kunde (leer = geteilt bzw. Infrastruktur).
   - Selektoren wie beim Kunden: ACI-Tenant/VRF/AP/EPG/BD, VDOM/Zone/Interface (je
     Quellsystem), Netz, Kürzel, IPAM-Merkmal; dazu **Anbindung** (`exit`: die Netze hinter
     einer von Hand erfassten Anbindung, z. B. 0.0.0.0/0 für Internet). Ein- bzw.
     ausschliessend.
   - Welche Netze dazugehören, ist abgeleitet (`customers.nets_of`, gleiche Auswertung wie beim
     Kunden): neue EPGs oder Interfaces gehören ohne Pflege dazu. Nichts wird gespeichert,
     was sich aus den gelesenen Daten ergibt.
2. **Zugehörigkeit nach längstem Präfix:** Eine IP bzw. ein Netz gehört zur Zone mit dem
   längsten Netz, das es enthält. Eine DMZ 10.1.5.0/24 in der Server-Zone 10.1.0.0/16 geht vor.
   Ein Bereich, der mehrere Zonen berührt, zählt für jede davon; der Rest ohne Zone ist „ohne
   Zone“. Dasselbe Präfix in zwei Zonen ist ein Befund (`zone_overlap`).
   - Empfehlung: Internet = Anbindung „Internet“ (0.0.0.0/0) **ohne** die privaten Netze
     (ausschliessende Selektoren), sonst fallen interne Netze ohne Zone ins Internet.
3. **Zonenmatrix** (`GET /zones/matrix`):
   - Je Regel (FortiGate-Policy, ACI-Contract; ohne Zwillinge paralleler Firewalls) Quelle und
     Ziel aus dem Regelindex (`rule_ip_range`) auf die Zonen abgebildet. Je Paar Quell- und
     Zielzone die erlaubenden und sperrenden Regeln, dazu Zeile bzw. Spalte `any` und „ohne
     Zone“.
   - **Absicht** je Paar (`zone_policy`): erlaubt, eingeschränkt, gesperrt, mit Notiz. Gesetzt
     vom Operator (Audit `zone_policy.set`).
   - Eine erlaubende Regel zwischen Zonen mit Absicht „gesperrt“ ist ein Befund
     (`zone_policy_violation`, je Regel mit allen Paaren). `any` und „ohne Zone“ sind kein
     Verstoss – sie stehen als eigene Zeile da und sind ein eigenes Thema (zu weite Regeln).
   - Regeln ohne IP-Bereich (nur FQDN, ungelöste Gruppe) werden gezählt, aber keiner Zelle
     zugeordnet.
4. **Zonenplan** (`GET /zones/plan`): je Zone die gelesenen Knoten des Topologie-Modells
   (Firewall/VDOM, ACI-VRF), an denen sie hängt:
   - am Interface bzw. L3Out (das Netz des Interfaces liegt in der Zone, ohne Management);
   - im Fabric (EPG- bzw. BD-Subnetz der VRF);
   - über die Interfaces einer Anbindung (Selektor `exit`);
   - nur für Zonen ohne Netz an einem Knoten: über eine Route zu einem Router ohne gelesenes
     Gerät (z. B. Default-Route zum Provider, MPLS). Routen über einen anderen gelesenen Knoten
     zählen nicht – die Zone hängt dort.
   - Zonen am selben Knoten sind über ihn verbunden (Übergang). Dazu die gelesenen Netze ohne
     Zone (Interfaces, L3Outs, Fabric).
   - Im Tool als Zeichnung (Zonen links/rechts nach Art, Knoten in der Mitte) und Tabelle.
5. **Befunde sofort:** Nach dem Speichern einer Zone bzw. Absicht rechnet Automato nur die
   Zonen-Befunde neu (`reconcile.refresh_zone_findings`); nach jedem Lauf wie alle anderen.
6. **Rechte:** Lesen Viewer, ändern Operator; Audit `zone.created/updated/deleted`,
   `zone_policy.set`. **Kunden-Benutzer** (seit 0.53.0, ADR 0018 Schritt 4) lesen ihre Zonen,
   die Matrix mit ihren Zonen (andere Kunden als „anderer Kunde“) und ihren Plan samt Export –
   ohne Selektoren, Vorschau, Versionen und Netze ohne Zone.
7. **Nur lesend** gegenüber allen Quellsystemen; Zonen und Absichten sind Daten der Plattform.
8. **Export (0.52.0):** ein Layout auf dem Server (`store/plan_export.layout`) für Oberfläche
   und Dateien – Zonen links bzw. rechts nach Art, Knoten in der Mitte:
   - **draw.io** (unkomprimiertes mxGraph-XML): Zonen und Knoten als Formen mit Eigenschaften
     (`automato_zone`, `automato_kind`, Netze bzw. Interfaces), Verbindungen als Kanten – in
     diagrams.net weiter bearbeitbar;
   - **SVG** für Wiki und Präsentation;
   - **PDF**: Plan, Zonenmatrix (Zahlen, Absicht als Farbe, Verstösse umrandet, Notizen), Zonen an
     den Knoten und Netze ohne Zone.
   - Ohne zusätzliche Abhängigkeit: XML über die Standardbibliothek, das PDF schreibt ein
     kleiner eigener Schreiber mit den Standardschriften (Helvetica, WinAnsi). Ausgeliefert als
     Download; nach Confluence erst später (neuer Schreibweg, eigene Freigabe).
9. **Versionen (0.52.0, `zone_plan_version`, Migration 0049):**
   - Operator speichert den aktuellen Stand mit Bezeichnung und Notiz: Zonen mit Selektoren,
     Plan und Matrix samt Absichten, so wie sie gerade abgeleitet sind. Nummer je Umgebung
     fortlaufend.
   - **Fingerabdruck** über das Design (Zonen, Absichten, Zonen an den Knoten, Netze ohne Zone;
     ohne Regel-Zahlen): „aktueller Stand = Version n“ bzw. „geändert seit Version n“.
   - **Vergleich** einer Version mit dem aktuellen Stand oder einer anderen Version: Zonen neu,
     entfernt bzw. geändert (Art, Kunde, Farbe, Selektoren), Absichten, Zonen an den Knoten,
     Netze ohne Zone, dazu Zellen der Matrix mit anderen Zahlen.
   - Jede Version lässt sich ansehen (Zeichnung) und exportieren.
   - Löschen nur Admin: der gespeicherte Stand fällt weg, die Nummer bleibt vergeben
     (`deleted_at`). Die Aufbewahrungsfristen (ADR 0022) gelten nicht für Versionen.

10. **Vorschläge (0.54.0, `store/zone_detect.py`, `GET /zones/suggestions`):** Entscheid des
    Anwenders (2026-10-25): Zonen automatisch erkennen und vorschlagen. Automato schlägt Zonen aus
    dem Gelesenen vor; gespeichert wird erst beim Übernehmen (Operator) – als normale Zone mit
    Selektoren, also weiter abgeleitet. Quellen in dieser Reihenfolge:
    - **Transfernetze** zwischen gelesenen Knoten (wie die Kanten `transfer`/`member` im
      Topologie-Modell) → eine Zone „Transfer“ mit den Netzen;
    - **FortiGate-Zonen**: gleicher Name auf mehreren Firewalls bzw. VDOMs = eine Zone (Selektor
      Zone für alle Firewalls);
    - **Interfaces ohne Firewall-Zone** mit sprechendem Namen bzw. Alias (`dmz-web`, `wan1`), der
      FortiOS-Rolle dmz/wan (`inv_interface.role`, Migration 0051) bzw. Management-Interfaces
      (`dedicated-to management`, Management-Tabelle) → Selektor Interface je Firewall;
    - **IPAM-Merkmal** Zone bzw. Rolle (NetBox-Rolle des Prefixes als `role=…`, Custom Field bzw.
      DDI-Attribut `zone`, `security_zone`, `role` …);
    - **ACI-VRFs** (`Tenant:VRF` mit EPG-/BD-Subnetzen; `common` geteilt, `mgmt` Management);
    - **Anbindungen**: alle Internet-Anbindungen als eine Zone „Internet“ ohne die privaten Netze
      (RFC 1918, 100.64/10, fc00::/7), sonst je Anbindung.

    Gleicher normalisierter Name (Trenner, „zone“/„vlan“, bei Interfaces Ziffern am Ende
    ignoriert) = ein Vorschlag mit allen Selektoren; Transfer und Management je eine Zone. Die
    **Art** aus Wörtern im Namen (dmz; internet/wan/outside/untrust; mgmt/oob/ilo; transfer/transit;
    shared/common), der FortiOS-Rolle bzw. der Anbindung, sonst intern. Der **Kunde**, wenn alle
    Netze und EPGs in genau einem Kunden liegen. Gibt es schon eine Zone mit dem Namen bzw.
    Kurznamen (Transfer/Management: die erste Zone dieser Art), **ergänzt** der Vorschlag ihre
    Selektoren. Ein Netz, das genau so schon in einer Zone bzw. einem früheren Vorschlag liegt,
    schliesst der Vorschlag mit einem ausschliessenden Selektor aus – nach dem Übernehmen kein
    `zone_overlap`. Ohne eigenes Netz kein Vorschlag (ausser Anbindungen).
    - Übernehmen: einzeln über den Editor (vorbelegt, anpassbar) oder mehrere auf einmal
      (`POST /zones/suggestions/accept`, Audit `zone.created`/`zone.updated` mit `via`).
    - Ausblenden (`zone_suggestion_dismissal`, Migration 0051) und wieder vorschlagen (Operator,
      Audit `zone_suggestion.dismissed/undismissed`).
    - Dazu die gelesenen Netze ohne Zone und ohne Vorschlag („nicht erkannt“).
    - Die Erkennung arbeitet ohne Wörterbücher; ein Grundschema der Zonen in der Namenskonvention
      (draw.io, 0.55.0) soll Namen und Art später vorgeben.

## Folgen

- Die Firewall-Zonen (`zone`, gelesen) bleiben, was sie sind; eine logische Zone kann sie als
  Selektor nutzen.
- Beim Löschen eines Kunden bleiben seine Zonen und gehören keinem mehr; beim Löschen einer
  Zone fällt ihre Absicht weg; beim Löschen eines Quellsystems seine Selektoren.
- Zonen sind nicht funktionsrelevant für den Rest: ohne Zonen arbeitet die Plattform wie
  bisher (Matrix und Plan sind leer, keine Befunde).
- Offen: Grundschema der Zonen in der Namenskonvention aus einer draw.io-Zeichnung (0.55.0),
  Export nach Confluence (eigene Freigabe), draw.io-Datei zurück einlesen (Korrekturen im Designer), Zonen in der Weg-Suche (Zonen je Hop), Absicht je Dienst (eingeschränkt = nur
  bestimmte Dienste), Relevanz nach Pfad für die Matrix (ADR 0019).
