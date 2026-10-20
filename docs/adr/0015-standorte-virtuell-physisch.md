# ADR 0015 — Standorte: virtuell und physisch

## Status

Angenommen (2026-10-20). Ergänzt ADR 0004 (Ergänzung Standorte), ADR 0010 (NetBox-Sync) und
ADR 0012 (Applikationen).

## Kontext

Bisher kannte Automato eine Art Standort mit dreistelligem Code (`000`). Beim Kunden gibt es
zwei Arten:

- **Virtuelle Standorte** sind Nummern der Namenskonvention (`000`, `001`, …). Sie stehen nur
  in **Netz-Namen** (`N_<tenant>-000-dmz01`, auch `T_`/`IPP_`) und in Namen **physischer
  Cluster** (`fwc11-000-<tenant>`: Firewall-Cluster 11, `000` = Stretched DC, Tenant). Ein
  virtueller Standort kann über mehrere physische laufen (Stretched DC).
- **Physische Standorte** sind Orte/Rechenzentren mit Ort-Code (Beispiel anonymisiert:
  `chzrh1`, `chbsl1`), z. B. im Namen physischer Hosts (`evh01-chzrh1-<tenant>`).
- **VMs** tragen keinen Standort im Namen: Der dreistellige Block in `H_app01-002-…` ist das
  ACI-Applikationsprofil (Projekt), dazu der Tenant. Das bleibt die Grundlage der
  Applikations-Vorschläge (ADR 0012).

Bestehende Daten (Standorte, Verortungen von Geräten, Systemen und Applikationen) dürfen nicht
verloren gehen.

## Entscheidung

1. **Modell (Alembic `0027`):** `site.kind` = `virtual` | `physical`, `site.code` bis 16
   Zeichen. Zuordnung virtuell → physisch n:m in `site_link`.
   - Bestand: Ziffern-Codes (`000`) werden virtuell, alle anderen (z. B. `ZRH`) physisch.
     Verortungen bleiben, wie sie sind.
   - Prüfung je Art: virtuell = drei Ziffern, physisch = beginnt mit einem Buchstaben,
     3–16 Zeichen. Ohne Angabe ergibt sich die Art aus dem Code.
2. **Verortung:**
   - Ein Gerät (Quellsystem) steht meist an einem physischen Standort, ein Stretched-Cluster
     an einem virtuellen.
   - Systeme und Applikationen können beides haben.
   - Angezeigt wird immer mit der Zuordnung, z. B. „000 · Stretched DC (chzrh1, chbsl1)“.
3. **Erkennung aus Namen, nur als Vorschlag:**
   - Ausgewertet wird der zweite Block nach dem Präfix in Netz-Objekten (`N_`, `T_`, `IPP_`,
     `R_`), BD-/Subnetz-Namen, Inventar-Geräten und Quellsystem-Namen. Er wird gegen die
     erfassten Standorte gelegt.
   - Hosts/VMs, VIPs und externe Objekte werden nicht ausgewertet.
   - Ein nicht erfasster Ziffern-Code wird als „nicht erfasst“ angezeigt. Ein unbekannter
     Ort-Code wird nicht vorgeschlagen (zu unsicher).
   - Ohne erfasste Standorte funktioniert alles wie bisher; es gibt keine Findings dazu.
4. **NetBox-Sync, beide Arten als eigenes Objekt dokumentiert:**
   - Jeder Standort wird eine NetBox-Site (Slug = Code) in der Standort-Gruppe *Virtuelle
     Standorte* (`standorte-virtuell`) bzw. *Physische Standorte* (`standorte-physisch`).
   - Der Kommentar nennt die Zuordnung („Läuft an den physischen Standorten: …“ bzw.
     „Virtuelle Standorte hier: …“).
   - Eine Site-Gruppe allein genügt nicht, weil NetBox eine Site nur einer Gruppe zuordnet –
     die n:m-Zuordnung steht deshalb im Kommentar (ohne Custom Fields, wie ADR 0010).
   - Gruppe und Kommentar pflegt Automato nur an eigenen Sites (Tag `automato`). Von Hand
     gepflegte Sites mit gleichem Slug werden nur referenziert und ergeben dafür keinen
     Konflikt.
   - Neuer Schreib-Objekttyp `dcim/site-groups` im Schreiber und im Sync-Benutzer
     (`source-of-truth/netbox/sync-user.sh`).
   - Gerät ohne Standort am Quellsystem: der einzige physische Standort des Mandanten, sonst
     wie bisher der einzige Standort.

## Konsequenzen

- Bestehende Installationen: Migration hoch, Standorte bleiben; die NetBox-Vorschau zeigt
  danach Standort-Gruppen zum Anlegen und eigene Sites mit geänderter Gruppe/Kommentar.
- Das Schreib-Token braucht zusätzlich das Recht auf *DCIM › Site Group*: Permission
  `automato-sync-write` in NetBox ergänzen oder `sync-user.sh` erneut ausführen (erzeugt einen
  neuen Token, ausser `SYNC_TOKEN` wird gesetzt).
- Offen: Virtuelle Standorte als Scope von Netzen in NetBox (Prefix-Scope ist
  versionsabhängig) und ein VM-Collector, der den physischen Standort einer VM aus
  vCenter/Proxmox liest.
