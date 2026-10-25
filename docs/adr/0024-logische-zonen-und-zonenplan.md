# ADR 0024 — Logische Zonen, Zonenmatrix und Zonenplan

## Status

Entwurf (2026-10-24), umgesetzt in 0.51.0 (Zonen, Matrix, Plan), 0.52.0 (Export, Versionen),
0.53.0 (Kunden auf Zonen, ADR 0018 Schritt 4), 0.54.0 (Zonen erkennen und vorschlagen), 0.55.0
(Grundschema der Zonen aus draw.io), 0.56.0 (Zonenmodell nach Ebenen, Plan je Kunde) und 0.57.0
(Zonen je Hop in der Weg-Suche, Absicht je Dienst).
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
    - Die Erkennung arbeitet ohne Wörterbücher; das Grundschema (Nr. 11) gibt Namen und Art vor,
      wenn es gepflegt ist.
11. **Grundschema der Zonen (0.55.0, `store/zone_schema.py`):** Entscheid des Anwenders
    (2026-10-25): ein Grundschema der möglichen Zonen als Grundlage, in der Namenskonvention
    anhand einer Zeichnung (draw.io) definiert.
    - **Ort:** im Namensprofil (Namensprofile → Grundschema der Zonen), gespeichert als
      Wörterbuch-Arten `zone_schema` (Zone), `zone_schema_link` (Übergang) und `zone_schema_meta`
      (die Zeichnung) – ohne Migration, kopierbar wie jede Art. Gilt für alle Mandanten des
      Profils (eigenes bzw. „default“). Ohne Funktionszwang: ohne Schema arbeiten Zonen und
      Vorschläge wie in 0.54.0.
    - **Zeichnung lesen:** `.drawio` unkomprimiert oder komprimiert (Base64 + Deflate), erste
      Seite bzw. die Seite mit „Zone“/„Schema“ im Namen. Jede Form mit Beschriftung = Zone (erste
      Zeile = Name, weitere = Beschreibung); Text, Beschriftungen von Verbindungen, Knoten aus dem
      Zonenplan-Export (`automato_node`) und `automato_ignore` nicht; eine Form mit Zonen darin =
      Gruppe. Eigenschaften (draw.io „Daten bearbeiten“): `automato_kind` (deutsch oder englisch),
      `automato_code`, `automato_match` (Muster mit `*`/`?`), `automato_order`; Farbe = Füllfarbe;
      ohne Art aus dem Namen. Jede Verbindung zwischen zwei Zonen = Übergang; Absicht aus
      `automato_intent`, der Beschriftung (erlaubt/eingeschränkt/gesperrt, auch englisch),
      gestrichelt = eingeschränkt, rote Linie = gesperrt, sonst erlaubt; Richtung nach Pfeil, ohne
      bzw. mit zwei Pfeilen beide Richtungen; doppelte Übergänge: der strengere gilt. Hinweise
      (doppelte Namen, lose Verbindungen, unbekannte Art, weitere Seiten) werden angezeigt.
    - **Hochladen** (Admin) mit Vorschau: Zonen, Übergänge, Hinweise und der Unterschied zum
      gespeicherten Schema; erst „speichern“ ersetzt es. Die Zeichnung bleibt erhalten
      (herunterladen, in draw.io weiterbearbeiten). Vorlage mit Beispiel-Zonen bzw. aus den Zonen
      und Absichten eines Mandanten als Ausgangspunkt; der Export des Zonenplans (Nr. 8) lässt sich
      ebenfalls lesen (nur die Zonen).
    - **Vorschläge nach dem Schema:** passt ein Name (bzw. bei Interfaces auch Alias) auf eine
      Zone des Schemas – gleicher normalisierter Name oder Kurzname, sonst das erste passende
      Muster –, gelten deren Name, Art und Farbe; Interfaces werden auch allein über die Muster
      erkannt. Transfer und Management gehören zur Schema-Zone dieser Art. Vorschläge ohne
      Treffer bleiben (markiert „nicht im Schema“).
    - **Abgleich mit den Zonen** (Reiter Vorschläge): Schema-Zonen ohne Zone und ohne Vorschlag
      (anlegen über den Editor), Zonen ausserhalb des Schemas, und die **Absichten** des Schemas
      zwischen den bestehenden Zonen, die in der Zonenmatrix fehlen bzw. abweichen. Fehlende
      übernimmt der Operator auf einmal, abweichende nur einzeln („ersetzen“); Notiz
      „Grundschema: A → B“, Audit `zone_policy.set` mit `via`.
    - **Sicherheit:** höchstens 2 MB, keine DTD/Entitäten, entpackt höchstens 10 MB, nur Daten der
      Plattform (SECURITY.md Nr. 10).

12. **Zonenmodell nach Ebenen (0.56.0, `store/zone_struct.py`):** Korrekturen des Anwenders nach
    dem Test der Zonen (2026-10-25). Die Zonen folgen dem Aufbau der Umgebung: die ACI-Tenants
    sind das **Datacenter**, die L3Outs die Übergänge vom DC zum **Edge** (Internet,
    Office-Anbindung, VPN, Management …), zwischen den Firewalls **Edge-Transferzonen**, danach
    **Core**, **Office Distribution/Access**, **Branch Distribution/Access** und zuletzt
    **Public/Internet**. Zwischen den Zonen liegen **Übergänge** – im ACI die L3Outs, bei den
    Firewalls die Interfaces, meist mit Border-Routern.
    - **Knoten einer Zone:** Selektoren VDOM (`fw_vdom`, je Firewall), ACI-Tenant (alle VRFs der
      EPGs des Tenants) und ACI-VRF ordnen ganze Knoten (Firewall bzw. VDOM, ACI-VRF wie im
      Topologie-Modell) einer Zone zu; es gilt die erste Zone in der Reihenfolge, mehrere Zonen
      je Knoten sind ein Hinweis im Plan. Die übrigen Selektoren nennen Netze wie bisher.
      Zuordnen auch direkt (`POST /zones/{id}/nodes`, Operator, Audit `zone.updated` mit `via`).
    - **Netze an den Knoten** (Interfaces bzw. L3Out-SVIs, verbunden wie im Topologie-Modell): nur
      Knoten einer Zone = Netz der Zone (Transfernetz zweier Firewalls derselben Zone ist
      zoneninterner Zugriff, kein Übergang); Knoten verschiedener Zonen = **Übergang**, das Netz
      gehört keiner Zone; ein Netz ausdrücklich in einer Zone mit Knoten anderer Zonen daran = deren
      Übergang zu ihr – ein LAN direkt am Interface (kein Router, keine Knoten der Zone) bleibt Netz
      seiner Zone, ein Transfernetz (Router dahinter, Knoten der Zone, Anbindung) nicht; Router ohne
      gelesenes Gerät = Übergang nach „ausserhalb“; Knoten ohne Zone an einem Netz einer Zone =
      Kontakt (gestrichelt), kein Übergang. Netze hinter einer Anbindung (0.0.0.0/0) ordnen kein
      Netz an den Knoten zu; dort zählen Next-Hop bzw. Interface der Anbindung.
    - **Zonen ohne eigene Netze** sind normal (z. B. die Internet-Firewall: ein Bein zur L3Out des
      DC, eines in Public; sie deklariert die Zone Edge Internet). Beispiel: zwei Firewalls der
      Zone „EdgeA“ mit einem Transfernetz in der Zone, der VPN-Abgang der einen ist der Übergang
      „EdgeA-Branch“, die andere hat den Übergang „DC-EdgeA“.
    - **Namen der Übergänge:** Kurz- bzw. Name beider Zonen, nach Ebene geordnet (DC vor Edge vor
      Transfer … vor Public), von beiden Seiten gleich („DC-EINET“, „EINET-PUB“).
    - **Vorlage** (eingebaut, ohne Grundschema): DC, Edge Internet/Office/VPN/Management, Edge
      Transfer, Core, Office Distribution/Access, Branch Distribution/Access, Public – mit Kurzname,
      Muster und **fester Farbe**, Übergänge mit Absicht. Neue Arten `dc`, `edge`, `core`,
      `distribution`, `access` (ohne Migration, Text).
    - **Farben:** aus dem Grundschema (gleiche Zone dort), ohne Schema aus der Vorlage, sonst die
      der Zone, sonst die der Art – dieselbe Zone ist überall gleich gefärbt; im Editor fest, wenn
      Schema bzw. Vorlage sie vorgeben.
    - **Vorschläge:** ACI-Tenants = DC (je Kunde eine Zone „DC <Kunde>“, Kunde über seinen
      Selektor ACI-Tenant), Firewalls in die Zone, deren Name/Muster passt, Firewalls mit einem Bein
      an einer Internet-Anbindung in Edge Internet, Anbindungen in Public, Firewall-Zonen und
      Interfaces nach den Mustern (auch der Vorlage), IPAM-Merkmal. Bestehende Zonen derselben
      Schema-Zone werden ergänzt. Knoten ohne Treffer stehen zum Zuordnen da. Transfernetze werden
      nicht mehr als eigene Zone vorgeschlagen.
    - **Zonenplan je Kunde** (`GET /zones/plan?customer_id=&all=`): ohne Kunde die Übersicht –
      Zonen der Kunden je Kunde als **eine Zone**; mit Kunde dessen Zonen, die geteilten Zonen
      dahinter und die **genutzten Zugänge** zu anderen Kunden (Übergang oder Regeln zwischen den
      Zonen) je als eine Zone; `all` alle Zonen einzeln. Seit 0.57.1 (Korrektur des Anwenders:
      „lediglich die Firewall und ACI, welche der Kunde braucht“) zeigen die geteilten Zonen im
      Plan eines Kunden nur die **benötigten Knoten** (`zone_struct.needed`): die Knoten an den
      Netzen seiner Zonen und die auf den kürzesten Wegen über gemeinsame Netze zu den Knoten der
      Zonen seiner Regeln (Zone ohne Knoten: die Knoten an ihren Netzen; gleich lange Wege alle,
      Partner eines Verbunds dazu); geteilte Zonen ohne benötigte Knoten und ohne Übergang fallen
      weg, Übergänge nur mit Netzen, an denen je Seite ein benötigter Knoten hängt
      (`nodes_total` nennt die Knoten der Zone insgesamt). Die Oberfläche folgt dem Kunden-Filter
      der Kopfzeile. Kunden-Benutzer sehen ihren Plan, andere
      Kunden als „anderer Kunde“, geteilte Zonen ohne Knoten und Netze (nur mit Übergang im
      Ausschnitt, ohne `nodes_total`). Zeichnung in Spalten nach
      Ebene, Zonen mit ihren Knoten, Übergänge als Linien mit Namen; Export (draw.io, SVG, PDF)
      in der gewählten Ansicht. Versionen speichern alle Zonen einzeln (Format 2); ältere
      Versionen behalten ihre Zeichnung.

13. **Zonen je Hop und Absicht je Dienst (0.57.0, `store/zone_path.py`, `store/zone_services.py`,
    Migration 0052):** Reihenfolge des Anwenders „Zonen/Kunden fertig“ (2026-10-25).
    - **Absicht je Dienst:** „eingeschränkt“ nennt die erlaubten Dienste (`zone_policy.services`):
      `tcp/443`, `udp/53`, `tcp/8000-8080`, `tcp` (alle Ports), `icmp`, `any` bzw. den Namen eines
      Service-Objekts oder ACI-Filters (aufgelöst wie in der Weg-Suche). Unbekannte Angaben lehnt die
      API ab (422). Eine erlaubende Regel zwischen den Zonen, deren Dienste nicht ganz abgedeckt
      sind (`ALL` bzw. negierte Dienste nur durch `any`), verstösst gegen die Absicht – in der
      Matrix gezählt, in den Regeln der Zelle mit den Diensten ausserhalb, als Befund
      `zone_policy_violation` („öffnet tcp/22 – erlaubt sind nur tcp/443“). Regeln ohne gelesene
      Dienste werden nicht beurteilt; ohne Dienste ist „eingeschränkt“ nur Absicht (kein Befund).
      Im Grundschema trägt ein Übergang die Dienste in `automato_services` (setzt „eingeschränkt“).
    - **Zonen in der Weg-Suche** (`GET /routing/trace`): Zone der Quelle und des Ziels (längstes
      Präfix), je Hop die Zone des Knotens (`hop.zone`), je Weg die Kette Quelle → Zonen der Hops →
      Ziel mit den Übergängen dazwischen (`path.zones`, „LAN-EINET“, „DMZ-EINET“; Knoten ohne Zone
      als „ohne Zone“). Dazu die **Absicht** zwischen Quell- und Zielzone mit Urteil gegenüber dem
      aktiven Weg (`zones.intents`): gesperrt und der Weg erlaubt → gegen die Absicht;
      eingeschränkt mit Diensten → der gesuchte Dienst ist erlaubt bzw. nicht (erlaubt der Weg ihn,
      gegen die Absicht; ohne Dienst nur ein Hinweis); erlaubt und der Weg sperrt → Hinweis.
      Ohne Zonen bleibt der Weg wie bisher. Nicht für Kunden-Benutzer (Weg-Suche gesperrt).

## Folgen

- Die Firewall-Zonen (`zone`, gelesen) bleiben, was sie sind; eine logische Zone kann sie als
  Selektor nutzen.
- Beim Löschen eines Kunden bleiben seine Zonen und gehören keinem mehr; beim Löschen einer
  Zone fällt ihre Absicht weg; beim Löschen eines Quellsystems seine Selektoren.
- Zonen sind nicht funktionsrelevant für den Rest: ohne Zonen arbeitet die Plattform wie
  bisher (Matrix und Plan sind leer, keine Befunde).
- Offen: Zonen je Kunde im Grundschema (Vorlage „je Kunde“), Befund „Zone ausserhalb des
  Schemas“, Export nach Confluence (eigene Freigabe), draw.io-Datei zurück einlesen (Korrekturen
  im Designer), Relevanz nach Pfad für die Matrix (ADR 0019), Übergänge über Routen ohne
  gemeinsames Netz (Zonen nur hinter einem Router) im Plan, Absicht je Dienst und Richtung im
  Zonenplan, Zonen in den Verbindungen der Applikations-Doku.
