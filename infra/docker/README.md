# Docker

Das Compose-Setup liegt im Repo-Root (`docker-compose.yml`):

```
docker compose --profile core --profile collectors up --build
```

- Profil `core`: Postgres, Redis, Orchestrator (führt beim Start
  `alembic upgrade head` aus), Web-UI (Dev-Server auf :5173)
- Profil `collectors`: Collector-Worker (FortiGate/ACI)
- Konfiguration über `.env` (Vorlage: `.env.example`)

Die Dockerfiles der Python-Services bauen aus dem Repo-Root (uv-Workspace).
Infoblox DDI ist ein externes System und läuft nicht im Compose.
