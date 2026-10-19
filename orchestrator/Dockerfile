# Build-Kontext: Repo-Root (uv-Workspace) -> docker compose setzt context: .

# --- Agent-Paket (ADR 0008): collectors-Wheel + Windows-Skripte, im UI herunterladbar ---
FROM python:3.12-slim AS agent-package
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY orchestrator/pyproject.toml orchestrator/pyproject.toml
COPY sot-gateway/pyproject.toml sot-gateway/pyproject.toml
COPY collectors/ collectors/
RUN uv build --package collectors --wheel --out-dir /agent-package
COPY deploy/collector-agent/ /agent-package/

# --- Orchestrator ---
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /workspace
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Workspace-Definitionen zuerst (Layer-Cache), dann Code
COPY pyproject.toml uv.lock ./
COPY sot-gateway/ sot-gateway/
COPY collectors/pyproject.toml collectors/pyproject.toml
COPY collectors/src/collectors/__init__.py collectors/src/collectors/__init__.py
COPY orchestrator/ orchestrator/

RUN uv sync --frozen --package orchestrator --no-dev
COPY --from=agent-package /agent-package /workspace/agent-package

WORKDIR /workspace/orchestrator
ENV PATH="/workspace/.venv/bin:$PATH"

# Nicht als root laufen (SECURITY.md)
RUN useradd --system --uid 1001 automato
USER automato

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
