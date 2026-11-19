# ADR 0063 — VPN-Tunnel (IPsec) lesen und in der Weg-Suche

## Status

**Angenommen** (2026-11-17), umgesetzt in 1.19.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-17: „Folgende Prio umsetzen im Automato: 1. VPN integrieren …“ – auf die
Rückfrage: „IPsec reicht, ssl-vpn wird nicht mehr unterstützt“. Ergänzt ADR 0019 (Topologie und
Pfade), ADR 0021 (Verbindungen Ende zu Ende), ADR 0048 (keine stillen Grenzen) und ADR 0049
(Datenstand nach Inhalt).

## Kontext

- Die Weg-Suche kannte IPsec-Tunnel nicht. Eine Route über ein Tunnel-Interface ohne
  Next-Hop-IP (FortiGate im Interface-Modus, typisch für statische Routen über den Tunnel) galt
  als **„Ziel angeschlossen“** – der Weg endete scheinbar erreicht an der Firewall, ohne dass die
  Gegenstelle, deren Firewall oder die Phase-2-Selektoren je geprüft wurden. Bei BGP über IPsec
  (Next-Hop = Tunnel-Adresse der Gegenstelle) ging der Weg zwar weiter, aber ohne Tunnel.
- Die Monitor-Tabelle der FortiGate meldet Routen über Dial-up-Instanzen (`<tunnel>_<n>`,
  `tunnel_parent`); die Instanz wechselt mit jeder Verbindung.
- Selektoren der Phase 2 entscheiden, welcher Verkehr durch den Tunnel geht. Eine Route über den
  Tunnel ohne passenden Selektor ist ein häufiger, im Weg bisher unsichtbarer Fehler.

## Entscheidung

1. **Nur IPsec im Interface-Modus, nur lesend** – mit dem Routing je VDOM (`FortiGateClient.
   routing_tables`): `cmdb vpn.ipsec/phase1-interface`, `cmdb vpn.ipsec/phase2-interface`,
   `monitor vpn/ipsec`. Felder über eine **Positivliste** (nie PSK, PPK, XAUTH, Zertifikats-
   Passwort); Proposals nur als Text. Selektoren als CIDR bzw. Bereich `a-b`, Adress- und
   Gruppen-Namen über die Adressen des VDOMs aufgelöst; nicht Auflösbares (FQDN, unbekannte
   Namen) steht als „nicht auflösbar“ da. Dial-up (`type dynamic`) ist als solcher gekennzeichnet,
   mit den verbundenen Gegenstellen aus dem Monitor (Instanz, Adresse, Netze). Ohne Leserecht
   steht der Teil wie beim Routing in `missing` des Kontexts („IPsec Phase 1 (HTTP 403)“); der
   Zustand ist dann **unbekannt**, nicht „down“, und der Verlauf des Routings vergleicht die
   Routen trotzdem (ADR 0030). SSL-VPN wird nicht gelesen.
2. **Speicher** `topo_vpn_tunnel` je Quellsystem und VDOM, je Lauf mit dem Routing ersetzt
   (`TOPOLOGY_MODELS`, Migration `0089_vpn`). Eine Route gehört zum Tunnel, wenn ein Next-Hop das
   Tunnel-Interface bzw. eine Dial-up-Instanz ist. **Netze hinter dem Tunnel** = Selektoren der
   Gegenstelle, Netze verbundener Dial-up-Gegenstellen und Routen über den Tunnel (ohne
   Default-Route).
3. **Weg-Suche:** der Tunnel ist ein Schritt am Ausgang des Hops (`egress.vpn`: Name, Art,
   Zustand, Gegenstelle, ob das Ziel in den Netzen der Gegenstelle liegt, gelesene Gegenstelle).
   Eine Route über den Tunnel ist nie mehr „angeschlossen“. Weiter geht der Weg:
   - über die Next-Hop-IP, wenn sie einem gelesenen Gerät gehört (BGP über IPsec);
   - sonst bei der **gelesenen FortiGate, der die Adresse der Gegenstelle gehört** – Eingang ist
     ihr Tunnel, dessen Gegenstelle eine eigene Adresse dieser Firewall ist (dann prüfen ihre
     Policies das Tunnel-Interface), sonst ein Tunnel auf dem Interface; ohne passenden Tunnel
     dort mit Hinweis;
   - gehört die Adresse keinem gelesenen Gerät (NAT davor), bei der FortiGate, deren Tunnel als
     Gegenstelle eine eigene Adresse dieser Firewall hat – nur **eindeutig** (mehrere Tunnel auf
     derselben Adresse: über die Selektoren), sonst nicht;
   - sonst **endet der Weg sichtbar an der Gegenstelle** (Ende `vpn`: „weiter über VPN … zur
     Gegenstelle … – kein gelesenes Gerät, der Weg dahinter ist nicht geprüft“). Das Ende zählt
     als erreicht (wie eine Anbindung), deckt kein Selektor das Ziel bzw. ist der Tunnel down,
     steht das am Ende und als Hinweis am Weg. Eine von Hand erfasste Anbindung auf dem
     Tunnel-Interface geht vor (bewusste Grenze, z. B. Partner) – die Weg-Suche bietet „Als
     Anbindung erfassen“ an.
4. **Keine stille Grenze (ADR 0048):** endet eine Verbindung einer Applikation an einer solchen
   Gegenstelle, zählt `app_paths._Cuts` sie (Art `vpn`: Verbindungen und Regeln) – Hinweis in den
   Verbindungen, die Applikation gilt als **unvollständig geprüft** (Befund `app_coverage` mit dem
   Hinweis: FortiGate der Gegenstelle einlesen bzw. Tunnel als Anbindung erfassen).
5. **Doku der Applikation:** Verbindungen (Tunnel je Hop, Zahl „über VPN“, Tunnel mit ihren
   Verbindungen), Netzwerkplan (Tunnel als eigener Knoten zwischen den Firewalls – beide Enden
   eine Kennung, gleich welche Richtung), Word-Export („→ VPN … (Gegenstelle)“) und „Warum dieser
   Weg?“ zeigen den Tunnel.
6. **Befunde** (Prüfung `checks/analyse/vpn.py`, ADR 0039): `vpn_tunnel_down` (Tunnel bzw. eine
   Phase 2 down; nicht Dial-up, dort kommen und gehen die Gegenstellen), `vpn_selector_partial`
   (ein Selektor deckt eine Route über den Tunnel nur teilweise), `vpn_route_no_selector` (Route
   über den Tunnel ohne passenden Selektor). Zustand bzw. Selektoren unbekannt: kein Befund.
7. **Datenstand (ADR 0049):** der Zustand der Tunnel (up/down, verbundene Dial-up-Gegenstellen)
   ist ein eigener Teil `routing.vpn_state` und wirkt über das Gerät; die Konfiguration
   `routing.vpn` wirkt überall – die Gegenstelle kann eine andere FortiGate sein, deren Tunnel den
   Weg erst weiterführt.
8. **Ansicht „VPN-Tunnel“** im Modul Analyse (`GET /vpn/tunnels`, `/vpn/tunnels/{id}`): Liste mit
   Zustand, Gegenstelle (gelesen bzw. nicht), Phase 2 und Routen, Detail mit Selektoren, Routen
   (deckt ein Selektor sie?), Netzen dahinter; mit Kunde nur seine VDOMs.

## Folgen

- Rein lesend; kein neuer Schreibweg. Das Profil des REST-API-Benutzers braucht Lesezugriff auf
  VPN (`vpngrp read`), sonst steht IPsec als „nicht lesbar“ im Lauf, in der Datenqualität und in
  der Ansicht.
- Wege, die bisher an einem Tunnel als „angeschlossen“ endeten, gehen jetzt weiter bzw. enden an
  der Gegenstelle – Verbindungen, Befunde und Doku können sich nach dem Update ändern; Wege zu
  einer fremden Gegenstelle machen die Applikation „unvollständig geprüft“, bis die Gegenstelle
  gelesen bzw. als Anbindung erfasst ist.
- Policy-basierte Tunnel (ohne Interface) und andere Hersteller werden nicht gelesen; eine Quelle
  hinter einem Tunnel beginnt in den Verbindungen am Tunnel (Eingang nach dem Rückweg), in der
  Weg-Suche mit gewähltem Startpunkt.

## Nachtrag 2026-11-18 (ADR 0070): ohne passenden Selektor bzw. mit Tunnel down verworfen

Anwender: „Es soll absolut der Wahrheit entsprechen, wie das Netzwerk in Echt funktioniert.“ Bis
1.20 zählte ein Weg über einen Tunnel als erreicht, auch wenn das Ziel in keinem Phase-2-Selektor
lag bzw. der Tunnel down war (nur ein Hinweis); die Quelle wurde gar nicht verglichen, die
Selektoren aller Phase 2 waren vereinigt.

- Die FortiGate verwirft Verkehr in einen Tunnel, den keine Phase 2 abdeckt („no matching IPsec
  selector, drop“): der Weg endet am Hop als **verworfen** (`end.kind = dropped`,
  `hop.dropped.stage = ipsec`) mit Begründung – wenn **keine einzelne** Phase 2 Quelle **und** Ziel
  in ihren Selektoren hat (bei Dial-up zählen die Netze der verbundenen Gegenstellen für das Ziel).
- Verglichen wird die Quelle nach dem SNAT dieser Firewall (Policy bzw. Central SNAT); ist sie
  danach unbekannt, nur das Ziel.
- Tunnel down (bei Dial-up die Instanz): verworfen. Eine einzelne Phase 2 down bei Tunnel up
  bleibt ein Hinweis (sie kommt bei Verkehr hoch).
- Nicht entscheidbar (Selektor nur teilweise, Selektoren nicht auflösbar): der Weg geht weiter,
  die Stelle ist als „nicht geprüft“ gezählt (`uncertain.vpn`, ADR 0048).
- Ein Tunnel ohne gelesene Phase 2 wird nicht verworfen (nichts zu vergleichen).
