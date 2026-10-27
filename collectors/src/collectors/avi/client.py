"""REST-Client fuer den VMware NSX Advanced Load Balancer (Avi), rein lesend (ADR 0027).

Anmeldung per `POST /login` (Sitzungs-Cookie `sessionid`, `csrftoken`), danach nur `GET` auf
eine Positivliste von Objekten je Avi-Tenant (`X-Avi-Tenant`), Abmelden am Ende
(`POST /logout`). Die Sicht ueber alle Tenants (`X-Avi-Tenant: *`) ist Superusern vorbehalten -
ein Nur-Lese-Konto liest Tenant fuer Tenant. Testbar ueber einen injizierten httpx.Client
(MockTransport) - kein Lab noetig.
"""

from typing import Any

import httpx
from sot_gateway.readonly import enforce_read_only
from sot_gateway.readonly import get as read_get

# Positivliste: nur diese Objekte liest Automato (keine Zertifikate, Schluessel, Benutzer)
OBJECTS = (
    "virtualservice",
    "vsvip",
    "pool",
    "poolgroup",
    "vrfcontext",
    "applicationprofile",
)
INVENTORY = "virtualservice-inventory"  # Betriebszustand der Virtual Services
PAGE_SIZE = 200
MAX_OBJECTS = 20_000  # je Art und Tenant
FALLBACK_VERSION = "20.1.1"  # API-Version, falls der Controller sie nicht nennt


def parse_scope(read_scope: str) -> list[str]:
    """`"admin, kunde-a"` -> `["admin", "kunde-a"]` (leer = alle sichtbaren Tenants)."""
    return [x.strip() for x in read_scope.split(",") if x.strip()]


class AviClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        verify_tls: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        # Nur lesend (ADR 0010/0027): POST nur fuer die Sitzung
        self._client = enforce_read_only(
            client or httpx.Client(verify=verify_tls, timeout=60.0),
            allow_post=("/login", "/logout"),
        )
        self._logged_in = False
        self.version = FALLBACK_VERSION

    # --- Sitzung ---

    def login(self) -> None:
        try:  # Version ohne Anmeldung (fehlt sie, gilt die feste)
            info = read_get(self._client, f"{self._base}/api/initial-data")
            if info.status_code == 200:
                found = ((info.json() or {}).get("version") or {}).get("Version")
                if found:
                    self.version = str(found)
        except (httpx.HTTPError, ValueError):
            pass
        body = {"username": self._username, "password": self._password}
        response = self._client.post(f"{self._base}/login", json=body)
        if response.status_code in (401, 403):
            raise RuntimeError("Avi-Login abgelehnt (Benutzer/Passwort pruefen)")
        response.raise_for_status()
        self._client.headers["X-Avi-Version"] = self.version
        self._logged_in = True

    def logout(self) -> None:
        if not self._logged_in:
            return
        try:
            token = self._client.cookies.get("csrftoken")
            headers = {"Referer": f"{self._base}/", **({"X-CSRFToken": token} if token else {})}
            self._client.post(f"{self._base}/logout", headers=headers)
        except httpx.HTTPError:
            pass  # Sitzung laeuft serverseitig ab
        self._logged_in = False

    def __enter__(self) -> "AviClient":
        self.login()
        return self

    def __exit__(self, *exc: object) -> None:
        self.logout()

    # --- Lesen ---

    def _list(self, kind: str, tenant: str | None) -> list[dict[str, Any]]:
        """Alle Objekte einer Art (Seiten zu 200, `include_name` fuer Namen in Verweisen)."""
        headers = {"X-Avi-Tenant": tenant} if tenant else {}
        params: dict[str, Any] = {"page_size": PAGE_SIZE, "include_name": "true", "page": 1}
        out: list[dict[str, Any]] = []
        while True:
            response = read_get(
                self._client, f"{self._base}/api/{kind}", params=params, headers=headers
            )
            response.raise_for_status()
            data = response.json() or {}
            out += [r for r in data.get("results") or [] if isinstance(r, dict)]
            if not data.get("next") or len(out) >= MAX_OBJECTS:
                return out[:MAX_OBJECTS]
            params["page"] += 1

    def tenants(self) -> list[str]:
        return sorted(t["name"] for t in self._list("tenant", None) if t.get("name"))

    def gather(self, read_scope: str = "") -> dict[str, Any]:
        """Je Tenant die Objekte der Positivliste; fehlende Rechte = Teilfehler (`missing`)."""
        wanted = parse_scope(read_scope)
        if wanted:
            names = wanted
        else:
            try:
                names = self.tenants()
            except httpx.HTTPStatusError:  # ohne Leserecht auf Tenants: der eigene
                names = []
            names = names or [""]
        out: dict[str, Any] = {"version": self.version, "tenants": {}, "missing": []}
        for tenant in names:
            data: dict[str, Any] = {}
            for kind in (*OBJECTS, INVENTORY):
                try:
                    data[kind] = self._list(kind, tenant)
                except httpx.HTTPStatusError as exc:
                    data[kind] = []
                    code = exc.response.status_code
                    out["missing"].append(f"{tenant}: {kind} (HTTP {code})")
            out["tenants"][tenant or "admin"] = data
        return out

    def ping(self) -> int:
        """Leichter Lese-Zugriff (Login + Virtual Services des Standard-Tenants)."""
        return len(self._list("virtualservice", None))
