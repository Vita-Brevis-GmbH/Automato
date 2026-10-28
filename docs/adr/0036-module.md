# ADR 0036 — Module: Verzeichnis, Grenzen, je Mandant schaltbar, Schreibpfad getrennt

## Status

**Entwurf** (2026-10-28), erste Stufe in 0.107.0 – zur Abnahme durch den Anwender.

Rückmeldung des Anwenders vom 2026-10-28:

- „Bitte die Modularität besser aufbauen, wir brauchen viel zu viel Entwicklungszeit. Mehr
  modularisieren und Elemente mit Schnittstellen besser bauen, dass wir die kleinen Module
  einfacher anpassen können … Es muss auch möglich sein, dass wir die Elemente dann wieder
  verwenden können. Zum Beispiel beim schreibenden Pfad, um zu sehen, ob das geschrieben wurde,
  was wir in Auftrag gegeben haben, oder dass wir das Routing anpassen können und gewarnt werden,
  wenn wir was Falsches unternehmen.“
- „Die Module, welche wir haben, auch die Worker, welche die Daten auf den Systemen abholt, sollen
  immer starr getrennt bleiben von dem schreibenden Pfad. So können wir später beim Schreiben auf
  die Systeme danach alternativ nachfragen, ob dies genau so umgesetzt wurde. Ich würde ganz klar
  die Module Worker, Gateway, Doku-Modul, Wegsucher, Findings und Analyse erstellen. Damit man die
  auch modular deaktivieren kann. Zudem soll das Cockpit nun langsam abgespalten werden, mit den
  Vorgaben und Basis-Settings.“
- Zonen: „Eigenes Modul, wenn es sauber trennbar ist“; Schaltung: „Je Mandant wählbar“.

Ergänzt ADR 0001 (Gesamtarchitektur), ADR 0002 (Adapter-Pattern) und ADR 0010 (nur nach NetBox
schreiben). Migration `0066` (`tenant_module`).

## Kontext

- Bis 0.106.0 lagen Orchestrator-API und -Store flach nebeneinander (29 API-, 84 Store-Dateien);
  welche Datei zu welcher Fachfunktion gehört, stand nur in Kommentaren. Jede Änderung zog sich
  durch viele Dateien, Tests liefen über Ansichten, die es nicht mehr brauchte (Doku je System).
- Der Schreibweg (NetBox-Sync, `sot_gateway.adapters.netbox_writer`) war bereits getrennt
  (Nur-Lese-Schutz der Lese-Clients, ADR 0010) – aber nicht als Regel prüfbar.
- Ein Mandant braucht nicht jede Funktion (z. B. ohne ACI keine Verbindungen, ohne NetBox kein
  Schreibpfad); ausblenden ging bisher nicht.

## Entscheid

1. **Module im Code** (`orchestrator/app/modules.py`): je Modul Schlüssel, Titel, Zweck, Art
   (Basis, lesend, schreibend), Abhängigkeiten, die Dateien in `app/api` und `app/store`, die
   Ansichten der Oberfläche und weitere Pakete. Jede Datei gehört **genau einem** Modul.

   | Modul | Art | Inhalt |
   |---|---|---|
   | Kern | Basis | Mandanten, Quellsysteme, Regeln & Contracts, Objekte, NAT/VIPs, Kunden, Benutzer, Routing-Daten |
   | Einstellungen | Basis | Vorgaben und Basis-Settings: Namensprofile, Kategorien, Grundschema, Module je Mandant – Vorstufe des Cockpits |
   | Worker | lesend | Collector, Agenten, Läufe, Zeitplan (`collectors`) |
   | Gateway | lesend | Referenzquellen IPAM/DNS (Infoblox, NetBox lesen, `sot_gateway` Lese-Adapter) |
   | Analyse | lesend | Ansicht Routing, Verkabelung, Abgleich IPAM, Topologie, Datenqualität |
   | Wegsucher | lesend | Weg-Suche, Verbindungen Ende zu Ende (Policies, Contracts je Hop) |
   | Findings | lesend | Befunde mit Triage, Befunde der Applikationen im Hintergrund |
   | Doku | lesend | Applikationen und ihre Doku nach Kategorien, Export |
   | Design | lesend | Netzwerkdesign: logische Zonen, Zonentyp, Geräte, Matrix, Zonenplan |
   | Schreibpfad | schreibend | einziger Schreibweg: NetBox-Sync (Vorschau → Übernehmen), braucht das Gateway |

   Die Zonen sind sauber trennbar (eigene API `/zones`, eigene Store-Dateien) – daher Design als
   eigenes Modul. Die Routing-Daten gehören zum Kern (sie nutzen Wegsucher, Design und Analyse);
   die Ansicht Routing ist Analyse.
2. **Grenzen prüfbar** (`orchestrator/tests/test_modules.py`, läuft in der CI):
   - jede Datei in `app/api` und `app/store` ist genau einem Modul zugeordnet – eine neue Datei
     ohne Modul lässt den Test scheitern;
   - **nur der Schreibpfad** importiert `sot_gateway.adapters.netbox_writer`, `store/netbox_sync`
     bzw. `api/netbox_sync` (`main.py` bindet nur den Router ein);
   - die Collector-Pakete und die Lese-Adapter des Gateways importieren nichts vom Orchestrator
     und nichts vom Schreibpfad.

   Damit kann der Schreibpfad die lesenden Module nutzen (Nachkontrolle: nach dem Schreiben über
   den Lesepfad prüfen, ob es so umgesetzt ist), umgekehrt nie.
3. **Je Mandant schaltbar** (Tabelle `tenant_module`, nur Abweichungen; ohne Zeile an):
   Fachmodule ein bzw. aus; Kern und Einstellungen immer an. Ein Modul mit einer ausgeschalteten
   Abhängigkeit ist auch aus (Schreibpfad ohne Gateway); einschalten geht erst, wenn die
   Abhängigkeit an ist (422).
   - **Aus heisst:** die Navigation blendet die Ansichten aus (Karten in der Doku: Verbindungen,
     Befunde), die API antwortet 403 mit `module_disabled` (Router-Abhängigkeit `modules.guard`;
     Mandant aus `tenant_id`, dem Objekt im Pfad oder dem einzigen des Benutzers), keine Arbeit im
     Hintergrund (Worker: keine Läufe, auch nicht nach Zeitplan; Gateway: keine Referenzquellen;
     Findings bzw. Wegsucher: keine Befunde der Applikationen). Die gelesenen Daten bleiben.
   - API `GET /modules?tenant_id=` (viewer), `PUT /modules/{key}?tenant_id=` (admin, Audit
     `module.switched`); Oberfläche Mandanten & Standorte → Karte **Module**.
4. **Schritt für Schritt:** Diese Stufe ordnet zu und prüft, ohne Dateien zu verschieben (keine
   Änderung an der Fachlogik). Verschieben in Pakete je Modul, schmale Schnittstellen zwischen den
   Modulen und weitere Regeln (z. B. Doku nutzt den Wegsucher nur über `app_paths.build`) folgen,
   wo sie eine Änderung erleichtern.
5. **Kein neuer Schreibweg.** Die Schaltung ist Konfiguration in Automato.

## Konsequenzen

- Neue Dateien bekommen beim Anlegen ihr Modul (sonst scheitert die CI); `CLAUDE.md` nennt die
  Regel.
- Ein ausgeschaltetes Modul spart Arbeit (Läufe, Befunde der Applikationen) und Ansichten; die
  Daten anderer Module bleiben gleich.
- Für das Cockpit (Mehrmandanten, eigene ADR) sind die Basis-Settings je Mandant der erste
  Baustein: Module, Namensprofil, Namen der Gegenstellen, Lese-Audit.
- Offen (ROADMAP): Schreibpfad mit **Nachkontrolle** über den Lesepfad (geschrieben = gelesen?),
  **Warnung vor Routing-Änderungen** (Wegsucher als Prüfer eines geplanten Zustands), Findings als
  steckbare Prüfungen, Doku im Hintergrund vorrechnen (siehe ROADMAP „Leistung“), Oberfläche je
  Modul aufteilen.
