import logging
import secrets
from contextlib import asynccontextmanager

from anyio import to_thread
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__, modules, scheduler
from app.api import (
    agents,
    applications,
    auth_config,
    cabling,
    customer_users,
    customers,
    data_quality,
    findings,
    health,
    internal,
    jobs,
    naming_profiles,
    nat,
    netbox_sync,
    objects,
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
from app.api import modules as module_api
from app.config import Settings, check_security_config, get_settings
from app.db import get_sessionmaker
from app.i18n import middleware as i18n_middleware
from app.secrets import resolve_settings
from app.store import customer_alias

log = logging.getLogger(__name__)


def _lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        stop = None
        # Threads fuer Anfragen (0.80.1): wartende Ergebnisse belegen je einen
        to_thread.current_default_thread_limiter().total_tokens = max(40, settings.api_threads)
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


async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
    """Unerwarteter Fehler (0.94.1, Dev-Befund „HTTP 500 beim Laden der Doku“): ins Protokoll mit
    einer kurzen Referenz, die Antwort nennt nur die Referenz - nie Details oder Daten. Mit der
    Referenz findet der Betrieb die Ursache (`docker compose logs orchestrator | grep <ref>`)."""
    ref = secrets.token_hex(4)
    log.error("Interner Fehler %s bei %s %s", ref, request.method, request.url.path,
              exc_info=exc)  # fmt: skip
    # beide Sprachen: die Sprache der Anfrage gilt hier ausserhalb der Middleware nicht mehr
    detail = (f"Interner Fehler – Referenz {ref} im Protokoll des Orchestrators / "
              f"internal error – reference {ref} in the orchestrator log")  # fmt: skip
    return JSONResponse(status_code=500, content={"detail": detail, "ref": ref})


def create_app() -> FastAPI:
    settings = get_settings()
    # Secret-Referenzen (env:/file:/openbao:) aufloesen, bevor die Werte geprueft/genutzt
    # werden; fail-closed, wenn ein Backend nicht erreichbar ist (E1.5, ADR 0007 D4).
    resolve_settings(settings)
    check_security_config(settings)  # Fail-closed bei unsicheren Defaults (ADR 0005)

    app = FastAPI(title="Automato Orchestrator", version=__version__, lifespan=_lifespan(settings))
    app.add_exception_handler(Exception, _internal_error)

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
    # Sprache je Anfrage aus ?lang= bzw. Accept-Language (0.87.0, ADR 0028) - zuletzt
    # hinzugefuegt = aussen, gilt also auch in den uebrigen Middlewares
    app.add_middleware(i18n_middleware)

    app.include_router(health.router)
    app.include_router(auth_config.router)
    app.include_router(jobs.router)
    app.include_router(internal.router)
    app.include_router(agents.router)
    app.include_router(agents.agent_router)
    app.include_router(rules.router)
    app.include_router(findings.router, dependencies=[Depends(modules.guard("findings"))])
    app.include_router(systems.router)
    app.include_router(tenants.router)
    app.include_router(nat.router)
    app.include_router(applications.router, dependencies=[Depends(modules.guard("doku"))])
    app.include_router(naming_profiles.router)
    app.include_router(reconcile.router, dependencies=[Depends(modules.guard("analyse"))])
    app.include_router(objects.router)
    app.include_router(virtual.router)
    app.include_router(netbox_sync.router, dependencies=[Depends(modules.guard("schreibpfad"))])
    app.include_router(system.router)
    app.include_router(routing.router, dependencies=[Depends(modules.guard(None))])
    app.include_router(topology.router, dependencies=[Depends(modules.guard("analyse"))])
    app.include_router(customers.router)
    app.include_router(customer_users.router)
    app.include_router(zones.router, dependencies=[Depends(modules.guard("design"))])
    app.include_router(cabling.router, dependencies=[Depends(modules.guard("analyse"))])
    app.include_router(data_quality.router, dependencies=[Depends(modules.guard("analyse"))])
    app.include_router(module_api.router)
    return app


app = create_app()
