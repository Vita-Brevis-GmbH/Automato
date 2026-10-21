# ADR 0018 — Umgebung und Kunden: einmal lesen, je Kunde einen Ausschnitt zeigen

## Status

**Entwurf** (2026-10-22) – zur Durchsicht durch den Anwender. Präzisiert ADR 0004
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
     NetBox-Sync und keine Daten anderer Kunden. Gegenstellen einer Regel erscheinen so, wie
     sie in der Regel stehen (Objektname, Netz), aber ohne Detail eines fremden Kunden (keine
     fremden VMs, EPG-Lagen oder IPAM-Daten).
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
- Offen zur Klärung: ein Selektor über Namensmuster (z. B. GG-Kürzel im Objektnamen) – ja
  oder nein; ob Kunden-Benutzer fremde Gegenstellen nur als „anderer Kunde“ sehen sollen
  statt mit Objektname und Netz.

## Umsetzung (Schritte)

1. Modell und Migration, Ableitung der Zugehörigkeit, API für Kunden und Selektoren mit
   Vorschau, Ansicht „Kunden“ unter Administration, Umschalter „Kunde: alle | …“.
2. Kunden-Filter in allen Ansichten (für Betreuer).
3. Kunden-Benutzer: `customer_ids` in Keycloak, serverseitige Einschränkung aller Lese-APIs,
   Tests je Endpunkt.
