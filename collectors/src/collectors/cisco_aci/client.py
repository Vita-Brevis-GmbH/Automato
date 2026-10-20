"""Cisco-APIC-REST-Client, rein lesend (Model 1, ADR 0009).

Login per `aaaLogin` mit einem read-only APIC-Benutzer (Session-Cookie `APIC-cookie`),
dann je ACI-Tenant der vollständige MO-Baum (`rsp-subtree=full`, verschachtelt wie ein
Tenant-Export) - genau das Format, das `mo.parse_mo_export`/`parse_epgs` lesen. Abmelden
am Ende (`aaaLogout`), damit keine Sitzungen offen bleiben. Testbar über einen injizierten
httpx.Client (MockTransport) - kein Lab nötig.
"""

from typing import Any

import httpx

# Fabric-interne Tenants: ohne ausdrückliche Auswahl nicht lesen (keine Kunden-Contracts)
SYSTEM_TENANTS = frozenset({"infra", "mgmt"})


def parse_scope(read_scope: str) -> list[str]:
    """`"TE_a, TE_b"` -> `["TE_a", "TE_b"]` (leer = alle Tenants)."""
    return [x.strip() for x in read_scope.split(",") if x.strip()]


class ApicClient:
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
        self._client = client or httpx.Client(verify=verify_tls, timeout=60.0)
        self._logged_in = False

    # --- Sitzung ---

    def login(self) -> None:
        body = {"aaaUser": {"attributes": {"name": self._username, "pwd": self._password}}}
        response = self._client.post(f"{self._base}/api/aaaLogin.json", json=body)
        response.raise_for_status()
        imdata = response.json().get("imdata") or [{}]
        token = (imdata[0].get("aaaLogin") or {}).get("attributes", {}).get("token")
        if not token:
            raise RuntimeError("APIC-Login ohne Token (Benutzer/Passwort pruefen)")
        self._client.cookies.set("APIC-cookie", token)
        self._logged_in = True

    def logout(self) -> None:
        if not self._logged_in:
            return
        try:
            body = {"aaaUser": {"attributes": {"name": self._username}}}
            self._client.post(f"{self._base}/api/aaaLogout.json", json=body)
        except httpx.HTTPError:
            pass  # Sitzung laeuft serverseitig ab
        self._logged_in = False

    def __enter__(self) -> "ApicClient":
        self.login()
        return self

    def __exit__(self, *exc: object) -> None:
        self.logout()

    # --- Lesen ---

    def _get(self, path: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        response = self._client.get(f"{self._base}{path}", params=params)
        response.raise_for_status()
        return response.json().get("imdata", [])

    def tenants(self) -> list[str]:
        rows = self._get("/api/class/fvTenant.json")
        return sorted(r["fvTenant"]["attributes"]["name"] for r in rows if "fvTenant" in r)

    def tenant_tree(self, name: str) -> list[dict[str, Any]]:
        """Vollständiger MO-Baum eines Tenants (verschachtelt, inkl. fvCEp/fvIp)."""
        return self._get(f"/api/mo/uni/tn-{name}.json", {"rsp-subtree": "full"})

    def export(self, read_scope: str = "") -> dict[str, Any]:
        """MO-Export der gewaehlten Tenants (leer = alle ausser infra/mgmt)."""
        wanted = parse_scope(read_scope)
        available = self.tenants()
        if wanted:
            missing = [t for t in wanted if t not in available]
            if missing:
                raise RuntimeError(f"ACI-Tenant nicht gefunden: {', '.join(missing)}")
            names = wanted
        else:
            names = [t for t in available if t not in SYSTEM_TENANTS]
        imdata = [mo for name in names for mo in self.tenant_tree(name)]
        return {"totalCount": str(len(imdata)), "imdata": imdata}

    def ping(self) -> int:
        """Leichter Lese-Zugriff (Login + Tenant-Liste); gibt die Anzahl Tenants zurueck."""
        return len(self.tenants())
