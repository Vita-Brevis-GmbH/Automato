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

## Nachtrag 1.23.0 (2026-11-20): Tunnel zweier gelesener FortiGates koppeln

Anwender (Test von 1.22.0 in Prod): „Die VPN Verbindungen werden herausgelesen aber nicht
verknüpft, also bleiben die verbundenen Standorte weiterhin getrennt“ – „Beide eingelesen.
Einseitig ist es dynamisch, kann man dies nicht koppeln? Manuell, wenn es dynamisch nicht geht?“

Bis 1.22 fand die Weg-Suche die andere FortiGate nur, wenn die Adresse der Gegenstelle einem ihrer
Interfaces gehörte bzw. (NAT davor) ihr Tunnel eindeutig auf uns zeigte. Eine statische Seite
hinter NAT bzw. eine VIP gegenüber einem Dial-up blieb „Gegenstelle nicht gelesen“; die Wahl des
Tunnels dort nahm bei mehreren Kandidaten still den ersten. Die Topologie kannte keine Tunnel.

Entscheid:

1. **Kopplung als eigener Begriff** (`store/vpn_kopplung.py`): zwei Tunnel gelesener FortiGates
   sind **ein** Übergang. Die Weg-Suche (`vpn.Index.far`) nimmt zuerst das gekoppelte Gegenstück –
   Eingang dort ist sein Tunnel; Dial-up wählt unter mehreren Gekoppelten nach der Adresse bzw.
   den Netzen der verbundenen Gegenstelle, sonst nach den lokalen Selektoren, die das Ziel
   enthalten, nur eindeutig. Ohne Kopplung gilt wie bisher die FortiGate, der die Adresse der
   Gegenstelle gehört – der Tunnel dort bleibt dann offen (nie mehr still der erste), mit dem
   Hinweis „Tunnel dort nicht eindeutig, in ‚VPN-Tunnel‘ koppeln“.
2. **Automatisch** nur, wo belegt und eindeutig (`pair = auto`, `how` aus Sicht des Tunnels):
   `gw` – die statische Seite zeigt auf eine Interface-Adresse der anderen FortiGate, dort zeigt ein
   statischer Tunnel zurück (bzw. hinter NAT: ein statischer Tunnel auf dem Interface mit
   gespiegelten Selektoren) oder ein Dial-up auf diesem Interface hat gespiegelte Selektoren;
   `vip` – dasselbe über eine VIP einer gelesenen FortiGate davor (ohne Port-Weiterleitung bzw.
   mit UDP 500/4500); `reverse` – die eigene Gegenstelle gehört keinem gelesenen Gerät, genau ein
   noch freier Tunnel einer gelesenen FortiGate zeigt auf uns und die Selektoren sind gespiegelt;
   `dialup` – eine verbundene Dial-up-Gegenstelle kommt von einer Adresse der anderen FortiGate,
   deren statischer Tunnel dort eindeutig ist und gespiegelte Selektoren hat. „Gespiegelt“ =
   lokal ↔ Gegenstelle gleich bzw. umfassend (ein Hub mit 0.0.0.0/0). Ein statischer Tunnel hat
   höchstens eine Gegenstelle; mehrere Kandidaten bzw. nicht gespiegelte Selektoren koppeln nicht,
   sie sind **Vorschläge**.
3. **Von Hand** (`vpn_pair`, Migration `0102_vpn_kopplung`): koppeln (`coupled`) bzw. eine
   automatische Kopplung lösen (`separated`), in der Ansicht „VPN-Tunnel“ mit Vorschlägen
   (nicht eindeutige Treffer, gespiegelte Selektoren) und der Wahl eines Tunnels einer anderen
   gelesenen FortiGate. Von Hand geht vor. Laufzeitdaten in Automato über den festen Schlüssel
   (Quellsystem, VDOM, Tunnel) – über die Läufe gültig; ein Ende, das kein Lauf mehr liefert,
   bleibt als verwaist sichtbar und lösbar (nie still gelöscht); mit dem Quellsystem bzw. Mandanten
   gelöscht. Rechte: Operator; für Mandanten-Benutzer „anpassen“ (`auth/tenant_routes.py`,
   ADR 0068). `PUT /vpn/pairs`, `DELETE /vpn/pairs/{id}`, Audit `vpn_pair.set`/`.deleted` – damit
   ändern sich Fingerabdruck und Wege-Speicher (ADR 0037, 0048). Kein Schreibweg zu einem Gerät.
4. **Selektoren und Zustand** gelten unverändert (Nachtrag ADR 0070): ohne passende Phase 2 bzw.
   mit Tunnel down ist der Weg am Ausgang verworfen, gleich ob gekoppelt.
5. **Anzeige:** Weg und Verbindungen nennen die andere Firewall mit ihrem Tunnel als Link
   (`peer.pair`, `peer.open`, Format von `paths.trace` nur erweitert); die Ansicht zeigt Herkunft
   „automatisch“/„von Hand“, Vorschläge, verwaiste Kopplungen; die Topologie hat eine Kante `vpn`
   (Herkunft `derived` bzw. `manual`) – die Standorte hängen zusammen.

Grenzen: Welche Phase 1 eine FortiGate für einen eingehenden Peer wählt, hängt von dessen
Absender-Adresse ab; hinter NAT ist sie nicht gelesen – dort entscheiden die Selektoren, sonst die
Hand. Eingehender Verkehr wird an den Selektoren der Gegenstelle nicht geprüft (nur am Ausgang).
