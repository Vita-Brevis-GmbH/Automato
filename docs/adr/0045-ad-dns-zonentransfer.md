# ADR 0045 — AD-DNS als Referenzquelle per Zonentransfer

## Status

**Angenommen** (2026-11-03), umgesetzt in 0.123.0 – zur Abnahme durch den Anwender.

Anwender 2026-11-03: „Da wir bei uns intern keinen Infoblox haben, muss ich den Netbox komplett
nutzen und eventuell DNS Geschichten bei dem AD abholen“ – auf die Rückfrage zum Weg: „Zonen
Transfer ist ok“. Setzt ADR 0044 Entscheid 6 um, ergänzt ADR 0010 (Referenzquellen). Keine
Migration (`source_system.platform` ist ein Text), kein neuer Schreibweg.

## Kontext

- Ohne Infoblox ist NetBox das führende IPAM. Die DNS-Namen stehen aber im AD-DNS – NetBox kennt
  sie oft nicht oder veraltet. Ohne Namen fehlen sie in Objekten, NAT, Weg-Suche, Namen der
  Gegenstellen und Doku.
- Wege, das AD-DNS zu lesen: LDAP (Konto im AD, Einträge als `dnsNode`-Binärdaten), WMI/PowerShell
  (Windows-Konto, nur vom Agenten), DNS-Abfragen je Name (keine Liste) oder **Zonentransfer
  (AXFR)**: Standard-DNS, liefert eine Zone komplett, auf dem DC je Zone pro Adresse freigegeben,
  ohne Konto.
- Ein DNS-Server kennt nur Namen und Adressen – keine Netze, Ranges, VLANs, Standorte.

## Entscheid

1. **Plattform `ad_dns`** als Referenzquelle (`REFERENCE_PLATFORMS`): Adresse = DNS-Server
   (`dns://server[:port]`, eingegeben als Name, IP oder `server:port`), Lesebereich = **Zonen**
   (Pflicht, je Zeile; Forward- und Reverse-Zonen). Keine Zugangsdaten: der DC erlaubt den
   Transfer nur der Adresse von Automato bzw. des Agenten (Zone → Eigenschaften →
   Zonenübertragungen → „Nur an folgende Server“). TSIG gibt es im AD nicht.
2. **Lesen** (`sot_gateway.adapters.dns_zone`, dnspython, ISC-Lizenz): je Zone ein AXFR über
   TCP 53. Daraus je Adresse ein Eintrag: A/AAAA → `a_record`, CNAME auf einen Host der gelesenen
   Zonen → `a_record` des Alias (mit `cname`), PTR der Reverse-Zonen → `ptr_record`; Zone als
   Merkmal. Übergangen: Zonenkopf (im AD die Adressen aller DCs), Dienst-/Standort-Einträge
   (Labels mit `_`), `DomainDnsZones`/`ForestDnsZones`, Platzhalter `*`, alles unterhalb einer
   Delegation. Alle Zonen werden zuerst gelesen, dann geliefert – eine abgelehnte Zone bricht den
   Lauf mit Zone, Code und Hinweis ab (kein halber Stand).
3. **Nur lesend vom Protokoll her:** der Adapter ruft von `dns.query` nur `xfr` auf und kennt
   `dns.update` nicht (Test prüft beides). Damit bleibt NetBox der einzige Schreibweg (ADR 0010).
4. **Immer Anreicherung:** die Rolle „führend“ lehnt die API ab, ein Lauf ohne Rolle wird
   Anreicherung. Im Kontext stehen DNS-Server nach den IPAM-Anreicherungen; Netz-Infos (Netz,
   VLAN, Standort) kommen nie aus dem DNS.
5. **Wirkung:** die DNS-Namen gelten überall, wo Referenzquellen Namen liefern (Objekte,
   NAT/VIP, Weg-Suche, Namen der Gegenstellen, Abgleich der Objekte über die Anreicherung).
6. **Vergleich im Abgleich:** die Anreicherung ist wählbar (z. B. NetBox oder AD-DNS). Gegen einen
   DNS-Server nur DNS-Namen: „DNS-Name abweichend“ (das IPAM hat einen anderen bzw. keinen Namen
   als jeder DNS-Name der IP), „nur im DNS“ (IP fehlt im IPAM). IPs des IPAM ohne DNS-Eintrag sind
   kein Fehler.
7. **Prüfung der Verbindung** (zentral): Name, TCP 53 und je Zone der Anfang des Transfers
   (Freigabe + SOA-Seriennummer); beim Agenten `run-agent.ps1 -Check`.

## Folgen

- Auf dem DC je Zone den Transfer für die Automato-VM bzw. den Agenten freigeben; TCP 53 auf dem
  Weg öffnen (Firewall davor). Docs: `docs/BETRIEB.md` (AD-DNS als Referenzquelle).
- Abhängigkeit `dnspython` im sot-gateway (auch im Agent-Paket, aus PyPI wie httpx).
- Offen: DNS-Namen aus dem AD per NetBox-Sync in NetBox übernehmen (Feld `dns_name`) – wäre eine
  Erweiterung des bestehenden Schreibwegs, eigener Entscheid (entschieden im Nachtrag 1.19.0).
- Verworfen: LDAP (Konto im AD, Binärformat), WMI (nur Windows/Agent, Konto), Abfragen je Name.

## Nachtrag 1.19.0 – DNS-Namen aus dem AD per NetBox-Sync (Version 2 Nr. 15)

Anwender 2026-11-17 (Priorität 7 „DNS aus AD“): „Der NetBox-Sync setzt `dns_name` der bestehenden
IP-Adressen in NetBox aus den A-Records der AD-DNS-Zonen; PTR nur zur Kontrolle (Befund bei
Widerspruch); keine neuen IP-Adressen; abweichender Name in NetBox: Vorschau zeigt beide, übernommen
nur nach Bestätigung.“ Damit ist der offene Punkt unter *Folgen* entschieden. Der Schreibweg bleibt
der NetBox-Sync (Vorschau → Übernehmen, ADR 0010); neu ist, was er schreibt.

### Entscheid

1. **Neuer Abschnitt des Syncs** *DNS-Namen aus dem AD* (`dns`, `store/netbox_sync_dns.py`),
   standardmässig aus. Art `dns_name` (PATCH an `ipam/ip-addresses`).
2. **Nur das Feld `dns_name` bestehender IP-Adressen**, auch an von Hand gepflegten (ohne Tag
   `automato`). Das ist die Erweiterung gegenüber ADR 0010/0031: dort ändert der Sync nur eigene
   Objekte. Der PATCH enthält nur `id` und `dns_name` – kein Tag, kein anderes Feld. Keine neuen
   IP-Adressen: A-Records auf IPs, die NetBox nicht kennt, zählt der Hinweis der Vorschau (und der
   Befund `dns_ip_unknown`).
3. **Zuordnung wie im ganzen Sync** über die Adresse (`_key_of`), je Mandant: AD-DNS-Quellen des
   Mandanten, Ziel-NetBox des Mandanten. Steht eine Adresse mehrfach in NetBox (mehrere VRFs), wird
   nicht geraten (Eintrag „mehrdeutig“). IPs, deren NetBox-Tenant ein anderer Mandant von Automato
   ist, bleiben unberührt (gezählt).
4. **Name je IP:** ein A-Record (bzw. AAAA) → sein Name. Mehrere A-Records → der PTR entscheidet,
   wenn er genau einen davon nennt; sonst kein Vorschlag, nur der Eintrag „mehrdeutig“ mit den
   Kandidaten und dem Grund (kein PTR, PTR nennt keinen bzw. mehrere, Rückwärtszone nicht gelesen).
   Aliase per CNAME zählen nie als Name. Gleich heisst ohne Gross-/Kleinschreibung; trägt NetBox
   bei einer mehrdeutigen IP einen ihrer A-Records, ist das „gleich“.
5. **Leer → setzen, abweichend → nur nach Bestätigung je Eintrag.** Leerer `dns_name` in NetBox:
   Aktion `update`, wird mit „Übernehmen“ geschrieben. Abweichender Name: Aktion `update` mit
   `confirm` – derselbe Weg wie die Kabelnummern (ADR 0031 Nachtrag 1.19.0). Die Vorschau zeigt
   beide Werte; `POST /netbox-sync/plans/{id}/apply` nimmt `confirm` (Liste der IPs bzw.
   Kabel). Nur sie werden überschrieben, alle anderen bleiben in NetBox, wie sie sind. Die
   Abweichungen stehen im Fingerabdruck: ändert sich ein Name nach der Vorschau, wird nichts
   geschrieben. Die Anzahl der Bestätigungen steht im Audit (`dns_confirmed`, neben
   `labels_confirmed`), die Schlüssel am Plan (`confirm`).
6. **Ein Abschnitt entscheidet:** für IPs mit A-Record fällt `dns_name` aus dem Soll der anderen
   Abschnitte weg (Firewall-Objekte). Legt ein anderer Abschnitt die IP im selben Sync erst an,
   bekommt sie den Namen gleich beim Anlegen (ohne eindeutigen Namen keinen, Hinweis). Das
   Netzinventar gibt für Interface-IPs keinen `dns_name` mehr vor (vorher leer). So löscht ein
   späterer Sync ohne diesen Abschnitt den Namen nicht wieder.
7. **Nur wenn diese NetBox das führende IPAM ist** bzw. keines eingelesen ist, wie bei den
   Firewall-Objekten. Ist ein anderes System führend (Infoblox), gehören die DNS-Namen dorthin
   (DDI-Spiegel). Die Vorschau sagt es und schreibt nichts.
8. **Keine stillen Grenzen:** Quelle nicht gelesen, letzter Lauf fehlgeschlagen und Zonen ohne
   Einträge nennt der Hinweis der Vorschau. Zonen ohne Einträge gelten nicht als gelesen.
9. **Befunde** (Prüfungen `app/checks/analyse/dns_*.py`, `store/dns_checks.py`, Grundlage
   `store/ad_dns.py`):
   - `dns_ptr_mismatch` je IP: der PTR nennt keinen ihrer A-Records. Warnung, wenn die IP andere
     A-Records hat bzw. der Name per A-Record auf eine andere IP zeigt; sonst Hinweis.
   - `dns_ip_unknown` je AD-DNS-Quelle: A-Records auf IPs, die die gelesene NetBox nicht kennt.
     Hinweis, nur gezählt.
   - `dns_unchecked` je AD-DNS-Quelle: was nicht prüfbar ist (nicht gelesen, letzter Lauf
     fehlgeschlagen, Zonen ohne Einträge, Rückwärtszone fehlt, PTR ausserhalb der gelesenen Zonen,
     NetBox nicht gelesen).

### Folgen

- Gegenüber dem AD bleibt alles nur lesend (Zonentransfer). Geschrieben wird nur nach NetBox, mit
  dem Schreib-Token der Ziel-NetBox, über die Vorschau.
- Neue Aktion der Vorschau `ambiguous` (mehrdeutig, nie geschrieben). Stehen nur unbestätigte
  Abweichungen an, gibt es nichts zu übernehmen (HTTP 409, „Übernehmen“ in der Oberfläche
  gesperrt, bis etwas bestätigt ist).
- Keine Migration (Befund-Typen als Text).
- Verworfen:
  - Abweichungen ohne Bestätigung überschreiben. Ein von Hand gepflegter Name kann richtig sein.
  - IP-Adressen aus dem DNS anlegen. Ein DNS-Server kennt kein Netz und keine Präfixlänge (Entscheid
    Nr. 4 des ursprünglichen ADR).
  - Den PTR als Quelle des Namens nehmen. Er ist im AD oft veraltet und dient nur zur Kontrolle.
