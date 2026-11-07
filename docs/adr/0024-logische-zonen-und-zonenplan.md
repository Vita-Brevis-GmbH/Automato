# ADR 0024 — Logische Zonen, Zonenmatrix und Zonenplan

## Status

Entwurf (2026-10-24), umgesetzt in 0.51.0 (Zonen, Matrix, Plan), 0.52.0 (Export, Versionen),
0.53.0 (Kunden auf Zonen, ADR 0018 Schritt 4), 0.54.0 (Zonen erkennen und vorschlagen), 0.55.0
(Grundschema der Zonen aus draw.io), 0.56.0 (Zonenmodell nach Ebenen, Plan je Kunde), 0.57.0
(Zonen je Hop in der Weg-Suche, Absicht je Dienst), 0.61.0 (Plan übersichtlich, Nr. 14) und
0.66.0 (Kommunikation im Plan, Vorschläge beim Erfassen, Nr. 15).
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
14. **Zonenplan übersichtlich (0.61.0, `store/plan_view.py`, Migration 0053):** Wunsch des
    Anwenders (2026-10-25): bei mehr als drei Tenants und mehreren Zonen ist der Plan
    unübersichtlich – Filter, Ebenen zum Ausblenden, Zonen von Hand anordnen.
    - **Filter** auf dem Server, damit Oberfläche und Export (draw.io, SVG, PDF) dasselbe zeigen:
      Suche (`q`: Name, Kurzname, Kunde, zusammengefasste Zonen, Knoten, Beispielnetze; Name eines
      Übergangs bzw. eines seiner Netze → beide Seiten) bzw. Fokus auf gewählte Zonen (`focus`),
      je mit den Nachbarn bis `depth` Übergänge weit (0–3, Vorgabe 1). Die übrigen Zonen und
      Übergänge fallen weg, das Layout rückt zusammen; der Export nennt den Filter im Kopf („Suche
      „DMZ“ + Nachbarn (2 von 5 Zonen)“). Kunden-Benutzer suchen nur über Zonen- und Kundennamen
      (kein Raten von Gerätenamen, ADR 0018).
    - **Ebenen** ausblenden (`hide_layer`): die Spalten DC (mit intern/geteilt), Kunden, Edge (mit
      DMZ/Management), Transfer, Core, Distribution, Access, Public, Knoten ohne Zone, ausserhalb;
      **Elemente** (`hide`): Knoten in den Zonen, Namen der Übergänge (bleiben als Eigenschaft im
      draw.io), Kontakte zu Knoten ohne Zone, Zonen ohne Knoten und Übergang. Gewählte bzw.
      gefundene Zonen bleiben sichtbar. Die Auswahl merkt sich der Browser je Betrachter.
    - **Anordnung** (`zone_plan_arrangement` je Mandant und Ansicht `overview`, `all`,
      `customer:<id>`): Zonen lassen sich in der Zeichnung ziehen; ein Operator speichert damit
      die Position aller Zonen der Ansicht (`PUT /zones/plan/arrangement`, zusammengeführt;
      `DELETE` setzt zurück; Audit `zone_plan.arranged`/`zone_plan.arrangement_reset`). Das Layout
      setzt gespeicherte Zonen an ihren Platz, neue Zonen in ihrer Spalte darunter, und rückt den
      Plan an den Rand (`offset`); Linien laufen nach der Lage (nebeneinander von rechts nach
      links, übereinander rechts herum – `plan_export.route`, in der Oberfläche gleich beim
      Ziehen). Filter behalten die Anordnung. Export und neue Versionen folgen ihr (die Version
      speichert die Positionen der Ansicht „alle Zonen einzeln“, ohne Einfluss auf den
      Fingerabdruck). Ohne Recht zum Speichern ordnet das Ziehen nur die eigene Ansicht.
      Doppelklick auf eine Zone setzt den Fokus. Kunden-Benutzer lesen gefiltert, speichern nicht.
      Seit 0.65.1 laufen die Linien weich: sie verlassen die Zone an der Seite zur anderen Zone
      (nebeneinander rechts/links, übereinander unten/oben, mit einer Zone dazwischen rechts herum)
      als Bezier-Kurve; die Anschlüsse je Seite sind nach der Lage der Gegenseite geordnet.
15. **Kommunikation im Plan und Vorschläge beim Erfassen (0.66.0):** Korrekturen des Anwenders
    nach dem Test (2026-10-26): im Plan eines Tenants fehlen die Tenants, mit denen er
    kommuniziert; Netze und Firewall-Interfaces einer Zone von Hand zuzuweisen ist mühsam.
    - **Kommunikation** (`flows`, Schalter „Kommunikation (Regeln)“, Ansicht und Export): je Paar
      der Zonen der Ansicht die erlaubenden Regeln zwischen ihnen (aus der Zuordnung der Matrix,
      ohne `any` und Adressen ohne Zone) als eigene, blau gepunktete Linie mit der Zahl der Regeln
      und je Richtung im Titel – auch zwischen Zonen ohne Übergang. Mit Fokus bzw. Suche kommen
      die Zonen dazu, mit denen die Treffer kommunizieren. Im Plan eines Kunden nur seine
      Kommunikation; die Zonen anderer Kunden, mit denen er Regeln hat, stehen einzeln da
      („Partner“, ohne ihre Knoten) statt je Kunde zusammengefasst. Kunden-Benutzer sehen den
      Partner weiter als „anderer Kunde“.
    - **Vorschläge beim Erfassen** (`store/zone_assist.py`, `POST /zones/assist`, nur lesend):
      der Zonen-Editor fragt mit Name, Kurzname und Art; vorgeschlagen werden FortiGate-Zonen
      (je Firewall), Interfaces (Name, Beschreibung, Rolle dmz/wan zur Art; nicht die Interfaces
      einer passenden FortiGate-Zone), VDOMs, IPAM-Netze (Name, Beschreibung, VLAN-Name),
      -Merkmale, -Tags, -Tenant und -VRF, ACI-Tenant/VRF/AP/BD/EPG und Anbindungen, deren Name
      passt: gleicher normalisierter Name bzw. Kurzname, alle Wörter des Namens, Erkennungsmuster
      der passenden Zone des Grundschemas bzw. der Vorlage. Je Vorschlag die Netze, ob die
      Selektoren der Zone sie schon erfassen und welche schon in einer anderen Zone liegen;
      gewählte Selektoren fallen weg. Übernehmen fügt den Selektor im Editor an (gespeichert
      wird mit der Zone).
16. **Grundschema in der Oberfläche bearbeiten (0.70.0):** Wunsch des Anwenders – nach dem
    ersten Hochladen Namen, Absicht usw. auch im Web-UI anpassen, ohne die Zeichnung neu zu
    zeichnen. `PATCH /naming-profiles/{id}/zone-schema` (Admin) ersetzt Zonen und Übergänge des
    Profils: je Zone Name (Umbenennen mit `previous`, die Übergänge ziehen mit), Art, Kurzname,
    Farbe (Hex), Erkennungsmuster, Gruppe, Beschreibung, Reihenfolge (= Liste); je Übergang
    Absicht, Dienste (nur bei „eingeschränkt“), Notiz. Geprüft wie beim Hochladen (Name doppelt,
    Zone fehlt, gleiche Zone, unbekannte Art/Absicht, Farbe → 422); die Lage in der Zeichnung
    bleibt, geänderte Art bzw. Absicht gilt als „von Hand“. Die hochgeladene Zeichnung bleibt
    gespeichert, „Zeichnung herunterladen“ erzeugt danach die Zeichnung aus dem bearbeiteten
    Stand (`edited_at`) – ein neues Hochladen beginnt wieder mit der Zeichnung. Audit
    `zone_schema.edited` mit neuen, entfernten und geänderten Zonen. Zonen und Absichten im
    Netzwerkdesign bleiben unverändert; die Vorschläge folgen dem neuen Schema.
17. **Zonen komplett, Teil 1 (0.78.0):** ROADMAP Schritt 5, drei der offenen Punkte.
    - **Zonen in den Verbindungen** der Applikations-Doku (ADR 0021): je Verbindung Zone von
      Quelle und Ziel, Kette der Zonen auf dem ersten Weg mit Übergängen und die Absicht mit
      Urteil wie in der Weg-Suche (Nr. 13); Zonen, Zonenmodell und Absichten werden je
      Applikation einmal geladen (`zone_path.ZoneContext`). Eine Kette nur aus „ohne Zone“
      entfällt. Die Befunde „Abweichung Regel ↔ Weg“ brauchen die Zonen nicht.
    - **Absicht je Richtung im Plan:** je Linie `intent_ab` (von a nach b) und `intent_ba` mit
      Absicht, Diensten, Zahl der Zonenpaare und `mixed`; gezeichnet als Pfeilspitze am Ziel in
      der Farbe der Absicht (wie im Grundschema: grün, orange, rot). In einer zusammengefassten
      Gruppe (Kunde in der Übersicht, „anderer Kunde“) gilt die strengste Absicht, `mixed` sagt,
      dass sie verschieden sind; Dienste = alle der eingeschränkten. Zonenpaare mit Absicht ohne
      Übergang (z. B. LAN → DMZ über die Firewall-Zone dazwischen) bekommen eine eigene,
      farbig gestrichelte Linie (`intents`), die nichts an der Auswahl der Zonen ändert. Im Plan
      eines Kunden nur Absichten mit einer eigenen Zone; Kunden-Benutzer ohne Notiz (sie kann
      andere Kunden nennen). Element „Absichten“ blendet beides aus. Export: SVG/PDF mit den
      Pfeilen und der Tabelle „Absicht je Richtung“, draw.io mit Pfeilen an den Kanten
      (`startArrow`/`endArrow`, Eigenschaft `automato_intents`; als Grundschema wieder
      eingelesen bleiben die Linien des Plans weiterhin keine Absichten), Word mit der Tabelle.
    - **Befund „Zone ausserhalb des Schemas“** (`zone_outside_schema`, Hinweis, Gegenstand
      `zone`): eine logische Zone, die zu keiner Zone des Grundschemas des Namensprofils passt
      (`Schema.match`: Name, Kurzname, Muster). Neu berechnet mit den übrigen Zonen-Befunden
      und nach Hochladen, Bearbeiten und Löschen des Grundschemas für alle Mandanten des
      Profils. Ohne Grundschema kein Befund.
18. **Zonen komplett, Teil 2 (0.81.0):** ROADMAP Schritt 5, zwei weitere offene Punkte.
    - **Relevanz nach dem Weg in der Zonenmatrix** (ADR 0019 Punkt 6, wie in der Doku): je
      Regel, Seite und Zone die erste Adresse der Zone im Bereich der Regel
      (`ZoneIndex.first_of`). Eine FortiGate-Policy erreicht die Zone nur, wenn das Routing
      ihres VDOMs die Adresse über Ein- bzw. Ausgang der Policy führt
      (`relevance.Relevance.check`); ein Contract nur, wenn er die Adresse erfassen kann
      (externe EPG, Scope – `relevance._aci_for_ip`, höchstens 2000 Adressen je Berechnung).
      Unbekannt (Routing nicht gelesen, Policy-Route, SD-WAN) bleibt drin. Ausgeschlossene
      Paare zählen in der Zelle als „nicht möglich“ (`impossible`), nicht als erlaubend bzw.
      sperrend und nicht als Verstoss gegen die Absicht – auch nicht im Befund
      `zone_policy_violation`, im Zonenplan (Kommunikation) und in der Sicht der Kunden. Die
      Zelle zeigt die Regeln auf Wunsch mit Begründung (`GET /zones/matrix/rules?impossible=true`);
      `summary.off_path` = Regeln, deren Zonen das Routing alle ausschliesst.
    - **Zone nur hinter einem Router:** liegt ein Netz einer Zone an keinem gelesenen Knoten,
      routet aber ein Knoten es (nicht über 0.0.0.0/0) über einen Router ohne gelesenes Gerät,
      ist das Netz dieses Routers der Übergang zur Zone – wie bei einer Anbindung
      (`zone_struct._routed`, höchstens 200 Netze je Zone). Der Übergang nennt die Routen
      (`networks[].routes`: Zone, Präfixe). Ohne solche Route bleibt es beim Übergang nach
      „ausserhalb“.

19. **Zonen komplett, Teil 3 (0.82.0): Zonen je Kunde im Grundschema.** Eine Zone des
    Grundschemas mit `automato_customer` = „je Kunde“ (Editor: „je Kunde“, `per_customer`) ist
    eine **Vorlage**: die Vorschläge (`zone_detect._per_customer`) verschieben Treffer, die ganz
    in den Netzen (`customers.nets_of` der Kunden-Selektoren) bzw. VDOMs (`fw_vdom`) genau eines
    Kunden liegen, in einen eigenen Vorschlag „<Zone> <Kunde>“ mit Kurzname
    „<Kurzname>-<KÜRZEL>“ und dem Kunden; die übrigen bleiben in der Zone selbst (ohne Treffer
    entfällt sie). Zonen eines Kunden passen auf die Vorlage, wenn Name bzw. Kürzel des Kunden
    wegfallen (`Schema.of_customer_zone`) – für „Zone ausserhalb des Schemas“, die Karte
    Grundschema und die Absichten. Absichten einer Vorlage gelten nur innerhalb eines Kunden
    (Zonen verschiedener Kunden bekommen keine Absicht aus dem Schema). Die Zeichnung trägt
    `automato_customer` hin und zurück.

20. **Zonen komplett, Teil 4 (0.83.0): Zonenplan aus draw.io zurück einlesen** (Korrekturen im
    Designer, `store/plan_import.py`). Der Export trägt am Titel die Ansicht und die
    Verschiebung (`automato_view`, `automato_offset`), jede Zone ihre ID in der Form
    (`zone-<id>` bzw. `zone-customer-<id>`). Gelesen wird nur Eindeutiges, übernommen erst nach
    der Vorschau und je Teil auf Wunsch (Operator, `POST /zones/plan/import[/preview]`):
    Lage = Anordnung der Ansicht (`plan_view.save`); erste Zeile der Beschriftung = neuer Name,
    ausser die Zone wurde seit dem Export in Automato umbenannt (dann gilt Automato) bzw. der
    Name ist vergeben; neue Pfeile zwischen Zonen bzw. Linien mit `automato_intent` = Absicht
    (Auswertung wie im Grundschema, `zone_schema.edge_intent`/`edge_pairs`; die Linien des
    Exports selbst nicht); neue Formen ohne `automato_*` = Zonen ohne Selektoren (nur auf
    Wunsch). Gelöschte Formen löschen nichts. Sicherheit wie beim Grundschema (2 MB, keine
    DTD/Entitäten); nur Daten der Plattform, kein Schreibweg zu Quellsystemen.

## Folgen

- Die Firewall-Zonen (`zone`, gelesen) bleiben, was sie sind; eine logische Zone kann sie als
  Selektor nutzen.
- Beim Löschen eines Kunden bleiben seine Zonen und gehören keinem mehr; beim Löschen einer
  Zone fällt ihre Absicht weg; beim Löschen eines Quellsystems seine Selektoren.
- Zonen sind nicht funktionsrelevant für den Rest: ohne Zonen arbeitet die Plattform wie
  bisher (Matrix und Plan sind leer, keine Befunde).
- Export für Confluence seit 0.71.0 als Word + draw.io zum Import von Hand (ADR 0026, kein
  Schreibweg).
- ROADMAP Schritt 5 ist mit 0.83.0 abgeschlossen (Nr. 20: draw.io-Datei des Zonenplans zurück
  einlesen). Erledigt in 0.82.0 (Nr. 19): Zonen je Kunde im Grundschema; in
  0.78.0 (Nr. 17): Befund „Zone ausserhalb des Schemas“, Absicht je Dienst und Richtung im
  Zonenplan, Zonen in den Verbindungen; in 0.81.0 (Nr. 18): Relevanz nach dem Weg in der
  Matrix, Übergänge zu Zonen nur hinter einem Router.
- Seit 0.101.0 trägt eine Zone optional den **Bereich der Kategorien der Applikations-Doku**
  (`appdoc_area`: Internet, Unternehmensnetzwerk, VPN, Remote Access, andere Security Tenants –
  ADR 0012 Ergänzung 0.101.0): Firewall-Regeln mit einer Gegenstelle in der Zone stehen in dieser
  Kategorie, auch über einen grossen Quell-Bereich; die Netze der Zone sind die Quell-Adressen.
  Seit 0.102.0 gepflegt im Reiter **Kategorien** des Netzwerkdesigns – mit Netzen und
  **Ausschlüssen** je Zone (Selektoren „Netz“ bzw. „ohne“ Netz, `PUT /zones/appdoc`) und dem
  Bereich DC Management (Kategorie 11); die Titel der Kategorien nennen ihre Zonen. Aus Netzen und
  Ausschlüssen je Zone lässt sich später ein BGP-Filter ableiten (nur Vorschlag/Export).
