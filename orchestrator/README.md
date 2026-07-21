# Orchestrator

Zentrale Steuerungseinheit. Getrennt von den Workern.

## Zuständigkeiten
- REST/GraphQL-API für Web UI
- Auth/RBAC-Prüfung
- Job-Modell, Verteilung über Job-Queue an Worker
- Job-Historie & Audit-Log-Schreibzugriff
- Kennt Worker-Typen, führt selbst **keine** Ansible-Jobs aus

## Geplanter Stack
Python (FastAPI), Redis + Celery/RQ als Job-Queue.

Status: 🚧 noch nicht implementiert (siehe ROADMAP.md, Phase 3)
