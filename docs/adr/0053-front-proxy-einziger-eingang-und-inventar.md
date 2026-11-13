# ADR 0053 — Front-Proxy als einziger Eingang der Domain, Inventar fürs Cockpit

## Status

**Angenommen** (2026-11-13), umgesetzt in 1.10.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-13 beim Dev-Aufbau mit zwei VMs (Automato-VM mit Front-Proxy, Magister-VM mit
NetBox-Host): „Die Magister Webseiten sollten doch über den Front Proxy reinkommen. Ich schlage
vor, dass wir das System nun erweitern, damit wir einen schlauen Stand haben und dann umstellen
und testen“. Auf die Rückfrage gewählt: „Übrige Namen an Magister“, „Eine Prüfung für alles“,
„Cockpit erfasst selbst“.

## Kontext

- Der Front-Proxy (ADR 0044 Nachtrag, 0050, 0051) bedient unter `AUTOMATO_FRONT_DOMAIN` die
  Startseiten und Werkzeuge der Kunden; jeden anderen Namen der Domain beantwortet er mit
  „Unbekannte Adresse“ (404).
- Im Dev teilen sich Front-Proxy und Magister-Kundenseiten eine Domain. Zeigte der Wildcard der
  Domain auf den Front-Proxy, waren die Magister-Seiten weg; zeigte er auf Magister, brauchte
  jede Adresse des Front-Proxys einen eigenen DNS-Eintrag. Beides fiel erst im Browser auf.
- Magister kann hinter einem Proxy laufen (`PLATTFORM_WAF` → `MAGISTER_TRUSTED_PROXIES`, echte
  Absenderadresse aus `X-Forwarded-For`).
- Das Cockpit (vitabrevis-cockpit, ADR 0003/0004 dort) zeigt alle Instanzen mit Ampel, musste sie
  aber von Hand erfassen – obwohl der Front-Proxy die Topologie schon kennt.

## Entscheidung

1. **Übrige Namen an die Plattform dahinter** (`AUTOMATO_FRONT_UEBRIGE=<ip>[:port]`, CA in
   `AUTOMATO_FRONT_UEBRIGE_CA`): der Auffang-Eintrag `*.<domain>` leitet weiter statt 404. Name
   und SNI bleiben (die Plattform erkennt ihre Kunden am Namen), die Sperre nach Absender gilt wie
   für Kunden-Adressen, Kopfzeilen der Anmeldung werden entfernt, eigene Kopfzeilen der Antwort
   setzt der Front-Proxy dort nicht (die Plattform setzt ihre selbst). Ohne Einstellung bleibt es
   bei „Unbekannte Adresse“. Der Wildcard der Domain zeigt dann auf den Front-Proxy – ein
   Eingang für alles.
2. **Eine Prüfung für alles** (`front.sh pruefen`, nur die Adressen: `front.sh adressen`): je
   Adresse, ob der Proxy sie kennt, das Zertifikat sie abdeckt und sie im DNS auf diese VM zeigt;
   der Wildcard der Domain (auf diese VM ohne `AUTOMATO_FRONT_UEBRIGE` = Warnung); die Ziele aus
   dem Proxy-Container (Automato, Magister, CMDB) bzw. von der VM (übrige Namen, NetBox je Kunde).
3. **Inventar fürs Cockpit** (`GET /inventory`, nur lesen): der Front-Proxy schreibt seine
   Adressen und Ziele nach `topologie.json` (Ordner des Host-Agenten, ADR 0050), der Orchestrator
   gibt sie mit Version, Umgebung und Keycloak-Issuer heraus – geprüft und gekürzt, ohne
   Geheimnisse und Fachdaten. Nur mit eigenem Token (`AUTOMATO_COCKPIT_TOKEN`, Bearer,
   `deploy/aufbau.sh cockpit-token`), nie mit der Anmeldung einer Person; ohne Token gibt es den
   Endpunkt nicht (404). Das Cockpit holt, Automato schreibt nichts ins Cockpit.

4. **Automato selbst an die NetBox seiner Kunden** (Anwender 2026-11-13: „das Konstrukt bitte
   weiter bauen“): ohne WAF geht der NetBox-Sync über den Front-Proxy auf derselben VM. Die
   Docker-Netze von Orchestrator, Worker und Proxy (aus den laufenden Containern) dürfen an `-nb`
   die API mit Token benutzen – vor der Sperre nach Absender und dem Zugriff je Kunde, ohne
   Kopfzeilen der Anmeldung. Zusätzliche CAs für diese Wege liegen in `deploy/trust/` statt in
   einer Override-Datei.

## Folgen

- DNS im Dev und in der Produktion: ein Wildcard der Kunden-Domain auf den Front-Proxy (bzw. die
  WAF davor); Magister trägt den Front-Proxy als `PLATTFORM_WAF`.
- Läuft Magister mit Let's Encrypt je Kundenname, endet TLS jetzt am Front-Proxy: dessen
  Zertifikat muss die Namen abdecken (Wildcard der Domain). Magister mit eigener ACME-Ausstellung
  hinter dem Front-Proxy ist nicht Teil dieses Entscheids.
- Neue Leseschnittstelle für ein anderes System: Inhalt begrenzt (Namen, Adressen), Token je
  Installation, ersetzbar durch erneutes `cockpit-token`.
- Vertrag im Cockpit: `docs/vertraege/inventar.md` (v1); neue Felder nur optional.
