"""ACI-Contracts auf dem Weg (0.35.0, ADR 0019 Stufe 3, Dev-Befund 0.34.0).

Je ACI-VRF-Hop eines Wegs (Rand der VRF ueber alle Border-Leafs) die Frage: laesst das Fabric
den Verkehr von der Quelle zum Ziel zu? Wie im APIC:

1. **Zuordnung** von Quelle und Ziel in der VRF: im Fabric ihre EPG (gelernter Endpunkt,
   EPG-Subnetz, BD-Subnetz mit genau einer EPG, Server der EPG), sonst die externe EPG ueber
   das laengste zuordnende Subnetz (`aci_relevance.Classifier`, 0.28.0). Erfasst keine EPG
   die IP, verwirft das Fabric den Verkehr.
2. **Gleiche EPG:** ohne Contract erlaubt.
3. **Taboo-Contracts** des Ziels (bzw. ueber vzAny/ESG) sperren vor allen anderen.
4. **Contracts:** Quelle ist Consumer, Ziel Provider - direkt, ueber vzAny der VRF bzw. eine
   ESG; Preferred Group (Mitglieder untereinander) gleich. Scope `application-profile`
   verlangt dasselbe AP. Ein passender Filter (Dienst) erlaubt; ohne Dienst nur `ip/any`
   sicher, sonst „moeglich“.
   **Rueckrichtung** (0.69.1, ACI ist zustandslos - `aci_direction`): Quelle ist Provider,
   Ziel Consumer - erlaubt nur mit den Diensten, die der Provider aufbauen kann (Filter ohne
   Ports wie `ip/any`, `tcp/any`; mit Zielport zurueck nur Antworten).
5. **Kein Contract:** gesperrt (Whitelist-Modell des ACI).

Ein Netz (0.39.0) gehoert zu einer EPG, wenn ihr Subnetz es ganz enthaelt; erfassen EPGs bzw.
externe EPGs nur Teile davon, ist die Zuordnung nicht sicher (Urteil „moeglich“ bzw. abhaengig).

Ergebnis wie bei den FortiGate-Hops (`policy_check`): `verdict` allow | deny | implicit_deny |
depends | unknown, `text`, `rules` (die Contracts mit `decides`/`unsure`), dazu die Zuordnung
(`src_epg`, `dst_epg`). Shared Services bzw. Route-Leaking (0.44.0): liegt das Ziel in einer
anderen VRF, in diese geleakt (`hop.leak`, `aci_shared`), gilt dort seine EPG; der Contract
braucht einen Scope ueber die VRF hinaus (global bzw. tenant). Nur lesend; nicht
beruecksichtigt: VRF im Modus „unenforced“, Intra-EPG-Isolation, ESG-IP-Selektoren, Service
Graphs.
"""

import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import (
    Epg,
    EpgEndpoint,
    EpgSubnet,
    NetworkObject,
    RefRole,
    Rule,
    RuleAction,
    RuleDomain,
    RuleEndpoint,
    RuleObjectRef,
    ServiceObject,
)
from app.models.rule import EndpointKind, rule_service
from app.store import aci_direction, aci_relevance, epg_locate
from app.store import epgs as epg_store
from app.store.rule_view import bare_service
from app.store.topology import is_net, overlaps, within

SHOWN = 12
WHY = 2  # Gruende je Seite, warum keine externe EPG zuordnet (0.103.0)
_ALL = ("", "any", "unspecified")


@dataclass
class _Contract:
    id: int
    external_id: str
    name: str
    allow: bool
    kind: str  # contract | taboo | preferred_group
    scope: str | None
    consumers: set[int] = field(default_factory=set)
    providers: set[int] = field(default_factory=set)
    any_consumer: bool = False
    services: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)  # Filter mit Namen (FT_https)
    # Dienste, die der Provider zum Consumer aufbauen kann (ACI zustandslos, 0.69.1)
    reverse: list[str] = field(default_factory=list)
    # Consumer -> Provider nur Antworten (Filter nur mit Quellport, 0.72.1)
    replies_only: bool = False
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class Member:
    """Zuordnung einer IP in der VRF: EPG (bzw. externe EPG), sicher oder nur moeglich."""

    epg: Epg
    sure: bool
    text: str


class AciChecker:
    """Laedt EPGs und Contracts des Mandanten einmal und prueft sie je VRF-Hop."""

    def __init__(self, session: Session, tenant_id: int,
                 classifier: aci_relevance.Classifier | None = None):  # fmt: skip
        self.session = session
        self.tenant_id = tenant_id
        self.cls = classifier or aci_relevance.Classifier(session, tenant_id)
        self._contracts: dict[int, list[_Contract]] = {}
        self._servers: dict[str, set[int]] = {}
        self._unknown: set[str] = set()  # Texte „im Fabric, EPG nicht bekannt“ (Merkmal)

    # -- Laden -------------------------------------------------------------------

    @cached_property
    def epgs(self) -> dict[int, Epg]:
        stmt = select(Epg).where(Epg.tenant_id == self.tenant_id)
        return {e.id: e for e in self.session.scalars(stmt)}

    @cached_property
    def locator(self) -> epg_locate.Locator:
        """Konfiguration vor Gelerntem (0.46.0): statischer Endpunkt, Portgruppe der VM."""
        return epg_locate.Locator(self.session, self.tenant_id, self.cls, self.epgs)

    @cached_property
    def _subnets(self) -> dict[tuple[int | None, str], list[tuple[object, str, int]]]:
        """(APIC, VRF) -> (Netz, Herkunft epg|bd, EPG) der EPGs im Fabric."""
        out: dict[tuple[int | None, str], list[tuple[object, str, int]]] = defaultdict(list)
        inside = {i: e for i, e in self.epgs.items() if e.kind == "epg"}
        if not inside:
            return out
        for s in self.session.scalars(
            select(EpgSubnet).where(
                EpgSubnet.epg_id.in_(list(inside)), EpgSubnet.origin.in_(("epg", "bd"))
            )
        ):
            epg = inside[s.epg_id]
            vrf = self.cls.vrf_of(epg)
            try:
                net = ipaddress.ip_network(s.ip, strict=False)
            except ValueError:
                continue
            if vrf:
                out[(epg.source_system_id, vrf)].append((net, s.origin, epg.id))
        return out

    def contracts(self, sid: int) -> list[_Contract]:
        """Contracts, Taboos und Preferred Groups eines APIC."""
        if sid in self._contracts:
            return self._contracts[sid]
        where = (Rule.source_system_id == sid, Rule.domain == RuleDomain.ACI_CONTRACT)
        out: dict[int, _Contract] = {}
        for r in self.session.scalars(select(Rule).where(*where).order_by(Rule.id)):
            raw = r.raw if isinstance(r.raw, dict) else {}
            out[r.id] = _Contract(
                r.id,
                r.external_id,
                r.name or "",
                r.action is RuleAction.ALLOW,
                str(raw.get("kind") or "contract"),
                r.aci_scope,
                raw=raw,
            )
        if out:
            for rid, role, kind, epg_id in self.session.execute(
                select(
                    RuleEndpoint.rule_id, RuleEndpoint.role, RuleEndpoint.kind, RuleEndpoint.epg_id
                )  # fmt: skip
                .join(Rule, Rule.id == RuleEndpoint.rule_id)
                .where(*where)
            ):
                c = out[rid]
                if role.value == "src" and kind is EndpointKind.ANY:
                    c.any_consumer = True
                elif epg_id is not None:
                    (c.consumers if role.value == "src" else c.providers).add(epg_id)
            for rid, name in self.session.execute(
                select(rule_service.c.rule_id, ServiceObject.canonical_name)
                .join(ServiceObject, ServiceObject.id == rule_service.c.service_object_id)
                .join(Rule, Rule.id == rule_service.c.rule_id)
                .where(*where)
            ):
                out[rid].services.append(name)
            for rid, name in self.session.execute(
                select(RuleObjectRef.rule_id, RuleObjectRef.name)
                .join(Rule, Rule.id == RuleObjectRef.rule_id)
                .where(*where, RuleObjectRef.role == RefRole.SERVICE)
                .order_by(RuleObjectRef.rule_id, RuleObjectRef.position)
            ):
                if (bare := bare_service(name)) not in out[rid].names:
                    out[rid].names.append(bare)
            for c in out.values():
                if c.allow and c.kind == "contract":
                    c.reverse = aci_direction.reverse_of(c.raw, c.services)
                    c.replies_only = aci_direction.replies_only(c.raw, c.services)
        # Taboos zuerst (sperren vor allen anderen), dann nach Name
        self._contracts[sid] = sorted(out.values(), key=lambda c: (c.allow, c.name.lower()))
        return self._contracts[sid]

    # -- Zuordnung ---------------------------------------------------------------

    def members(self, sid: int, vrf: str, ip) -> tuple[list[Member], str]:
        """EPG(s) der IP in der VRF und eine Beschreibung; leer = keine EPG erfasst sie."""
        if ip is None:
            return [], L("Quelle unbekannt", "source unknown")
        inside = self._inside(sid, vrf, ip)
        if inside is not None:
            return inside
        found, why = self._externals(sid, vrf, ip, self.cls.check)
        if not found:
            # 0.103.0 (Anwender): lehnt das Routing der Firewalls hinter den L3Outs jede externe
            # EPG ab (z. B. die Firewall leitet die IP zurueck ins ACI), ordnet das Fabric sie
            # trotzdem zu - VRF-weit das laengste zuordnende Subnetz (10.0.0.0/8), mit Contract
            # erlaubt
            found, _ = self._externals(sid, vrf, ip, self.cls.classify, fabric=True)
        if not found and is_net(ip):  # Netz (0.39.0): externe EPGs, die nur einen Teil erfassen
            found = self._partial_external(sid, vrf, ip)
        if not found:
            text = L(f"keine EPG bzw. externe EPG erfasst {ip} in VRF {vrf}",
                     f"no EPG or external EPG covers {ip} in VRF {vrf}")  # fmt: skip
            if why:
                text += f" ({'; '.join(dict.fromkeys(why[:WHY]))})"
            return [], text
        return found, _either(m.text for m in found)

    def _externals(self, sid: int, vrf: str, ip, decide,
                   fabric: bool = False) -> tuple[list[Member], list[str]]:  # fmt: skip
        """Externe EPGs der VRF, die die IP zuordnen, und warum die uebrigen nicht (0.103.0)."""
        found: list[Member] = []
        why: list[str] = []
        for epg in self.cls.externals(ip):
            if epg.source_system_id != sid:
                continue
            name = aci_relevance.ext_label(epg)
            net = self.cls.subnet_for(epg, ip)
            if self.cls.vrf_of(epg) != vrf:
                other = self.cls.vrf_of(epg)
                why.append(L(f"externe EPG {name} ({net}) liegt in VRF {other}",
                             f"external EPG {name} ({net}) is in VRF {other}"))  # fmt: skip
                continue
            rel = decide(epg, ip)
            if rel is None:
                continue
            if rel[0] == aci_relevance.IMPOSSIBLE:
                why.append(rel[1])
                continue
            label = L(f"externe EPG {name}", f"external EPG {name}") + (f" ({net})" if net else "")
            if fabric:
                label += L(" – Zuordnung im ACI", " – assignment in the ACI")
            found.append(Member(epg, rel[0] == aci_relevance.POSSIBLE, label))
        return found, why

    def _partial_external(self, sid: int, vrf: str, net) -> list[Member]:
        out: dict[int, Member] = {}
        for epg, n in self.cls.overlapping(sid, vrf, net):
            if epg.id not in out:
                name = aci_relevance.ext_label(epg)
                label = L(f"externe EPG {name} ({n}, nur Teil von {net})",
                          f"external EPG {name} ({n}, only part of {net})")  # fmt: skip
                out[epg.id] = Member(epg, False, label)
        return list(out.values())

    def _inside(self, sid: int, vrf: str, ip) -> tuple[list[Member], str] | None:
        """Im Fabric der VRF - Konfiguration vor Gelerntem (0.46.0): statischer Endpunkt,
        Portgruppe der VM (vCenter), dann gelernter Endpunkt bzw. Server der EPG, EPG- bzw.
        BD-Subnetz."""
        conf = [] if is_net(ip) else self.locator.configured(str(ip), sid, vrf)
        if conf:
            sure = len(conf) == 1
            members = [Member(e, sure, f"EPG {epg_store.inventory_label(e)} ({why})")
                       for e, why in conf]  # fmt: skip
            return members, _either(m.text for m in members)
        own = [
            self.epgs[i]
            for i in self.session.scalars(
                select(EpgEndpoint.epg_id).where(EpgEndpoint.ip == str(ip))
            )
            if i in self.epgs
        ]
        own += [
            e
            for e in self.epgs.values()
            if e.network_object_id
            and e.network_object_id in self._server_ids(str(ip))
            and e not in own
        ]
        own = [e for e in own if e.kind == "epg" and e.source_system_id == sid
               and self.cls.vrf_of(e) == vrf]  # fmt: skip
        if own:
            members = [Member(e, True, f"EPG {epg_store.inventory_label(e)}") for e in own]
            return members, _either(m.text for m in members)
        nets = [(n, origin, i) for n, origin, i in self._subnets.get((sid, vrf), [])
                if within(ip, n)]  # fmt: skip
        if nets:
            epg_nets = [x for x in nets if x[1] == "epg"]
            pool = epg_nets or nets
            best = max(x[0].prefixlen for x in pool)
            ids = list(dict.fromkeys(i for n, _o, i in pool if n.prefixlen == best))
            sure = len(ids) == 1
            members = [
                Member(self.epgs[i], sure, f"EPG {epg_store.inventory_label(self.epgs[i])}")
                for i in ids
            ]
            text = _either(m.text for m in members)
            if not sure:
                text = L(f"im Bridge-Domain-Subnetz {pool[0][0]}: {text}",
                         f"in bridge domain subnet {pool[0][0]}: {text}")  # fmt: skip
            return members, text
        where = self.cls._fabric(sid, vrf, ip)  # Inventar: BD-Subnetz ohne bekannte EPG
        if where:
            text = L(f"im Fabric ({where}), EPG nicht bekannt",
                     f"in the fabric ({where}), EPG not known")  # fmt: skip
            self._unknown.add(text)  # Merkmal fuer `check` (nicht am Text der Sprache pruefen)
            return [], text
        if is_net(ip):  # Netz (0.39.0), das EPG-Subnetze nur teilweise enthaelt
            parts = [i for n, _o, i in self._subnets.get((sid, vrf), []) if overlaps(ip, n)]
            if parts:
                members = [
                    Member(self.epgs[i], False, f"EPG {epg_store.inventory_label(self.epgs[i])}")
                    for i in dict.fromkeys(parts)
                ]
                return members, L(f"Teile von {ip}: ", f"Parts of {ip}: ") + _either(
                    m.text for m in members
                )
        return None

    def _server_ids(self, ip: str) -> set[int]:
        if ip not in self._servers:
            self._servers[ip] = set(
                self.session.scalars(
                    select(NetworkObject.id).where(
                        NetworkObject.tenant_id == self.tenant_id,
                        NetworkObject.value.in_((ip, f"{ip}/32", f"{ip}/128")),
                    )
                )
            )
        return self._servers[ip]

    def proxies(self, epg: Epg, vrf: str) -> dict[int, str]:
        """EPG-IDs, deren Contracts fuer die EPG gelten: sie selbst, vzAny ihrer VRF, ESGs,
        die sie per EPG-Selektor einschliessen."""
        out = {epg.id: "direkt"}
        for other in self.epgs.values():
            if other.kind == "vzany" and other.source_system_id == epg.source_system_id:
                if self.cls.vrf_of(other) == vrf:
                    out[other.id] = f"vzAny {vrf}"
            elif other.kind == "esg" and epg.dn and f"EPG {epg.dn}" in (other.selectors or []):
                out[other.id] = f"ESG {other.name}"
        return out

    # -- Pruefen -----------------------------------------------------------------

    def check(self, sid: int, vrf: str, src, dst, service, dst_vrf: str | None = None) -> dict:
        """Urteil des Fabrics; `dst_vrf`: Ziel in einer anderen VRF, in diese geleakt
        (Shared Service, Route-Leaking - `aci_shared`, 0.44.0)."""
        srcs, src_text = self.members(sid, vrf, src)
        dsts, dst_text = self.members(sid, dst_vrf or vrf, dst)
        # L3Outs der externen EPGs (0.74.0, Netzwerkplan der Applikation)
        l3outs = sorted({m.epg.l3out for m in (srcs or []) + (dsts or [])
                         if m.epg.kind == "external" and m.epg.l3out})  # fmt: skip
        base = {"src_epg": src_text, "dst_epg": dst_text, "l3outs": l3outs}
        if src is None:
            text = L("Quelle unbekannt – Contracts nicht prüfbar",
                     "source unknown – contracts not verifiable")  # fmt: skip
            return base | {"verdict": "unknown", "rules": [], "text": text}
        if not srcs or not dsts:
            missing = src_text if not srcs else dst_text
            if missing in self._unknown:  # im Fabric, EPG nicht bekannt
                return base | {"verdict": "unknown", "rules": [], "text": missing}
            text = L(f"{missing} – das Fabric verwirft den Verkehr",
                     f"{missing} – the fabric drops the traffic")  # fmt: skip
            return base | {"verdict": "implicit_deny", "rules": [], "text": text}
        results = [self._pair(sid, vrf, s, d, service, dst_vrf) for s in srcs for d in dsts]
        verdicts = {r["verdict"] for r in results}
        rules: list[dict] = []
        for r in results:
            for e in r["rules"]:
                if e["rule_id"] not in {x["rule_id"] for x in rules} and len(rules) < SHOWN:
                    rules.append(e)
        if len(verdicts) == 1:
            one = results[0]
            unsure = len(results) == 1 and not all(m.sure for m in srcs + dsts)
            text = one["text"] + (
                L(" (Zuordnung nicht sicher)", " (assignment not certain)") if unsure else ""
            )
            return base | {"verdict": one["verdict"], "rules": rules, "text": text}
        text = L("abhängig von der EPG – je nach Zuordnung erlaubt bzw. gesperrt",
                 "depends on the EPG – allowed or blocked depending on the assignment")  # fmt: skip
        return base | {"verdict": "depends", "rules": rules, "text": text}

    def _pair(self, sid: int, vrf: str, s: Member, d: Member, service,
              dst_vrf: str | None = None) -> dict:  # fmt: skip
        if s.epg.id == d.epg.id:
            text = L(f"gleiche EPG ({s.text}) – ohne Contract erlaubt",
                     f"same EPG ({s.text}) – allowed without contract")  # fmt: skip
            return {"verdict": "allow", "rules": [], "text": text}
        ps, pd = self.proxies(s.epg, vrf), self.proxies(d.epg, dst_vrf or vrf)
        shown: list[dict] = []
        closed_entries: list[dict] = []
        decisive = None
        for c in self.contracts(sid):
            hit = self._match(c, ps, pd, s, d, service)
            if hit is None:
                continue
            ok, back, consumer, provider, note, closed = hit
            if back:  # Rueckrichtung: die Quelle ist Provider, das Ziel Consumer
                via_src, via_dst = ps[min(provider)], pd[min(consumer)]
            else:
                via_src = "any" if not consumer else ps[min(consumer)]
                via_dst = pd[min(provider)]
            entry = {
                "rule_id": c.id,
                "external_id": c.external_id,
                "name": c.name,
                "label": _label(c),
                "kind": c.kind,
                "action": "allow" if c.allow else "deny",
                "src": via_src,
                "dst": via_dst,
                "srcintf": [],
                "dstintf": [],
                "services": (c.reverse if back else (c.names or c.services))[:4],
                "unsure": [] if ok else [L("Dienst", "Service")],
                "decides": bool(ok),
                "snat": None,
                "reverse": back,
            }
            if note:  # greift nicht: nur Antworten (0.72.1) - zur Erklaerung, entscheidet nicht
                entry |= {"note": note, "unsure": [], "services": c.names[:4]}
            if closed:  # verbindet die EPGs, oeffnet den Dienst nicht (0.111.0) - zur Erklaerung
                if c.allow and c.kind == "contract" and len(closed_entries) < SHOWN:
                    closed_entries.append(
                        entry | {"closed": True, "unsure": [], "open": c.services[:8]}
                    )
                continue
            if not ok:
                if len(shown) < SHOWN:
                    shown.append(entry)
                continue
            shown.append(entry)
            decisive = entry  # Taboos stehen vorn: sie sperren vor allen Contracts
            break
        return _verdict(decisive, shown, s, d, service, closed_entries)

    def _match(self, c: _Contract, ps: dict, pd: dict, s: Member, d: Member, service):
        """(Urteil Dienst, Rueckrichtung, Consumer, Provider) bzw. None: Consumer -> Provider
        nach den Filtern, sonst zurueck (ACI zustandslos) mit den Diensten, die der Provider
        aufbauen kann - die sichere Richtung gewinnt. Oeffnet der Contract Consumer -> Provider
        nur Antworten (0.72.1), greift er dort nicht - mit Hinweis, wenn sonst nichts passt."""
        best = None
        note = None
        closed = None  # Consumer und Provider passen, der Filter oeffnet den Dienst nicht
        for back in (False, True):
            if back and not c.reverse:
                break
            consumer = (pd if back else ps).keys() & c.consumers
            provider = (ps if back else pd).keys() & c.providers
            if not provider or not (consumer or (c.any_consumer and not c.allow and not back)):
                continue
            a, b = (d, s) if back else (s, d)
            if not self.cls.scope_allows(c.scope, a.epg, b.epg):
                continue  # Scope (AP, VRF, Tenant) - auch bei Contracts aus common (0.43.1)
            if not back and c.replies_only:
                note = (False, False, consumer, provider, aci_direction.replies(), False)
                continue
            ok = service_ok(c.reverse if back else c.services, service)
            if ok is False:
                if closed is None and not back:
                    closed = (False, back, consumer, provider, None, True)
                continue
            if best is None or (ok and not best[0]):
                best = (ok, back, consumer, provider, None, False)
            if ok:
                break
        return best or note or closed


def _label(c: _Contract) -> str:
    if c.kind == "taboo":
        return f"Taboo {c.name}"
    if c.kind == "preferred_group":
        return c.name
    return f"Contract {c.name}"


def _verdict(decisive: dict | None, shown: list[dict], s: Member, d: Member,
             service=None, closed: list[dict] | None = None) -> dict:  # fmt: skip
    # Contracts, die nur Antworten zulassen (`note`), greifen nicht - kein „moeglich“
    before = [e for e in shown if not e["decides"] and not e.get("note")]
    if decisive is not None:
        other = [e for e in before if e["action"] != decisive["action"]]
        if other:
            label = other[0]["label"]
            text = L(f"{label} kann sperren (Dienst nicht angegeben)",
                     f"{label} can block (service not specified)")  # fmt: skip
            return {"verdict": "depends", "rules": shown, "text": text}
        label = decisive["label"]
        if decisive["action"] == "allow":
            text = L(f"erlaubt durch {label}", f"allowed by {label}")
        else:
            text = L(f"gesperrt durch {label}", f"blocked by {label}")
        if decisive.get("reverse"):
            text += L(f" in der Rückrichtung ({aci_direction.text()})",
                      f" in the reverse direction ({aci_direction.text()})")  # fmt: skip
        return {"verdict": decisive["action"], "rules": shown, "text": text}
    if before:
        text = L("nur mögliche Contracts (Dienst nicht angegeben), sonst gesperrt",
                 "only possible contracts (service not specified), otherwise blocked")  # fmt: skip
        return {"verdict": "depends", "rules": shown, "text": text}
    notes = [e for e in shown if e.get("note")]
    if closed:
        # 0.111.0 (Anwender): der vorhandene Contract hat den Port nicht offen - statt „kein
        # Contract“; mit den Diensten, die er oeffnet
        labels = ", ".join(e["label"] for e in closed[:3])
        opened = ", ".join(dict.fromkeys(x for e in closed[:3] for x in e["open"])) or "–"
        want = _want_text(service)
        text = L(f"{labels} von {s.text} zu {d.text} öffnet {want} nicht (offen: {opened}) – "
                 "im ACI gesperrt",
                 f"{labels} from {s.text} to {d.text} does not open {want} (open: {opened}) – "
                 "blocked in the ACI")  # fmt: skip
        if notes:
            text += f" ({notes[0]['label']}: {notes[0]['note']})"
        return {"verdict": "implicit_deny", "rules": closed + notes, "text": text}
    text = L(f"kein Contract von {s.text} zu {d.text} – im ACI gesperrt",
             f"no contract from {s.text} to {d.text} – blocked in the ACI")  # fmt: skip
    if notes:
        text += f" ({notes[0]['label']}: {notes[0]['note']})"
    return {"verdict": "implicit_deny", "rules": notes, "text": text}


def _want_text(service) -> str:
    """Gesuchter Dienst im Satz: `tcp/1433`, `icmp` bzw. „den Dienst“."""
    if not service:
        return L("den Dienst", "the service")
    return service[0] if service[1] is None else f"{service[0]}/{service[1]}"


def _either(texts) -> str:
    """Mehrere Zuordnungen: „EPG A bzw. EPG B“."""
    return L(" bzw. ", " or ").join(texts)


def service_ok(names: list[str], want: tuple[str, int | None] | None) -> bool | None:
    """Filter-Eintraege (`tcp/443`, `tcp/80-90`, `tcp/any`, `ip/any`, `icmp/any`) gegen den
    Dienst; ohne Dienst nur `ip/any` sicher."""
    if not names:
        return None
    results: list[bool | None] = []
    for name in names:
        proto, _, port = name.lower().partition("/")
        if proto in ("ip", "all", "unspecified") and port in _ALL:
            results.append(True)
        elif want is None:
            results.append(None)
        elif proto in ("tcp", "udp"):
            if proto != want[0]:
                results.append(False)
            elif port in _ALL:
                results.append(True)
            elif (rng := _range(port)) is None:
                results.append(None)
            elif want[1] is None:
                results.append(True if rng[0] <= 1 and rng[1] >= 65535 else None)
            else:
                results.append(rng[0] <= want[1] <= rng[1])
        elif proto in ("icmp", "icmpv4", "icmpv6"):
            results.append(want[0] == "icmp")
        else:
            results.append(False)  # z. B. arp: kein IP-Verkehr
    return True if True in results else (None if None in results else False)


def _range(text: str) -> tuple[int, int] | None:
    a, _, b = text.partition("-")
    if not a.isdigit():
        return None
    return int(a), int(b) if b.isdigit() else int(a)
