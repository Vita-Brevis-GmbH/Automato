# ADR 0016 — IPv4/IPv6 getrennt und abgeleiteter Regelindex

## Status

Angenommen (2026-10-21). Ergänzt ADR 0004 (Datenmodell) und ADR 0010 (Inventar, Abgleich).
Mildert Risiko R18 (Datenmenge/Leistung).

## Kontext

Dev-Befunde gegen echte Systeme:

1. **IPv4 und IPv6 gleichnamig:** Die FortiGate führt `address`/`addrgrp` (IPv4) und
   `address6`/`addrgrp6` (IPv6) getrennt. Viele Objekte heissen in beiden Familien gleich,
   haben aber verschiedene Werte. Automato kannte je Firewall und Name nur ein Objekt:
   - Beim Einlesen ging das IPv6-Objekt verloren („erstes Objekt gilt“).
   - Der Collector löste IPv4-Felder teils gegen IPv6-Objekte auf.
2. **Regeln je Familie:** Meist gibt es je Familie eine eigene Policy, die dasselbe abdeckt
   (gleiche Objektnamen, Dienste, Interfaces), mit Ausnahmen. Sie sollen eigene Regeln
   bleiben wie auf der FortiGate, aber sichtbar zusammengehören.
3. **Regel-Zahl bei Objekten:** Die Zahl zählte nur direkte Verweise, nicht die Nutzung über
   (verschachtelte) Gruppen. Das Objekt-Detail zeigte dagegen alle Regeln.
4. **Grosse Regelwerke sind langsam.** Gemessen mit 3000 Regeln, 9500 Objekten und
   39 000 Endpunkten:
   - Suche 3,5–4 s, Objekt-Detail 8,7 s, Doku je System 2,2 s, Einlesen 216 s.
   - Ursache: Jede Anfrage lud alle Regeln mit Kindern und baute den Suchtext neu.
   - Beim Einlesen gab es je Endpunkt mehrere Datenbankzugriffe.

## Entscheidung

### IPv4 und IPv6 sind verschiedene Objekte

- `device_object` ist eindeutig je Quellsystem, Kategorie, **Familie** und Name (Alembic
  `0030`).
- Der Collector löst `srcaddr`/`dstaddr` gegen die IPv4-Tabellen auf, `srcaddr6`/`dstaddr6`
  gegen die IPv6-Tabellen. Gruppen lösen ihre Mitglieder in der eigenen Familie auf.
  - Die Verweise kommen getrennt (`source_refs6`/`destination_refs6`).
  - Die Regel trägt `family` (v4 | v6 | dual).
- `firewall/policy6` (FortiOS 6.x) wird zusätzlich gelesen. Ab FortiOS 7 gibt es die Tabelle
  nicht mehr; der Collector überspringt sie dann still.
- `rule_object_ref.family` hält fest, aus welchem Feld ein Verweis stammt; `rule.ip_family`
  hält die Familie der Regel.
- Befunde zu IPv6-Adressobjekten tragen die Familie in der Bezeichnung
  („H_web01 (IPv6)“), damit sie nicht mit dem IPv4-Objekt zusammenfallen.

### IPv4/IPv6-Paare nur markieren

- Je Firewall wird zu jeder IPv4-Policy das IPv6-Gegenstück gesucht (`store/rule_pairs.py`,
  reine Funktion):
  - **gleich:** Aktion, Interfaces/Zonen, Quelle, Ziel und Dienste sind gleich
    (Objektnamen).
  - **ähnlich:** gleicher Regelname ohne IPv4/IPv6-Kennung (`web-in`/`web-in-v6`), oder
    nur eine Seite weicht ab. Die Abweichungen werden genannt.
  - Jede Regel hat höchstens ein Gegenstück. Bei Gleichstand gewinnt die nächstgelegene
    Regel in der Reihenfolge auf der Firewall.
- Beide bleiben eigene Regeln. Die Liste zeigt Familie und Gegenstück (Link, Hervorhebung
  beim Überfahren). Policies mit beiden Familien (`dual`) werden nicht gepaart.

### Abgeleiteter Regelindex

Je Regel wird beim Einlesen einmal berechnet (`store/rule_index.py`):

| Tabelle | Inhalt | genutzt von |
|---|---|---|
| `rule_index` | Suchtext, Objekt-/EPG-Namen, VIP-/Pool-Namen, Listenzeile, Gegenstück | Liste, Suche, Filter `object`/`nat` |
| `rule_object_usage` | verwendete Firewall-Objekte, direkt oder über Gruppen | Regel-Zahl, Objekt-Detail, Filter `object_id` |
| `rule_ip_range` | IP-Bereiche als Quelle/Ziel (Hex fester Breite, Collation „C“) | IP-Suche, Applikations-Doku |

Wann der Index aufgebaut wird:

- je Quellsystem nach dessen Lauf,
- für den ganzen Mandanten nach einem Referenzlauf und nach „neu abgleichen“, weil die
  DNS-Namen aus dem IPAM im Suchtext stehen.

Fehlt der Index (direkt nach dem Update), baut die erste Anfrage ihn einmalig auf.

Die Abfragen laufen per SQL, die Seiten werden in der Datenbank geschnitten:

- Mehrere Suchwörter müssen alle vorkommen.
- Eine IP trifft auch innerhalb eines Netzes oder Bereichs; `any` ist dabei ausgenommen.
- Nicht aufgelöste Objekte werden live gezählt, weil der Abgleich sie ändert.

Beim Einlesen hält ein Zwischenspeicher je Lauf vor:

- Werte-Objekte, Zonen und Services,
- IPAM-Treffer je IP bzw. Name,
- das Ergebnis je Endpunkt.

Neue Objekte werden gesammelt angelegt, Regeln per SQL gelöscht.

## Konsequenzen

Gemessen mit demselben Regelwerk:

| Messung | vorher | nachher |
|---|---|---|
| Regelliste (500 Zeilen) | 0,9 s | 0,07 s |
| Suche | 3,5 s | 0,04 s |
| IP-Suche | 4,1 s | 0,1 s |
| Objekt-Detail | 8,7 s | 0,05 s |
| Doku je System | 2,2 s | 0,04 s |
| Einlesen | 216 s | 25 s |

- Die Regel-Zahl bei Objekten stimmt mit dem Objekt-Detail überein (direkt und über Gruppen).
- Der Index ist abgeleiteter Zustand. Er wird nie von Hand gepflegt und lässt sich jederzeit
  neu aufbauen.
- Agenten liefern die getrennten IPv6-Verweise erst ab 0.9.0. Ältere Agenten liefern weiter
  alles zusammen; Automato ordnet dann nach Namen zu, IPv4 zuerst.
- Downgrade (`0030` → `0029`) entfernt gleichnamige IPv6-Objekte. Der nächste Lauf einer
  älteren Version liest sie nicht wieder ein.
- Die Volltext-Suche ist bewusst `LIKE` ohne Zusatz-Erweiterung (kein `pg_trgm`). Wird sie bei
  sehr grossen Mandanten zu langsam, folgt ein Trigram-Index als eigene Migration.
