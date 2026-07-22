# FortiGate-Lab (Zielsystem, Model 2)

Einrichtung einer FortiGate-VM als read-only Regelquelle für den UC1-Durchstich (M7).
**Nur für Labor/Test.** Anders als NetBox lässt sich die FortiGate nicht per Skript
provisionieren (lizenziertes Image, Hypervisor-Import) – daher als Anleitung + CLI-Blöcke.

## 1. VM bereitstellen

- **FortiGate-VM64** von Fortinet Support laden (15-Tage-Eval genügt; Formate für
  KVM/`qcow2`, VMware/`ova`, Hyper-V, VirtualBox).
- Import in den Hypervisor, mind. 1 vCPU / 2 GB RAM, eine Management-Netzwerkkarte.
- Erststart: Konsole → `admin` ohne Passwort → Passwort setzen.

## 2. Management + HTTPS-API freischalten

```
config system interface
    edit "port1"
        set mode static
        set ip <MGMT_IP> 255.255.255.0
        set allowaccess ping https ssh
    next
end
```

Die API läuft über HTTPS (Port 443). Der Collector spricht die REST-API an; kein
Schreibzugriff nötig.

## 3. Read-only API-User + Token (Least Privilege, ADR 0005)

```
config system accprofile
    edit "automato_ro"
        set secfabgrp read
        set ftviewgrp read
        set fwgrp read
        set netgrp read
        set sysgrp read
    next
end
config system api-user
    edit "automato-collector"
        set accprofile "automato_ro"
        set vdom "root"
        config trusthost
            edit 1
                set ipv4-trusthost <AUTOMATO_HOST_IP> 255.255.255.255
            next
        end
    next
end
execute api-user generate-key automato-collector
```

`generate-key` gibt den API-Key **einmalig** aus → als `AUTOMATO_FORTIGATE_TOKEN`
verwenden. `trusthost` schränkt die Quelle auf den Automato-Host ein.

## 4. Beispiel-Objekte + Policy (passend zum NetBox-Seed)

Deckungsgleich mit `source-of-truth/netbox/seed_lab.py` (Server 10.10.10.0/24,
DMZ 10.10.20.0/24), damit das Matching Treffer erzeugt. Die Namen folgen der
Konvention (`N_`/`H_`) aus ADR 0004:

```
config firewall address
    edit "N_dmz-net"
        set subnet 10.10.20.0 255.255.255.0
    next
    edit "N_server-net"
        set subnet 10.10.10.0 255.255.255.0
    next
    edit "H_web01"
        set subnet 10.10.10.5 255.255.255.255
    next
    edit "H-EXT_partner-api"
        set type fqdn
        set fqdn "api.partner.example"
    next
end
config firewall policy
    edit 1
        set name "allow-dmz-to-web"
        set srcintf "port2"
        set dstintf "port3"
        set srcaddr "N_dmz-net"
        set dstaddr "H_web01"
        set action accept
        set schedule "always"
        set service "HTTPS"
    next
    edit 2
        set name "deny-dmz-to-mgmt"
        set srcintf "port2"
        set dstintf "port1"
        set srcaddr "N_dmz-net"
        set dstaddr "all"
        set action deny
        set schedule "always"
        set service "ALL"
    next
end
```

`H-EXT_partner-api` ist bewusst extern → der Collector markiert es als
`expected_external` (kein „unmatched"-Fehler); `H_web01`/`N_*` sollten in NetBox
auflösbar sein.

## 5. REST-Endpunkte, die der Collector liest (read-only)

| Zweck | Endpunkt |
|---|---|
| Policies | `GET /api/v2/cmdb/firewall/policy` |
| Adressobjekte | `GET /api/v2/cmdb/firewall/address` |
| Service-Objekte | `GET /api/v2/cmdb/firewall/service/custom` |
| Zonen | `GET /api/v2/cmdb/system/zone` |

Auth-Header: `Authorization: Bearer <API-Key>`. TLS-Zertifikat im Lab meist
selbstsigniert → im Collector `verify_tls=false` (nur Lab!).

## 6. Automato anbinden

```
AUTOMATO_FORTIGATE_URL=https://<MGMT_IP>
AUTOMATO_FORTIGATE_TOKEN=<api-key aus generate-key>
```

## Hinweise

- Bis das Lab steht, liest der Collector Beispieldaten (`sample_policies.json`) durch
  dieselbe Normalisierung – der Ende-zu-Ende-Pfad ist damit ohne FortiGate testbar.
- Die Interface-Namen (`port2`=DMZ, `port3`=Server) an dein Lab anpassen; der Collector
  liest `srcintf`/`dstintf` als Zonen-Hinweis.
