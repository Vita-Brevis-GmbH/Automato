# Build-Kontext: Repo-Root (uv-Workspace) -> docker compose setzt context: .
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /workspace
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
COPY sot-gateway/ sot-gateway/
COPY orchestrator/pyproject.toml orchestrator/pyproject.toml
COPY orchestrator/app/__init__.py orchestrator/app/__init__.py
COPY collectors/ collectors/

RUN uv sync --frozen --package collectors --no-dev

ENV PATH="/workspace/.venv/bin:$PATH"

RUN useradd --system --uid 1001 automato
USER automato

CMD ["celery", "-A", "collectors.celery_app", "worker", "-Q", "collectors", "--loglevel=INFO"]
