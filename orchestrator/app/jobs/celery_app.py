"""Celery-Producer: Der Orchestrator verteilt Jobs, fuehrt aber selbst keine aus.

Collector-Tasks laufen in eigenen Prozessen (siehe collectors/); der Orchestrator
kennt nur Task-Namen und Queue. Ergebnisse kommen ueber die interne HTTP-API
zurueck, nicht ueber ein Celery-Result-Backend.
"""

from celery import Celery

from app.config import get_settings

COLLECTOR_QUEUE = "collectors"


def get_celery_app() -> Celery:
    return Celery("automato", broker=get_settings().redis_url)


def dispatch_collect(platform: str, run_id: str) -> None:
    get_celery_app().send_task(
        f"collectors.{platform}.collect",
        kwargs={"run_id": run_id},
        queue=COLLECTOR_QUEUE,
    )
