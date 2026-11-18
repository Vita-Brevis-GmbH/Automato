# ADR 0067 — Ansichten nach Netzwerkschichten, Modul „Wege vergleichen“

## Status

**Angenommen** (2026-11-18), umgesetzt in 1.19.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-17: „Folgende Prio umsetzen im Automato: … 3. Modul nach Netzwerkschichten …“.
Vertrag: Oberfläche neu gegliedert – **L1** Verkabelung, Ports, Transceiver; **L2** VLANs,
Spanning Tree; **L3** Netze, VRF, Routing (statisch, BGP); **L4–7** Firewall-Regeln, NAT/VIP,
Load Balancer, VPN; bestehende Ansichten ziehen in ihre Schicht, Adressen alter Ansichten leiten
um; Modul „Wege vergleichen“: ein L3-Weg gegen L1/L2 (VLAN auf allen Trunks erlaubt, Kabel und
Nachbarn passen, Spanning Tree blockiert nicht), Befunde je Abweichung.

Ergänzt ADR 0025 (Verkabelung), ADR 0036 (Module), ADR 0037 (Stand der Doku), ADR 0039
(Prüfungen und Ansichten je Modul), ADR 0048 (keine stillen Grenzen), ADR 0049 (Datenstand) und
ADR 0066 (Reservierungen).

## Kontext (Analyse)

**Module heute** (`orchestrator/app/modules.py`, Oberfläche `web-ui/frontend/src/modules/<modul>/`):

| Modul | Art | Ansichten | Guard der Router |
|---|---|---|---|
| Kern | Basis, immer an | Übersicht, Regeln & Contracts, Objekte & Inventar, NAT / VIPs, Benutzer, Mandanten, Kunden, Kunden-Benutzer, Kunden-Portal, System | keiner |
| Einstellungen | Basis, immer an | Namensprofile | keiner |
| Worker | lesend | Collector-Agenten, Läufe | – (Läufe prüfen das Modul) |
| Gateway | lesend | – | – (Referenzquellen prüfen das Modul) |
| Analyse | lesend | Routing, Statische Routen, AS-Nummern, VPN-Tunnel, Verkabelung, Spanning Tree, Reservierungen, Abgleich | `guard("analyse")` |
| Wegsucher | lesend | Weg-Suche | `guard("wege")`, Teile im Routing über `PATHS` |
| Findings | lesend | Findings | `guard("findings")` |
| Doku | lesend | Applikationen | `guard("doku")` |
| Design | lesend | Netzwerkdesign | `guard("design")` |
| Schreibpfad | schreibend, braucht Gateway | NetBox-Sync | `guard("schreibpfad")` |

**Wo Modulnamen gespeichert sind:** Schalter je Mandant in `tenant_module` (Schlüssel = Modul),
Audit `module.switched`; Prüfungen je Mandant in `tenant_check` (Schlüssel = Prüfung, das Modul
der Prüfung steht nur im Code, `Check.module`); `ModuleInfo.views` blendet die Ansichten
ausgeschalteter Module aus; `ViewEntry.module` in der Oberfläche. **Rechte** hängen nicht an
Modulen (Rollen viewer/operator/admin, Kunden-Rollen mit Capabilities); Freigaben und Inventar des
Cockpits führen keine Modulschlüssel.

**Schichten liegen quer zu den Modulen:** Analyse umfasst L1 (Verkabelung), L2 (VLANs, Spanning
Tree), L3 (Routing, statische Routen, AS-Nummern) und den Abgleich; L4–7 (Regeln, Objekte, NAT)
liegt im Kern, der immer an ist; „Objekte & Inventar“ mischt Firewall-Objekte (L4–7) mit dem
Inventar (Subnetze und VRFs = L3). Auch die Dateien sind verflochten: `api/cabling` liefert Kabel
(L1) und VLANs/MAC-Tabellen (L2), `store/l2_topo` (L2) nutzt Kabel und Spanning Tree,
`api/routing` trägt die Weg-Suche (`PATHS`).

## Entscheid

1. **Die Module des Servers bleiben** – Schlüssel, Guards, Schalter je Mandant, Prüfungen, Rechte
   und API-Pfade unverändert, keine Migration. Gründe:
   - Analyse in L1/L2/L3 zu teilen hiesse Router und Stores teilen und die Schalter übernehmen
     (`analyse` aus → drei Module aus) samt Modul jeder Prüfung – viel Risiko ohne Nutzen für den
     Anwender, der Ansichten sucht, nicht Module schaltet;
   - L4–7 liegt im Kern: ein schaltbares Modul L4–7 änderte die Bedeutung „Kern immer an“ (die
     Regeln sind die Grundlage der Doku);
   - Module beantworten „was läuft bzw. lässt sich je Mandant abschalten“ (ADR 0036), Schichten
     „wo finde ich es“. Darum ist die **Schicht ein Merkmal der Ansicht**, nicht des Moduls.
2. **Schicht je Ansicht** (`ViewEntry.layer`: `l1`, `l2`, `l3`, `l4-7`; ohne = übergreifend) und
   **Stellen** (`ViewEntry.places`): Einträge der Navigation, die einen Reiter einer Ansicht öffnen
   und einer eigenen Schicht angehören (z. B. „BGP“ = Routing, Reiter BGP-Nachbarn). Die Ansicht
   bleibt im Ordner ihres Moduls (ADR 0039); ein ausgeschaltetes Modul blendet auch ihre Stellen
   aus. `tests/test_modules.py` hält die Zuordnung unten fest.
3. **Navigation:** Dokumentation (übergreifend) → L1 · Physik → L2 · Switching → L3 · Routing →
   L4–7 · Firewall & Dienste → Administration. Übergreifendes (Applikationen/Doku, Weg-Suche,
   Wege vergleichen, Findings, Abgleich, Netzwerkdesign) und die Administration (Einstellungen,
   Schreibpfad) bleiben, wo sie sind.

   | Stelle | Schicht | Ansicht (Modul) | Adresse |
   |---|---|---|---|
   | Verkabelung | L1 | Verkabelung (Analyse) | `#l1/verkabelung` |
   | Ports | L1 | Verkabelung, Reiter Ports | `#l1/ports` |
   | Transceiver | L1 | Verkabelung, Reiter Transceiver (neu) | `#l1/transceiver` |
   | Kabelnummern | L1 | Verkabelung, Reiter Kabelnummern | `#l1/kabelnummern` |
   | VLANs | L2 | VLANs (Analyse, neu aus dem Reiter der Verkabelung) | `#l2/vlans` |
   | Spanning Tree | L2 | Spanning Tree (Analyse) | `#l2/spanningtree` |
   | VLAN-Reservierungen | L2 | Reservierungen, Reiter VLANs (Analyse) | `#l2/vlan-reservierungen` |
   | Netze | L3 | Objekte & Inventar, Reiter Subnetze (Kern, nicht für Kunden-Benutzer) | `#l3/netze` |
   | VRF | L3 | Objekte & Inventar, Reiter VRFs (Kern, nicht für Kunden-Benutzer) | `#l3/vrf` |
   | Routing | L3 | Routing (Analyse) | `#l3/routing` |
   | Statische Routen | L3 | Statische Routen (Analyse) | `#l3/statisch` |
   | BGP | L3 | Routing, Reiter BGP-Nachbarn | `#l3/bgp` |
   | AS-Nummern | L3 | AS-Nummern (Analyse) | `#l3/asn` |
   | AS-Reservierungen | L3 | Reservierungen, Reiter AS-Nummern | `#l3/as-reservierungen` |
   | Regeln & Contracts | L4–7 | Regeln & Contracts (Kern) | `#l4-7/regeln` |
   | Objekte & Inventar | L4–7 | Objekte & Inventar (Kern) | `#l4-7/objekte` |
   | NAT / VIPs | L4–7 | NAT / VIPs (Kern) | `#l4-7/nat` |
   | Load Balancer | L4–7 | NAT / VIPs, Typ Load Balancer (NSX ALB, Virtual Server) | `#l4-7/loadbalancer` |
   | VPN-Tunnel | L4–7 | VPN-Tunnel (Analyse) | `#l4-7/vpn` |

4. **Adressen:** Ansichten einer Schicht haben `#<schicht>/<stelle>[?…]`, übergreifende und
   Administration weiter `#<ansicht>`. **Alte Adressen leiten um** (ohne neuen Eintrag im Verlauf):
   `#<ansicht>` auf ihre Stelle, mit `tab` auf die Stelle dieses Reiters, umgezogene Reiter auf die
   neue Ansicht (`#verkabelung?tab=vlans` → `#l2/vlans`); übrige Parameter bleiben. Links innerhalb
   der Oberfläche (Befunde, Doku, Weg-Suche) erzeugen die neuen Adressen; alte in Lesezeichen und
   Doku gelten weiter. API-Pfade ändern sich nicht.
5. **Neues Modul „Wege vergleichen“** (`vergleich`, lesend, je Mandant schaltbar, braucht Analyse
   und Wegsucher; ohne Eintrag an – keine Migration). Ein **L3-Abschnitt** reicht vom Ausgang eines
   Hops (Geräte, Interface) bis zum Eingang des nächsten, dazu in einem Weg der Anschluss von
   Quelle und Ziel (MAC/ARP). Je Abschnitt:
   - **VLAN:** das VLAN des Ausgangs (VLAN-Subinterface), sonst das Access- bzw. Native VLAN des
     Switch-Ports dahinter. Gesucht wird der Weg über die Kabel (CDP/LLDP, `l2_topo`): zuerst nur
     über Kabel, die das VLAN zulassen und die Spanning Tree nicht blockiert.
   - **VLAN erlaubt:** führt kein Weg, der das VLAN überall zulässt, aber einer über die Kabel →
     Abweichung mit den Ports, die es sperren (Access-VLAN anders, nicht unter den erlaubten VLANs
     des Trunks). Ports ohne bekannte VLAN-Konfiguration werden gezählt.
   - **Kabel und Nachbarn:** je Kabel auf dem Weg die Befunde der Verkabelung – CDP/LLDP gegen das
     Kabel in NetBox (`cable_labels`), Speed, Duplex, VLANs, Port-Channel abweichend bzw. nur
     einseitig gemeldet (`cabling`). Ohne Kabel aus NetBox an einem Kabel: gezählt.
   - **Spanning Tree:** führt jeder Weg im VLAN über einen Port, den Spanning Tree blockiert
     (Zustand je VLAN bzw. MST-Instanz aus dem Spanning Tree, sonst die blockierten VLANs am Port;
     err-disabled) → Abweichung mit den Ports. Switches ohne gelesenen Spanning Tree: gezählt.
   - **Nie still:** nicht prüfbar (kein Interface bzw. ACI-Fabric, kein Kabel am Ausgang, kein Weg
     über die Kabel – Switch dazwischen nicht gelesen –, VLAN unbekannt) wird je Grund gezählt und
     ausgewiesen; Tunnel (VPN) und Inter-VDOM-Links sind keine L2-Abschnitte und stehen als solche
     da.
6. **Befunde** (Prüfungen `app/checks/vergleich/`, ADR 0039) über **alle L3-Abschnitte des
   Mandanten** (Next-Hops der Routing-Tabellen, die einem anderen gelesenen Gerät gehören, je
   Paar einmal): `path_vlan_missing`, `path_cable_mismatch`, `path_stp_blocked` (Warnung je
   Abschnitt) und `path_l2_unchecked` (Hinweis je Grund mit Zahl und Beispielen). So deckt der
   Abgleich jeden Weg der Weg-Suche und der Applikationen ab, ohne alle Wege zu rechnen; der
   Anschluss von Quelle und Ziel steht nur in der Ansicht.
7. **Ansicht „Wege vergleichen“** (übergreifend, neben der Weg-Suche): Weg wie in der Weg-Suche
   bzw. die Verbindungen einer Applikation (je Verbindung bzw. alle nacheinander, gezählt) und die
   Abschnitte des Mandanten; je Abschnitt die drei Prüfungen mit Ports und Geräten, je Weg die
   Summe. API `GET /path-compare/trace` (wie `/routing/trace`; `connection=true` wie die
   Verbindungen einer Applikation) und `GET /path-compare/sections`. Weg-Suche und Verbindungen
   verlinken auf die Ansicht.
8. **Doku und Datenstand:** die Doku der Applikation ändert sich nicht (der Vergleich steht nicht
   im Stand, die Ansicht rechnet bei Bedarf) – kein neuer Teil im Fingerabdruck (ADR 0037) und im
   Datenstand (ADR 0049); die Eingaben (Läufe der Switches, Kabel aus NetBox, Kabelnummern,
   Soll-Root) ändern ihn bereits. Die Befunde kommen mit dem Abgleich nach den Läufen.
9. Rein lesend; kein neuer Schreibweg.

## Folgen

- Navigation und Adressen ändern sich sichtbar; alte Lesezeichen und Links gelten weiter
  (Umleitung). Die UI-Tests prüfen die neuen Adressen, die Umleitungen und die Navigation je
  Schicht.
- Ein Modul „L1“ bzw. „L2“ lässt sich je Mandant nicht schalten – geschaltet wird weiter Analyse
  (alle Schichten der Analyse) bzw. Wege vergleichen. Braucht es das später, wird Analyse geteilt
  (eigene ADR, Übernahme der Schalter).
- Neue Ansichten nennen ihre Schicht (`layer`) bzw. ihre Stellen; die Zuordnung steht in dieser
  ADR und in `tests/test_modules.py`.
- Befunde von „Wege vergleichen“ wiederholen zum Teil Befunde der Verkabelung bzw. des Spanning
  Tree – bewusst: hier mit dem Weg, den sie treffen (VLAN, Abschnitt).
