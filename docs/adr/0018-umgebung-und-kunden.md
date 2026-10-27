# ADR 0018 — Umgebung und Kunden: einmal lesen, je Kunde einen Ausschnitt zeigen

## Status

**Entwurf** (2026-10-22) – zur Durchsicht durch den Anwender; Schritte 1–3 umgesetzt in 0.30.0–0.33.0, Schritt 4 in 0.53.0 und 0.58.0, der Kunden-Filter für alle Ansichten in 0.76.0
(siehe „Umsetzung“). Präzisiert ADR 0004
(Ergänzung E1, Ebene über dem Mandanten) und ADR 0007 (Rechte); betrifft R13 und R14.

Grundsatzentscheide des Anwenders (2026-10-22):

1. **Eine Automato-Instanz je Umgebung.** Später richtet ein Cockpit wie bei Magister nur
   noch die Instanzen ein und setzt Grundeinstellungen (ADR 0014).
2. **Die Zuordnung zum Kunden ist nicht statisch.** Oft ist es der ACI-Tenant, manchmal
   weniger: ein Teil eines Tenants bzw. ein Application Profile.
3. **Benutzer der Kunden sollen sich später selbst anmelden können.** Heute arbeiten nur
   Betreuer mit Automato; die Trennung läuft über Keycloak.

## Kontext

- Heute ist der Mandant (`tenant`) der oberste Scope. Jedes Quellsystem gehört genau einem
  Mandanten, alle gelesenen Daten tragen `tenant_id`, die Rechte kommen über `tenant_ids`
  aus Keycloak.
- In Wirklichkeit dienen die Firewalls (mit ihren VDOMs) und die ACI-Fabric allen Kunden
  gemeinsam. Ein Kunde besitzt einen ACI-Tenant oder nur einen Teil davon (ein AP) und einen
  Teil der Verbindungen durch die Firewalls.
- Legt man dieselben Geräte je Kunde an, werden sie mehrfach gelesen und gespeichert, und
  die Topologie (ADR 0019) zerfällt in Bruchstücke.
- Eine zentrale Datenbank über alle Umgebungen wäre R14 in Reinform.

## Entscheidung

1. **Begriffe:**
   - **Umgebung:** die betriebene Infrastruktur mit ihren Quellsystemen (FortiGates mit
     VDOMs, APIC, vCenter, DDI/NetBox), Referenzquellen und Namensprofil. Technisch ist das
     der heutige `tenant`; in der Oberfläche heisst er „Umgebung“.
   - **Kunde:** ein Ausschnitt der Umgebung, z. B. eine Gruppengesellschaft. Neu.
   - Eine produktive Instanz hält eine Umgebung. Test- und Entwicklungsinstanzen dürfen
     mehrere halten.
2. **Eine Instanz je Umgebung** (R14). Mehrere Umgebungen sind mehrere Instanzen; es gibt
   keine Daten über Instanzen hinweg. Das Cockpit (ADR 0014) verwaltet die Instanzen.
3. **Einlesen:** Quellsysteme gehören der Umgebung und werden **vollständig** gelesen – alle
   ACI-Tenants, alle VDOMs (ADR 0019). `read_scope` bleibt nur als Ausnahme, z. B. aus
   Lastgründen.
4. **Zuordnung über Selektoren statt Listen.** Ein Kunde hat Regeln der Form „gehört dazu,
   wenn …“:
   - ACI: Tenant, VRF, Application Profile, EPG bzw. ESG, Bridge Domain (je per DN bzw. Pfad
     `Tenant/AP/EPG`).
   - FortiGate: VDOM, Zone, Interface (je Quellsystem).
   - Netze: Präfixe, von Hand oder als Merkmal aus dem IPAM (z. B. NetBox-Tenant,
     DDI-Netzansicht bzw. -Attribut). Der IPAM-Adapter liefert nur das Merkmal (ADR 0002).
   - **Kürzel aus der Namenskonvention:** Jeder Kunde hat ein eigenes Kürzel, das in den
     Namen steht (`TE_<kürzel>`, `VRF_<kürzel>`, Objekt- und Servernamen). Ein Selektor
     „Kürzel“ ordnet alles mit diesem Kürzel zu (Anwender, 2026-10-22). Das Kürzel kommt aus
     dem Kunden selbst (`code`); ein Wörterbuch ist dafür nicht nötig.
   - Einschliessen und Ausschliessen, z. B. „Tenant `TE_x` ohne `AP_900`“.
   - Die Subnetze zugeordneter EPGs bzw. Bridge Domains zählen automatisch als Netze des
     Kunden.
   - **Übergreifend:** Selektoren auf Ebene der Umgebung (z. B. Tenant `common`,
     Infrastruktur-VRFs, der Internet-Rand) gelten für alle Kunden und sind als „geteilt“
     markiert.
5. **Zugehörigkeit ist abgeleiteter Zustand** (`customer_member`). Sie wird nach jedem Lauf
   und bei geänderten Selektoren neu berechnet, wie Regelindex und Findings, und nie von Hand
   gepflegt:
   - EPGs, Subnetze, VMs (über Portgruppe → EPG bzw. IP), Firewall-Objekte (über ihre
     Werte), NAT (über die interne IP), Findings (über ihr Subjekt).
   - Regeln: ein ACI-Contract, wenn eine Seite eine EPG des Kunden ist; eine
     Firewall-Regel, wenn ihr VDOM bzw. ihre Zone oder ihr Interface dem Kunden gehört oder
     Quelle bzw. Ziel ein Netz des Kunden berührt. Mit ADR 0019 zählen nur Regeln auf einem
     möglichen Pfad.
   - Ein Element darf mehreren Kunden gehören (geteilte Server, Shared Services).
   - Beim Bearbeiten zeigt eine **Vorschau**, was die Selektoren erfassen (z. B. „12 EPGs,
     340 Regeln, 85 VMs“), dazu eine Liste „keinem Kunden zugeordnet“.
6. **Vorschläge statt Pflicht:** je ACI-Tenant ein Kunde, ergänzt aus der Namenskonvention
   (GG-Kürzel in `TE_<gg>`/`VRF_<gg>`), mit einem Klick zu übernehmen – wie die
   Applikations-Vorschläge. Ohne Wörterbuch funktioniert alles.
7. **Applikationen gehören einem Kunden.** Ihre Mitglieder müssen im Ausschnitt des Kunden
   liegen; Betreuer dürfen übergreifend zuordnen (geteilte Systeme).
8. **Rechte (Keycloak, ADR 0007):**
   - **Betreuer** (Rollen auf Ebene der Umgebung) sehen die ganze Umgebung und verwalten
     Quellsysteme, Agenten, Zeitpläne, NetBox-Sync, Kunden und Selektoren.
   - **Kunden-Benutzer** tragen das Attribut `customer_ids` und die Rollen viewer bzw.
     operator je Kunde. Serverseitig wird jede Abfrage auf `customer_member` eingeschränkt:
     Listen, Suche, Detail, Doku, Export, Findings und Zähler. Ohne Zuordnung sehen sie
     nichts (fail-closed).
   - Kunden-Benutzer sehen keine Quellsystem-Konfiguration, keine Agenten, keinen
     NetBox-Sync und keine Daten anderer Kunden.
   - **Direkte Verbindungen** (Anwender, 2026-10-22): Hat eine Regel des Kunden eine
     Gegenstelle eines anderen Kunden, sieht er deren Namen und das vollständige Objekt
     (Werte, Gruppe, VM, EPG-Lage). Er sieht aber **nur die Regeln, die ihn betreffen** –
     nicht, welche weiteren Regeln es zu diesem Punkt sonst noch gibt (keine Regel-Zahl,
     keine Liste „Regeln mit diesem Objekt“ über den eigenen Ausschnitt hinaus).
   - Lesezugriffe von Kunden-Benutzern werden immer auditiert.
9. **Übergang ohne Datenumzug:** Der bestehende Mandant wird zur Umgebung, die gelesenen
   Daten bleiben, wo sie sind. Neu kommen `customer`, `customer_selector`,
   `customer_member` und `application.customer_id` dazu. Bestehende Benutzer sind Betreuer.

## Konsequenzen

- Ein Gerät wird einmal gelesen, auch wenn es zehn Kunden dient; die Topologie (ADR 0019)
  sieht das ganze Netz.
- Neue EPGs in einem zugeordneten AP oder neue Netze eines Kunden wirken nach dem nächsten
  Lauf, ohne Pflege.
- Jede Lese-API braucht die Einschränkung auf den Kunden. Das ist der grösste Aufwand und
  wird je Endpunkt mit Tests abgesichert, wie heute das Mandanten-Scoping (R13).
- Geklärt (2026-10-22): Selektor über das Kürzel ja; fremde Gegenstellen mit Namen und
  vollständigem Objekt, aber nur die eigenen Regeln.

## Umsetzung (Schritte)

1. Modell und Migration, Ableitung der Zugehörigkeit, API für Kunden und Selektoren mit
   Vorschau, Ansicht „Kunden“ unter Administration, Umschalter „Kunde: alle | …“.
   **Umgesetzt in 0.30.0** (Alembic `0044`, `store/customers.py`, `/customers`):
   - `customer` (Name, Kürzel, Beschreibung, `shared` = geteilter Teil),
     `customer_selector` (Art, Wert, Quellsystem, ausschliessen), `customer_member`
     (abgeleitet: EPG, Netz, Werte-Objekt, Regel, VM mit Grund).
   - Selektoren: ACI-Tenant, VRF (aufgelöst wie im APIC: eigener Tenant, sonst `common`), AP,
     EPG (DN bzw. Pfad), BD; VDOM, Zone, Interface (je Quellsystem); Präfix; Kürzel (Token in
     Tenant/VRF/AP bzw. in Objekt- und VM-Namen). Ausschliessende Präfixe werden aus den
     Netzen herausgeschnitten.
   - Netze des Kunden: Präfixe, Subnetze der EPGs bzw. BDs (nicht die externer EPGs) und die
     Netze an zugeordneten Interfaces. Regeln: Contracts mit einer EPG des Kunden,
     Firewall-Regeln über VDOM/Zone/Interface bzw. mit Quelle oder Ziel in einem Netz des
     Kunden (ohne `any`).
   - Abgeleitet nach jedem Lauf (auch IPAM), beim Speichern und nach dem Löschen eines
     Quellsystems; Vorschau, Vorschläge je ACI-Tenant (`common` geteilt), „keinem Kunden
     zugeordnet“; Audit `customer.*`.
   - **0.31.0:** Selektoren aus dem IPAM (`ipam_tenant`, `ipam_vrf` = NetBox-VRF bzw.
     DDI-Netzansicht, `ipam_tag` = Tag bzw. Extensible Attribute `key=value`) über den
     aktuellen Referenzstand; NAT über interne bzw. externe IP; Findings über ihr Subjekt
     (Regel, Objekt, VM, Portgruppe, Subnetz).
   - Noch nicht: der Umschalter „Kunde: …“ mit Filter (Schritt 2), Relevanz nach Pfad
     (ADR 0019), „Umgebung“ als Name in der Oberfläche.
2. Kunden-Filter in allen Ansichten (für Betreuer). **Umgesetzt in 0.32.0:** Umschalter
   „Kunde“ in der Kopfzeile; `customer_id` an den Listen und Zählern (Regeln, Findings, NAT,
   Objekte, VMs, Portgruppen, Systeme, Applikationen) zeigt den Ausschnitt samt geteiltem
   Teil (`store/customer_scope.View`, per SQL). Applikationen über ihre Mitglieder,
   Firewall-Objekte über Werte bzw. Regeln des Kunden.
   **Präzisiert in 0.33.0:** Der geteilte Teil gibt Zusammenhang – seine EPGs, Netze und
   Objekte zählen für alle Kunden. Regeln, NAT, Findings und VMs zählen nur aus den eigenen
   Kunden (eine Regel von Kunde B zum geteilten DNS betrifft Kunde A nicht, Nr. 8).
   **Ergänzung 0.76.0 (Anwender: „oben den Kunden wählen, und alles filtert nach dem Kunden –
   keine zusätzlichen Infos“; Entscheid: eigenes plus geteiltes, das geteilte markiert):** der
   Umschalter gilt jetzt auch für Routing (Kontexte, Routen, Nachbarn, HA, Policy-Routen,
   L3Outs, Anbindungen, parallele Firewalls), Topologie, Weg-Suche (Namen), Inventar (Subnetze,
   VLANs, Ports, Geräte, VRFs), Verkabelung (Geräte, Kabel, Ports, VLANs, Endpunkte, Plan),
   Abgleich, publizierte Dienste und Zonen (Liste, Matrix). `store/customer_infra.py` bestimmt
   den Ausschnitt auf der Infrastruktur:
   - **Kontexte:** VDOMs der VDOM-Selektoren und der Regeln, VRFs der EPGs. *Eigen* nur über
     einen VDOM-Selektor des Kunden bzw. im eigenen ACI-Tenant und ohne Regeln/EPGs anderer
     Kunden, sonst *geteilt*. In geteilten Kontexten zeigt das Routing nur Routen, die ein Netz
     des Kunden enthalten oder darin liegen (Default-Route eingeschlossen) – keine Routen
     anderer Kunden.
   - **Netze, EPGs, VLANs, Ports, Geräte:** Netze und EPGs aus der Zugehörigkeit; VLANs der
     EPGs, der Netze und der Portgruppen; Ports mit seinen EPGs, in seinen VDOMs, mit IPs in
     seinen Netzen bzw. seinen VLANs; Geräte an diesen Ports, Knoten seiner Kontexte und
     ESXi-Hosts seiner VMs; Kabel mit einem solchen Ende; Endpunkte = NICs seiner VMs bzw. IPs in
     seinen Netzen.
   - Geteilte Elemente tragen `shared: true` („geteilt“ in der Oberfläche); oben steht, dass
     ein Ausschnitt gezeigt wird („alle Kunden“ hebt ihn auf).
   - Unverändert ohne Kunden: Betrieb (Läufe, Betriebszustand, Quellsysteme) und die Weg-Suche
     selbst (ein eingegebener Weg wird ganz geprüft; nur die Namenssuche ist eingeschränkt).
     Kunden-Benutzer erreichen diese Ansichten weiterhin nicht (Freigabeliste).
3. Kunden-Benutzer: `customer_ids` in Keycloak, serverseitige Einschränkung aller Lese-APIs,
   Tests je Endpunkt. **Umgesetzt in 0.33.0:**
   - Erkennung: Realm-Rolle `customer` bzw. Kunden im Claim `customer_ids` (leerer Claim
     allein zählt nicht, bestehende Benutzer bleiben Betreuer). `Principal.customer_ids`;
     Kunden-Benutzer sind nie Admin, die Rolle `customer` liest (viewer), `operator`
     triagiert zusätzlich eigene Findings. Ohne Mandant bzw. Kunden: 403 (fail-closed).
   - **Freigabeliste** `auth/customer_routes.py`: `require_role` lässt Kunden-Benutzer nur an
     die gelisteten Lese-Endpunkte der Doku (Regeln, Findings, NAT, Objekte, VMs,
     Portgruppen, Doku je System, Applikationen, eigene Kunden, Mandanten, Standorte,
     `GET /systems/labels`); alles andere, auch künftige Endpunkte, ergibt 403. Ein Test
     prüft jeden Endpunkt der App gegen die Liste.
   - Je Endpunkt: Listen und Zähler auf den Ausschnitt, Details ausserhalb 404, Regel-Zahlen
     und -Listen (Objekt, NAT, Doku je System, Applikation) nur mit eigenen Regeln, Findings
     ohne Namen fremder Regeln. Lese-Audit immer an (`read.*` mit den Kunden-IDs).
   - Keycloak: Rolle und Mapper in der Realm-Vorlage; bei SAML-Brokern Kunden-IDs und Rolle
     fest je IdP, nie aus der Assertion (sonst könnte ein Kunden-IdP sich fremde Kunden
     geben).
   - Export (Markdown) und Druck entstehen im Browser aus der bereits eingeschränkten Doku;
     protokolliert ist der Lesezugriff.
   - Noch nicht: Relevanz nach Pfad (ADR 0019) für die Zugehörigkeit von Regeln, eigene
     Kunden-Benutzer-Verwaltung in der Oberfläche (heute in Keycloak).
4. **Kunden auf logischen Zonen** (Entscheide des Anwenders 2026-10-23; umgesetzt in 0.53.0 nach
   den logischen Zonen aus ADR 0024, 0.51.0 – dort schon der Kunde je Zone):
   - Ein Kunde besitzt logische Zonen. Geteilte Zonen (Internet, Shared Services, Management)
     gehören keinem Kunden. Die Selektoren bleiben für Ausnahmen (einzelne Netze, Objekte).
   - Der Kunde sieht die Contracts und Elemente in seinem Tenant und die Contracts zwischen den
     Kunden, damit ihm bewusst ist, was offen ist und was nicht. Die Gegenseite eines fremden
     Kunden erscheint ohne Namen („anderer Kunde“).
   - Die Zugehörigkeit von Regeln folgt den Zonen auf dem Weg: Quelle oder Ziel in einer Zone
     des Kunden, und der Weg ist möglich (Relevanz nach Pfad, ADR 0019).
   - Sein Plan enthält nur seine Systeme und die geteilten Elemente, an denen er angebunden ist
     (auch Redundanzen), keine Fremdsysteme.
   - Seine Doku enthält nur seine Dinge – nie die Infrastruktur oder andere Kunden. Der Umfang
     wächst mit der Zeit.
   - Die Zuordnung Benutzer → Kunde wird in Automato gepflegt, getrennt von der Anmeldung
     (Keycloak meldet nur, wer jemand ist). Der Claim `customer_ids` bleibt als Alternative.
     Die Trennung ist Grundlage für feinere Rollen. Automato schreibt nicht nach Keycloak.
   - **Umsetzung 0.53.0:**
     - Zugehörigkeit: Netze und EPGs der Zonen des Kunden zählen wie seine Selektoren
       (`customers.owned_zones`, Grund „Zone …“); ausschliessende Selektoren des Kunden gelten
       auch für sie; geänderte Zonen leiten die betroffenen Kunden sofort neu ab.
     - Sicht der Kunden-Benutzer (`zones.CustomerView`): eigene Zonen mit Netzen, geteilte nur
       mit Name/Art/Farbe; Matrix nur Zellen mit einer eigenen Zone, Zonen anderer Kunden als
       `other` („anderer Kunde“), Absichten nur eigener Paare; Plan mit den Knoten der eigenen
       Zonen und den geteilten Zonen dort (ohne Netze, Router und Netze ohne Zone); Export davon;
       keine Selektoren, Vorschau, Versionen.
     - Zuordnung Benutzer → Kunde (`customer_user`, Migration 0050, `/customer-users`, Admin):
       Kennung = Benutzername, E-Mail bzw. `sub` (klein); Rolle viewer/operator; angewendet in
       `get_principal` (`auth/assignments.py`) – macht immer zum Kunden-Benutzer, Mandanten =
       die der Kunden, Vereinigung mit dem Claim; eigenes Konto nicht zuordenbar; nicht lesbar =
       503 (fail-closed). `GET /auth/me` liefert der Oberfläche die Sicht des Orchestrators.
     - Noch offen (in 0.58.0 umgesetzt bis auf feinere Rollen): Zugehörigkeit von Regeln nach
       dem Weg, Doku des Kunden ohne Infrastruktur.
   - **Umsetzung 0.58.0:**
     - **Zugehörigkeit nach dem Weg** (`customers.evaluate`, `_Data.off_path`): Regeln, die nur
       über ein Netz des Kunden dazukommen (Quelle bzw. Ziel überschneidet es, ohne `any`),
       zählen nur, wenn der Weg möglich ist – je Treffer Seite und erste gemeinsame Adresse:
       Firewall-Regel über die Relevanz (`relevance.Relevance.check`: Route der Firewall bzw. des
       VDOMs zum Netz über Ein- bzw. Ausgang der Regel), Contract über die Zuordnung im ACI
       (`relevance._aci_for_ip`: externe EPG, Scope, Konfiguration vor Gelerntem). Sind alle
       Treffer unmöglich, fällt die Regel weg (`Scope.off_path` mit Begründung, in der Vorschau
       `off_path`); nicht prüfbar zählt sie weiter. Regeln über VDOM, Zone, Interface bzw. EPG des
       Kunden zählen ohne Prüfung. Folgt nach jedem Lauf und beim Speichern (wie bisher).
     - **Ohne Gerätenamen** (`store/customer_alias.py`): Jede JSON-Antwort an Kunden-Benutzer läuft
       durch eine Middleware (`require_role` merkt sich die Mandanten des Kunden-Benutzers), der
       Export des Zonenplans vor dem Zeichnen – auch künftige Endpunkte. Quellsysteme „Firewall
       1“ … (Reihenfolge der Anlage), „ACI“, „vCenter“, „IPAM“; VDOMs über alle Firewalls nach
       Namen nummeriert (`root` bleibt), Verbund „Verbund n“, Inventar-Geräte „Leaf“/„Spine“/
       „Controller“, LLDP-Nachbarn „Nachbar“; Felder `vdom`/`vdoms`/`by_vdom` neutral, `host`,
       `cluster`, `datacenter`, `switch`, `neighbor` leer; in Texten (Begründungen, Sätze der
       Findings, Knoten „fw-01 · VDOM x“, Knoten-IDs) dieselben Ersetzungen als ganze Wörter.
       Befunde in der Applikations-Doku des Kunden ohne Angaben zu fremden Regeln (wie in der
       Findings-Liste). Grenze: Namen der Infrastruktur in Namen von Regeln, Objekten und VMs
       bzw. in Kommentaren bleiben.
     - Noch offen: feinere Rollen.
