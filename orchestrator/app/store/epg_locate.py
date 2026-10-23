"""Zu welcher EPG gehoert eine IP? Konfiguration vor Gelerntem (0.46.0, R12, Anwender).

Reihenfolge je APIC und VRF - die erste Stufe, die eine EPG findet, gilt:

1. **statischer Endpunkt** (fvStCEp bzw. fvCEp mit `lcC` static) - Konfiguration im ACI;
2. **Portgruppe der VM** (vCenter): die NIC mit der IP haengt an einer Portgruppe, die einer
   EPG zugeordnet ist (`virtual.PortgroupIndex`, Name/VLAN/Switch) - Konfiguration;
3. **gelernter Endpunkt** (fvCEp/fvIp, auch aus der VMM-Domaene);
4. **EPG-Subnetz** (fvSubnet der EPG), dann **BD-Subnetz** - laengstes Praefix; mehrere EPGs
   im selben BD-Subnetz bleiben mehrdeutig.

Widerspricht ein gelernter Endpunkt der Konfiguration (andere EPG derselben VRF), ist das ein
Befund (`conflicts`, Typ `epg_assignment_conflict`): VM an der falschen Portgruppe, IP doppelt
vergeben bzw. statischer Endpunkt veraltet. Gleiche IPs in verschiedenen VRFs sind
verschiedene Systeme (0.42.3) und kein Widerspruch. Nur lesend.
"""

import ipaddress
from collections import defaultdict
from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Epg, EpgEndpoint, EpgSubnet, Vm, VmNic
from app.store import epgs as epg_store

STATIC, VM, LEARNED, SUBNET = "static", "vm", "learned", "subnet"
LABEL = {STATIC: "statischer Endpunkt", VM: "Portgruppe der VM", LEARNED: "gelernter Endpunkt",
         SUBNET: "Subnetz"}  # fmt: skip
CONFIG = (STATIC, VM)


class Locator:
    """IP -> EPG(s) je APIC und VRF; einmal je Mandant geladen."""

    def __init__(self, session: Session, tenant_id: int, classifier=None, epgs=None):
        from app.store import aci_relevance  # spaet: aci_relevance -> paths -> ...

        self.session = session
        self.tenant_id = tenant_id
        self.cls = classifier or aci_relevance.Classifier(session, tenant_id)
        self._epgs = epgs

    @cached_property
    def epgs(self) -> dict[int, Epg]:
        if self._epgs is not None:
            return self._epgs
        stmt = select(Epg).where(Epg.tenant_id == self.tenant_id)
        return {e.id: e for e in self.session.scalars(stmt)}

    def _where(self, epg_id: int) -> tuple[int | None, str | None] | None:
        e = self.epgs.get(epg_id)
        if e is None or e.kind not in ("epg", "esg"):
            return None
        return e.source_system_id, self.cls.vrf_of(e)

    @cached_property
    def _endpoints(self) -> dict[str, list[tuple[int, str]]]:
        """IP -> (EPG, Herkunft static | learned) der Endpunkte."""
        out: dict[str, list[tuple[int, str]]] = defaultdict(list)
        ids = list(self.epgs)
        if not ids:
            return out
        for epg_id, ip, origin in self.session.execute(
            select(EpgEndpoint.epg_id, EpgEndpoint.ip, EpgEndpoint.origin).where(
                EpgEndpoint.epg_id.in_(ids)
            )
        ):
            out[ip].append((epg_id, STATIC if origin == "static" else LEARNED))
        return out

    @cached_property
    def _vms(self) -> dict[str, list[tuple[int, str]]]:
        """IP -> (EPG der Portgruppe, VM) aus dem vCenter."""
        from app.store.virtual import PortgroupIndex  # spaet: virtual importiert epgs

        index = PortgroupIndex.load(self.session, self.tenant_id)
        out: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for vm, nic in self.session.execute(
            select(Vm, VmNic)
            .join(VmNic, VmNic.vm_id == Vm.id)
            .where(
                Vm.tenant_id == self.tenant_id, Vm.template.is_(False), VmNic.portgroup.is_not(None)
            )  # fmt: skip
        ):
            pg = index.of(vm.source_system_id, nic)
            if pg is None or not pg.epg_id:
                continue
            for ip in nic.ips or []:
                if (pg.epg_id, vm.name) not in out[ip]:
                    out[ip].append((pg.epg_id, vm.name))
        return out

    @cached_property
    def _subnets(self) -> dict[tuple[int | None, str | None], list[tuple[object, str, int]]]:
        out: dict[tuple[int | None, str | None], list[tuple[object, str, int]]] = defaultdict(list)
        ids = [i for i, e in self.epgs.items() if e.kind in ("epg", "esg")]
        if not ids:
            return out
        for epg_id, value, origin in self.session.execute(
            select(EpgSubnet.epg_id, EpgSubnet.ip, EpgSubnet.origin).where(
                EpgSubnet.epg_id.in_(ids), EpgSubnet.origin.in_(("epg", "bd", "esg"))
            )
        ):
            where = self._where(epg_id)
            try:
                net = ipaddress.ip_interface(value).network
            except ValueError:
                continue
            if where is not None:
                out[where].append((net, origin, epg_id))
        return out

    # -- Fragen --------------------------------------------------------------------

    def configured(self, ip: str, sid: int | None, vrf: str | None) -> list[tuple[Epg, str]]:
        """Konfigurierte EPG(s) der IP in der VRF: statischer Endpunkt, sonst Portgruppe."""
        static = [(self.epgs[i], LABEL[STATIC]) for i, o in self._endpoints.get(ip, [])
                  if o == STATIC and self._where(i) == (sid, vrf)]  # fmt: skip
        if static:
            return _unique(static)
        vms = [(self.epgs[i], f"{LABEL[VM]} {name}") for i, name in self._vms.get(ip, [])
               if self._where(i) == (sid, vrf)]  # fmt: skip
        return _unique(vms)

    def learned(self, ip: str, sid: int | None, vrf: str | None) -> list[Epg]:
        return list(dict.fromkeys(self.epgs[i] for i, o in self._endpoints.get(ip, [])
                                  if o == LEARNED and self._where(i) == (sid, vrf)))  # fmt: skip

    def locate(self, ip: str, sid: int | None, vrf: str | None) -> tuple[list[Epg], str | None]:
        """EPG(s) der IP in der VRF nach der Reihenfolge oben und die Stufe."""
        conf = self.configured(ip, sid, vrf)
        if conf:
            return [e for e, _w in conf], conf[0][1]
        learned = self.learned(ip, sid, vrf)
        if learned:
            return learned, LABEL[LEARNED]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return [], None
        nets = [(n, o, i) for n, o, i in self._subnets.get((sid, vrf), []) if addr in n]
        if not nets:
            return [], None
        pool = [x for x in nets if x[1] in ("epg", "esg")] or nets
        best = max(x[0].prefixlen for x in pool)
        ids = list(dict.fromkeys(i for n, _o, i in pool if n.prefixlen == best))
        return [self.epgs[i] for i in ids], LABEL[SUBNET]

    def vrfs(self) -> set[tuple[int | None, str | None]]:
        return {w for i in self.epgs if (w := self._where(i)) is not None}

    def conflicts(self) -> list[dict]:
        """IPs, die das ACI in einer anderen EPG gelernt hat als konfiguriert (gleiche VRF)."""
        out = []
        for ip in sorted(set(self._endpoints) | set(self._vms)):
            by_vrf: dict[tuple, dict[str, list]] = defaultdict(lambda: {"conf": [], "learned": []})
            for i, o in self._endpoints.get(ip, []):
                w = self._where(i)
                if w is not None:
                    by_vrf[w]["conf" if o == STATIC else "learned"].append((i, LABEL[o], None))
            for i, name in self._vms.get(ip, []):
                w = self._where(i)
                if w is not None:
                    by_vrf[w]["conf"].append((i, f"{LABEL[VM]} {name}", name))
            for (sid, vrf), x in by_vrf.items():
                conf_ids = {i for i, _w, _vm in x["conf"]}
                other = [i for i, _w, _vm in x["learned"] if i not in conf_ids]
                if not conf_ids or not other:
                    continue
                conf = x["conf"][0]
                out.append({
                    "ip": ip,
                    "source_system_id": sid,
                    "vrf": vrf,
                    "configured": epg_store.inventory_label(self.epgs[conf[0]]),
                    "via": conf[1],
                    "vm": conf[2],
                    "learned": [epg_store.inventory_label(self.epgs[i])
                                for i in dict.fromkeys(other)],
                })  # fmt: skip
        return out


def _unique(items: list[tuple[Epg, str]]) -> list[tuple[Epg, str]]:
    seen: dict[int, tuple[Epg, str]] = {}
    for e, why in items:
        seen.setdefault(e.id, (e, why))
    return list(seen.values())
