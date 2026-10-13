# UC1 — Applikations-Doku: Ausgabe-Spezifikation

> Zielformat der Applikations-Dokumentation (entspricht den Kunden-Tabellen, bisher in
> Confluence). Bezug: `docs/usecases/uc1-firewall-regel-dokumentation.md` (DoD),
> `docs/naming-conventions.md` (Profil SOP), ADR 0004 (Datenmodell + Ergänzung),
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

## Spalten je Tabelle

**Tabelle 1 — Internet Zugriff (Über eine Firewall):**
`Server Name` · `TCP/UDP` · `Target Systems` · `Schutzmechanismen` · `NAT Externe IPv4` ·
`Description`

**Tabelle 2 — Zugriff aus dem Internet (Über eine Firewall):**
`Source` · `TCP/UDP` · `Server Name` · `VIP IP Adresse` · `Externer DNS Name` ·
`Externe IP Adressen` · `Schutzmechanismen` · `Description`

**Tabellen 3–10 (gleiche Struktur):**
`Server Name` · `TCP/UDP` · `Target Systems` · `Schutzmechanismen` · `Description`

## Spalten → Datenquelle

| Spalte | Quelle |
|---|---|
| Server Name | Host-Objekt des Systems dieser Applikation (`H_<fqdn>`); immer die App-Seite |
| Source / Target Systems | Gegenstelle der Regel (`network_object`/`H-EXT_`/anderer `TE_`) |
| TCP/UDP | Service/Port der Regel (`service_object`, aus `FT_`-Filter) |
| Schutzmechanismen | FortiGate-UTM-Profile der Policy (`WEB_`/`APP_`/`IPS_`/`WAF_`/`SSL_`); ACI: Service-Graph/PBR |
| NAT Externe IPv4 | Source-NAT IP-Pool (`IPP_`, `nat_rule.snat_pool`) |
| VIP IP / Externer DNS Name / Externe IP | Destination-NAT (`V-EXT_`, `nat_rule.dnat_vip`): mapped-IP, FQDN aus `V-EXT_`-Name, ext-IP |
| Description | Regel-/Policy-Kommentar |

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
