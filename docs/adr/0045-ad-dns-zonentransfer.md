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
  Erweiterung des bestehenden Schreibwegs, eigener Entscheid.
- Verworfen: LDAP (Konto im AD, Binärformat), WMI (nur Windows/Agent, Konto), Abfragen je Name.
