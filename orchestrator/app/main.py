from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    agents,
    appdoc,
    applications,
    auth_config,
    findings,
    health,
    internal,
    jobs,
    naming_profiles,
    nat,
    netbox_sync,
    objects,
    reconcile,
    rules,
    systems,
    tenants,
)
from app.config import check_security_config, get_settings
from app.secrets import resolve_settings


def create_app() -> FastAPI:
    settings = get_settings()
    # Secret-Referenzen (env:/file:/openbao:) aufloesen, bevor die Werte geprueft/genutzt
    # werden; fail-closed, wenn ein Backend nicht erreichbar ist (E1.5, ADR 0007 D4).
    resolve_settings(settings)
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
    app.include_router(auth_config.router)
    app.include_router(jobs.router)
    app.include_router(internal.router)
    app.include_router(agents.router)
    app.include_router(agents.agent_router)
    app.include_router(rules.router)
    app.include_router(findings.router)
    app.include_router(systems.router)
    app.include_router(tenants.router)
    app.include_router(nat.router)
    app.include_router(applications.router)
    app.include_router(appdoc.router)
    app.include_router(naming_profiles.router)
    app.include_router(reconcile.router)
    app.include_router(objects.router)
    app.include_router(netbox_sync.router)
    return app


app = create_app()
