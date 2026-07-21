# ADR 0002: SoT Gateway Adapter-Pattern, Rule Store, Topology Service

## Status
Angenommen (Grundprinzip) – Detailmodellierung offen

## Kontext

Das Projekt wird modular für mehrere Kunden/Use Cases eingesetzt:
- Manche Kunden haben ServiceNow im Einsatz, andere nicht
- Manche Kunden nutzen NetBox als IPAM/Source of Truth, andere Infoblox DDI
- Anforderung: Tools müssen später austauschbar sein, ohne bestehende Daten neu
  erfassen zu müssen

Zusätzliche fachliche Anforderung für Firewall-Regeln:
- Adressobjekte müssen aus dem jeweils aktiven IPAM gelesen bzw. dort neu angelegt werden
- Service-Objekte (TCP/UDP) müssen mit den Adressobjekten zu Regeln kombiniert und an
  den Orchestrator übergeben werden
- Wunsch: aktive und Backup-Pfade (Primary/Backup-Firewall je Verbindung) sollen bekannt
  sein, damit eine Regel automatisch auf allen relevanten Firewalls landet

## Entscheidung

### 1. SoT Gateway (Adapter-Pattern)
Ein eigener Microservice abstrahiert den Zugriff auf das jeweils aktive IPAM-/SoT-Tool:

- Kanonisches internes Datenmodell: `NetworkObject { name, type: host|network|range,
  value, tags }`
- Adapter pro Backend (`netbox-adapter`, `infoblox-adapter`), austauschbar/erweiterbar
  je Tenant/Kunde
- Einheitliche API nach außen: lesen (`get_by_name`, `search_by_ip`) und schreiben
  (`create_address_object`)
- Rest der Plattform kennt nie NetBox- oder Infoblox-spezifische Formate

### 2. Rule & Service Store
Zentrale, tool-unabhängige Ablage für:
- Service-Objekte (TCP/UDP-Definitionen) – IPAM-unabhängig, direkt gepflegt
- Firewall-Rule-Intents: Referenz auf Quell-/Ziel-Adressobjekt (über SoT Gateway
  aufgelöst), Referenz auf Service-Objekt, Aktion (allow/deny), Referenz auf
  Connection/Zone (siehe Topology Service)

Offen: Persistenz rein als Git/YAML (versioniert, PR-Review, kein UI-Editing) vs.
DB-backed mit Sync nach Git (UI/Wizard-freundlich, GitOps-Audit-Trail). Tendenz:
DB + Git-Sync, wegen geplanter Wizards im Web UI.

### 3. Topology Service (Primary-/Backup-Pfade)
Eigener kleiner Service, modelliert als Graph:
- Zonen (z. B. "DC1-DMZ", "Campus-Intern", "Cloud-VPC-X") als Knoten
- Connections zwischen Zonen als Kanten mit Attributen: `enforcement_device`
  (welche Firewall setzt durch), `role: primary|backup`, `priority`

Der Policy Compiler fragt beim Kompilieren einer Regel: "welche Firewalls liegen auf
dem Pfad zwischen Zone A und Zone B?" und bestückt automatisch alle relevanten Geräte
(Primary + Backup), statt dass das manuell gepflegt werden muss.

Offen: Zonen-Zuordnung aus Tags/Sites der IPAM-Tools ableiten (müsste pro Adapter
normalisiert werden) oder manuell im Topology Service pflegen.

### 4. Policy Compiler: Aerleon statt Eigenbau
**Aerleon** (aktiv gepflegter Fork von Googles Capirca, Apache-2.0, vollständig Open
Source, keine kommerzielle Variante) übernimmt die Übersetzung von plattformneutralen
YAML-Policies in native Firewall-Configs. Dedizierte Generatoren für Fortinet und
Proxmox vorhanden, dazu Cisco und viele weitere.

Datenfluss: SoT Gateway (aufgelöste Adressobjekte) + Rule Store (Service-Objekte,
Intents) + Topology Service (Ziel-Firewalls) → Aerleon generiert Configs → Orchestrator
verteilt an die passenden Worker.

## Betrachtete Alternativen (bewusst nicht gewählt bzw. optional/ergänzend)

- **NetBox-Plugins** (`netbox-security`, `netbox-data-flows`, `netbox-nsm`): würden
  Firewall-Regeln direkt in NetBox ablegen. Nicht gewählt als Kernlösung, da das der
  Anforderung "tool-unabhängig, später austauschbar" widerspricht (Bindung an NetBox).
  Bleiben als Option, falls ein Kunde Regeln zusätzlich in NetBox sichtbar haben will.
- **Tufin / AlgoSec / FireMon**: etablierte kommerzielle NSPM-Suiten mit
  Compliance-/Change-Workflows. Nicht gewählt: kostenpflichtig, Overkill für
  "klein anfangen", widerspricht Open-Source-Präferenz.
- **Firewall Orchestrator** (Open Source, Apache-2.0): importiert/auditiert bestehende
  Firewall-Configs mehrerer Hersteller. Als ergänzendes Audit-/Compliance-Werkzeug für
  die Documentation Engine relevant, nicht als Ersatz für den Policy Compiler.
- **iTop** (Open Source CMDB/ITSM) für Contracts: Option für Kunden ohne ServiceNow.
  Noch nicht final entschieden – siehe offene Punkte.
- **Batfish** (Open Source, multi-vendor Reachability-Analyse): als Cross-Check-Tool
  erwägenswert, um zu verifizieren, dass eine Regeländerung das gewünschte
  Pfad-Verhalten tatsächlich erzeugt. Kein Ersatz für den Compiler.

## Offene Punkte
- Rule-Store-Persistenz: Git-only vs. DB + Git-Sync
- Zonen-Zuordnung: automatisch aus IPAM-Tags oder manuell im Topology Service
- Contracts: pro Kunde ServiceNow (falls vorhanden) oder iTop – Entscheidungskriterium
  noch festzulegen
- Genaues Datenmodell für Rule & Service Store (nächster Planungsschritt)
