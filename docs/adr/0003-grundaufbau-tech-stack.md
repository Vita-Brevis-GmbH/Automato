# ADR 0003: Grundaufbau – Tech-Stack und Service-Schnitt für UC1

## Status
Angenommen

## Kontext

UC1 (Firewall-Regel-Dokumentation, read-only) ist festgelegt. Für den Grundaufbau
(Roadmap M6) müssen Tooling, Basis-Services und der Service-Schnitt entschieden
werden – klein genug zum Starten, aber so geschnitten, dass die Modularität aus
ADR 0002 (Adapter-Pattern, Austauschbarkeit) erhalten bleibt.

## Entscheidung

| Bereich | Wahl | Begründung |
|---|---|---|
| Sprache | Python ≥ 3.11 | wie ARCHITECTURE.md |
| Paket-/Workspace-Manager | **uv** (Workspace mit `orchestrator`, `collectors`, `sot-gateway`) | ein Tool für Dependencies/venvs, schnell, ein Lockfile |
| Lint/Format | Ruff (inkl. Security-Regeln) | ein Tool statt drei |
| API | FastAPI + Pydantic v2, Konfiguration via pydantic-settings (ENV) | wie ARCHITECTURE.md; 12-Factor-Konfiguration |
| Datenbank | PostgreSQL 16, SQLAlchemy 2, Alembic-Migrationen ab Tag 1 | Migrationen von Anfang an, kein Schema-Wildwuchs |
| Job-Queue | Celery + Redis (Beat für zyklische Collector-Läufe) | in ARCHITECTURE.md vorgesehen; Scheduler inklusive |
| Frontend | React + Vite + TypeScript | ARCHITECTURE.md nennt React/Vue; React als Default – nur Skeleton, austauschbar |
| Secrets | `.env` lokal (nie committet), gitleaks in pre-commit und CI; Ansible Vault sobald Lab-Credentials nötig | Risiko R7 |
| CI | GitHub Actions, pfad-gefiltert je Komponente | Vorgabe aus ADR 0001 (kein monolithischer Build) |

### Service-Schnitt (bewusst kleiner als die Zielarchitektur)

1. **Orchestrator** (ein Service): REST-API, Job-Verteilung, Audit-Log **und** der
   Rule & Service Store. Der Store ist ein eigenes Python-Package (`app/store`)
   mit Repository-Schicht – herauslösbar, sobald es sich lohnt.
2. **SoT Gateway** als eigenständiges Python-Package (`sot-gateway/`) mit stabilem
   Adapter-Interface (`NetworkObject`, `SoTAdapter`), zunächst als Library vom
   Orchestrator konsumiert. Der Schnitt aus ADR 0002 bleibt erzwungen (eigenes
   Package, eigene Tests, kein Import von Orchestrator-Code); die spätere
   Heraustrennung als Microservice ändert nur den Transport, nicht das Interface.
3. **Collectors** (FortiGate, Cisco ACI) sind ab Tag 1 **eigene Prozesse** mit
   eigenem Container: Sie erhalten Jobs über die Queue und liefern Ergebnisse per
   HTTP an die interne Orchestrator-API (Service-Token). **Kein direkter
   DB-Zugriff** – ein Collector besitzt nur die Lesecredentials seiner Plattform
   plus ein API-Token (Least Privilege, SECURITY.md).

## Konsequenzen

- Weniger Betriebsaufwand am Anfang (1 API-Service statt 3–4), Paketgrenzen statt
  Netzwerkgrenzen sichern die Modularität.
- Das normalisierte Regel-DTO (`orchestrator/app/schemas/collector.py`) ist der
  Vertrag zwischen Collectors und Store; es wird in M5 finalisiert.
- Wächst ein Teil (z. B. SoT Gateway für mehrere Mandanten), wird das Package zum
  Service promoviert – Re-Evaluation spätestens beim zweiten Kunden-Setup.
