import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__, scheduler
from app.api import (
    agents,
    appdoc,
    applications,
    auth_config,
    customer_users,
    customers,
    findings,
    health,
    internal,
    jobs,
    naming_profiles,
    nat,
    netbox_sync,
    objects,
    published_services,
    reconcile,
    routing,
    rules,
    system,
    systems,
    tenants,
    topology,
    virtual,
    zones,
)
from app.config import Settings, check_security_config, get_settings
from app.db import get_sessionmaker
from app.secrets import resolve_settings
from app.store import customer_alias

log = logging.getLogger(__name__)


def _lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        stop = None
        try:  # mit dem alten Prozess gestorbene NetBox-Syncs beenden (Dev-Befund 0.11.4)
            netbox_sync.abort_interrupted(get_sessionmaker())
        except Exception:  # noqa: BLE001 - z. B. Datenbank noch nicht migriert: Start geht vor
            log.exception("Haengende NetBox-Syncs nicht bereinigt")
        if settings.scheduler_enabled:  # zeitgesteuerte Laeufe je Quellsystem
            stop = scheduler.start(
                get_sessionmaker(),
                settings.scheduler_timezone,
                settings.scheduler_interval_seconds,
            )
        yield
        if stop is not None:
            stop.set()

    return lifespan


def create_app() -> FastAPI:
    settings = get_settings()
    # Secret-Referenzen (env:/file:/openbao:) aufloesen, bevor die Werte geprueft/genutzt
    # werden; fail-closed, wenn ein Backend nicht erreichbar ist (E1.5, ADR 0007 D4).
    resolve_settings(settings)
    check_security_config(settings)  # Fail-closed bei unsicheren Defaults (ADR 0005)

    app = FastAPI(title="Automato Orchestrator", version=__version__, lifespan=_lifespan(settings))

    # CORS aus Konfiguration; Default nur localhost (Dev), Produktion explizit setzen
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Total-Count"],  # Gesamtzahl seitenweiser Listen (/rules)
    )

    # Antworten an Kunden-Benutzer ohne Geraetenamen (0.58.0, ADR 0018 Schritt 4)
    app.middleware("http")(customer_alias.middleware)

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
    app.include_router(published_services.router)
    app.include_router(applications.router)
    app.include_router(appdoc.router)
    app.include_router(naming_profiles.router)
    app.include_router(reconcile.router)
    app.include_router(objects.router)
    app.include_router(virtual.router)
    app.include_router(netbox_sync.router)
    app.include_router(system.router)
    app.include_router(routing.router)
    app.include_router(topology.router)
    app.include_router(customers.router)
    app.include_router(customer_users.router)
    app.include_router(zones.router)
    return app


app = create_app()
