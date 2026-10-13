from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import findings, health, internal, jobs, rules, systems
from app.config import check_security_config, get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    check_security_config(settings)  # Fail-closed bei unsicheren Defaults (ADR 0005)

    app = FastAPI(title="Automato Orchestrator", version="0.1.0")

    # CORS aus Konfiguration; Default nur localhost (Dev), Produktion explizit setzen
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(internal.router)
    app.include_router(rules.router)
    app.include_router(findings.router)
    app.include_router(systems.router)
    return app


app = create_app()
