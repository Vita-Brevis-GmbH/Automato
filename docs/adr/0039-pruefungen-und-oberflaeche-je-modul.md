# ADR 0039 — Findings als steckbare Prüfungen, Oberfläche je Modul

## Status

**Entwurf** (2026-10-29), umgesetzt in 0.110.0 – zur Abnahme durch den Anwender.

Roadmap des Anwenders vom 2026-10-29, Version 1 Nr. 1: „Module in eigene Pakete, Findings als
steckbare Prüfungen, Oberfläche je Modul.“ Gewählt: „Prüfungen + Pakete schrittweise“ – Prüfungen
als Dateien je Modul, die Seite Findings mit Dauer und Anzahl, je Prüfung je Mandant schaltbar
(Audit); in der Oberfläche ein Ordner je Modul, Navigation und Ansichten aus einem Verzeichnis.
Doku und Wege bleiben unverändert.

Ergänzt ADR 0004/0010 (Findings, Abgleich), ADR 0029 (Datenqualität), ADR 0036 (Module) und
ADR 0037 (Stand der Doku). Migration 0068 (`tenant_check`).

## Kontext

- Alle Prüfungen des Abgleichs standen in `store/reconcile.py` (knapp 1 300 Zeilen, jetzt knapp 800) und wurden von
  `reconcile_tenant` fest nacheinander aufgerufen. Eine neue Prüfung hiess: diese Datei ändern;
  welche Prüfung welche Befund-Typen liefert und wie lange sie braucht, war nicht zu sehen.
- Module (ADR 0036) waren je Mandant schaltbar, die Prüfungen nicht: ein Mandant ohne VMware
  bzw. ohne Verkabelung rechnete trotzdem alles.
- Die Oberfläche führte alle Ansichten in `App.tsx` (eine Liste und eine Kette von Bedingungen);
  zu welchem Modul eine Ansicht gehört, stand nur im Verzeichnis des Servers.

## Entscheid

1. **Je Prüfung eine Datei** `orchestrator/app/checks/<modul>/<prüfung>.py` mit
   `CHECK = Check(key, module, title, purpose, types, run, step=None, in_reconcile=True, order)`:
   Schlüssel, Modul nach ADR 0036, Titel und Zweck deutsch/englisch, die Befund-Typen und
   `run(session, tenant_id, cx) -> list[Issue]`. Das Verzeichnis (`app/checks/__init__.py`)
   findet die Dateien selbst (`pkgutil`); der Abgleich ruft alle laufenden Prüfungen nach `order`
   auf. Eine neue Prüfung ist eine neue Datei – `reconcile.py` bleibt unberührt.
   - Gemeinsame Eingaben (Referenzen des IPAM, Präfixe der Namenskonvention) rechnet der
     Abgleich einmal und gibt sie als `Context` mit.
   - **Teil-Durchgänge** (`step`): `zonen` (Speichern einer Zone), `quellen` (Zeitplan, Stand der
     Quellsysteme) und `applikationen` (im Hintergrund mit Zeitbudget, nicht im Abgleich) rechnen
     nur ihre Prüfungen.
   - Eine Prüfung liefert nur Befunde ihrer Typen; die Tests prüfen das (`STRICT`), im Betrieb
     nur eine Warnung im Log.
   - Fünfzehn Prüfungen: Kern (Firewall-Objekte und Namenskonvention, Regeln ohne Quelle/Ziel,
     SNAT-Pool, drei ACI-Prüfungen), Analyse (Regel-Endpunkte im IPAM, IPAM-Abgleich, VMware,
     Topologie, Verkabelung, Verbund, Stand der Quellsysteme), Design (Zonen), Doku
     (Verbindungen der Applikationen). Jeder Befund-Typ hat genau eine Prüfung (ausser
     `invalid_name`: Objekte und Regel-Endpunkte; `manual` hat keine).
2. **Je Mandant schaltbar** (Tabelle `tenant_check`, nur Abweichungen von der Vorgabe „an“):
   `GET /checks` (viewer) und `PUT /checks/{key}` (admin, Audit `check.switched`), Router im Modul
   Findings. Ausgeschaltet läuft die Prüfung nicht; ihre Befunde fallen beim nächsten Abgleich
   weg, eingeschaltet kommen sie zurück (Triage wie bisher über den Schlüssel). Ist das Modul der
   Prüfung aus, läuft sie auch nicht (`effective`). Das Schalten ändert den Fingerabdruck der Doku
   nicht (ADR 0037: Befunde kommen live).
3. **Dauer und Anzahl** des letzten Durchgangs je Mandant und Prüfung hält der Prozess des
   Orchestrators (nach einem Neustart leer, beim Umschalten gelöscht) – keine neue Tabelle.
4. **Oberfläche je Modul:** `web-ui/frontend/src/modules/<modul>/index.tsx` führt die Ansichten
   des Moduls (`VIEWS`: Schlüssel, Modul, Bezeichnung, Symbol, Bereich, Reihenfolge, Rechte,
   `render`); `src/modules/index.ts` ist das Verzeichnis, aus dem `App.tsx` Navigation und Anzeige
   baut. `tests/test_modules.py` prüft, dass Pakete der Prüfungen und Ordner der Oberfläche wie
   ein Modul heissen und jede Ansicht im Ordner ihres Moduls steht.
5. **Schrittweise:** die Prüfungen sind aus `reconcile.py` umgezogen; die übrigen Dateien in
   `app/api`, `app/store` und `src/` bleiben vorerst, wo sie sind, und ziehen um, wenn sie
   ohnehin angepasst werden. Neues kommt gleich in den Ordner seines Moduls (Reiter „Prüfungen“:
   `src/modules/findings/Checks.tsx`).
6. **Nur lesend, kein neuer Schreibweg:** die Prüfungen lesen die gespeicherten Daten; die
   Schaltung ist Konfiguration in Automato.

## Konsequenzen

- Seite Findings mit Reiter „Prüfungen“: Modul, Befund-Typen (Klick filtert die Befunde), wann
  sie läuft, Dauer, Anzahl; Admin schaltet je Mandant, Operator gleicht neu ab.
- Eine neue Prüfung (V1: BGP-Filter je Zone, Juniper; V2: Schichten L1–L7, Spanning Tree) ist eine
  Datei im Paket ihres Moduls, eine neue Ansicht ein Eintrag im Ordner ihres Moduls.
- Für Version 2 (Module nach Netzwerkschichten, Cockpit) ist die Trennung je Modul vorbereitet:
  Prüfungen und Ansichten lassen sich je Modul verschieben bzw. ausliefern.
- Offen: die übrigen Dateien je Modul in Pakete verschieben (`app/<modul>/…`, `src/modules/…`),
  wenn sie ohnehin angepasst werden.
