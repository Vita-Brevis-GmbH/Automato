# ADR 0010 — Inventar, Referenz, Abgleich; Schreiben nur nach NetBox

## Status

Angenommen (2026-10-20). Umgesetzt in Phase A (A1–A6), siehe ROADMAP „Phase A".

## Kontext

Die Regeldoku zeigte nur IP-Adressen: Der FortiGate-Collector löste Objektnamen zu Werten
auf (Gruppen flach), und vom IPAM wurde nur „gefunden ja/nein" gespeichert. Ein Datensatz
(`network_object`) vermischte drei Dinge: was auf der Firewall steht, was im IPAM steht und
wie beides zusammenpasst. Dazu kommen neue Anforderungen:

- Die Testsysteme (FortiGate, APIC) sind produktiv; die echten Referenzdaten liegen in der
  **Infoblox DDI**, die nur der Windows-Agent im Kundennetz erreicht.
- **NetBox** soll zusätzliche Daten aufnehmen: VLANs (auch der EPGs), reale Subnetze,
  Portkonfigurationen, später VMs und Systeme.
- Bis auf Weiteres gilt: **nur nach NetBox schreiben, alles andere strikt nur lesen.**

## Entscheidung

### 1. Drei Ebenen im Orchestrator

1. **Inventar (Ist)** – je Quellsystem, wie gelesen: Firewall-Objekte mit Namen
   (`device_object`: Adressen, Gruppen mit Mitgliedern, VIPs, IP-Pools, Services,
   Service-Gruppen), Regel-Verweise per Objektname (`rule_object_ref`), Netzinventar
   (`inv_device`, `inv_interface`, `inv_vlan`, `inv_subnet`). Wird je Lauf ersetzt.
2. **Referenz** – Stand der IPAM-Quellen (`reference_record`: IP-Adressen, Host-/A-/PTR-
   Records, Prefixe, Ranges, VLANs), komplett gelesen, je Quelle versioniert über den Lauf.
3. **Abgleich** – verknüpft Objekte und Referenzen (genaue IP → Name → umschliessendes
   Prefix), bewertet Abweichungen als Findings und vergleicht DDI ↔ NetBox. Wird nach jedem
   Lauf eines Quellsystems des Mandanten neu berechnet (Triage-Status bleibt erhalten).

Die kanonische Werte-Ebene (`network_object`, Regel-Endpunkte) bleibt für Matching,
Applikations-Doku und Suche bestehen.

### 2. Referenzquellen sind Quellsysteme

Infoblox DDI und NetBox werden wie FortiGate/APIC beim Mandanten erfasst (Plattformen
`infoblox`, `netbox`), mit verschlüsselten Zugangsdaten (ADR 0009) und optional einem Agenten.
Die DDI liest der Windows-Agent komplett (seitenweise, Teil-Upload), die zentrale NetBox der
zentrale Worker. Jede Referenzquelle hat eine **Rolle**: `primary` (IPAM führend) oder
`enrichment` (Anreicherung). Gematcht wird gegen den gespeicherten Stand, nicht mehr live –
der Live-Adapter bleibt nur als Rückfall für Mandanten ohne Referenzquelle.

### 3. Feld-Hoheit

| Daten | führend | NetBox |
|---|---|---|
| IP, Prefix, Range, DNS-Name, PTR | DDI (primary) | Spiegel, gekennzeichnet, nur abgleichen |
| VLANs, Ports, Geräte, VRFs, ACI-Attribute (EPG/BD/AP/Tenant), später VMs | Automato-Inventar | wird dort abgelegt |
| System/Applikation, Owner, Kommentare | NetBox | nur dort gepflegt |

Kunden ohne DDI: NetBox ist `primary` (kein Spiegel nötig). Kunden nur mit DDI: Referenz
ohne Anreicherung.

### 4. Schreibregel: nur NetBox, nur nach Vorschau

- **Konten:** FortiGate, APIC, DDI nur mit Read-only-Benutzern.
- **Code:** Die Lese-Clients (FortiGate, APIC, Infoblox, NetBox-Leser) laufen über einen
  Nur-Lese-Zugang (`sot_gateway.readonly`): ausser GET/HEAD/OPTIONS nur ausdrücklich erlaubte
  Login-/Logout-Pfade (APIC). Jede andere Methode bricht ab (`ReadOnlyViolation`), Tests
  halten das fest. Für FortiGate, APIC und DDI gibt es keinen Schreibcode.
- **Agent:** bleibt rein lesend und bekommt nie Schreib-Zugangsdaten. Das NetBox-
  Schreib-Token (`write_token`) liegt verschlüsselt im Orchestrator und wird nie an einen
  Agenten ausgeliefert.
- **NetBox:** einziger Schreibweg ist der NetBox-Sync des Orchestrators
  (`sot_gateway.adapters.netbox_writer`) mit eigenem Schreib-Token (NetBox-Berechtigungen auf
  die synchronisierten Objekttypen begrenzen). Automato ändert/löscht nur Objekte mit Tag
  `automato`; vorhandene, von Hand gepflegte Objekte werden höchstens referenziert, nie
  geändert (Konflikt wird angezeigt). Jede Änderung entsteht als **Vorschau** (Plan), wird
  per Klick übernommen (admin), auditiert, und danach liest Automato NetBox neu.

### 5. Umsetzung NetBox-Sync (A6)

- **Umfang** wählbar: *Netzinventar* (Geräte mit Ports – Modus, untagged/tagged VLANs, LAG,
  Parent, Interface-IPs –, VLANs je Quellsystem in einer eigenen VLAN-Gruppe, reale
  Subnetze inkl. ACI BD/EPG/VRF, VRFs) und *DDI-Spiegel* (Netze, Ranges, IPs mit DNS-Namen
  aus dem führenden IPAM; entfällt, wenn die Ziel-NetBox selbst führend ist).
- **Abbildung ohne Custom Fields:** Herkunft und ACI-Bezug stehen in Beschreibung/Kommentar
  (z. B. „BD BD_app · TE/AP/EG_web“, „Spiegel ddi“), Besitz über das Tag `automato`. So
  braucht das Schreib-Token keine Rechte auf `extras.custom-fields`.
- **Natürliche Schlüssel:** Tag/Tenant/Site/Hersteller/Rolle/VLAN-Gruppe per Slug, Gerätetyp
  per Modell, VRF/Gerät per Name, VLAN per Gruppe+VID, Netz per Prefix, Range per Start–Ende,
  IP per Adresse, Port per Gerät+Name. Standort eines Geräts = Standort des Quellsystems
  (bei genau einem Standort des Mandanten dieser); ohne Standort keine Geräte (Hinweis).
- **Vorschau:** anlegen / ändern (nur getaggte Objekte) / unverändert / Konflikt (manuelles
  Objekt weicht ab – bleibt) / löschen (nur mit „Löschen zulassen“, nur getaggte Objekte des
  Mandanten) / veraltet. Gespeichert als `sync_plan` mit Fingerabdruck der Schreib-Aktionen.
- **Übernahme:** im Hintergrund; Plan wird neu berechnet und nur bei gleichem Fingerabdruck
  geschrieben (sonst nichts schreiben, neue Vorschau); Bulk in Abhängigkeitsreihenfolge,
  Löschen rückwärts; LAG/Parent werden nach dem Anlegen nachgezogen. Fehler von NetBox stehen
  am Plan; Audit `netbox_sync.planned`/`netbox_sync.applied` (nur Anzahlen); danach wird
  NetBox neu eingelesen. Vorschauen gelten 60 Minuten.

## Konsequenzen

- Regeln zeigen die echten Objektnamen der Firewall (Gruppen aufklappbar) mit Wert und
  Referenzdaten (DNS-Name, Prefix, VLAN, Standort, Link ins IPAM).
- Neue Abweichungstypen: nicht im IPAM, Name ≠ DNS-Name, Typ passt nicht, doppelte Werte,
  unbenutzte Objekte, ACI-Subnetz nicht im IPAM, VLAN abweichend; DDI ↔ NetBox als eigene
  Vergleichssicht (nicht als Einzel-Findings, wegen der Menge).
- Das Agent-Paket enthält zusätzlich das `sot-gateway`-Wheel (Infoblox-/NetBox-Leser).
- Phase B (Bereinigen auf Firewall/DDI) und Phase C (Regeln schreiben) brauchen eigene
  ADRs und eine ausdrückliche Freigabe; bis dahin bleibt der Schreibweg auf NetBox begrenzt.

## Ergänzung – Verkabelung und Applikationen (ADR 0031)

Zwei weitere Abschnitte des NetBox-Syncs, nur ausdrücklich gewählt (ohne Angabe gelten
*Netzinventar*, *DDI-Spiegel*, *Virtualisierung*): *Verkabelung* (Switches/Router mit Ports und
Kabeln `dcim/cables` zwischen beidseitig gemeldeten Ports; Modus/VLANs nur mit vorhandenen VLANs)
und *Applikationen* (Tag `automato-app-…` je Applikation an VMs und IP-Adressen, fremde Tags
bleiben). Objekte eines nicht gewählten Abschnitts sind weder veraltet noch werden sie gelöscht –
bei Geräten nach dem Kommentar „Automato: gelesen aus <Quellsystem>“. Regeln wie oben: nur
Objekte mit Tag `automato` ändern, von Hand gepflegte höchstens referenzieren (Konflikt).

## Ergänzung 0.76.1 – externe Adressen ohne IPAM-Befund

Rückmeldung des Anwenders (2026-10-26): externe Subnetze und Host-Adressen gehören nicht in die
Befunde – ausser das externe Netz ist das gemanagte Interface-Netz am WAN-Anschluss der
Firewalls.

- **Extern** ist ein Wert (IP, Netz, Bereich), dessen Adressen alle öffentlich sind
  (`ipaddress.is_global`: nicht RFC 1918, nicht CGNAT 100.64/10, nicht Dokumentations-,
  Link-Local- oder Loopback-Bereiche) und der **kein gemanagtes Netz überlappt**. Gemanagt sind
  die Netze der gelesenen Geräte (`inv_subnet`: Interface-Netze der FortiGates inkl. WAN,
  ACI-BD/-EPG-Subnetze, Interfaces der Router) – sie sind konfiguriert, also unsere.
- Externe Werte-Objekte ohne IPAM-Treffer erhalten den bestehenden Status `expected_external`
  (wie `H-EXT_`, FQDN, ISDB) und erzeugen keinen Befund `unmatched_object`. Anders als diese
  wird der Status nach jedem Abgleich neu berechnet (`store/external.apply` nach
  `references.rematch`): steht die Adresse später im IPAM, ist sie gefunden; liegt sie in einem
  neuen Interface-Netz, ist sie wieder ein Befund.
- Unverändert: „Subnetz fehlt im IPAM“ für Interface-/BD-Netze (auch öffentliche WAN-Netze),
  VIPs (publizierte eigene Adressen), private Adressen, Objekte ohne Wert, Namenskonvention.

