"""Celery-Worker-App der Collectors.

Konsumiert die Queue "collectors"; Task-Namen sind der Vertrag mit dem
Orchestrator (collectors.<platform>.collect).
"""

from celery import Celery

from collectors.config import get_settings

app = Celery(
    "automato-collectors",
    broker=get_settings().redis_url,
    include=[
        "collectors.fortigate.tasks",
        "collectors.cisco_aci.tasks",
        "collectors.ipam.tasks",
        "collectors.vmware.tasks",
    ],
)
app.conf.task_default_queue = "collectors"
