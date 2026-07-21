# Roadmap

Prinzip: klein anfangen, jede Phase liefert einen nutzbaren Mehrwert, Ausbau erfolgt schrittweise
bis zur Full-Stack-Automatisierung.

## Phase 0 – Fundament
- [ ] Git-Repo-Struktur (dieses Repo) aufsetzen
- [ ] NetBox als Source of Truth aufsetzen, Naming-/Tagging-Standards definieren
- [ ] Basis-CI (Lint, Tests) einrichten
- [ ] Erste ADRs für Kernentscheidungen dokumentieren

## Phase 1 – Read-only Dokumentation
- [ ] Documentation Engine: Live-State-Erfassung für **eine** Plattform (z. B. Linux oder Cisco Switches)
- [ ] Automatische Doku-Generierung (MkDocs) aus erfassten Daten
- [ ] Kein Schreibzugriff auf Zielsysteme in dieser Phase

## Phase 2 – Compliance / Drift Detection
- [ ] Vergleich Live-Zustand vs. NetBox-Soll-Zustand
- [ ] Compliance-Reports (weiterhin rein lesend)

## Phase 3 – Orchestrator + erster Worker
- [ ] Orchestrator-API (Auth, Job-Modell, Queue) implementieren
- [ ] Ersten Worker-Typ produktiv nehmen (z. B. Proxmox oder VMware)
- [ ] Ende-zu-Ende-Test: ein einfacher automatisierter Vorgang (z. B. VM-Provisioning)

## Phase 4 – Worker-Ausbau
- [ ] Weitere Worker: Fortigate, Cisco ACI, Windows, Linux, Cisco Switches
- [ ] Ansible-Rollen je Plattform konsolidieren

## Phase 5 – Web UI & Wizards
- [ ] Web-UI-Grundgerüst (Auth gegen Orchestrator)
- [ ] Contract-Wizard
- [ ] Neue-VM-Wizard
- [ ] Weitere Wizards nach Bedarf

## Phase 6 – Integrationen
- [ ] ServiceNow-Anbindung
- [ ] Weitere Schnittstellen nach Bedarf (Monitoring, CMDB, Ticketing)

## Phase 7 – Skalierung
- [ ] Multiple Worker-Instanzen pro Typ, Lastverteilung
- [ ] Umzug auf Kubernetes
- [ ] Hochverfügbarkeit für Orchestrator

## Phase 8 – Hardening & Full Stack
- [ ] Security-Audit, HashiCorp Vault statt Ansible Vault
- [ ] Vollständiges RBAC
- [ ] Monitoring/Alerting (z. B. Prometheus/Grafana)
- [ ] Vollständige CI/CD-Pipeline, Disaster Recovery
