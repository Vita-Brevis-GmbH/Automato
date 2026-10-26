# Collectors

Read-only Erfassung von Firewall-Regeln (Use Case #1). Ein Collector pro
Plattform, jeweils als eigener Celery-Worker-Prozess:

| Ordner | Quelle | Status |
|---|---|---|
| `fortigate/` | FortiOS REST API (Policies, Objekte, Zonen) | Skeleton – liest Beispieldaten, echter Abruf in M7 |
| `cisco_aci/` | APIC REST API (Contracts, Filter, EPGs) | Skeleton – liest Beispieldaten, echter Abruf in M7 |

Grundsätze (ADR 0003 / SECURITY.md):

- **Kein Schreibzugriff** auf Zielsysteme – Collector-Accounts sind read-only
- **Kein DB-Zugriff** – Jobs kommen über die Queue (`collectors`), Ergebnisse
  gehen als normalisierte Regeln (`common/dto.py`) per HTTP an die interne
  Orchestrator-API (Service-Token)
- Normalisierung ist pro Plattform gekapselt (`normalize.py`) und gegen
  Beispieldaten getestet – Tests brauchen kein Lab

Worker starten: `celery -A collectors.celery_app worker -Q collectors`
