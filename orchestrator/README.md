# Orchestrator

Zentrale Steuerungseinheit. Getrennt von den Workern.

## Zuständigkeiten
- REST/GraphQL-API für Web UI
- Auth/RBAC-Prüfung
- Job-Modell, Verteilung über Job-Queue an Worker
- Job-Historie & Audit-Log-Schreibzugriff
- Kennt Worker-Typen, führt selbst **keine** Ansible-Jobs aus

## Stack
Python (FastAPI, SQLAlchemy/Alembic auf Postgres), Celery + Redis als Job-Queue
(Entscheide: ADR 0003). Enthält im Grundaufbau auch den Rule & Service Store
(`app/store`) als herauslösbares Package.

Status: 🏗️ Grundgerüst vorhanden (Health, Job-Kreislauf, Audit-Log, interne
Collector-API). Regel-Datenmodell folgt in M5, Auth/RBAC in M3/M4.

Lokal: `uv run --package orchestrator pytest orchestrator/tests` ·
`uv run uvicorn app.main:app --app-dir orchestrator`
