# ADR 0020 — Parallele Firewalls (Verbund): Regeln zusammenfassen, Abweichungen melden

## Status

Angenommen (2026-10-22), umgesetzt in 0.18.0 (Alembic `0039`). Ergänzt ADR 0016
(Regelindex) und ADR 0019 (Session-Sync-Paar als ein Hop folgt dort).

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
- Offen:
  - im Weg (ADR 0019) das Session-Sync-Paar als einen Hop zeigen;
  - Abweichungen an Policy-Routen, Routing und NAT zwischen den Firewalls.
