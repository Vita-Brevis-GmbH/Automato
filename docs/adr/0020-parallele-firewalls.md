# ADR 0020 — Parallele Firewalls (Verbund): Regeln zusammenfassen, Abweichungen melden

## Status

Angenommen (2026-10-22), umgesetzt in 0.18.0 (Alembic `0039`). Ergänzt ADR 0016
(Regelindex) und ADR 0019 (Session-Sync-Paar als ein Hop folgt dort). Ergänzung 0.72.0:
Policy-Routen, Routing und NAT im Verbund vergleichen (Abschnitt unten).

## Kontext

Dev-Befund 0.17.0: Mehrere parallele Firewalls sind eingebunden, z. B. zwei HA-Cluster,
deren Sessions synchronisiert werden (FGSP). Sie tragen dieselben Regeln, deshalb erscheint
jede Policy doppelt in der Liste, in der Suche und in der Applikations-Doku.

Wunsch des Anwenders: die Regeln zusammenfassen und unter Findings melden, wo sie nicht gleich
konfiguriert sind.

## Entscheidung

### Verbund am Quellsystem

- `source_system.parallel_group`: gleicher Name = ein Verbund paralleler Firewalls.
- Der Admin setzt den Verbund im Quellsystem-Dialog oder per Vorschlag
  (`POST /systems/parallel-group`). Änderungen stehen im Audit (`source.parallel_group`).
- Automato fasst nicht von sich aus zusammen. Es schlägt vor (`GET /systems/parallel-suggestions`),
  wenn zwischen zwei FortiGates Session-Sync besteht (FGSP-Peer = Interface-IP der anderen)
  oder mindestens die Hälfte der Policies gleich ist (mindestens drei).
- **Ablehnen (0.19.0, Alembic `0040`):**
  - Ein abgelehnter Vorschlag bzw. ein aufgelöster Verbund wird als Paar in `parallel_rejection`
    gemerkt und nicht mehr vorgeschlagen (`POST /systems/parallel-reject`, Firewalls oder
    `group`).
  - `…/undo` hebt die Ablehnung auf, ebenso ein von Hand gesetzter Verbund.
  - Audit: `source.parallel_rejected` / `_unrejected`.

### Vergleich (abgeleiteter Zustand, `store/parallel.py`)

- Führend ist im Verbund die Firewall mit dem kleinsten Namen.
- Eine Policy der anderen Firewall gilt als dieselbe bei gleicher Policy-ID im selben VDOM,
  sonst bei gleichem, eindeutigem Namen.
- Verglichen werden die Policy-Felder Name, Status, Aktion, Interfaces, Quelle/Ziel
  (IPv4/IPv6, Negation), Dienst, Internet-Services, Benutzer/Gruppen, Zeitplan, NAT/IP-Pool,
  Schutzprofile und Logging. Nicht verglichen werden uuid und Kommentare, die sind je Firewall
  verschieden.
- Mögliche Ergebnisse:
  - **gleich:** Die Policy ist ein Zwilling (`rule_index.twin_of`). Listen, Kennzahlen und die
    Applikations-Doku zeigen nur die führende Regel, mit „+ fw-b“.
  - **abweichend:** Beide Regeln bleiben in der Liste, dazu der Befund `parallel_rule_mismatch`
    mit den Feldern und Werten.
  - **fehlt:** Die Policy fehlt auf einer Firewall des Verbunds, Befund `parallel_rule_missing`.
- Gleichnamige Objekte mit anderen Werten bzw. Mitgliedern ergeben den Befund
  `parallel_object_mismatch`. Sonst wären Regeln mit gleichen Namen trotzdem nicht gleich.
- Neu berechnet wird nach jedem Lauf (Abgleich und Regelindex) und nach jeder Änderung des
  Verbunds. Gelesen wird nur.

### Anzeige

- Regel-Liste:
  - Chips „+ fw-b“ (gleich), „≠ fw-b“ (abweichend), „fehlt auf fw-b“.
  - Filter „Verbund: abweichend / fehlt“.
  - Mit Filter auf ein Quellsystem bzw. `twins=true` erscheinen dessen Regeln vollständig.
- Regel-Detail: Verbund, gleiche Firewalls, Abweichung zur führenden Regel.
- Findings: Kategorie „Parallele Firewalls“.

## Konsequenzen

- Die Liste zeigt je Verbund jede Policy nur einmal. Die Zahl der Regeln in der Übersicht
  zählt Zwillinge nicht doppelt.
- Objekte stehen weiterhin je Firewall in der Objekt-Liste. Abweichende Objekte zeigen die
  Findings.
- Anbindungen nach aussen (0.20.0): Verbund und Session-Sync-Partner fassen erkannte Router zu
  einer Anbindung für beide Firewalls zusammen (ADR 0019).
- Im Weg (ADR 0019) ist der Verbund seit 0.29.0 ein Hop.

## Ergänzung 0.72.0: Policy-Routen, Routing und NAT

Umplanung des Anwenders (2026-10-26), Schritt 2: Abweichungen bei Policy-Routen, Routing und
NAT zwischen den Firewalls eines Verbunds zeigen. Rein lesend, ohne Migration
(`store/parallel_net.py`, Teil von `parallel.compare`); führend bleibt die Firewall mit dem
kleinsten Namen.

1. **Policy-Routen und SD-WAN-Regeln** je VDOM und Art über die Nummer (seq-num bzw. ID), sonst
   den Namen. Verglichen: Status, Bedingung (Eingang, Quelle, Ziel, Protokoll, Ports,
   Internet-Service), Aktion bzw. Modus, Gateway, Ausgang (bei SD-WAN mit der Reihenfolge der
   Mitglieder), SD-WAN-Zonen. Befund `parallel_policy_route_mismatch` je Eintrag (abweichend
   bzw. „fehlt auf …“), Gegenstand `node` `<Quellsystem>|<VDOM>|<Art>|<Nummer>`.
2. **Routing** je VDOM gegen die führende Firewall:
   - wirksame Tabelle: je Präfix (in seiner VRF bzw. Management) Protokoll und
     Ausgangs-Interfaces; **Next-Hop-IPs nicht** – parallele Firewalls haben eigene Router bzw.
     Adressen, verglichen wird die Wirkung (wohin der Verkehr geht);
   - statische Routen: je Präfix die Einträge mit Distanz, Interface, Gateway, Status,
     Blackhole (mehrere gleiche Präfixe als Menge);
   - ein VDOM nur auf einer Firewall ist ein eigener Eintrag;
   - nicht lesbare Tabellen (`topo_context.missing`) und Firewalls ohne gelesenes Routing
     zählen nicht;
   - **ein Befund je Firewall und VDOM** (`parallel_route_mismatch`, Gegenstand `node`
     `<Quellsystem>|<VDOM>`) mit der Liste der Präfixe (höchstens 200, dazu die Zahl) – sonst
     ergäben z. B. eine fehlende BGP-Sitzung Hunderte Befunde;
   - BGP-Nachbarn und -Pfade werden nicht verglichen (je Firewall eigene Nachbarn; die Wirkung
     steht in der Routing-Tabelle).
3. **NAT:** VIPs und IP-Pools je VDOM, Art, Familie und Name: externe/interne IP und Ports,
   Protokoll, Port-Weiterleitung, Typ, externes Interface, alle internen Ziele, Real Server
   (Adresse, Port, Status, Gewicht) und Einstellungen (Methode, Persistenz, Health-Check,
   Pool-Typ, Quellbereich). Befund `parallel_nat_mismatch` je VIP bzw. Pool, neuer Gegenstand
   `nat` `<Quellsystem>|<VDOM>|<Art>|<Familie>|<Name>` (stabil über Läufe, die NAT-Zeilen werden
   je Lauf ersetzt).
4. Werte im Befund: zuerst die Firewall des Befunds, dann die führende.
5. **Übersicht** `GET /systems/parallel` (viewer, nicht Kunden-Benutzer): je Verbund Mitglieder
   und je Bereich (Policies, Objekte, Policy-Routen, Routen, NAT) gleich/abweichend/fehlt; UI
   Routing → Reiter „Parallele Firewalls“ mit Link auf die Befunde. Die Befunde öffnen das
   Routing (Routen des VDOMs bzw. Policy-Routen) bzw. die NAT-Ansicht.
6. Kunden: die neuen Befunde gehören keinem Kunden (Infrastruktur), wie die übrigen
   Topologie-Befunde.
