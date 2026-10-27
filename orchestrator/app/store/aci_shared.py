"""ACI zwischen VRFs (0.44.0, Befund 0.43.1): Shared Services und Route-Leaking.

Ein Contract zwischen EPGs verschiedener VRFs allein oeffnet keinen Weg: das Fabric muss die
Subnetze in die andere VRF leaken. Das tut es

- als **Shared Service**: Ein Contract mit Scope `global` (ueber Tenants) bzw. `tenant` (im
  Tenant) verbindet eine EPG in VRF A mit einer in VRF B, und das Subnetz der Gegenseite ist
  geteilt (`fvSubnet.scope` mit `shared`, Subnetz der EPG bzw. ihrer Bridge Domain). Das ACI
  leakt es in die VRF des Partners. Fuer Hin- und Rueckweg braucht es beide Seiten;
- per **Route-Leaking** (`leakRoutes`, ACI 5.0+): Das Subnetz ist von VRF B nach A geleakt.

Ohne Leak kennt die VRF das Ziel nicht; der Verkehr folgt dann ihrer Routing-Tabelle (L3Out,
Firewall). Weg-Suche (`paths`, `aci_check`) und Applikations-Doku (`appdoc`) entscheiden mit
diesem Modul gleich. Vereinfacht: Subnetze der EPG und ihres BD zaehlen gleich (je nach
ACI-Version liegt das Provider-Subnetz an der EPG bzw. am BD).
"""

import ipaddress
from collections import defaultdict
from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import Epg, EpgSubnet, InvVrf, Rule, RuleDomain, RuleEndpoint
from app.store import aci_relevance
from app.store import epgs as epg_store
from app.store.topology import within

POSSIBLE, IMPOSSIBLE, UNKNOWN = (aci_relevance.POSSIBLE, aci_relevance.IMPOSSIBLE,
                                 aci_relevance.UNKNOWN)  # fmt: skip
_CROSS = ("global", "tenant")  # Scopes, die ueber eine VRF hinaus gelten koennen


def shared(scope: str | None) -> bool:
    return "shared" in (scope or "").replace(" ", "").split(",")


class Leaks:
    """Wege zwischen VRFs im Fabric je APIC - einmal je Mandant geladen."""

    def __init__(self, session: Session, tenant_id: int, classifier: aci_relevance.Classifier):
        self.session = session
        self.tenant_id = tenant_id
        self.cls = classifier

    @cached_property
    def epgs(self) -> dict[int, Epg]:
        stmt = select(Epg).where(Epg.tenant_id == self.tenant_id)
        return {e.id: e for e in self.session.scalars(stmt)}

    def vrf(self, epg: Epg) -> tuple[int | None, str | None]:
        return epg.source_system_id, self.cls.vrf_of(epg)

    @cached_property
    def subnets(self) -> dict[int, list[tuple[object, str | None]]]:
        """EPG-ID -> (Netz, Scope) ihrer EPG- und BD-Subnetze (EPGs und ESGs im Fabric)."""
        ids = [i for i, e in self.epgs.items() if e.kind in ("epg", "esg")]
        out: dict[int, list[tuple[object, str | None]]] = defaultdict(list)
        if not ids:
            return out
        for s in self.session.scalars(
            select(EpgSubnet).where(
                EpgSubnet.epg_id.in_(ids), EpgSubnet.origin.in_(("epg", "bd", "esg"))
            )
        ):
            try:
                out[s.epg_id].append((ipaddress.ip_network(s.ip, strict=False), s.scope))
            except ValueError:
                continue
        return out

    @cached_property
    def partners(self) -> dict[int, set[int]]:
        """EPG-ID -> EPG-IDs, mit denen sie ein Contract mit Scope global bzw. tenant verbindet
        (beide Richtungen, auch vzAny) - die Paare, fuer die das ACI Subnetze leakt."""
        roles: dict[int, tuple[str | None, set[int], set[int]]] = {}
        for rid, scope, role, epg_id in self.session.execute(
            select(Rule.id, Rule.aci_scope, RuleEndpoint.role, RuleEndpoint.epg_id)
            .join(RuleEndpoint, RuleEndpoint.rule_id == Rule.id)
            .where(
                Rule.tenant_id == self.tenant_id,
                Rule.domain == RuleDomain.ACI_CONTRACT,
                Rule.aci_scope.in_(_CROSS),
                RuleEndpoint.epg_id.is_not(None),
            )
        ):
            entry = roles.setdefault(rid, (scope, set(), set()))
            (entry[1] if role.value == "src" else entry[2]).add(epg_id)
        out: dict[int, set[int]] = defaultdict(set)
        for scope, cons, prov in roles.values():
            for c in cons:
                for p in prov:
                    a, b = self.epgs.get(c), self.epgs.get(p)
                    if a is None or b is None or not self.cls.scope_allows(scope, a, b):
                        continue
                    out[c].add(p)
                    out[p].add(c)
        return out

    @cached_property
    def explicit(self) -> dict[tuple[int, str], list[tuple[object, set[str], str]]]:
        """(APIC, VRF) -> (Netz, Ziel-VRFs, Praefix) aus `leakRoutes`."""
        out: dict[tuple[int, str], list[tuple[object, set[str], str]]] = defaultdict(list)
        for sid, name, leaks in self.session.execute(
            select(InvVrf.source_system_id, InvVrf.name, InvVrf.leaks).where(
                InvVrf.tenant_id == self.tenant_id, InvVrf.leaks.is_not(None)
            )
        ):
            for leak in leaks or []:
                try:
                    net = ipaddress.ip_network(str(leak.get("prefix")), strict=False)
                except ValueError:
                    continue
                out[(sid, name)].append((net, set(leak.get("to") or []), str(net)))
        return out

    # -- Fragen --------------------------------------------------------------------

    def _leak(self, owner: Epg, net, scope, into: tuple[int | None, str]) -> tuple[str, str] | None:
        """(Art `shared` | `leak`, Grund), warum das Subnetz der EPG in die VRF `into` geleakt
        ist - sonst None. Die Art fuer die Oberflaeche (nicht am Text unterscheiden)."""
        label = epg_store.inventory_label(owner)
        if shared(scope):
            for pid in self.partners.get(owner.id, ()):
                partner = self.epgs.get(pid)
                if partner is not None and self.vrf(partner) == into:
                    other = epg_store.inventory_label(partner)
                    return "shared", L(
                        f"Shared Service: {net} von {label} geteilt, Contract mit {other}",
                        f"Shared Service: {net} shared by {label}, contract with {other}",
                    )
        sid, own_vrf = self.vrf(owner)
        for leak_net, to, prefix in self.explicit.get((sid, own_vrf or ""), []):
            if into[1] in to and within(net, leak_net):
                return "leak", L(
                    f"Route-Leaking {prefix} von VRF {own_vrf} nach {into[1]}",
                    f"Route-Leaking {prefix} from VRF {own_vrf} to {into[1]}",
                )
        return None

    def _leaked(self, owner: Epg, net, scope, into: tuple[int | None, str]) -> str | None:
        """Grund, warum das Subnetz der EPG in die VRF `into` geleakt ist - sonst None."""
        found = self._leak(owner, net, scope, into)
        return found[1] if found else None

    def into(self, sid: int, vrf: str, ip) -> tuple[Epg, str, str, str] | None:
        """Ist die IP (im Fabric einer anderen VRF des APIC) in die VRF geleakt? ->
        (EPG dort, ihre VRF, Grund, Art `shared` | `leak`) mit dem laengsten Subnetz."""
        best: tuple[int, Epg, str, str, str] | None = None
        for epg_id, nets in self.subnets.items():
            owner = self.epgs[epg_id]
            other = self.vrf(owner)
            if other[0] != sid or not other[1] or other[1] == vrf:
                continue
            for net, scope in nets:
                if not within(ip, net) or (best is not None and net.prefixlen <= best[0]):
                    continue
                found = self._leak(owner, net, scope, (sid, vrf))
                if found is not None:
                    best = (net.prefixlen, owner, other[1], found[1], found[0])
        return None if best is None else best[1:]

    def between(self, a: Epg, b: Epg) -> tuple[str, str]:
        """Weg im Fabric zwischen zwei EPGs: (possible | impossible | unknown, Begruendung).
        Gleiche VRF ist immer moeglich; verschiedene VRFs brauchen den Leak beider Seiten."""
        va, vb = self.vrf(a), self.vrf(b)
        if None in (va[1], vb[1]) or va == vb:
            return POSSIBLE, ""
        nets_a, nets_b = self.subnets.get(a.id, []), self.subnets.get(b.id, [])
        to_a = next((w for n, s in nets_b if (w := self._leaked(b, n, s, va))), None)
        to_b = next((w for n, s in nets_a if (w := self._leaked(a, n, s, vb))), None)
        if to_a and to_b:
            return POSSIBLE, to_a
        if not nets_a or not nets_b or all(s is None for _n, s in nets_a + nets_b):
            # Altbestand ohne Scope der Subnetze bzw. EPG ohne Subnetz: nicht entscheidbar
            if not self.explicit:
                return UNKNOWN, L(
                    f"VRF {va[1]} ↔ {vb[1]}: Scope der Subnetze nicht bekannt – neu einlesen",
                    f"VRF {va[1]} ↔ {vb[1]}: scope of the subnets not known – collect again",
                )
        if to_a or to_b:
            return UNKNOWN, L(
                f"VRF {va[1]} ↔ {vb[1]}: nur in eine Richtung geleakt ({to_a or to_b})",
                f"VRF {va[1]} ↔ {vb[1]}: leaked in one direction only ({to_a or to_b})",
            )
        return IMPOSSIBLE, L(
            f"Contract zwischen VRF {va[1]} und VRF {vb[1]} – kein geteiltes Subnetz "
            "(Scope shared) und kein Route-Leaking: im Fabric kein Weg. Verkehr geht über das "
            "Routing der VRF (L3Out, Firewall) – siehe Weg-Suche",
            f"Contract between VRF {va[1]} and VRF {vb[1]} – no shared subnet (scope shared) "
            "and no Route-Leaking: no path in the fabric. Traffic goes via the routing of the "
            "VRF (L3Out, firewall) – see path search",
        )
