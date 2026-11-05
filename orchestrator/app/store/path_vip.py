"""Ziel hinter einer VIP von aussen über die VIP (0.134.0).

Anwender 2026-11-05: „Ich nehme als Source die Internet Firewall und als Ziel den Server
zuhinterst, aber leider zeigt er mir dennoch nicht den VIP als Pfad an, sondern nur der direkte
Pfad … Er will direkt zum Server, auch wenn die zweite Policy den richtigen Weg wählt“.

Von aussen (ohne Quelle, mit öffentlicher Quelle bzw. ab einer Anbindung) ist ein Server hinter
einer VIP nur über die VIP erreichbar: öffentliche IP → VIP der Firewall (DNAT) → ggf. Virtual
Service des Load Balancers → Server. `published` findet die VIP, deren Kette beim Server endet -
die der gewählten Firewall zuerst, mit passendem Port; die Weg-Suche geht dann zur externen
Adresse der VIP. Nur lesend."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.i18n import L
from app.store import nat as nat_store


@dataclass
class Published:
    """VIP, über die ein Server von aussen erreichbar ist."""

    name: str
    ext_ip: str
    ext_port: str | None
    source_system_id: int
    vdom: str | None
    extintf: str | None  # Eingang der VIP (`extintf`), None = any
    balancer: str | None  # Virtual Service dazwischen
    server: str
    others: int = 0  # weitere VIPs zum selben Server

    def out(self) -> dict:
        return {
            "name": self.name,
            "ext_ip": self.ext_ip,
            "ext_port": self.ext_port,
            "source_system_id": self.source_system_id,
            "vdom": self.vdom,
            "extintf": self.extintf,
            "balancer": self.balancer,
            "server": self.server,
            "others": self.others,
            "text": self.text(),
        }

    def text(self) -> str:
        lb = L(f" → {self.balancer}", f" → {self.balancer}") if self.balancer else ""
        more = L(f" (und {self.others} weitere)", f" (and {self.others} more)") \
            if self.others else ""  # fmt: skip
        return L(
            f"Ziel {self.server} ist über VIP {self.name} ({self.ext_ip}){lb} veröffentlicht"
            f"{more} – Weg von aussen über die VIP",
            f"Destination {self.server} is published via VIP {self.name} ({self.ext_ip}){lb}"
            f"{more} – path from outside via the VIP",
        )


def _port_fits(port: str | None, want: int | None) -> bool | None:
    """Port der VIP passt zum gesuchten (None: ohne Port bzw. ohne Dienst)."""
    rng = nat_store._ports(port)
    if rng is None or want is None:
        return None
    return rng[0] <= want <= rng[1]


def published(session: Session, tenant_id: int, server: str, service=None,
              prefer: int | None = None) -> Published | None:  # fmt: skip
    """VIP einer Firewall, deren Kette beim Server endet (direkt `mappedip` bzw. Real Server,
    oder über einen Virtual Service des Load Balancers); die der Firewall `prefer` zuerst, dann
    mit passendem Port. None, wenn keine VIP zum Server führt."""
    ip = nat_store.host_ip(server)
    if ip is None:
        return None
    lbs = nat_store.Balancers.load(session, tenant_id)
    want = service[1] if service else None
    found: list[tuple[tuple, Published]] = []
    for n in lbs.rows:
        if nat_store.is_balancer(n):
            continue
        ext = nat_store.host_ip(n.ext_ip)
        if ext is None:
            continue
        legs: list[tuple[str | None, str | None]] = []  # (externer Port, Load Balancer)
        if ip in {nat_store.host_ip(v) for v in nat_store.internal_ips(n)}:
            legs.append((n.ext_port, None))
        for link in lbs.links(n):
            if ip in {nat_store.host_ip(v) for v in nat_store.internal_ips(link.lb)}:
                legs.append((link.ext_port, link.lb.name))
        raw = n.raw if isinstance(n.raw, dict) else {}
        extintf = str(raw.get("extintf") or "").strip() or None
        for port, lb in legs:
            fits = _port_fits(port, want)
            rank = (n.source_system_id != prefer, fits is False, fits is None, n.name)
            found.append(
                (
                    rank,
                    Published(
                        n.name,
                        ext,
                        port,
                        n.source_system_id,
                        n.vdom,
                        None if extintf == "any" else extintf,
                        lb,
                        ip,
                    ),
                )
            )
    if not found:
        return None
    found.sort(key=lambda x: x[0])
    best = found[0][1]
    best.others = len({(p.name, p.source_system_id) for _r, p in found}) - 1
    return best
