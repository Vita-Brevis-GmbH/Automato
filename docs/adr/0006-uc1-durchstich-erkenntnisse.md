# ADR 0006 – UC1-Durchstich: Erkenntnisse & Entscheide aus dem echten Lab (M8)

- Status: akzeptiert
- Datum: 2026-10-13
- Kontext-Meilenstein: M8 (Review-Checkpoint nach dem Durchstich)
- Bezug: ADR 0002 (Adapter), ADR 0004 (Datenmodell), ADR 0005 (Security),
  `docs/usecases/uc1-firewall-regel-dokumentation.md`

## Kontext

Der M7-Durchstich wurde gegen eine **echte FortiGate** (75 Policies) und eine
frisch aufgesetzte **NetBox 4.6** auf einer VM gefahren (`lab/bootstrap.sh`).
Dabei traten mehrere Dinge auf, die in der Planung (ADR 0004/0005) nicht
vorgesehen waren. Dieses ADR hält die getroffenen Entscheide fest, damit M9 auf
bestätigtem Boden startet.

Ergebnis des Laufs: **75 Regeln gelesen, 0 nicht aufgelöste Endpunkte, 0 offene
Findings** nach vollständigem Import und typ-bewusster Klassifizierung; Anzeige
über das Web-UI mit Filtern (System/Zone/Aktion/Freitext).

## Entscheide

### D1 – NetBox: Legacy-v1-Tokens + gepinnte Version

Seit **NetBox 4.6** werden *neue* API-Tokens gehasht gespeichert (v2, Header
`Bearer nbt_<key>.<token>`); ein fester Token-Wert lässt sich nicht mehr vorgeben
(`value too long for type character varying(12)`). Der NetBox-Adapter nutzt die
klassischen Tokens (`Authorization: Token <key>`).

**Entscheid:** Das Lab-Setup legt **v1-Legacy-Tokens** an (`version=1`, Klartext)
und pinnt NetBox auf eine verifizierte 4.6er-Linie (`NETBOX_VERSION`). Read-only
für den Collector wird über eine **ObjectPermission** (`actions=["view"]`) +
`write_enabled=false` am Token doppelt abgesichert (NetBox hat ein eigenes
Group-Modell; Django-Permissions allein genügen nicht).

### D2 – Collector liefert den FortiGate-Objekttyp mit (typ-bewusste Einordnung)

Die Kunden-FortiGate folgt **nicht** der Namenskonvention (`H_/N_/VIP_/…`);
Objekte heissen z. B. „DMZ address". Eine rein namensbasierte Klassifizierung
(ADR 0004) greift hier nicht. Ausserdem gibt es viele Nicht-IPAM-Objekttypen
(fqdn, geography, mac, dynamic, interface-subnet, iprange, VIP).

**Entscheid:** Der Collector löst Adressobjekte **typbasiert** auf und markiert
nicht-IP-Werte mit einem Typ-Präfix (`fqdn:`, `range:`, `geo:`, `mac:`,
`dynamic:`, `vip:`); der DTO-Vertrag bleibt `list[str]` (abwärtskompatibel). Der
Orchestrator ordnet ein: `fqdn` → `expected_external`, `geo/mac/dynamic` →
`not_applicable` (kein Fehler), `interface-subnet` → echtes Subnetz,
`iprange`/`vip` → gematcht (siehe D4). Nur echte IPAM-Lücken erzeugen Findings.

### D3 – NetBox aus der FortiGate seeden (Bootstrap-Import)

Ohne echte IPAM-Daten in NetBox matcht nichts. Die FortiGate enthält die
Adressobjekte aber bereits.

**Entscheid:** Ein **opt-in Bootstrap-Import** (`import_from_fortigate.py`)
übernimmt FortiGate-Adressobjekte als Ausgangsbasis nach NetBox
(ipmask/interface-subnet → Prefixe/Hosts, iprange → IP-Ranges, VIP-extip →
IP-Adressen; Tag `automato-import`). Dieser Schritt **schreibt** in NetBox und
nutzt bewusst den **Admin-Schreib-Token** – der Collector bleibt rein read-only.
Danach ist **NetBox die Source of Truth** und wird dort gepflegt; die Firewall
ist nur der Startpunkt. (Bricht die Least-Privilege-Regel nicht: das Schreiben
ist ein separates Admin-Werkzeug, kein Collector-Pfad.)

### D4 – VIPs über die externe IP; internes Mapping = NAT (Phase 1.5)

VIPs stehen in `firewall/vip` (nicht `firewall/address`) und sind DNAT-Objekte
(extip → mappedip:port).

**Entscheid:** Der Collector liest VIPs und matcht sie über ihre **externe IP**
(Name bleibt erhalten). Das **interne Mapping** (mappedip, Ports, Portforward)
ist NAT und wird im **nat_rule**-Schema (ADR 0004, Phase 1.5) modelliert – nicht
in diesem Durchstich.

### D5 – Findings sind abgeleiteter Zustand

**Entscheid:** Findings werden pro Collector-Lauf **neu aufgebaut** (die des
Mandanten werden zu Beginn gelöscht), statt über Läufe zu akkumulieren. Sobald
ein Quittierungs-Workflow existiert (Schreibpfad), wird auf „stale auflösen"
statt „löschen" umgestellt (Kommentar im Code).

## Konsequenzen

- UC1 Model 2 ist auf echten Daten end-to-end bestätigt; die Namenskonvention ist
  **ein** Signal, nicht die alleinige Grundlage – der Objekttyp ist gleichwertig.
- Kleinere Folgearbeiten offen: **VIP-NAT-Mapping** (Phase 1.5), **Applikations-Doku**
  (letzter offener UC1-Punkt), **Zonen** aus Interfaces/SoT statt nur SoT-Tags.
- Neue Risiken ins Register: SoT-API-/Versionsdrift, FortiGate-API-Auth per
  Trusted-Host, Schreibzugriff des Bootstrap-Imports (siehe `docs/RISKS.md`).
