"""Agent-Kanal ueber das mTLS-Agent-Gateway (Produktions-Gate, ADR 0008/0013).

Das Gateway (Caddy, `deploy/agent-gateway`) verlangt ein Client-Zertifikat der Agent-CA und
reicht zwei Header an den Orchestrator weiter, die es selbst setzt (vom Client gesendete
Werte werden ueberschrieben): `X-Gateway-Secret` (beweist, dass der Aufruf vom Gateway kommt -
direkt am Orchestrator kann ihn kein Agent setzen) und `X-Client-Cert-Subject` (Subject des
geprueften Zertifikats). Modus `require`: Agent-Aufrufe nur ueber das Gateway; `bind`:
zusaetzlich muss der CN dem Agent-Namen entsprechen. Der zentrale Worker (internes Token)
ist nicht betroffen.
"""

import hmac
import re
from dataclasses import dataclass

from fastapi import Header, HTTPException

from app.config import get_settings

_CN = re.compile(r"(?:^|,)\s*CN=([^,]+)")


@dataclass(frozen=True)
class Channel:
    via_gateway: bool
    cn: str | None


def common_name(subject: str | None) -> str | None:
    hit = _CN.search(subject or "")
    return hit.group(1).strip() if hit else None


def agent_channel(
    x_gateway_secret: str | None = Header(default=None),
    x_client_cert_subject: str | None = Header(default=None),
) -> Channel:
    secret = get_settings().agent_gateway_secret
    via = bool(secret and x_gateway_secret) and hmac.compare_digest(x_gateway_secret, secret)
    return Channel(via_gateway=via, cn=common_name(x_client_cert_subject) if via else None)


def enforce(channel: Channel, agent_name: str) -> None:
    mode = get_settings().internal_mtls_mode
    if mode == "off":
        return
    if not channel.via_gateway or not channel.cn:
        raise HTTPException(status_code=403, detail="agent channel requires the mTLS gateway")
    if mode == "bind" and channel.cn != agent_name:
        raise HTTPException(
            status_code=403, detail="client certificate does not belong to this agent"
        )
