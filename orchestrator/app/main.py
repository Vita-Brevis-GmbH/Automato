from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, internal, jobs, rules


def create_app() -> FastAPI:
    app = FastAPI(title="Automato Orchestrator", version="0.1.0")

    # Dev-CORS fuer das Web-UI (Vite auf :5173); produktiv einschraenken (M3)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(internal.router)
    app.include_router(rules.router)
    return app


app = create_app()
