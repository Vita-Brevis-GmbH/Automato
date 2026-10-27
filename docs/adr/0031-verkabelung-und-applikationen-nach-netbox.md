# ADR 0031 — Verkabelung und Applikationen nach NetBox (zwei Abschnitte im NetBox-Sync)

## Status

Angenommen (2026-10-27). Freigabe des Anwenders vom 2026-10-27 („Verkabelung, Applikationen …
Go“) auf die offenen Punkte „Kabel und Ports nach NetBox“ (ADR 0025, Konsequenzen) und „Export
der Applikationen nach NetBox“ (ADR 0012, Konsequenzen). Ergänzt ADR 0010 (einziger Schreibweg:
NetBox-Sync, Vorschau → Übernehmen) und ADR 0017 (Abschnitt Virtualisierung). Keine Migration –
die Abschnitte stehen wie bisher in `sync_plan.options`.

## Kontext

- Die Verkabelung (ADR 0025) kennt Switches und Router (`cisco_switch`) mit ihren Ports und die
  Kabel aus CDP/LLDP – nach NetBox ging davon bisher nichts. Der Abschnitt *Netzinventar* nimmt
  nur FortiGate und ACI.
- Applikationen (ADR 0012) sind ein eigenes Objekt in Automato. In NetBox sieht man an einer VM
  bzw. IP-Adresse nicht, zu welcher Applikation sie gehört.
- Vorgaben: nur nach NetBox schreiben, nur Objekte mit Tag `automato` ändern oder löschen,
  keine Custom Fields (das Schreib-Token braucht keine Rechte darauf), Vorschau mit
  Fingerabdruck, Audit, gespeicherte bzw. nach NetBox geschriebene Texte deutsch (ADR 0028).

## Entscheidung

### 1. Zwei neue Abschnitte, ausdrücklich wählbar

- `cabling` (Verkabelung) und `applications` (Applikationen) neben `inventory`, `mirror`,
  `virtualization` (`netbox_sync.SECTIONS`). Ohne Angabe gelten die bisherigen drei
  (`DEFAULT_SECTIONS`); in der Oberfläche sind die neuen standardmässig aus und erklären sich,
  sobald sie gewählt sind.
- Ablauf unverändert: Vorschau (liest NetBox nur) mit anlegen / ändern / unverändert / Konflikt /
  löschen bzw. veraltet und Hinweisen; Übernahme nur bei gleichem Fingerabdruck; Audit
  `netbox_sync.planned`/`applied` jetzt zusätzlich mit den gewählten Abschnitten (`sections`).
- **Löschbereich je Abschnitt:** Objekte eines nicht gewählten Abschnitts sind weder veraltet
  noch werden sie gelöscht. Für Geräte zeigt der bestehende Kommentar „Automato: gelesen aus
  <Quellsystem>“ den Abschnitt (Plattform des Quellsystems `cisco_switch` → Verkabelung, sonst –
  auch unbekannt – Netzinventar, wie bisher); Ports folgen ihrem Gerät. Kabel liest der Sync nur
  mit dem Abschnitt Verkabelung, VMs ohne Virtualisierung nur für die Applikations-Tags (wie
  bisher nie veraltet).

### 2. Verkabelung

- **Geräte** aller Quellsysteme `cisco_switch` mit denselben Regeln wie im Netzinventar
  (`_device_want`): Standort des Quellsystems bzw. der einzige (physische) Standort, Rolle
  (`switch`, `router`), Hersteller, Gerätetyp, Seriennummer, Kommentar. **Nichts raten:** ohne
  Standort bzw. ohne gelesenes Modell wird das Gerät mit seinen Ports und Kabeln nicht übernommen
  (Hinweis) – anders als im Netzinventar kein Ersatz-Gerätetyp. Hersteller aus dem Modell
  (MX/EX/QFX/SRX/ACX/PTX = Juniper, sonst Cisco).
- **Ports** mit Name, Beschreibung, aktiviert, Port-Channel (`lag`) bzw. Parent (Junos-Units) und
  Typ, soweit ableitbar (`port_type`): Port-Channel `lag`, SVI/Loopback/Unit `virtual`, physisch
  nach Speed (Kurzform des Namens bzw. gelesener Speed) und Medium (`attrs.media`: Kupfer bzw.
  SFP/optisch; ohne Angabe bis 5G Kupfer, darüber optisch), sonst `other`.
- **Modus und VLANs nur mit vorhandenen VLANs.** Automato legt für die Switches keine VLANs an.
  Nach dem Lesen von NetBox (`resolve_after_read`) sucht der Sync je VLAN-ID das VLAN in dieser
  Reihenfolge: VLAN-Gruppe des Quellsystems (`<Mandant>-<Quellsystem>`), am Standort des Geräts
  (VLAN mit diesem Standort bzw. Gruppe mit Geltungsbereich Standort), global (ohne Standort, Gruppe
  ohne Geltungsbereich – z. B. die Gruppen des Netzinventars, auch wenn sie im selben Sync
  entstehen). Mehrere Treffer auf derselben Stufe = nicht eindeutig.
  - Access: das Access-VLAN muss da sein.
  - Trunk mit allen VLANs (1–4094): `tagged-all`.
  - Trunk mit Liste: `tagged` mit den erlaubten VLANs, die es auf den Switches des Quellsystems
    gibt (`show vlan`); Native VLAN und VLAN 1 nur, wenn vorhanden.
  - Fehlt ein nötiges VLAN oder ist es nicht eindeutig, bleiben Modus und VLANs am Port, wie sie
    in NetBox sind; der Hinweis nennt je Gerät die VLANs und die Zahl der Ports.
- **Kabel** (`dcim/cables`, neu in `netbox_writer.WRITABLE` und `sync-user.sh`) aus der
  Verkabelung: nur Verbindungen im Zustand *beidseitig* zwischen zwei physischen, gelesenen Ports
  – auch zu ACI-Leafs. Beide Ports müssen in NetBox sein oder im selben Sync angelegt werden (Ports
  der Leafs mit dem Abschnitt Netzinventar); sonst Hinweis. Einseitige Verbindungen, Gegenstellen
  ohne CDP/LLDP (Firewall, ESXi-Host) und Ports mit mehreren Nachbarn nur als Hinweis (gezählt,
  mit Beispielen). Schlüssel = beide Enden `<Gerät>/<Port>` sortiert; Kabel mit anderen Enden
  (Patchpanel, Circuit, mehrere Ports je Seite) bleiben ausserhalb.
  - Status `connected`, Tenant, Beschreibung „Automato: CDP beidseitig“; Tag `automato`.
  - Kabel an von Hand gepflegten Geräten/Ports sind erlaubt (die Ports werden nur referenziert).
    Ein von Hand gepflegtes Kabel zwischen denselben Ports bleibt (unverändert, nie gelöscht).
  - **Belegter Port:** trägt ein Ende schon ein anderes Kabel, ist das neue Kabel ein Konflikt
    (es wird nichts angelegt). Ist das alte Kabel ein eigenes, nicht mehr gemeldetes und
    „Löschen zulassen“ gewählt, löscht die Übernahme zuerst die alten Kabel und legt dann die
    neuen an (umgesteckter Port in einem Durchgang).

### 3. Applikationen

- Je Applikation ein **Tag** `automato-app-<Mandant>-<Kürzel bzw. Name>` (Slug; Kürzel, sonst
  Name; teilen sich mehrere denselben, behält die älteste ihn, die übrigen bekommen den Namen bzw.
  die ID dazu), Name = Applikation, Beschreibung „Automato: Applikation · Kürzel … · Zweck … ·
  Kritikalität …“, Farbe nach Kritikalität. **Keine Custom Fields.**
  - Tags sind in NetBox global, ihre Namen eindeutig: ist der Name vergeben, heisst der Tag
    „<Name> (<Mandant>)“, sonst „<Name> (<Slug>)“ – konvergiert wie die übrigen Namen.
  - Das Präfix `automato-app-` gehört Automato. Eigen ist ein solcher Tag für den Mandanten, dessen
    Slug am längsten passt (`app_tag_owner`, mehrere Mandanten in einer NetBox); ein Tag mit dem
    Slug eines anderen Mandanten ist ein Konflikt und wird nicht zugeordnet.
  - Tags gelöschter Applikationen sind veraltet; gelöscht nur mit „Löschen zulassen“.
- **Zuordnung** an die **VMs** (Virtualisierung) und **IP-Adressen** der Systeme der
  Applikation – wie die Doku (`applications.systems_by_application`): VMs an den Portgruppen
  ihrer EPGs und hinter ihren Servern (gleiche IP), Server ohne VM; IPs dieser VMs und Server.
  - Neue Arten `app_tag_vm` / `app_tag_ip`: kein eigenes NetBox-Objekt, sondern die Tags des
    Ziels. Vorschau „ändern“ mit `tags` alt → neu (nur die eigenen Applikations-Tags);
    Übernahme als PATCH nach allen übrigen Arten, **fremde Tags bleiben** (auch `automato`).
  - Nur an Objekten mit Tag `automato`, die es in NetBox gibt oder die der Sync anlegt
    (Virtualisierung, DDI-Spiegel, Netzinventar). An von Hand gepflegten Objekten ist eine
    Abweichung ein **Konflikt** – sie bleiben unverändert (ADR 0010). Ziele, die es nicht gibt,
    zählt ein Hinweis; Ziele, die der Sync löscht, bekommen nichts.
  - Gehört ein Objekt nicht mehr zur Applikation, wird nur ihr Tag entfernt (ändern, ohne
    „Löschen zulassen“ – es wird kein Objekt gelöscht).

### 4. Sprache

Plan, Hinweise und alles, was nach NetBox geht (Beschreibungen, Kommentare, Tag-Namen), sind
deutsch (`compute_job`/`apply_job` `@german_only`); die Anzeige übersetzt Arten und Hinweise
(`KIND_EN`, `WARNINGS_EN`).

## Konsequenzen

- Der Sync-Benutzer braucht zusätzlich Schreibrechte auf `dcim.cable`
  (`source-of-truth/netbox/sync-user.sh`); Tags (`extras.tag`) und Geräte/Ports hatte er schon.
- Mit dem Abschnitt Applikationen liest der Sync alle Tags und alle VMs der NetBox, mit der
  Verkabelung alle VLANs und die Kabel der betroffenen Geräte – nur lesend.
- Bewusst nicht (später bei Bedarf): VLANs der Switches anlegen; IP-Adressen der Switch- und
  Router-Interfaces (Junos); Kabel zu Geräten ohne CDP/LLDP (Firewalls, ESXi-Hosts) bzw. aus
  einseitigen Meldungen; Applikations-Tags an von Hand gepflegten Objekten (NetBox als führendes
  IPAM) – bräuchte eine eigene Freigabe, weil fremde Objekte geändert würden; Tags an Geräten,
  Netzen oder EPG-VLANs der Applikation.
- Am Grundsatz ändert sich nichts: geschrieben wird nur nach NetBox, nie auf Switches, Router,
  Firewalls, APIC oder vCenter.
