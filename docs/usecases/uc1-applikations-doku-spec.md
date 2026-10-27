# UC1 — Applikations-Doku: Ausgabe-Spezifikation

> Zielformat der Applikations-Dokumentation (entspricht den Kunden-Tabellen, bisher in
> Confluence). Bezug: `docs/usecases/uc1-firewall-regel-dokumentation.md` (DoD),
> `docs/naming-conventions.md` (Standardprofil „default"), ADR 0004 (Datenmodell + Ergänzung),
> ADR 0006 (Objekttyp/Farbe-Signal), ADR 0007 (Mandant/Kunde, RBAC).

## Einheit & Gliederung

- **Mittelpunkt ist das System** — ein Container / eine VM / ein Server
  (`network_object` host). Die Doku beschreibt alle Zugriffe **dieses** Systems.
- **Klammer darüber**: Projekt / Application Profile (`AP_<projektnr>` in ACI, in Model 2
  die 3-stellige Projektnummer aus dem Namen). Rollup je Projekt/AP möglich.
- Die Zugriffe werden in **Kategorien** gruppiert nach **Richtung × Scope × Enforcement**
  (Firewall vs. ACI-intern).

Ableitung der Kategorie je Regel/Contract:

| # | Tabelle | Enforcement | Ableitung (Gegenstelle/Scope) |
|---|---|---|---|
| 1 | Internet Zugriff | Firewall | → `L3O_inet`/extern, **Source-NAT** (ausgehend) |
| 2 | Zugriff aus dem Internet | Firewall | extern → System über **DNAT/VIP** (`V-EXT_`) |
| 3 | Zugriff in das Unternehmensnetzwerk | Firewall | Gegenstelle `L3O_bit` (Büko/Business-IT), eingehend zum Unternehmensnetz |
| 4 | Zugriff aus dem Unternehmensnetzwerk | Firewall | Gegenstelle `L3O_bit`, ausgehend |
| 5 | Zugriff `L3O_vpn` | Firewall | Gegenstelle `L3O_vpn` |
| 6 | Zugriff `L3O_sra` (Remote Access) | Firewall | Gegenstelle `L3O_sra` (Administratoren-Access) |
| 7 | Zugriff innerhalb des Applikationsprofils | **ACI intern** | EPG↔EPG im **selben `AP_`** (Contract) |
| 8 | Zugriff innerhalb des Security Tenant | **ACI intern** | EPG↔EPG im **selben `TE_`**, anderes AP |
| 9 | Zugriff zu anderen Security Tenants | Firewall | Gegenstelle anderer `TE_` (inkl. `L3O_res` → Ressourcen) |
| 10 | Unspezifische Zugriffe auf die Applikation | Firewall | globale Regel, grosser IP-Range, nicht app-spezifisch |

**Model 2 (FortiGate + NetBox):** Tabellen 1–6, 9, 10 werden befüllt; die ACI-internen
Tabellen 7/8 bleiben leer und erscheinen erst mit **Model 1** (ACI).

## Spalten je Tabelle (seit 0.84.0)

Quelle und Ziel stehen in **Verkehrsrichtung** (Anwender 2026-10-27: „heute steht alles in Server
Name“): eingehend ist die Gegenstelle die Source und das System der Applikation die Destination,
ausgehend umgekehrt. Beide mit Name und IP; mehrere Einträge einer Zelle je auf einer Zeile.
Optionale Spalten (NAT, Externer DNS Name, VIP, Load Balancer, Schutzmechanismen) fallen weg, wenn
sie in keiner Zeile der Kategorie einen Wert haben.

**Tabelle 1 — Internet Zugriff (Über eine Firewall):**
`Source` · `Destination` · `TCP/UDP` · `NAT Externe IPv4` · `Aktion` · `Schutzmechanismen` ·
`Gerät` · `Description`

**Tabelle 2 — Zugriff aus dem Internet (Über eine Firewall):** die Kette öffentliche IP → VIP der
Firewall → Load Balancer → Server in eigenen Spalten:
`Source` · `Externer DNS Name` · `VIP (Firewall)` · `Load Balancer` · `Destination` · `TCP/UDP` ·
`Aktion` · `Schutzmechanismen` · `Gerät` · `Description`

**Tabellen 3–10 (gleiche Struktur):**
`Source` · `Destination` · `TCP/UDP` · `NAT` · `Aktion` · `Schutzmechanismen` · `Gerät` ·
`Description`

## Spalten → Datenquelle

| Spalte | Quelle |
|---|---|
| Source / Destination | je nach Richtung das System der Applikation (VM- bzw. Host-Name mit IP, bei der Kette mit dem Port im Pool, EPG mit ganzem ACI-Pfad; unspezifische Regel: ihr Objekt, die Systeme auf Knopfdruck) bzw. die Gegenstelle der Regel mit Name und Wert (`N_partner (192.0.2.0/24)`, `H-EXT_api (203.0.113.9)`, EPG/externe EPG, `any → Netze hinter …`) |
| TCP/UDP | Service/Port der Regel (`service_object`, aus `FT_`-Filter) |
| Externer DNS Name | DNS-Name der öffentlichen IP aus dem IPAM, sonst aus dem Namen der VIP (`V-EXT_<fqdn>`) |
| VIP (Firewall) | VIP der Regel: `V-EXT_shop: 203.0.113.10 → 10.30.0.50` (mit Ports bei Port-Weiterleitung) |
| Load Balancer | Virtual Service des NSX ALB hinter der VIP, nur mit einem Port der Regel: `vs-web · 10.30.0.50:443 · Pool pool-web` |
| NAT Externe IPv4 / NAT | Source-NAT der Regel (Pool-Adresse bzw. Interface-IP) |
| Aktion · Gerät | Allow/Deny; Firewall (mit VDOM) bzw. APIC |
| Schutzmechanismen | FortiGate-UTM-Profile der Policy (`WEB_`/`APP_`/`IPS_`/`WAF_`/`SSL_`); ACI: Service-Graph/PBR |
| Description | Name der Regel bzw. des Contracts (Link zur Regel) |

## Neue Collector-/Modell-Felder (daraus folgend)

1. **UTM-Schutzprofile pro Policy** (`webfilter`, `application-list`, `ips-sensor`,
   `waf`, `ssl-ssh-profile`, `av`) — der FortiGate-Collector muss die Profil-Referenzen
   mitlesen; am `rule` abzulegen.
2. **Source-NAT IP-Pools** (`IPP_`) — `nat_rule` um `snat_pool` befüllen (Schema vorhanden,
   ADR 0004).
3. **Externer DNS-Name** aus `V-EXT_`-Objektname — am `nat_rule` (dnat_vip) ergänzen.
4. **FortiGate-Farbcode** als Herkunfts-/Typ-Signal (ADR 0006 / `naming-conventions.md`).

## Export

Die Ansicht „Applikations-Doku" erzeugt diese Tabellen je System; Export als Markdown
(später zusätzlich Confluence-kompatibel). Ausnahmen (die ~10 %, die nicht automatisch
zuzuordnen sind) werden über den Designer/Override gesetzt bzw. als Finding geführt.
