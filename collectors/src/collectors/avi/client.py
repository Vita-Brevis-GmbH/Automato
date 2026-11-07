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

from collectors.common.i18n import L

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
DEFAULT_TENANT = "Standard-Tenant"  # ohne X-Avi-Tenant: der Standard-Tenant des Kontos


def error_text(response: httpx.Response) -> str:
    """Begruendung des Controllers (`{"error": "..."}`, sonst der Text) - kurz, eine Zeile."""
    try:
        body = response.json()
    except ValueError:
        body = None
    text = (body.get("error") or body.get("detail") or "") if isinstance(body, dict) else ""
    text = text if isinstance(text, str) else str(text)
    if not text and body is None:
        text = response.text or ""
    return " ".join(text.split())[:200]


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
        # 0.78.3 (Befund: HTTP 400 auf jede Abfrage): lehnt der Controller die API-Version ab,
        # gilt einmal die Abfrage ohne `X-Avi-Version` (sein Standard) - danach fuer alle
        self._version_checked = False

    # --- Sitzung ---

    def _detect_version(self) -> bool:
        """API-Version des Controllers aus `/api/initial-data` - ein Versuch, kurz (0.78.3:
        optional, darf den Lauf nicht mit Wiederholungen aufhalten)."""
        try:
            info = self._client.get(f"{self._base}/api/initial-data", timeout=15.0)
            if info.status_code == 200:
                found = ((info.json() or {}).get("version") or {}).get("Version")
                if found:
                    self.version = str(found)
                    return True
        except (httpx.HTTPError, ValueError, AttributeError):
            pass
        return False

    def login(self) -> None:
        found = self._detect_version()  # meist ohne Anmeldung lesbar
        body = {"username": self._username, "password": self._password}
        response = self._client.post(f"{self._base}/login", json=body)
        if response.status_code in (401, 403):
            raise RuntimeError(
                L(
                    "Avi-Login abgelehnt (Benutzer/Passwort pruefen)",
                    "Avi login rejected (check user/password)",
                )
            )
        response.raise_for_status()
        self._logged_in = True
        if not found:  # sonst angemeldet, erst dann die feste
            self._detect_version()
        self._client.headers["X-Avi-Version"] = self.version

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

    def _get(self, url: str, **kwargs) -> httpx.Response:
        response = read_get(self._client, url, **kwargs)
        if (response.status_code == 400 and not self._version_checked
                and "X-Avi-Version" in self._client.headers):  # fmt: skip
            self._version_checked = True
            version = self._client.headers.pop("X-Avi-Version")
            retry = read_get(self._client, url, **kwargs)
            if retry.status_code == 400:  # nicht die Version: wie vorher weiter
                self._client.headers["X-Avi-Version"] = version
                return response
            self.version = f"{version} (ohne X-Avi-Version)"
            return retry
        if response.is_success:
            self._version_checked = True
        return response

    def _list(self, kind: str, tenant: str | None) -> list[dict[str, Any]]:
        """Alle Objekte einer Art (Seiten zu 200, `include_name` fuer Namen in Verweisen)."""
        headers = {"X-Avi-Tenant": tenant} if tenant else {}
        params: dict[str, Any] = {"page_size": PAGE_SIZE, "include_name": "true", "page": 1}
        out: list[dict[str, Any]] = []
        while True:
            response = self._get(f"{self._base}/api/{kind}", params=params, headers=headers)
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
                    code, why = exc.response.status_code, error_text(exc.response)
                    out["missing"].append(f"{tenant or DEFAULT_TENANT}: {kind} (HTTP {code}"
                                          f"{': ' + why if why else ''})")  # fmt: skip
            out["tenants"][tenant or "admin"] = data
        return out

    def ping(self, read_scope: str = "") -> int:
        """Leichter Lese-Zugriff: Login + Virtual Services des ersten gewaehlten Avi-Tenants
        (sonst des Standard-Tenants des Kontos)."""
        wanted = parse_scope(read_scope)
        return len(self._list("virtualservice", wanted[0] if wanted else None))
