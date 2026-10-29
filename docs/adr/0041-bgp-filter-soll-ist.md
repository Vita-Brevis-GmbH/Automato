# ADR 0041 — BGP-Filter und Route Control lesen: Soll/Ist je Nachbar

## Status

**Angenommen** (2026-10-29): Stufe 1 in 0.113.0 (abgenommen mit 0.113.1), Stufe 2 in 0.114.0 –
zur Abnahme durch den Anwender.

Rückmeldung des Anwenders zu 0.110 (2026-10-29): „Ich habe in der Wegsuche Backup Wege erhalten,
welche nicht funktionieren würden. Die Firewalls dahinter lernen zwar das Netzwerk auch, aber die
Firewalls davor würden das Netzwerk von der Firewall dahinter niemals lernen, da sie in den Filter
gesperrt wurden.“ Roadmap Version 1 Nr. 3 („Filter im Routing“). Gewählt für 0.113.0: **Filter
lesen + Soll/Ist** – die BGP-Konfiguration der FortiGate und die Route Control der ACI-L3Outs strikt
lesend einlesen, je Nachbar erwartet/erhalten/angekündigt zeigen, Abweichungen als Befund, die
Netze der Filter als Vorschlag für „Netze dahinter“ der Anbindungen. Filter je Zone ableiten
folgt in der nächsten Version (Stufe 2).

Ergänzt ADR 0019 (Routing, BGP), ADR 0020 (Anbindungen), ADR 0034 (L3Out Firewall führend),
ADR 0036 (Module: Analyse), ADR 0039 (Prüfungen je Modul) und ADR 0040 (Netzlisten der
Anbindungen). Migration 0070 (`topo_bgp_object`, `topo_l3out.route_control`).

## Kontext

- Bis 0.112.0 kam BGP nur aus dem Monitor-API der FortiGate (Nachbarn mit Zustand, alle Pfade).
  Die Konfiguration `router bgp` wurde wegen der Passwörter der Nachbarn nicht gelesen – damit
  fehlten die Filter (Prefix-Listen, Route-Maps), die bestimmen, was eine Firewall von einem
  Nachbarn übernimmt und ihm weitergibt.
- 0.112.0 zeigt Backup-Wege nur noch, wenn das Gerät das Netz gelernt hat. Ob die Firewall davor
  das Netz von der dahinter je lernen würde, sagt erst ihr Eingangsfilter bzw. der Ausgangsfilter
  der anderen.
- Beim ACI entscheidet die Route Control eines L3Outs (`enforceRtctrl`, Subnetze der externen EPGs
  mit `import-rtctrl` bzw. `export-rtctrl`), welche Routen übernommen bzw. angekündigt werden;
  `import-rtctrl` wirkt nur, wenn Import am L3Out erzwungen ist.

## Entscheid

1. **Lesen, strikt nur lesend** (Collector, Positivliste):
   - FortiGate je VDOM: `router/bgp` (AS, Router-ID, `network`, `aggregate-address`, eingeschaltete
     `redistribute`, je Nachbar bzw. Nachbar-Gruppe die Filter `route-map-in/-out`,
     `prefix-list-in/-out`, `distribute-list-in/-out`, `filter-list-in/-out` – je auch `…6` – und
     wenige Angaben wie `remote-as`, `shutdown`, `route-reflector-client`; Nachbar-Bereiche für
     dynamische Nachbarn), `router/prefix-list`, `prefix-list6`, `route-map`, `access-list`,
     `access-list6` mit ihren Regeln. **Nie** `password`, `auth-options`, `keychain`.
   - Die Konfiguration je Nachbar hängt am gelesenen Nachbarn (`topo_peer.attrs.config`, bei
     dynamischen Nachbarn die der Gruppe); konfigurierte Nachbarn ohne Sitzung im Monitor stehen nur
     im BGP-Prozess (keine Nachbarn ohne Zustand – Verlauf, Anbindungen und Befunde bleiben).
   - ACI je L3Out: `enforceRtctrl` (fehlt = `export`) und die Subnetze mit `export-rtctrl` bzw.
     `import-rtctrl` (`aggregate` = alle Längen darin) → `topo_l3out.route_control`.
2. **Auswerten wie das Gerät** (`store/bgp_filters`): Prefix-Liste ohne ge/le = genau das Netz,
   `le`/`ge` als Bereich, `any` = alles; Access-Liste = das Netz und alles darin (`exact` = nur das
   Netz); Route-Map = erste passende Regel, eine Regel ohne Bedingung passt immer, keine passt =
   deny; alle Filter einer Richtung müssen erlauben, ohne Filter alles. Bedingungen, die die
   Plattform nicht auswertet (AS-Path, Community, Wildcard), ergeben „unbekannt“, nie „erlaubt“.
3. **Soll/Ist je Nachbar:** **erwartet** = was die Eingangsfilter ausdrücklich erlauben (permit-
   Regeln, auch über `match ip-address` einer Route-Map; „alles“-Regeln zählen nicht als Soll);
   **erhalten** = die BGP-Pfade vom Nachbarn (`learned_from`) mit dem Urteil des Filters;
   **angekündigt** (berechnet, die FortiGate meldet es nicht) = beste BGP-Pfade (nicht vom Nachbarn
   selbst, von iBGP an iBGP nur über Route-Reflector), `network`, `aggregate` und `redistribute`
   (RIB des Protokolls, mit dessen Route-Map) durch die Ausgangsfilter. ACI je L3Out: Import
   (erwartet, nur mit enforce import), erhalten = Routen der VRF über die Peers des L3Outs, Export.
4. **Befunde** (Prüfung `app/checks/analyse/bgp_filter.py`, Modul Analyse, je Mandant schaltbar):
   `bgp_expected_missing` (Filter erlaubt Netze, der verbundene Nachbar liefert nichts davon; ACI:
   Import erzwungen, nichts davon kommt an), `bgp_outside_filter` (erhalten, obwohl der Filter
   sperrt – Konfiguration und Tabelle passen nicht zusammen), `bgp_no_inbound_filter` (Info:
   eBGP-Nachbar ohne Eingangsfilter), `aci_import_not_enforced` (Info: import-rtctrl ohne enforce
   import – wirkt nicht).
5. **Vorschlag „Netze dahinter“:** für die Next-Hops einer Anbindung die Netze, die die
   Eingangsfilter der Nachbarn mit dieser IP bzw. die import-rtctrl-Subnetze der L3Outs mit diesem
   Peer erlauben (ohne „alles“ und Default-Route) – übernehmen wie beim Vorschlag aus dem Routing.
6. **Oberfläche:** Routing → BGP-Nachbarn mit Spalte „Filter (ein / aus)“ und „Soll/Ist“, darüber
   die gelesene BGP-Konfiguration je Firewall; L3Outs mit Spalte „Route Control“ und „Soll/Ist“;
   Anbindung mit „aus den BGP-Filtern“. API `GET /routing/bgp/config`, `/peers/{id}`,
   `/l3outs/{id}`, `/filtered` (viewer, Kunden-Ausschnitt wie die Listen).

## Folgen

- Das Read-only-Profil der FortiGate braucht Lesezugriff auf **Router** (Konfiguration, nicht nur
  Monitor); fehlt er, steht „BGP-Konfiguration“ in der Lauf-Meldung als nicht lesbar und die
  Soll/Ist-Sicht sagt „nicht gelesen“.
- Der Verlauf des Routings (ADR 0030) vergleicht die Filter noch nicht – Änderungen an
  Prefix-Listen erscheinen erst über ihre Wirkung (erhaltene Pfade).
- Kein neuer Schreibweg; alles Gelesene bleibt beim Quellsystem.

## Stufe 2 (0.114.0)

Anwender 2026-10-29 („go 114“, gewählt: Backup-Wege prüfen und das Soll aus **allen** Zonen, die
über den Nachbarn erreicht werden):

7. **Backup-Wege gegen die Filter** (`paths.Graph.backups`, `bgp_filters.Filters.blocked`): ein
   BGP-Backup vom Nachbarn X gilt nur, wenn der Eingangsfilter des Geräts von X das Netz erlaubt
   und – ist X eine gelesene FortiGate mit einem Nachbarn auf dieses Gerät – deren Ausgangsfilter
   ebenso. Sonst kein Backup-Weg, sondern der Hinweis „Backup an … über X verworfen: Eingangsfilter
   … vom Nachbarn X sperrt …“ (FortiGate mit soft-reconfiguration führt solche Pfade in der
   BGP-Tabelle, übernimmt sie aber nie). Unbekannt (AS-Path, Community) verwirft nicht.
8. **Soll je Nachbar aus den Zonen** (`store/bgp_zones`, Analyse): Zone der Firewall (Geräte der
   Zone), Zone am Nachbarn (gelesenes Gerät mit seiner IP, sonst der Übergang mit seinem Netz bzw.
   dem Interface dorthin), **Zonen dahinter** = alle, deren kürzester Weg im Zonenplan über die Zone
   am Nachbarn führt. Soll = ihre Netze (wie erfasst und abgeleitet, grössere umfassen kleinere)
   `permit … le 32`, Ausschlüsse davor `deny … le 32`, je Familie eine Prefix-Liste
   `PL_<Zone am Nachbarn>_IN`.
9. **Vergleich** mit den Eingangsfiltern: Netz der Zonen gesperrt („fehlt im Filter“), Ausschluss
   erlaubt, Regel ausserhalb der Zonen bzw. „alles“ („zu offen“), ohne Eingangsfilter. Befund
   `bgp_zone_mismatch` (Info, Prüfung `app/checks/analyse/bgp_zonen.py` im Teil-Durchgang „Zonen“ –
   neu nach jedem Speichern einer Zone).
10. **CLI-Vorschlag** (FortiOS `config router prefix-list`/`prefix-list6`, ohne Filter am Nachbarn
    mit `set prefix-list-in`) zum Kopieren im Soll/Ist des Nachbarn – nur Text, Automato schreibt
    nichts auf die Firewall. API `GET /routing/bgp/peers/{id}/zones`.

Offen: Ausgangsfilter je Zone (was eine Firewall wem ankündigen soll – zum Provider meist nicht die
internen Netze), ACI import-rtctrl aus den Zonen und der Vergleich im Verlauf des Routings.
