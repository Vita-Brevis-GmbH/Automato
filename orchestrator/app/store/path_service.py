"""Dienst je Stelle des Wegs (0.137.0).

Anwender 2026-11-05: „wenn wir bei den VIPs oder Loadbalancer die Ports anpassen, dann wird
ebenso nicht sauber angezeigt, da das Service Feld nicht genau definiert ist, wo es hin gehört,
zum Zielsystem oder zum VIP … Der Dienst soll zugewiesen werden können, auswahlfeld, also
Clientseitig, Eingang Loadbalancer oder beim Ziel Server, Standard wäre Clientseitig.“

Stellen (`AT`):

- **client** (Vorgabe): der Port, den der Client an der ersten Adresse nutzt - der VIP der
  Firewall bzw. des Load Balancers, sonst am Ziel.
- **lb**: der Port am Eingang des Load Balancers (Virtual Service).
- **server**: der Port am Ziel-Server.

Die FortiGate prüft die Policy nach der Port-Weiterleitung der VIP (DNAT vor der Policy), der
Load Balancer geht zum Port des Pools. Die Weg-Suche rechnet immer mit dem Port des Clients:
`client_service` rechnet den Port einer anderen Stelle rückwärts über die VIP und den Load
Balancer, `chain` zeigt die Ports entlang des Wegs, `at_end` den Port an einer Stelle des Wegs
(für die Wahl des Virtual Service nach der Port-Weiterleitung). Nur lesend."""

import ipaddress

from sqlalchemy.orm import Session

from app.i18n import L
from app.models import NatRule
from app.store import nat as nat_store

AT = ("client", "lb", "server")
Service = tuple[str, int | None]


def port_range(text: str | None) -> tuple[int, int] | None:
    a, _, b = str(text or "").partition("-")
    if not a.strip().isdigit():
        return None
    lo = int(a)
    return lo, int(b) if b.strip().isdigit() else lo


def text(service: Service | None) -> str | None:
    if not service:
        return None
    return f"{service[0]}/{service[1]}" if service[1] is not None else f"{service[0]}/any"


def forwarded(service: Service | None, nat: dict | None) -> Service | None:
    """Port-Weiterleitung einer VIP am Hop (`hop.nat.ports` = [extern, intern]): externer Port
    -> interner Port (Bereich mit Abstand)."""
    if not service or service[1] is None or not nat or not nat.get("ports"):
        return service
    ext, mapped = (port_range(p) for p in nat["ports"])
    if ext is None or mapped is None or not ext[0] <= service[1] <= ext[1]:
        return service
    return (service[0], mapped[0] + service[1] - ext[0])


def behind_lb(service: Service | None, lb: dict) -> Service | None:
    """Vom Load Balancer zum Server: der Port des Pools."""
    port = lb.get("server_port")
    if service and port not in (None, ""):
        try:
            return (service[0], int(port))
        except (TypeError, ValueError):
            return service
    return service


def at_end(hops: list[dict], service: Service | None) -> Service | None:
    """Port am Ende dieser Hops: nach der Port-Weiterleitung der VIPs und hinter den Load
    Balancern davor (der Load Balancer des letzten Hops zählt noch nicht)."""
    cur, prev = service, None
    for h in hops:
        if prev is not None and prev.get("lb"):
            cur = behind_lb(cur, prev["lb"])
        cur = forwarded(cur, h.get("nat"))
        prev = h
    return cur


def chain(path: dict, service: Service | None) -> list[dict] | None:
    """Ports entlang des Wegs: Client, nach der VIP, am Eingang des Load Balancers, am Server -
    [{at, label, service}]; None ohne Dienst mit Port."""
    if not service or service[1] is None:
        return None
    out = [{"at": "client", "label": L("Client", "Client"), "service": text(service)}]
    cur, prev, server = service, None, False
    for h in path.get("hops") or []:
        if prev is not None and prev.get("lb"):
            cur = behind_lb(cur, prev["lb"])
            out.append({"at": "server", "label": L("Server", "Server"), "service": text(cur)})
            server = True
        nat = h.get("nat")
        new = forwarded(cur, nat)
        if nat and new != cur:
            out.append({"at": "vip", "label": f"VIP {nat.get('name')}", "service": text(new)})
        cur = new
        if h.get("lb"):
            name = h["lb"].get("name")
            out.append({"at": "lb", "label": L(f"Eingang Load Balancer {name}",
                                               f"Load balancer entry {name}"),
                        "service": text(cur)})  # fmt: skip
        prev = h
    if prev is not None and prev.get("lb"):
        out.append({"at": "server", "label": L("Server", "Server"),
                    "service": text(behind_lb(cur, prev["lb"]))})  # fmt: skip
    elif not server:
        out.append({"at": "server", "label": L("Ziel", "Destination"), "service": text(cur)})
    return out


def label(at: str) -> str:
    return {
        "client": L("beim Client", "at the client"),
        "lb": L("am Eingang des Load Balancers", "at the load balancer entry"),
        "server": L("am Ziel-Server", "at the destination server"),
    }.get(at, at)


def reverse_vip(n: NatRule, port: int) -> int | None:
    """Port des Clients an der VIP der Firewall, der nach der Port-Weiterleitung `port` ist."""
    if n.vip_type == nat_store.SLB:  # Virtual Server: Port der VIP, Real Server mit Port
        ports = {int(t["port"]) for t in n.targets or []
                 if isinstance(t, dict) and str(t.get("port") or "").isdigit()}  # fmt: skip
        if ports and port not in ports:
            return None
        ext = port_range(n.ext_port)
        return ext[0] if ext else port
    if not n.portforward:
        return port
    ext, mapped = port_range(n.ext_port), port_range(n.mapped_port)
    if ext is None or mapped is None:
        return port
    if not mapped[0] <= port <= mapped[1]:
        return None
    return ext[0] + port - mapped[0]


def _client_port(n: NatRule | None, lb: NatRule | None, port: int, at: str) -> int | None:
    """Port des Clients für die Kette VIP der Firewall (`n`) -> Virtual Service (`lb`)."""
    if at == "lb":
        if lb is None:
            return None
        vs = port_range(lb.ext_port)
        if vs is not None and not vs[0] <= port <= vs[1]:
            return None
        return reverse_vip(n, port) if n is not None else port
    if lb is not None:  # Server hinter dem Load Balancer: Port des Pools -> Port des Service
        ports = {int(t["port"]) for t in lb.targets or []
                 if isinstance(t, dict) and str(t.get("port") or "").isdigit()}  # fmt: skip
        if ports and port not in ports:
            return None
        vs = port_range(lb.ext_port)
        if vs is not None and vs[0] != vs[1]:
            return None  # Bereich auf einen Port des Pools: nicht eindeutig
        entry = vs[0] if vs is not None else port
        return reverse_vip(n, entry) if n is not None else entry
    if n is None:
        return None
    return reverse_vip(n, port)


def _chains(lbs: nat_store.Balancers, dst: str, published=None) -> list[tuple]:
    """Ketten zum Ziel: (VIP der Firewall | None, Virtual Service | None)."""
    if published is not None:  # Server hinter der VIP (0.134.0): genau diese Kette
        n = next((r for r in lbs.rows if not nat_store.is_balancer(r)
                  and r.name == published.name
                  and r.source_system_id == published.source_system_id), None)  # fmt: skip
        if n is None:
            return []
        lb = next((x for x in lbs.behind(n) if x.name == published.balancer), None)
        return [(n, lb)]
    ip = nat_store.host_ip(dst)
    if ip is None:
        return []
    addr = ipaddress.ip_address(ip)
    out: list[tuple] = []
    for n in lbs.rows:
        if nat_store.is_balancer(n):
            continue
        s = nat_store.span(n.ext_ip)
        if s is None or s[0].version != addr.version or not s[0] <= addr <= s[1]:
            continue
        links = lbs.links(n)
        out += [(n, link.lb) for link in links] or [(n, None)]
    out += [(None, lb) for lb in lbs.at(ip)]
    return out


def client_service(session: Session, tenant_id: int, dst: str | None, service: Service | None,
                   at: str, published=None) -> tuple[Service | None, str | None]:  # fmt: skip
    """Port des Clients zum Dienst an der Stelle `at` -> (Dienst beim Client, Hinweis).
    `published`: Server hinter einer VIP (`path_vip.Published`) - dann deren Kette."""
    if at == "client" or not service or service[1] is None or not dst:
        return service, None
    lbs = nat_store.Balancers.load(session, tenant_id)
    found: list[int] = []
    for n, lb in _chains(lbs, dst, published):
        port = _client_port(n, lb, service[1], at)
        if port is not None and port not in found:
            found.append(port)
    where = label(at)
    if not found:
        what = L("kein Load Balancer", "no load balancer") if at == "lb" else L(
            "keine VIP bzw. kein Load Balancer mit diesem Port", "no VIP or load balancer with "
            "this port")  # fmt: skip
        return service, L(
            f"Dienst {text(service)} {where}: auf dem Weg zu {dst} {what} – gerechnet wie beim "
            "Client",
            f"Service {text(service)} {where}: {what} on the path to {dst} – calculated as at "
            "the client",
        )
    client = (service[0], found[0])
    note = L(f"Dienst {text(service)} {where} = beim Client {text(client)}",
             f"Service {text(service)} {where} = at the client {text(client)}")  # fmt: skip
    if len(found) > 1:
        rest = ", ".join(str(p) for p in found[1:4])
        note += L(f" (auch möglich: Port {rest})", f" (also possible: port {rest})")
    return client, note
