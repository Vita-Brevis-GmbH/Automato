"""FortiGate-REST-Client (FortiOS cmdb-API), rein lesend (UC1).

Liest Policies, Adress-/Adressgruppen-, Service-Objekte und Zonen. Auth über
Bearer-Token eines read-only API-Users (siehe LAB.md). Testbar über einen
injizierten httpx.Client (MockTransport) - kein Lab nötig.

VDOMs (ADR 0019): `gather` liest die Liste der VDOMs und danach jeden VDOM einzeln
(`vdom=`-Parameter). Ohne Recht auf die Liste (VDOM-Admin) bleibt es beim Standard-VDOM des
Kontos; sein Name kommt aus der Antwort (`"vdom": "root"`).
"""

import re
from typing import Any

import httpx
from sot_gateway.readonly import enforce_read_only


class NotJson(ValueError):
    """FortiGate antwortet mit HTTP 2xx, aber ohne JSON (leer, HTML) - mit Pfad statt des
    nackten "Expecting value: line 1 column 1 (char 0)" (Dev-Befund 0.11.1)."""

    def __init__(self, path: str, response: httpx.Response) -> None:
        kind = response.headers.get("content-type", "").split(";")[0].strip() or "ohne Typ"
        size = len(response.content)
        body = "leer" if not size else f"{kind}, {size} Bytes"
        title = _title(response)  # z. B. Login- oder Sperrseite eines Proxys dazwischen
        super().__init__(
            f"FortiGate antwortet auf {path} nicht mit JSON (HTTP {response.status_code}, {body}"
            + (f", Seite „{title}“" if title else "")
            + ")"
        )
        self.path = path


_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _title(response: httpx.Response) -> str:
    """Titel einer HTML-Antwort (nie der Inhalt selbst), gekuerzt."""
    match = _TITLE.search(response.text[:4000]) if response.content else None
    return " ".join(match.group(1).split())[:80] if match else ""


def _json(path: str, response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise NotJson(path, response) from exc


# Interne VDOMs von FortiOS 7.x (HA, FortiManager-Verbindung) - ohne Policies
_INTERNAL_VDOM = re.compile(r"^vsys_", re.IGNORECASE)


class FortiGateClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        verify_tls: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self._root = f"{base_url.rstrip('/')}/api/v2"
        self._base = f"{self._root}/cmdb"
        self._headers = {"Authorization": f"Bearer {token}"}
        # Nur lesend (ADR 0010): jede schreibende Methode bricht vor dem Senden ab
        self._client = enforce_read_only(client or httpx.Client(verify=verify_tls, timeout=20.0))
        self.default_vdom: str | None = None  # VDOM der Antworten ohne vdom-Parameter

    def _get(self, path: str, vdom: str | None = None) -> list[dict[str, Any]]:
        response = self._client.get(
            f"{self._base}/{path}",
            headers=self._headers,
            params={"vdom": vdom} if vdom else None,
        )
        response.raise_for_status()
        body = _json(path, response)
        if not isinstance(body, dict):
            return []
        if vdom is None and isinstance(body.get("vdom"), str):
            self.default_vdom = self.default_vdom or body["vdom"]
        return body.get("results", [])

    def vdoms(self) -> list[str | None]:
        """Namen der VDOMs (cmdb system/vdom, global). Ohne Recht darauf bzw. ohne die Tabelle:
        `[None]` = nur der Standard-VDOM des Kontos (Abfragen ohne vdom-Parameter)."""
        rows = self._optional("system/vdom")
        names = [
            str(r["name"])
            for r in rows
            if isinstance(r, dict) and r.get("name") and not _INTERNAL_VDOM.match(str(r["name"]))
        ]
        return list(dict.fromkeys(names)) or [None]

    def interfaces(self, vdom: str | None = None) -> list[dict[str, Any]]:
        return self._get("system/interface", vdom)

    def zones(self, vdom: str | None = None) -> list[dict[str, Any]]:
        return self._get("system/zone", vdom)

    def system(self) -> dict[str, Any]:
        """Hostname (cmdb system/global) + Modell/Seriennummer/Version (monitor, optional)."""
        info: dict[str, Any] = {}
        glob = self._get("system/global")
        if isinstance(glob, dict):
            info["hostname"] = glob.get("hostname")
        try:
            response = self._client.get(
                f"{self._root}/monitor/system/status", headers=self._headers
            )
            response.raise_for_status()
            body = _json("monitor/system/status", response)
            results = body.get("results") or {}
            info.update(
                model=results.get("model_name") or results.get("model"),
                serial=body.get("serial"),
                version=body.get("version"),
            )
        except (httpx.HTTPError, NotJson, AttributeError):
            pass  # Monitor-API im Profil nicht freigegeben/anders: ohne Modell/Seriennummer
        return info

    def policies(self, vdom: str | None = None) -> list[dict[str, Any]]:
        return self._get("firewall/policy", vdom)

    def policies6(self, vdom: str | None = None) -> list[dict[str, Any]]:
        """IPv6-Policies in eigener Tabelle (FortiOS 6.x). Ab FortiOS 7.0 stehen sie in
        firewall/policy (srcaddr6/dstaddr6) - die Tabelle fehlt dann (404/405/400)."""
        return self._optional("firewall/policy6", vdom)

    def addresses(self, vdom: str | None = None) -> list[dict[str, Any]]:
        return self._get("firewall/address", vdom)

    def address_groups(self, vdom: str | None = None) -> list[dict[str, Any]]:
        return self._get("firewall/addrgrp", vdom)

    def services(self, vdom: str | None = None) -> list[dict[str, Any]]:
        # FortiOS-CMDB: Service-Objekte liegen unter dem gepunkteten Pfad
        # "firewall.service/custom" (= config firewall service custom), NICHT
        # "firewall/service/custom" (das liefert 400 Bad Request).
        return self._get("firewall.service/custom", vdom)

    def service_groups(self, vdom: str | None = None) -> list[dict[str, Any]]:
        return self._get("firewall.service/group", vdom)

    def addresses6(self, vdom: str | None = None) -> list[dict[str, Any]]:
        return self._get("firewall/address6", vdom)

    def address_groups6(self, vdom: str | None = None) -> list[dict[str, Any]]:
        return self._get("firewall/addrgrp6", vdom)

    def vips(self, vdom: str | None = None) -> list[dict[str, Any]]:
        # Virtual IPs (DNAT/Port-Forward): externe IP -> internes Ziel.
        return self._get("firewall/vip", vdom)

    def vips6(self, vdom: str | None = None) -> list[dict[str, Any]]:
        """IPv6-VIPs inkl. Virtual Server (server-load-balance)."""
        return self._optional("firewall/vip6", vdom)

    def ldb_monitors(self, vdom: str | None = None) -> list[dict[str, Any]]:
        """Health-Checks der Virtual Server (Typ, Port, Intervall) - ohne Lese-Recht leer."""
        return self._optional("firewall/ldb-monitor", vdom)

    def _optional(self, path: str, vdom: str | None = None) -> list[dict[str, Any]]:
        """Tabelle, die je nach FortiOS-Version, Modell bzw. Profil fehlen darf (400/403/404/405
        oder eine Antwort ohne JSON)."""
        try:
            return self._get(path, vdom)
        except NotJson:
            return []
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (400, 403, 404, 405):
                return []
            raise

    def ippools(self, vdom: str | None = None) -> list[dict[str, Any]]:
        # IP-Pools fuer Source-NAT (IPP_).
        return self._get("firewall/ippool", vdom)

    def ping(self) -> None:
        """Leichter Lese-Zugriff zur Erreichbarkeits-/Auth-Pruefung (kein Ingest)."""
        self._get("system/global")

    def vdom_tables(self, vdom: str | None) -> dict[str, list]:
        """Was die Normalisierung je VDOM braucht (IPv4+IPv6, NAT, Zonen, Interfaces)."""
        return {
            "policies": self.policies(vdom),
            "policies6": self.policies6(vdom),
            "addresses": self.addresses(vdom),
            "addresses6": self.addresses6(vdom),
            "groups": self.address_groups(vdom),
            "groups6": self.address_groups6(vdom),
            "services": self.services(vdom),
            "service_groups": self.service_groups(vdom),
            "vips": self.vips(vdom),
            "vips6": self.vips6(vdom),
            "ldb_monitors": self.ldb_monitors(vdom),
            "ippools": self.ippools(vdom),
            "zones": self.zones(vdom),
            "interfaces": self.interfaces(vdom),
        }

    def gather(self) -> dict[str, Any]:
        """Alles einlesen, je VDOM (ADR 0019): `vdoms` = [{vdom, policies, ...}], dazu die
        Interfaces aller VDOMs (global, je Interface sein VDOM) und das System.

        Ein VDOM ohne Leserecht (HTTP 403) fehlt und steht in `skipped`, statt den Lauf
        abzubrechen; ist keiner lesbar, bricht der Lauf mit dem ersten Fehler ab."""
        vdoms: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        interfaces: dict[str, dict[str, Any]] = {}
        denied: httpx.HTTPStatusError | None = None
        for name in self.vdoms():
            try:
                tables = self.vdom_tables(name)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 403:
                    raise
                denied = denied or exc
                skipped.append({"vdom": str(name), "error": f"HTTP {exc.response.status_code}"})
                continue
            vdom = name or self.default_vdom
            # Interfaces sind global; je nach Version bzw. Profil liefert die Abfrage alle oder
            # nur die des VDOMs - zusammenfuehren, ohne VDOM-Feld gilt der abgefragte VDOM
            for iface in tables.pop("interfaces"):
                if isinstance(iface, dict) and iface.get("name"):
                    interfaces.setdefault(iface["name"], {"vdom": vdom, **iface})
            vdoms.append({"vdom": vdom, **tables})
        if not vdoms and denied is not None:
            raise denied
        return {
            "vdoms": vdoms,
            "skipped": skipped,
            "interfaces": list(interfaces.values()),
            "system": self.system(),
        }
