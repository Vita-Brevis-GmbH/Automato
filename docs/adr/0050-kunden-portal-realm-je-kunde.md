# ADR 0050 — Kunden-Portal in der Oberfläche, Host-Agent, Realm je Kunde

## Status

**Angenommen** (2026-11-10), umgesetzt in 1.3.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-10: „Ich möchte das Erstellen und Verwalten von Kunden im Cockpit haben“ und
„Ich möchte für das Login jeweils ein eigener Realm erstellen, damit die Kunden mit dem Login von
Azure oder LDAPS verwenden können. Dazu möchte ich, dass der erste Kunde wir selber sind und der
Realm auch teilen mit dem Kunden“. Auf den Vorschlag (Stufe 1 in Automato, Anwenden über einen
Host-Agenten, Realm je `-doc`-Adresse): „1.3.0 so bauen“. Ergänzt ADR 0044 (Plattform für viele
Kunden, Nachtrag Front-Proxy), ADR 0018 (Kunden-Benutzer) und ADR 0007 (Keycloak). Das Cockpit
(ADR 0014, Version 2) übernimmt diese Daten später.

## Kontext

- Seit 1.2.0 legt `deploy/front/front.sh` Startseiten und Weiterleitungen je Kunde an – von Hand
  auf der VM. Der Anwender will Kunden in einer Oberfläche pflegen.
- Automato nahm Anmeldungen aus genau einem Keycloak-Realm an (bekannte Einschränkung von 1.0.0).
  Kunden sollen sich mit ihrem eigenen Verzeichnis (Azure/Entra, LDAPS) anmelden; das geht sauber
  nur mit einem Realm je Kunde, dessen Identity-Provider bzw. User Federation der Kunde vorgibt.
- Eine Weboberfläche darf keinen Root- bzw. Docker-Zugriff auf die VM bekommen (Container
  gehärtet seit 1.0.0).

## Entscheid

1. **Kunden-Portal** (`portal_customer`, Administration → Kunden-Portal, nur Admin): Kurzname,
   Anzeigename, Werkzeuge (`-doc`, `-um`, `-cmdb`; NetBox immer), weitere Kacheln, Realm, Mandant,
   Kunden (ADR 0018) und die Standard-Rolle für Benutzer des Realms. Prüfungen wie in `front.sh`
   (Kurzname als DNS-Label, reservierte Namen und Endungen, Kacheln nur `https://`). Jede Änderung
   im Audit.
2. **Host-Agent statt Zugriff aus dem Container:** Der Orchestrator schreibt nach jeder Änderung
   nur den Soll-Stand (`soll.json`) in einen eigenen Ordner (`deploy/front/state`, im Container
   `/var/lib/automato-front`, `AUTOMATO_FRONT_STATE_DIR`). Auf der VM beobachtet systemd die Datei
   (`automato-front-sync.path`, dazu ein Timer alle 10 Minuten) und ruft `front.sh sync` auf: Der
   prüft alles noch einmal wie bei `kunde`/`link` (Vertrauensgrenze), schreibt die Kunden mit
   `QUELLE=oberflaeche`, entfernt nur eigene, erzeugt die Einträge, lädt den Proxy neu und meldet
   das Ergebnis mit der Prüfsumme des Soll-Stands in `status.json`. Die Oberfläche zeigt, ob der
   Front-Proxy aktuell ist. Von Hand angelegte Kunden bleiben unberührt; Kunden aus der Oberfläche
   ändert der Befehl nicht. Eingerichtet mit `front.sh agent`.
3. **Realm je Kunde:** Die Oberfläche fragt `/auth/config?host=<adresse>`; unter
   `<kurzname>-doc.<domain>` liefert der Orchestrator den Realm dieses Kunden. Er nimmt Tokens des
   eigenen Realms (wie bisher) und der Realms aus dem Portal an – Issuer und JWKS je Realm, sonst
   abgelehnt.
4. **Ausschnitt nur aus dem Portal:** Benutzer eines Kunden-Realms sind immer Kunden-Benutzer –
   Mandant und Kunden aus dem Portal, nie aus dem Token, nie Admin, keine Zuordnung über
   `customer_user` (ihre Kennungen stammen aus einem fremden Verzeichnis). Die Kunden-Rolle darf
   der Realm melden (`customer_role` bzw. Realm-Rollen), sonst gilt die Standard-Rolle. Ohne
   Mandant sieht der Benutzer nichts (fail-closed).
5. **Gemeinsamer Realm:** Ist der Realm eines Kunden der Realm von Automato selbst (der Betreiber
   als erster eigener Kunde), gilt die bisherige Anmeldung mit allen Rollen – interne Benutzer und
   dieser Kunde teilen den Realm.
6. **Was (noch) von Hand bleibt:** NetBox-Instanz (`netbox-host.sh neu`) und Keycloak-Realm
   (`deploy/keycloak/make_realm.py`, Import in der Admin-Konsole) – die Oberfläche zeigt die
   Befehle je Kunde. Automato schreibt nicht nach Keycloak (kein neuer Schreibpfad); ein späteres
   Anlegen über die Admin-Schnittstelle braucht eine eigene ADR und Freigabe.

## Folgen

- Kunden lassen sich ohne Anmeldung auf der VM anlegen und ändern; der Front-Proxy folgt innert
  Sekunden (Pfad-Einheit) bzw. spätestens nach 10 Minuten (Timer).
- Der Orchestrator schreibt in genau einen zusätzlichen Ordner; Dateisystem sonst weiter nur
  lesend.
- Ein kompromittierter Kunden-Realm kann höchstens den eigenen Ausschnitt mit der höchsten
  Kunden-Rolle (`customer_admin`) erreichen – nie Verwaltung, nie fremde Kunden.
- Realms werden kurz gemerkt (30 s je Prozess); eine Änderung im Portal leert den Speicher sofort.
- Offene Punkte für Version 2 (Cockpit): NetBox-Instanz über einen Agenten auf dem NetBox-Host,
  Keycloak-Realm über die Admin-Schnittstelle (eigene ADR), Anmeldung an der NetBox des Kunden über
  seinen Realm (OIDC).
