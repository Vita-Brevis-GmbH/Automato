# Build-Kontext: Repo-Root (uv-Workspace) -> docker compose setzt context: .

# --- Agent-Paket (ADR 0008): collectors-Wheel + Windows-Skripte, im UI herunterladbar ---
FROM python:3.12-slim AS agent-package
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY orchestrator/pyproject.toml orchestrator/pyproject.toml
# sot-gateway komplett (nicht nur pyproject.toml) - sonst entsteht ein leeres Wheel ohne Modul
COPY sot-gateway/ sot-gateway/
COPY collectors/ collectors/
RUN uv build --package collectors --wheel --out-dir /agent-package \
    && uv build --package sot-gateway --wheel --out-dir /agent-package
# Bau abbrechen, wenn ein Wheel sein Modul nicht enthaelt (Agent startet sonst nicht)
COPY scripts/check_agent_package.py /tmp/check_agent_package.py
RUN python /tmp/check_agent_package.py /agent-package
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

# Nicht als root laufen (SECURITY.md). /var/lib/automato haelt die Schluesseldatei der
# Geraete-Zugangsdaten (ADR 0009) - eigenes Volume, gehoert dem Dienstbenutzer.
RUN useradd --system --uid 1001 automato \
    && mkdir -p /var/lib/automato && chown automato /var/lib/automato && chmod 700 /var/lib/automato
USER automato

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
