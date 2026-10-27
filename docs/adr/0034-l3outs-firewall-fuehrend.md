# ADR 0034 — L3Outs: Firewall führend

## Status

**Entwurf** (2026-10-27) – zur Abnahme durch den Anwender. Befunde des Anwenders aus dem Test vom
2026-10-27:

- „Jedes L3O hat eine Funktion, welches vorgegeben wird, welche Systeme man darüber lernen soll und
  welche mehr matchen (Best Match). … auf dem L3O_bit die Netzwerke 192.168.0.0/16 … und auf dem
  L3O_inet 0.0.0.0/0. Es sollte aber nicht auf das ACI geschaut werden (ist meist mehr offen
  routingtechnisch) sondern eher auf die Firewalls. Eventuell müsste man definieren, was welche
  Firewall genutzt wird und welche Wege entsprechend vorgesehen sind.“
- „Contracts über L3Os immer nur als Hilfs-Contract anschauen, der alleine kann keine Verbindung
  haben, die L3O-Contracts zum External EPG haben immer eine Firewall hintendran, welche den Traffic
  regelt. Daher dies nicht in der Liste separat auflisten.“
- „Die unspezifischen Zugriffe sortieren nach L3O wo sie reinkommen, damit ein besserer Eindruck
  entsteht.“

Ergänzt ADR 0019 (Punkt 6: ACI-Contracts über externe EPGs, 0.28.0), ADR 0012 (Ergänzung 0.78.2
„Richtung L3Out: Firewall führend“) und ADR 0021 (Verbindungen Ende zu Ende). Migration `0063`
(`l3out_firewall`).

## Kontext

- Welche externe EPG (und damit welche Contracts) für eine Adresse ausserhalb des Fabrics gilt,
  entschied bisher das ACI: das längste Subnetz mit Scope `import-security` je VRF, bei gleich
  langen (0.0.0.0/0 in mehreren L3Outs) das Routing der VRF (`aci_relevance`, 0.28.0). Diese
  Subnetze sind im Betrieb bewusst weit offen (L3O_bit 192.168.0.0/16, L3O_inet 0.0.0.0/0) – ein
  Netz, das die Firewall des Internet-L3Outs routet (z. B. eine DMZ 192.168.50.0/24), ordnete das
  ACI dem L3O_bit zu.
- Hinter jedem L3Out steht eine Firewall, die den Verkehr regelt. Die Plattform kennt die Firewall
  aber nur indirekt (Transfernetz im Topologie-Modell, BGP-Peer).
- In der Applikations-Doku standen die Contracts zu externen EPGs als eigene Regeln (Liste „Regeln
  & Contracts“, Export); in den Kategorien nur, wenn sie enger sind als die Firewall (0.78.2).
  Für den Anwender ist die Firewall-Regel die Verbindung, der Contract nur die nötige Öffnung im
  ACI.
- Unspezifische Zugriffe (any, Netz, vzAny) standen in der Reihenfolge des Regelwerks – ohne
  Bezug zum Weg ins Fabric.

## Entscheidung

### 1. Zuordnung L3Out → Firewall dahinter

- **Abgeleitet** (`store/l3out_firewall.Behind`) aus den gelesenen Daten wie im Topologie-Modell:
  eine SVI bzw. ein Interface des L3Outs im selben Netz wie ein Interface einer Firewall (auch mit
  abweichender Maske bis /16 bzw. /64, Management ausgenommen), ein BGP-Peer bzw. ein Next-Hop
  einer statischen Route des L3Outs = Interface-IP einer Firewall. Je L3Out also eine oder
  mehrere Firewalls (Quellsystem, VDOM, Interface zum L3Out mit Zone) bzw. keine.
- **Von Hand** (Operator, Routing → Reiter „L3Outs (ACI)“, Spalte „Firewall dahinter“):
  bestätigen (`confirmed`), eine andere Firewall bzw. ein anderes Interface wählen (`manual`),
  ausdrücklich keine (`none`), zurücksetzen (wieder abgeleitet). Tabelle `l3out_firewall`
  (Migration `0063`): APIC und DN des L3Outs (stabil – `topo_l3out` wird je Lauf ersetzt), je
  Firewall eine Zeile (Quellsystem, VDOM, Interface) bzw. eine Zeile ohne Firewall; Fremdschlüssel
  mit `ON DELETE CASCADE`, beim Löschen eines Quellsystems bzw. Mandanten mitgelöscht. Ohne Zeile
  gilt die Ableitung.
- API: `GET /routing/l3outs` je L3Out `behind` (Herkunft, Firewalls, abgeleitete, wer/wann),
  `GET /routing/l3outs/firewall-choices` (Firewalls bzw. VDOMs mit Interfaces),
  `PUT /routing/l3outs/firewall` (`confirm` | `none` | `firewalls`), `DELETE …/firewall`
  (operator). Audit `routing.l3out_firewall.set` (L3Out, Herkunft, Firewalls) bzw. `.removed`.
  Danach ziehen die Befunde der Applikationen und Zonen sowie die Zugehörigkeit der Kunden hinter
  der Antwort nach (`app_refresh.schedule_tenant`). Kunden-Benutzer erreichen das Routing nicht
  (Freigabeliste, fail-closed) – keine neuen Freigaben.
- Parallele Firewalls (Verbund, ADR 0020) gelten als dieselbe Firewall.

### 2. Firewall führend: über welchen L3Out kommt eine Gegenstelle

- `Behind.decide(APIC, VRF, IP)`: je L3Out der VRF mit Firewall die Route dieser Firewall zur IP
  (längstes Präfix in der VRF des Interfaces zum L3Out). Geht sie **nach aussen** (nicht über das
  Interface zu einem L3Out, nicht über einen Next-Hop im Fabric), ist der L3Out ein Kandidat mit
  der Präfixlänge; **es gewinnt die genaueste Route** (Best Match), gleich genaue = mehrere L3Outs.
  Leitet die Firewall die IP **zurück ins ACI**, kommt sie nicht über diesen L3Out. Ohne Firewall
  bzw. ohne Route bleibt der L3Out „unbekannt“.
- In `aci_relevance.Classifier` (Zuordnung einer IP zu einer externen EPG) nach den bisherigen
  Vorbedingungen (Subnetz der externen EPG erfasst die IP, Scope `import-security`, nicht im
  Fabric): ist der L3Out der externen EPG ein Gewinner, gilt sie (innerhalb der gewinnenden
  L3Outs weiter das längste Subnetz) – mit Begründung „über L3O_inet: fw-01 routet
  203.0.113.0/24 über wan1“; gewinnt ein anderer L3Out bzw. leitet die Firewall zurück, ist sie
  nicht möglich („… kommt über L3O_inet: … – nicht über L3O_bit“). **Rückfall auf das ACI**
  (längstes Subnetz, Routing der VRF) nur ohne Firewall bzw. ohne Aussage für diesen L3Out.
- Das wirkt überall, wo die Zuordnung gebraucht wird: Applikations-Doku (Systeme ausserhalb des
  Fabrics, Treffer, Relevanz der Contracts), Weg-Suche und Verbindungen Ende zu Ende (ACI-Hop
  `aci_check`), Regel-Liste nach IP (`relevance.for_ip`), Zonen nach dem Weg und Zugehörigkeit
  der Kunden.

### 3. Contracts zu externen EPGs: Hilfs-Contracts

- Ein **erlaubender** Contract, dessen Gegenstellen nur externe EPGs sind, deren L3Outs eine
  Firewall dahinter haben, ist ein **Hilfs-Contract** (`appdoc._mark_l3outs`): keine eigene Zeile
  in den Kategorien, nicht in „Regeln & Contracts“ der Doku (Ansicht, Markdown, Word, Druck),
  nicht in den Kennzahlen. Er steht an den erlaubenden Firewall-Regeln dieser Firewall in
  derselben Richtung (Interface bzw. Zone zum Fabric passt) in der neuen Spalte **„L3Out“**:
  „L3O_inet · Contract CT_inet“. Die Treffer behalten ihn (`helper`), die Doku listet sie unter
  `helpers` (L3Outs, Firewall-Regeln, Systeme), die Liste nennt ihre Zahl.
- **Enger als die Firewall** (Dienste einer dieser Firewall-Regeln öffnet er nicht): dann sperrt er
  real – „L3O_inet · Contract CT_inet – es fehlt tcp/8443“ an der Regel und weiter der Befund
  `app_contract_narrower` (je Contract und Applikation, jetzt mit genau den Firewall-Regeln, denen
  etwas fehlt).
- **Ohne Firewall dahinter** (L3Out gelesen, keine abgeleitet bzw. „keine“): der Contract regelt
  allein – er bleibt sichtbar (Kategorien, Regeln & Contracts), Spalte „L3O_vpn (ohne Firewall)“,
  Hinweis am Treffer („keine Firewall hinter L3O_vpn – der Contract regelt allein“). **L3Out nicht
  gelesen** (APIC ohne Routing): wie 0.78.2, mit Hinweis „Firewall hinter … nicht bekannt“.
- Sperrende Contracts (Taboo) bleiben wie bisher stehen. Contracts eines Systems ausserhalb des
  Fabrics über „seine“ externe EPG (0.28.0) bleiben Zeilen. Ohne Firewall im Mandanten bleibt
  alles wie bisher.

### 4. Unspezifische Zugriffe nach L3Out

- Firewall-Regeln einer Firewall hinter einem L3Out der VRFs des Systems tragen den L3Out (Spalte
  „L3Out“, `_l3out_in`). In jeder Kategorie stehen die spezifischen Zugriffe wie bisher zuerst,
  danach die unspezifischen (Subnetz, any, vzAny, Preferred Group) **je L3Out**, über den sie
  hereinkommen (`appdoc.by_l3out`; ohne L3Out zuletzt) – in der Applikations-Doku, der Doku je
  System, im Markdown- und Word-Export (Spalte); die Oberfläche setzt dazu Zwischenüberschriften
  „über L3O_inet“.

### 5. Verbindungen Ende zu Ende

- Der Hilfs-Contract ist **keine eigene Verbindung** (seine Treffer erzeugen keine), bleibt aber
  Teil des ACI-Hops: die Verbindung der Firewall-Regel prüft ihn auf dem Weg mit (`aci_check`),
  und er steht mit seinem Zustand bei den Regeln der Verbindung, wenn er ihren Dienst öffnet (wie
  bisher der Contract zum Netz der externen EPG).

### 6. Sprache

- Alle neuen Texte zweisprachig (`L()`), gespeicherte Befunde deutsch (`@german_only` der
  Einstiege). Spaltenschlüssel `L3Out` (Fachbegriff, in beiden Sprachen gleich).

## Konsequenzen

- Die Zuordnung zu externen EPGs folgt dem Routing der Firewalls – Begründungen nennen Firewall,
  Präfix und Interface. Ist eine Firewall nicht gelesen bzw. nicht zugeordnet, bleibt das ACI
  massgebend; die Zuordnung von Hand behebt fehlende bzw. falsche Ableitungen.
- Die Applikations-Doku zeigt je Zugriff die Firewall-Regel als Verbindung, den Contract nur als
  Vermerk; „Regeln & Contracts“ wird kürzer. Bestehende Befunde `app_contract_narrower` bleiben
  (Typ unverändert), Gegenstand und Text wie 0.78.2.
- Nur Datenhaltung der Plattform, kein Zugriff auf Geräte (nur lesend).

## Offen

- Gegenstellen einer Firewall-Regel, die das ACI keiner externen EPG des L3Outs zuordnet (z. B.
  eine Adresse ausserhalb aller Subnetze des L3Outs), sind noch kein eigener Befund.
- Policy-Routen bzw. SD-WAN der Firewall gehen in die Entscheidung „über welchen L3Out“ noch nicht
  ein (nur die RIB).
- Mehrere L3Outs am selben Interface einer Firewall: ohne passendes Interface der Regel zählen
  alle.
