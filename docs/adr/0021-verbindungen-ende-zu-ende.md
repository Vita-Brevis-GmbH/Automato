# ADR 0021 — Verbindungen Ende zu Ende in der Applikations-Doku

## Status

Entwurf (2026-10-23), erste Fassung in 0.45.0, Befund und Export in 0.59.0. Baut auf ADR 0012 (Applikation), ADR 0019
(Weg Hop für Hop, Regeln und NAT auf dem Weg, ACI zwischen VRFs) und ADR 0020 (Verbund als ein
Hop) auf. Nur lesend.

## Kontext

Die Applikations-Doku listet die Regeln je Gerät: je Firewall-Policy und je ACI-Contract eine
Zeile. Geht eine Verbindung über das ACI und mehrere Firewalls, sind das mehrere Zeilen, und
ob sie zusammen einen Weg öffnen, sieht man nicht.

Wunsch des Anwenders (2026-10-23):

- eine Zeile je Weg, wenn mehrere Firewalls und das ACI hintereinander stehen;
- im Detail, welche Contracts und Policies dort gelten und welche Objekte sie nennen;
- Regel und Weg abgleichen: Eine Regel zeigte Zugriff auf ein System in einem anderen Tenant,
  die Weg-Suche sperrte ihn (Befund 0.43.2, Ursache in 0.44.0 behoben). Solche Abweichungen
  sollen sichtbar werden.

## Entscheidung

### Verbindung

- Eine Verbindung ist Quelle → Ziel mit Dienst. Sie wird aus den Treffern der Doku abgeleitet,
  nicht von Hand gepflegt.
- **Eigene Seite:** ein System der Applikation (VM, Server, sonst die EPG mit der IP ihrer
  ersten VM bzw. ihres ersten Endpunkts) mit seiner IP im Objekt der Regel.
- **Gegenseite:**
  - Firewall-Regel: ihre Objekte als IP-Bereiche aus dem Regelindex. Ein Host bzw. ein Netz
    gilt als Ganzes (ADR 0019, 0.39.0); ein Bereich, der kein Netz ist, wird mit seiner ersten
    Adresse geprüft. Höchstens 6 je Regel und Seite.
  - Contract: die Gegenstellen im Scope (`parties`, 0.43.2). Eine EPG wird mit der IP ihrer
    ersten VM (Portgruppe) bzw. ihres ersten Endpunkts geprüft, sonst mit ihrem Subnetz. Eine
    externe EPG zählt mit ihren Subnetzen ohne 0.0.0.0/0.
  - Gegenstelle `any`, vzAny und Preferred Group sind unspezifisch. Sie bleiben Regeln ohne
    Verbindung. FQDN und ISDB sind nicht als IP prüfbar und stehen als Hinweis da.
- **Dienste:** je Regel höchstens drei, tcp/udp mit dem ersten Port. „Alle Dienste“ wird ohne
  Dienst geprüft.
- **Zusammenfassen:**
  - Alle Systeme einer EPG sind für einen Contract eine Verbindung, geprüft mit dem ersten.
  - Gleiche Quelle, gleiches Ziel und gleicher Dienst aus mehreren Regeln ergeben eine Zeile.
  - Ein Contract zu einer (externen) EPG, geprüft mit ihrem Subnetz, gehört zur Verbindung
    derselben Seite zu einem Host darin.
- Höchstens 120 Wege je Applikation, sonst ein Hinweis.

### Weg und Urteil

- Gleich wie die Weg-Suche (`paths.trace`): Routing, Policy-Routen, NAT, Verbund als ein Hop,
  ACI zwischen VRFs. Graph und geladene Policies bzw. Contracts werden für alle Verbindungen
  einer Applikation einmal geladen (`Graph`, `policy_check.Checks`). Es zählen nur die aktiven
  Wege, keine Backup-Wege.
- Ist die Quelle an keinem gelesenen Gerät angeschlossen (Internet, Partner), beginnt der Weg auf
  dem Gerät der Regel: FortiGate-VDOM bzw. VRF der EPG.
- Je Hop das Urteil mit der entscheidenden Policy bzw. dem Contract, den möglichen davor und den
  Objekten (Quelle, Ziel, Dienste), Ein- und Ausgang, NAT und Leak. Je Verbindung das
  Gesamturteil des Wegs; bei mehreren aktiven Wegen (ECMP) mit verschiedenem Urteil „abhängig“.

### Abgleich Regel ↔ Weg

Je Regel der Doku, aus der die Verbindung stammt:

| Zustand | Bedeutung |
|---|---|
| entscheidet | sie ist auf dem Weg die erste sichere Regel ihres Geräts |
| möglich | sie kann greifen, aber nicht alles ist prüfbar |
| greift nicht | ihr Gerät liegt auf dem Weg, aber vorher entscheidet eine andere bzw. sie passt nicht |
| nicht auf dem Weg | ihr Gerät (bzw. VDOM) liegt nicht auf dem Weg |
| nicht prüfbar | kein Weg bekannt (Routing nicht gelesen, Quelle an keinem Gerät) |

**Abweichung:** Eine erlaubende Regel, deren Verbindung der Weg sperrt, die nicht auf dem Weg
liegt bzw. dort nicht greift, oder ein erlaubter Weg, der vor dem Ziel endet. Die Liste zeigt
Abweichungen zuerst.

### Zugriff

- `GET /applications/{id}/connections`, Rolle viewer.
- Nicht für Kunden-Benutzer (nicht in der Freigabeliste, ADR 0018): Der Weg zeigt die
  Infrastruktur.

## Folgen

- Die Doku sagt nicht nur, welche Regeln es gibt, sondern ob sie zusammen einen Weg öffnen.
- Schwächen der Ableitung: ein System statt aller einer EPG bzw. eines Netzes, nur der erste
  Port eines Dienstes, nur der erste aktive Weg im Detail. Die Weg-Suche (Link je Verbindung)
  zeigt den ganzen Weg mit allen Varianten.
- Offen:
  - Regeln einer Applikation direkt zuordnen;
  - Abhängigkeiten zwischen Applikationen.

## Ergänzung 0.59.0 — Abweichung als Befund, Export und Druck

- **Befund `app_connection_mismatch`** (Gegenstand Regel, `store/reconcile._app_issues`): je
  erlaubender Regel und Applikation, deren Verbindungen der Weg nicht öffnet (`app_paths.deviates`:
  Weg sperrt, Regel nicht auf dem Weg, greift dort nicht bzw. erlaubter Weg endet vor dem Ziel –
  wie die Abweichung der Verbindung). Gegenstand „<Regel> · Applikation <Name>“, im Detail die
  Applikation und bis zu 10 Verbindungen (Quelle, Ziel, Dienst, Urteil, Zustand der Regel,
  Begründung). Der Befund gehört über seine Regel zum Kunden (ADR 0018) und erscheint in der
  Applikations-Doku nur bei seiner Applikation.
- **Wann:** nach jedem Lauf und nach dem Abgleich – erst nach dem Regelindex, eigener Schritt
  (`refresh_app_findings`), damit der allgemeine Abgleich diese Befunde nicht anfasst und die
  Triage bleibt; nach dem Löschen eines Quellsystems und bei Änderungen am Verbund; beim
  Anlegen, Ändern, Ergänzen und Löschen einer Applikation nur für sie. Graph und Policies werden
  für alle Applikationen einmal geladen. Fehler einer Applikation lassen den Lauf nicht
  scheitern (ihre Befunde bleiben).
- **Zeitbudget:** nach einem Lauf höchstens `AUTOMATO_APP_FINDINGS_SECONDS` (Standard 120 s, 0 =
  ohne Grenze); die übrigen Applikationen behalten ihre Befunde und kommen beim nächsten Mal
  zuerst dran.
- **Export und Druck:** der Markdown-Export der Applikations-Doku enthält „Verbindungen Ende zu
  Ende“ (eine Zeile je Verbindung mit dem Weg und der entscheidenden Regel je Hop, Urteil und
  Abweichung); der Druck zeigt Weg und Abweichung ausgeschrieben. Nicht für Kunden-Benutzer.
