"""FortiGate-REST-Client (FortiOS cmdb-API), rein lesend (UC1).

Liest Policies, Adress-/Adressgruppen-, Service-Objekte und Zonen. Auth über
Bearer-Token eines read-only API-Users (siehe LAB.md). Testbar über einen
injizierten httpx.Client (MockTransport) - kein Lab nötig.

VDOMs (ADR 0019): `gather` liest die Liste der VDOMs und danach jeden VDOM einzeln
(`vdom=`-Parameter). Ohne Recht auf die Liste (VDOM-Admin) bleibt es beim Standard-VDOM des
Kontos; sein Name kommt aus der Antwort (`"vdom": "root"`).
"""

import json
import re
from typing import Any

import httpx
from sot_gateway.readonly import enforce_read_only
from sot_gateway.readonly import get as read_get


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

# Monitor-Listen (Routen, BGP-Pfade): kuerzt die FortiGate auf eine Seite, wird mit start/count
# weitergelesen; mehr als ROUTE_LIMIT Eintraege je Tabelle werden nicht uebernommen
MONITOR_PAGE = 1000
ROUTE_LIMIT = 50_000


class TooLarge(ValueError):
    """Tabelle groesser als ROUTE_LIMIT (z. B. volle Internet-Tabelle per BGP)."""


def routing_label(key: str) -> str:
    from collectors.fortigate.routing import TABLE_LABELS

    return TABLE_LABELS.get(key, key)


def _optional_read(read) -> tuple[Any, str | None]:
    """(Ergebnis, Grund): ohne Recht bzw. zu gross -> Grund; fehlende Tabelle -> leer."""
    try:
        return read(), None
    except TooLarge as exc:
        return [], f"zu gross: {exc}"
    except NotJson:
        return [], None
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (400, 404, 405, 424):  # Tabelle fehlt (Version) bzw. Funktion aus
            return [], None
        return [], f"HTTP {code}"


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
        response = read_get(
            self._client,
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
            response = read_get(
                self._client, f"{self._root}/monitor/system/status", headers=self._headers
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

    # --- Routing, BGP, HA (ADR 0019 Stufe 2, nur lesend) ---

    def _monitor(self, path: str, vdom: str | None = None, params: dict | None = None) -> Any:
        query = {**(params or {}), **({"vdom": vdom} if vdom else {})}
        response = read_get(
            self._client,
            f"{self._root}/monitor/{path}",
            headers=self._headers,
            params=query or None,
        )
        response.raise_for_status()
        body = _json(f"monitor/{path}", response)
        return body.get("results", []) if isinstance(body, dict) else []

    def _monitor_rows(self, path: str, vdom: str | None = None) -> list[dict[str, Any]]:
        """Liste aus dem Monitor-API; kuerzt die FortiGate, weiter mit start/count (ohne
        Doppelte - eine FortiGate, die start ignoriert, liefert nichts Neues)."""
        rows = self._monitor(path, vdom)
        if not isinstance(rows, list):
            return []
        seen = {json.dumps(r, sort_keys=True, default=str) for r in rows}
        while len(rows) >= MONITOR_PAGE:
            if len(rows) > ROUTE_LIMIT:
                raise TooLarge(f"{path}: mehr als {ROUTE_LIMIT} Eintraege")
            try:
                page = self._monitor(path, vdom, {"start": len(rows), "count": MONITOR_PAGE})
            except (httpx.HTTPStatusError, NotJson):
                break
            new = [r for r in page or [] if json.dumps(r, sort_keys=True, default=str) not in seen]
            if not new:
                break
            rows += new
            seen |= {json.dumps(r, sort_keys=True, default=str) for r in new}
        return rows

    def routing_tables(self, vdom: str | None) -> dict[str, Any]:
        """Routing eines VDOMs: wirksame Tabelle, BGP-Pfade und -Nachbarn, statische und
        Policy-Routen, SD-WAN. Ohne Leserecht (401/403) bzw. zu gross steht die Tabelle in
        `missing`; fehlt sie (400/404/405, andere Version, nicht konfiguriert), bleibt sie leer."""
        reads = {
            "rib4": lambda: self._monitor_rows("router/ipv4", vdom),
            "rib6": lambda: self._monitor_rows("router/ipv6", vdom),
            "bgp_paths4": lambda: self._monitor_rows("router/bgp/paths", vdom),
            "bgp_paths6": lambda: self._monitor_rows("router/bgp/paths6", vdom),
            "bgp_neighbors4": lambda: self._monitor("router/bgp/neighbors", vdom),
            "bgp_neighbors6": lambda: self._monitor("router/bgp/neighbors6", vdom),
            "static4": lambda: self._get("router/static", vdom),
            "static6": lambda: self._get("router/static6", vdom),
            "policy4": lambda: self._get("router/policy", vdom),
            "policy6": lambda: self._get("router/policy6", vdom),
            "sdwan": lambda: self._get("system/sdwan", vdom),
        }
        out: dict[str, Any] = {"missing": []}
        for key, read in reads.items():
            out[key], reason = _optional_read(read)
            if reason:
                out["missing"].append(f"{routing_label(key)} ({reason})")
        return out

    def ha(self) -> dict[str, Any]:
        """HA-Konfiguration und -Mitglieder, Session-Sync zu anderen Clustern (global)."""
        out: dict[str, Any] = {"missing": []}
        for key, read in (
            ("ha", lambda: self._get("system/ha")),
            ("peers", lambda: self._monitor("system/ha-peer")),
            ("sync", lambda: self._get("system/standalone-cluster")),
        ):
            out[key], reason = _optional_read(read)
            if reason:
                out["missing"].append(f"HA {key} ({reason})")
        if not out["sync"]:  # FortiOS bis 7.0: config system cluster-sync
            out["sync"], _ = _optional_read(lambda: self._get("system/cluster-sync"))
        return out

    def ippools(self, vdom: str | None = None) -> list[dict[str, Any]]:
        # IP-Pools fuer Source-NAT (IPP_).
        return self._get("firewall/ippool", vdom)

    def ippools6(self, vdom: str | None = None) -> list[dict[str, Any]]:
        """IPv6-IP-Pools fuer Source-NAT (0.47.0) - ohne Tabelle bzw. Recht leer."""
        return self._optional("firewall/ippool6", vdom)

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
            "ippools6": self.ippools6(vdom),
            "zones": self.zones(vdom),
            "interfaces": self.interfaces(vdom),
        }

    def gather(self) -> dict[str, Any]:
        """Alles einlesen, je VDOM (ADR 0019): `vdoms` = [{vdom, policies, ..., routing}], dazu
        die Interfaces aller VDOMs (global, je Interface sein VDOM), HA und das System.

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
            vdoms.append({"vdom": vdom, **tables, "routing": self.routing_tables(name)})
        if not vdoms and denied is not None:
            raise denied
        return {
            "vdoms": vdoms,
            "skipped": skipped,
            "interfaces": list(interfaces.values()),
            "ha": self.ha(),
            "system": self.system(),
        }
