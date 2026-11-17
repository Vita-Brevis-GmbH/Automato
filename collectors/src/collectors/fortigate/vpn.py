"""IPsec-Tunnel der FortiGate (1.19.0, ADR 0063) - Rohdaten je VDOM -> NormalizedVpnTunnel.

Gelesen wird nur lesend (`FortiGateClient.routing_tables`, wie das uebrige Routing):

- Phase 1 im Interface-Modus (`cmdb vpn.ipsec/phase1-interface`): Gegenstelle (`remote-gw`,
  `remote-gw6` bzw. bei DDNS der Name), gebundenes Interface, Art (`static`, `dynamic` =
  Dial-up, `ddns`), IKE-Version und Proposals nur als Text;
- Phase 2 (`cmdb vpn.ipsec/phase2-interface`): Selektoren lokal und der Gegenstelle - Subnetz,
  Bereich, Adresse bzw. Adress- und Gruppen-Namen, aufgeloest ueber die Adressen des VDOMs;
- der Zustand aus dem Monitor (`monitor vpn/ipsec`): je Tunnel und Phase 2 up/down, bei Dial-up
  die verbundenen Gegenstellen (Instanzen mit `parent`) mit ihren Netzen.

SSL-VPN wird nicht gelesen (nicht mehr unterstuetzt). Felder nur ueber eine Positivliste:
Pre-Shared-Keys, Zertifikats-Passwoerter, XAUTH und PPK kommen nie mit. Was nicht lesbar war,
steht wie beim Routing in `missing` des Kontexts (Bezeichnungen in `routing.TABLE_LABELS`).
"""

import ipaddress
from typing import Any

from collectors.common.dto import NormalizedVpnPhase2, NormalizedVpnTunnel
from collectors.fortigate.normalize import address_maps, resolve_address
from collectors.fortigate.routing import TABLE_LABELS, prefix

_KIND = {"static": "static", "dynamic": "dialup", "ddns": "ddns"}
_NO_ADDR = {"", "0.0.0.0", "::"}  # noqa: S104 - Platzhalter „nicht gesetzt“, kein Bind
# Positivliste je Phase (nie psksecret, psksecret-remote, ppk-*, authpasswd, xauth*, certificate)
_P1_KEEP = ("proposal", "dhgrp", "mode", "authmethod", "peertype", "net-device", "add-route",
            "auto-discovery-sender", "auto-discovery-receiver", "dpd", "nattraversal", "keylife",
            "comments", "remotegw-ddns", "mode-cfg", "ip-version", "exchange-interface-ip",
            "tunnel-search")  # fmt: skip
_P2_KEEP = ("proposal", "pfs", "dhgrp", "keylifeseconds", "auto-negotiate", "replay", "keepalive",
            "protocol", "src-port", "dst-port", "comments")  # fmt: skip


def _keep(row: dict, keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: row[k] for k in keys if row.get(k) not in (None, "", [], {})}


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _addr(value: Any) -> str | None:
    text = str(value or "").strip()
    return None if text in _NO_ADDR else text


def _host(value: Any) -> str | None:
    """Einzelne Adresse als CIDR (/32 bzw. /128)."""
    try:
        ip = ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None
    return None if str(ip) in _NO_ADDR else f"{ip}/{ip.max_prefixlen}"


def _range(start: Any, end: Any) -> str | None:
    """Bereich `a-b`; ein Bereich aus einer Adresse ist die Adresse, ein ganzes Netz das Netz."""
    try:
        a = ipaddress.ip_address(str(start or "").strip())
        b = ipaddress.ip_address(str(end or start or "").strip())
    except ValueError:
        return None
    if a.version != b.version or a > b:
        return None
    nets = list(ipaddress.summarize_address_range(a, b))
    return str(nets[0]) if len(nets) == 1 else f"{a}-{b}"


def _value(raw: str) -> str | None:
    """Wert eines aufgeloesten Adressobjekts (CIDR, Host, `range:a-b`) -> CIDR bzw. `a-b`."""
    text = raw.split(":", 1)[1] if raw.startswith("range:") else raw
    if "-" in text and "/" not in text:
        a, _, b = text.partition("-")
        return _range(a, b)
    return prefix(text) or _host(text)


def selector(row: dict, side: str, maps: tuple[dict, dict]) -> tuple[list[str], list[str], list]:
    """Selektor einer Seite (`src` lokal, `dst` Gegenstelle): (Werte, Namen, nicht aufloesbar)."""
    kind = str(row.get(f"{side}-addr-type") or "subnet")
    six = kind.endswith("6")
    base = kind.removesuffix("6")
    sfx = "6" if six else ""
    if base == "name":
        name = str(row.get(f"{side}-name{sfx}") or "").strip()
        if not name:
            return [], [], []
        values, unresolved = [], []
        for raw in resolve_address(name, maps[1] if six else maps[0]):
            value = _value(raw) if raw != "all" else ("::/0" if six else "0.0.0.0/0")
            if value:
                values.append(value)
            else:
                unresolved.append(raw)
        return list(dict.fromkeys(values)), [name], unresolved
    if base == "range":
        value = _range(row.get(f"{side}-start-ip{sfx}"), row.get(f"{side}-end-ip{sfx}"))
    elif base == "ip":
        value = _host(row.get(f"{side}-start-ip{sfx}"))
    else:  # subnet
        value = prefix(row.get(f"{side}-subnet{sfx}"))
    return ([value] if value else []), [], []


def _proxy_nets(entries: Any) -> list[str]:
    """Netze eines Selektors im Monitor (`proxy_dst`: [{"subnet": "10.1.0.0/255.255.0.0"}])."""
    out = []
    for e in entries or []:
        net = prefix(e.get("subnet")) if isinstance(e, dict) else None
        if net and net not in out:
            out.append(net)
    return out


def _state(phase2: dict[str, str]) -> str:
    states = set(phase2.values())
    if states and states <= {"up"}:
        return "up"
    return "partial" if "up" in states else "down"


def _monitor(rows: Any) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Monitor -> (Tunnel -> {Gegenstelle, Phase 2 -> Zustand}, Dial-up -> Instanzen)."""
    tunnels: dict[str, dict] = {}
    instances: dict[str, list[dict]] = {}
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("name"):
            continue
        p2 = {str(p.get("p2name") or ""): ("up" if p.get("status") == "up" else "down")
              for p in r.get("proxyid") or [] if isinstance(p, dict)}  # fmt: skip
        parent = r.get("parent") or None
        if parent:
            nets = [n for p in r.get("proxyid") or [] if isinstance(p, dict)
                    for n in _proxy_nets(p.get("proxy_dst"))]  # fmt: skip
            entry = {
                "name": str(r["name"]),
                "remote_gw": _addr(r.get("rgwy")),
                "status": _state(p2) if p2 else "down",
                "phase2": dict(sorted(p2.items())),
                "nets": list(dict.fromkeys(nets)),
            }
            instances.setdefault(str(parent), []).append(entry)
        else:
            tunnels[str(r["name"])] = {"remote_gw": _addr(r.get("rgwy")), "phase2": p2}
    return tunnels, instances


def tunnels(raw: dict, vdom_tables: dict, node: str, vdom: str) -> list[NormalizedVpnTunnel]:
    """IPsec-Tunnel eines VDOMs: `raw` = Rohtabellen des Routings (`ipsec_*`, `missing`),
    `vdom_tables` = Adressen und Gruppen des VDOMs fuer die Namen der Selektoren."""
    phase1 = [r for r in raw.get("ipsec_phase1") or [] if isinstance(r, dict) and r.get("name")]
    if not phase1:
        return []
    maps = address_maps(vdom_tables.get("addresses") or [], vdom_tables.get("groups") or [],
                        vdom_tables.get("addresses6") or [],
                        vdom_tables.get("groups6") or [])  # fmt: skip
    # Zustand nur, wenn der Monitor lesbar war - sonst „unbekannt“ statt „down“
    unread = any(str(m).startswith(TABLE_LABELS["ipsec_status"]) for m in raw.get("missing") or [])
    states, dialups = _monitor(raw.get("ipsec_status"))
    by_p1: dict[str, list[dict]] = {}
    for r in raw.get("ipsec_phase2") or []:
        if isinstance(r, dict) and r.get("name") and r.get("phase1name"):
            by_p1.setdefault(str(r["phase1name"]), []).append(r)
    out = []
    for p1 in phase1:
        name = str(p1["name"])
        kind = _KIND.get(str(p1.get("type") or "static"), "static")
        six = str(p1.get("ip-version") or "4") == "6"
        seen = states.get(name)
        remote = _addr(p1.get("remote-gw6" if six else "remote-gw"))
        if kind == "ddns":  # aufgeloeste Adresse aus dem Monitor, sonst der Name
            remote = (seen or {}).get("remote_gw") or _addr(p1.get("remotegw-ddns"))
        elif kind == "dialup":
            remote = None
        instances = sorted(dialups.get(name, []), key=lambda i: i["name"])
        phase2 = []
        for r in by_p1.get(name, []):
            src, src_names, src_open = selector(r, "src", maps)
            dst, dst_names, dst_open = selector(r, "dst", maps)
            p2name = str(r["name"])
            if unread:
                status = None
            elif kind == "dialup":
                found = [i["phase2"].get(p2name) for i in instances]
                status = "up" if "up" in found else ("down" if found else None)
            else:
                status = (seen or {}).get("phase2", {}).get(p2name, "down")
            phase2.append(NormalizedVpnPhase2(
                name=p2name, src=src, dst=dst, src_names=src_names, dst_names=dst_names,
                unresolved=list(dict.fromkeys([*src_open, *dst_open])), status=status,
                attrs=_keep(r, _P2_KEEP)))  # fmt: skip
        if unread:
            status = None
        elif kind == "dialup":
            status = "up" if any(i["status"] == "up" for i in instances) else "idle"
        elif seen is None or not seen["phase2"]:
            status = "down"
        else:
            status = _state(seen["phase2"])
        attrs = _keep(p1, _P1_KEEP)
        out.append(NormalizedVpnTunnel(
            node=node, context=vdom, name=name, kind=kind,
            interface=str(p1.get("interface") or "") or None,
            remote_gw=remote, local_gw=_addr(p1.get("local-gw6" if six else "local-gw")),
            ike_version=_int(p1.get("ike-version")), status=status, phase2=phase2,
            instances=instances, attrs=attrs))  # fmt: skip
    return out
