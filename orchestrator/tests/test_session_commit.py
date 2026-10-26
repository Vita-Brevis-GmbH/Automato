"""Commit vor der Antwort (Dev-Befund 0.11.1): FastAPI fuehrt den Teil einer Dependency nach
`yield` ohne `scope="function"` erst nach dem Senden aus - ein Agent bekam 204 fuer den
letzten Teil-Upload, bevor er gespeichert war, und das Ergebnis zaehlte 6000 von 6624."""

from app.db import get_session
from app.main import create_app
from fastapi.routing import APIRoute


def _session_deps(dependant):
    for dep in dependant.dependencies:
        if dep.call is get_session:
            yield dep
        yield from _session_deps(dep)


def _api_routes(routes):
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        inner = getattr(r, "original_router", None)  # eingebundener Router (FastAPI >= 0.13x)
        if inner is not None:
            yield from _api_routes(inner.routes)


def test_jede_route_committet_vor_der_antwort():
    routes = list(_api_routes(create_app().routes))
    using = [(r.path, d) for r in routes for d in _session_deps(r.dependant)]
    assert len(using) > 50  # praktisch alle Routen nutzen die Session
    late = sorted({path for path, d in using if d.scope != "function"})
    assert late == []
