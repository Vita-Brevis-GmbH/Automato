# Infra

Deployment- und Betriebs-Konfiguration der Plattform.

- `docker/` – Docker-Compose-Setup für den Start (alle Services lokal/einfach betreibbar)
- `ci-cd/` – Pipelines (Lint, Tests, Build, Deploy)

Wachstumspfad: Docker Compose → Kubernetes, sobald Multi-Worker-Skalierung ansteht (Roadmap Phase 7).
