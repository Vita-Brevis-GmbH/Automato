"""Celery-Producer: Der Orchestrator verteilt Jobs, fuehrt aber selbst keine aus.

Collector-Tasks laufen in eigenen Prozessen (siehe collectors/); der Orchestrator
kennt nur Task-Namen und Queue. Ergebnisse kommen ueber die interne HTTP-API
zurueck, nicht ueber ein Celery-Result-Backend.
"""

from celery import Celery

from app import tls
from app.config import get_settings

COLLECTOR_QUEUE = "collectors"


def get_celery_app() -> Celery:
    settings = get_settings()
    app = Celery("automato", broker=tls.redis_url(settings))
    # TLS intern (0.49.0): rediss:// mit Pruefung gegen die interne CA
    app.conf.broker_use_ssl = tls.redis_ssl(settings)
    return app


def dispatch_collect(platform: str, run_id: str) -> None:
    get_celery_app().send_task(
        f"collectors.{platform}.collect",
        kwargs={"run_id": run_id},
        queue=COLLECTOR_QUEUE,
    )
