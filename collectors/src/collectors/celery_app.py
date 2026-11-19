"""Celery-Worker-App der Collectors.

Konsumiert die Queue "collectors"; Task-Namen sind der Vertrag mit dem
Orchestrator (collectors.<platform>.collect).
"""

from celery import Celery
from sot_gateway import trust

from collectors.config import get_settings

# zusaetzliche CAs aus deploy/trust (1.10.0, ADR 0053) - der zentrale Worker liest wie der
# Orchestrator (z. B. die NetBox eines Kunden hinter dem Front-Proxy); ohne Ordner nichts
trust.zusaetzliche_cas()

app = Celery(
    "automato-collectors",
    broker=get_settings().redis_url,
    include=[
        "collectors.fortigate.tasks",
        "collectors.cisco_aci.tasks",
        "collectors.ipam.tasks",
        "collectors.vmware.tasks",
        "collectors.cisco_switch.tasks",
        "collectors.avi.tasks",
    ],
)
app.conf.task_default_queue = "collectors"
# TLS intern (0.49.0): rediss:// mit Pruefung gegen die interne CA
app.conf.broker_use_ssl = get_settings().broker_ssl()
