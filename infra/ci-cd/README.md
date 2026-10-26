# CI/CD

Die Pipeline liegt in `.github/workflows/ci.yml` (GitHub Actions):

- **gitleaks** – Secret-Scan über das ganze Repo, läuft immer
- **python** – Ruff (Lint + Format) und pytest je Package (orchestrator,
  collectors, sot-gateway), nur bei Änderungen an Python-Pfaden
- **frontend** – `tsc --noEmit` und Vite-Build, nur bei Änderungen unter
  `web-ui/frontend/`

Selektive Ausführung pro Komponente ist Vorgabe aus ADR 0001. Ansible-Lint/
Molecule und Image-Publishing folgen mit den späteren Use Cases.
