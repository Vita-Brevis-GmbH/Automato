"""IPsec-Tunnel der FortiGates (1.19.0, ADR 0063) - Speicher, Weg, Ansicht und Pruefung.

Anwender 2026-11-17: „VPN integrieren“ - „IPsec reicht, ssl-vpn wird nicht mehr unterstützt“.

- **Gelesen** mit dem Routing (`collectors/fortigate/vpn.py`) und je Lauf ersetzt
  (`topo_vpn_tunnel`, `topology.replace`): Phase 1 im Interface-Modus, Phase 2 mit Selektoren,
  Zustand je Tunnel und Phase 2, bei Dial-up die verbundenen Gegenstellen.
- **Routen zugeordnet:** eine Route (RIB bzw. statisch) gehoert zum Tunnel, wenn ein Next-Hop
  das Tunnel-Interface ist - bei Dial-up auch eine Instanz (`<tunnel>_<n>`).
- **Netze hinter dem Tunnel:** die Selektoren der Gegenstelle (Phase 2), bei Dial-up die Netze
  der verbundenen Gegenstellen, und die Routen ueber den Tunnel.
- **Gegenstelle gelesen?** Gehoert die Adresse der Gegenstelle (Dial-up: der verbundenen) einem
  Interface einer gelesenen FortiGate, geht der Weg dort weiter - Eingang ist ihr Tunnel, dessen
  Gegenstelle eine eigene Adresse dieser Firewall ist (sonst ein Tunnel auf dem Interface). Ohne
  Treffer, aber mit einem Tunnel einer gelesenen FortiGate, dessen Gegenstelle eine eigene Adresse
  ist (NAT davor), ebenfalls dort. Sonst endet der Weg sichtbar an der Gegenstelle (`paths`: Ende
  `vpn`; in den Verbindungen gezaehlt, ADR 0048).
- **Befunde** (`checks/analyse/vpn.py`): Tunnel bzw. Phase 2 down; ein Phase-2-Selektor deckt
  eine Route ueber den Tunnel nur teilweise; Route ueber den Tunnel ohne passenden Selektor.

Nur lesend; nichts davon geht an ein Geraet.
"""

import ipaddress
import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import InvInterface, Platform, SourceSystem, TopoRoute, TopoVpnTunnel

KINDS = ("static", "dialup", "ddns")
STATES = ("up", "partial", "down", "idle")
# Bezeichnungen der Tabellen, wenn das Leserecht fehlt (wie `collectors/fortigate/routing.py`)
MISSING = ("IPsec Phase 1", "IPsec Phase 2", "IPsec-Zustand")
_INSTANCE = re.compile(r"^(.+)_(\d+)$")  # Dial-up-Instanz `<tunnel>_<n>`
_CONNECTED = ("connected", "local")
_ROUTES_SHOWN = 200  # Routen je Tunnel in der Antwort (Zahl immer ganz)

Span = tuple[int, int, int]  # Familie, von, bis


def is_vpn_missing(text: str) -> bool:
    """Gehoert ein „nicht lesbar“ eines Kontexts zum IPsec (nicht zur Routing-Tabelle)?"""
    return str(text).startswith(MISSING)


def kind_text(kind: str | None) -> str:
    return {"dialup": L("Dial-up", "Dial-up"), "ddns": "DDNS"}.get(
        kind or "", L("statisch", "static")
    )


def status_text(status: str | None) -> str:
    return {"up": "up", "down": "down", "partial": L("teilweise up", "partially up"),
            "idle": L("keine Gegenstelle verbunden", "no peer connected")}.get(
        status or "", L("Zustand unbekannt", "state unknown"))  # fmt: skip


# -- Speichern ------------------------------------------------------------------------------


def rows(base: dict, tunnels: list) -> list[dict]:
    """Zeilen `topo_vpn_tunnel` eines Laufs (`topology.replace`)."""
    return [
        {
            **base,
            "node": t.node,
            "context": t.context,
            "name": t.name,
            "kind": t.kind,
            "interface": t.interface,
            "remote_gw": t.remote_gw,
            "local_gw": t.local_gw,
            "ike_version": t.ike_version,
            "status": t.status,
            "phase2": [p.model_dump(mode="json") for p in t.phase2] or None,
            "instances": t.instances or None,
            "attrs": t.attrs or None,
        }
        for t in tunnels
    ]


# -- Bereiche -------------------------------------------------------------------------------


def span(value) -> Span | None:
    """CIDR, Adresse bzw. Bereich `a-b` -> (Familie, von, bis); sonst None."""
    if isinstance(value, ipaddress.IPv4Network | ipaddress.IPv6Network):
        return value.version, int(value.network_address), int(value.broadcast_address)
    if isinstance(value, ipaddress.IPv4Address | ipaddress.IPv6Address):
        return value.version, int(value), int(value)
    text = str(value or "").strip()
    try:
        if "-" in text and "/" not in text:
            a, _, b = text.partition("-")
            lo, hi = ipaddress.ip_address(a.strip()), ipaddress.ip_address(b.strip())
            return (lo.version, int(lo), int(hi)) if lo.version == hi.version else None
        net = ipaddress.ip_network(text, strict=False)
    except ValueError:
        return None
    return net.version, int(net.network_address), int(net.broadcast_address)


def coverage(selectors: list[str], target) -> str:
    """Deckt die Vereinigung der Selektoren das Ziel (Adresse, Netz bzw. Text)?
    `full` | `partial` | `none`."""
    t = span(target)
    if t is None:
        return "none"
    fam, lo, hi = t
    parts = sorted(s[1:] for s in (span(x) for x in selectors) if s is not None and s[0] == fam)
    merged: list[list[int]] = []
    for a, b in parts:
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    if any(a <= lo and hi <= b for a, b in merged):
        return "full"
    return "partial" if any(a <= hi and lo <= b for a, b in merged) else "none"


# -- Tunnel ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tunnel:
    """Tunnel als reine Daten (ohne Sitzung) - fuer Weg, Ansicht und Pruefung."""

    id: int
    source_system_id: int
    node: str
    context: str
    name: str
    kind: str
    interface: str | None
    remote_gw: str | None
    local_gw: str | None
    ike_version: int | None
    status: str | None
    phase2: tuple = ()
    instances: tuple = ()
    attrs: dict = field(default_factory=dict, hash=False, compare=False)

    @classmethod
    def of(cls, r: TopoVpnTunnel) -> "Tunnel":
        return cls(r.id, r.source_system_id, r.node, r.context, r.name, r.kind, r.interface,
                   r.remote_gw, r.local_gw, r.ike_version, r.status, tuple(r.phase2 or []),
                   tuple(r.instances or []), dict(r.attrs or {}))  # fmt: skip

    @property
    def key(self) -> tuple[int, str]:
        return (self.source_system_id, self.context)

    @property
    def ref(self) -> str:
        """Stabiler Schluessel `<Quellsystem>|<VDOM>|<Tunnel>` (Kennungen wechseln je Lauf)."""
        return f"{self.source_system_id}|{self.context}|{self.name}"

    def selectors(self, side: str) -> list[str]:
        """Selektoren einer Seite ueber alle Phase 2 (`src` lokal, `dst` Gegenstelle)."""
        out: list[str] = []
        for p in self.phase2:
            for v in p.get(side) or []:
                if v not in out:
                    out.append(v)
        return out

    @property
    def remote(self) -> list[str]:
        """Netze der Gegenstelle: Selektoren, bei Dial-up dazu die der verbundenen."""
        out = self.selectors("dst")
        for i in self.instances:
            out += [n for n in i.get("nets") or [] if n not in out]
        return out

    @property
    def unresolved(self) -> list[str]:
        """Selektoren, die sich nicht aufloesen liessen (FQDN, unbekannter Name)."""
        return list(dict.fromkeys(x for p in self.phase2 for x in p.get("unresolved") or []))

    def covers(self, target) -> str | None:
        """Ziel in den Netzen der Gegenstelle? `full` | `partial` | `none`; ohne Phase 2 bzw.
        mit nicht aufloesbaren Selektoren und ohne Treffer None (unbekannt)."""
        if not self.phase2:
            return None
        found = coverage(self.remote, target)
        return None if found == "none" and self.unresolved else found

    def phase2_for(self, target) -> dict | None:
        """Phase 2, deren Selektor der Gegenstelle das Ziel enthaelt (die erste)."""
        for p in self.phase2:
            if coverage(p.get("dst") or [], target) == "full":
                return p
        return None


class Index:
    """Tunnel eines Mandanten je Knoten und Interface (auch Dial-up-Instanzen) und die
    Adressen der Interfaces gelesener Firewalls - einmal je Graph bzw. Abruf geladen."""

    def __init__(self, session: Session, tenant_id: int, customer=None):
        self.session = session
        self.tenant_id = tenant_id
        stmt = (
            select(TopoVpnTunnel)
            .where(TopoVpnTunnel.tenant_id == tenant_id)
            .order_by(TopoVpnTunnel.source_system_id, TopoVpnTunnel.context, TopoVpnTunnel.name)
        )
        self.tunnels = [Tunnel.of(r) for r in session.scalars(stmt)]
        self.by_name: dict[tuple[int, str, str], Tunnel] = {}
        self.by_instance: dict[tuple[int, str, str], tuple[Tunnel, dict]] = {}
        self.by_sid: dict[int, list[Tunnel]] = defaultdict(list)
        for t in self.tunnels:
            self.by_name[(t.source_system_id, t.context, t.name)] = t
            self.by_sid[t.source_system_id].append(t)
            for i in t.instances:
                self.by_instance[(t.source_system_id, t.context, str(i.get("name")))] = (t, i)
        self._addrs: dict[str, list[tuple[int, str | None, str]]] | None = None
        self._firewalls: set[int] | None = None
        self._own: dict[tuple[int, str], set[str]] | None = None
        self._pairing = None  # vpn_kopplung.Pairing (1.23.0), erst bei Bedarf

    def pairing(self):
        """Kopplungen der Tunnel (1.23.0, `store/vpn_kopplung.py`): automatisch erkannt bzw.
        von Hand - einmal je Index."""
        if self._pairing is None:
            from app.store import vpn_kopplung  # spaet: vpn_kopplung -> vpn

            self._pairing = vpn_kopplung.Pairing(
                self, vpn_kopplung.rows(self.session, self.tenant_id) if self.tunnels else []
            )
        return self._pairing

    def __bool__(self) -> bool:
        return bool(self.tunnels)

    def find(
        self, key: tuple[int, str], interface: str | None
    ) -> tuple[Tunnel, dict | None] | None:
        """Tunnel hinter einem Interface des Knotens: (Tunnel, Dial-up-Instanz bzw. None)."""
        if not interface or not self.tunnels:
            return None
        sid, ctx = key
        t = self.by_name.get((sid, ctx, interface))
        if t is not None:
            return t, None
        hit = self.by_instance.get((sid, ctx, interface))
        if hit is not None:
            return hit
        m = _INSTANCE.match(interface)  # Instanz, die der Monitor (noch) nicht zeigte
        t = self.by_name.get((sid, ctx, m.group(1))) if m else None
        return (t, {"name": interface}) if t is not None and t.kind == "dialup" else None

    # -- Adressen ---------------------------------------------------------------------------

    def addresses(self) -> dict[str, list[tuple[int, str | None, str]]]:
        """IP -> (Quellsystem, VDOM, Interface) der Interfaces der gelesenen FortiGates."""
        if self._addrs is None:
            out: dict[str, list[tuple[int, str | None, str]]] = defaultdict(list)
            for sid, vdom, name, ips in self.session.execute(
                select(InvInterface.source_system_id, InvInterface.vdom, InvInterface.name,
                       InvInterface.ips).where(InvInterface.tenant_id == self.tenant_id,
                                               InvInterface.source_system_id.in_(self.firewalls()))
            ):  # fmt: skip
                for ip in ips or []:
                    out[str(ip).split("/")[0]].append((sid, vdom, name))
            self._addrs = out
        return self._addrs

    def firewalls(self) -> set[int]:
        """Quellsysteme, die FortiGates sind (Gegenstelle gelesen nur dort)."""
        if self._firewalls is None:
            stmt = select(SourceSystem.id).where(
                SourceSystem.tenant_id == self.tenant_id,
                SourceSystem.platform == Platform.FORTIGATE,
            )
            self._firewalls = set(self.session.scalars(stmt))
        return self._firewalls

    def own(self, t: Tunnel) -> set[str]:
        """Eigene Adressen des Tunnels: fest eingestellt bzw. die des gebundenen Interfaces."""
        if t.local_gw:
            return {t.local_gw}
        if self._own is None:
            self._own = defaultdict(set)
            for ip, uses in self.addresses().items():
                for sid, _vdom, name in uses:
                    self._own[(sid, name)].add(ip)
        return self._own.get((t.source_system_id, t.interface or ""), set())

    def far(self, t: Tunnel, inst: dict | None = None, owners=None, dst=None) -> list[dict]:
        """Gelesene Gegenstelle(n): je Treffer Quellsystem, VDOM, Tunnel dort (bzw. None) und
        wie gefunden (`how`: `gw` Adresse der Gegenstelle, `vip` VIP davor, `reverse` deren
        Tunnel zeigt auf eine eigene Adresse, `dialup` verbundene Gegenstelle, `manual` von Hand)
        mit der Kopplung (`pair`: `auto` | `manual`, 1.23.0). Gekoppelte Tunnel gehen vor
        (`vpn_kopplung`); sonst die FortiGate, der die Adresse der Gegenstelle gehoert - Tunnel
        dort nicht eindeutig: None. `owners(ip)` -> [(Quellsystem, VDOM, Interface)] (Graph der
        Wege), sonst die Interfaces aller gelesenen Geraete; `dst` waehlt bei Dial-up das
        Gegenstueck. Leer = Gegenstelle nicht gelesen."""
        pairing = self.pairing()
        link = pairing.partner(t, inst, dst)
        if link is not None:
            x = link.other(t)
            return [{"source_system_id": x.source_system_id, "context": x.context,
                     "tunnel": x.name, "interface": x.interface, "how": link.how_for(t),
                     "pair": link.origin}]  # fmt: skip
        gw = (inst or {}).get("remote_gw") if t.kind == "dialup" else t.remote_gw
        found: list[dict] = []
        if gw:
            uses = owners(gw) if owners is not None else self.addresses().get(gw, [])
            for sid, vdom, iface in uses:
                if sid == t.source_system_id or sid not in self.firewalls():
                    continue
                entry = {"source_system_id": sid, "context": vdom, "tunnel": None,
                         "interface": iface, "how": "gw"}  # fmt: skip
                if vdom is None:  # eine FortiGate ohne VDOM: ihr einziger Kontext
                    ctxs = {x.context for x in self.by_sid.get(sid, [])}
                    entry["context"] = next(iter(ctxs)) if len(ctxs) == 1 else None
                open_ = [h for h in pairing.suggestions(t) if h.tunnel.source_system_id == sid]
                if open_:
                    entry["open"] = len(open_)  # nicht eindeutig: in der Ansicht koppeln
                if entry not in found:
                    found.append(entry)
        return found

    def peers(self, t: Tunnel) -> list[dict]:
        """Gegenstellen eines Tunnels fuer die Ansicht: alle Kopplungen (Dial-up: je
        gekoppelter Gegenstelle), sonst wie im Weg (`far`, Dial-up je verbundener)."""
        links = self.pairing().of(t)
        out = [{"source_system_id": k.other(t).source_system_id, "context": k.other(t).context,
                "tunnel": k.other(t).name, "interface": k.other(t).interface, "how": k.how_for(t),
                "pair": k.origin, "pair_id": k.pair_id, "tunnel_id": k.other(t).id,
                "ref": k.other(t).ref} for k in links]  # fmt: skip
        if t.kind != "dialup":
            return out or self.far(t)
        for i in t.instances:
            if any(self.pairing().partner(t, i) is k for k in links):
                continue
            for p in self.far(t, i):
                if p not in out:
                    out.append(p)
        return out

    # -- Routen ueber die Tunnel -------------------------------------------------------------

    def routes(self) -> dict[int, list[dict]]:
        """Routen ueber die Tunnel: Kennung des Tunnels -> RIB (beste) und statische Routen
        (eingeschaltet), deren Next-Hop das Tunnel-Interface bzw. eine Dial-up-Instanz ist -
        ohne angeschlossene Netze des Tunnels selbst."""
        if not self.tunnels:
            return {}
        names = sorted({t.name for t in self.tunnels})
        out: dict[int, list[dict]] = defaultdict(list)
        hops = cast(TopoRoute.next_hops, String)
        stmt = (
            select(TopoRoute.source_system_id, TopoRoute.context, TopoRoute.prefix,
                   TopoRoute.family, TopoRoute.table, TopoRoute.protocol, TopoRoute.distance,
                   TopoRoute.best, TopoRoute.next_hops)
            .where(TopoRoute.tenant_id == self.tenant_id,
                   TopoRoute.source_system_id.in_(set(self.by_sid)),
                   TopoRoute.table.in_(("rib", "static")),
                   or_(*(hops.like(f'%"{n}%') for n in names)))
            .order_by(TopoRoute.family, TopoRoute.prefix, TopoRoute.id)
        )  # fmt: skip
        seen: set[tuple] = set()
        for sid, ctx, prefix, family, table, protocol, distance, best, next_hops in \
                self.session.execute(stmt):  # fmt: skip
            if (protocol or "") in _CONNECTED or not best:
                continue
            for h in next_hops or []:
                hit = self.find((sid, ctx), h.get("interface"))
                if hit is None:
                    continue
                t, inst = hit
                mark = (t.id, prefix, table)
                if mark in seen:
                    continue
                seen.add(mark)
                entry = {
                    "prefix": prefix,
                    "family": family,
                    "table": table,
                    "protocol": protocol,
                    "distance": distance,
                }
                if h.get("ip"):
                    entry["next_hop"] = h["ip"]
                if inst is not None:
                    entry["instance"] = inst.get("name")
                out[t.id].append(entry)  # fmt: skip
        return out

    def behind(self, t: Tunnel, routes: list[dict] | None = None) -> list[dict]:
        """Netze hinter dem Tunnel mit Herkunft: Selektor (Phase 2), Dial-up-Gegenstelle bzw.
        Route ueber den Tunnel (ohne Default-Route)."""
        out: dict[str, dict] = {}
        for p in t.phase2:
            for v in p.get("dst") or []:
                out.setdefault(v, {"net": v, "from": []})["from"].append(f"phase2:{p['name']}")
        for i in t.instances:
            for v in i.get("nets") or []:
                out.setdefault(v, {"net": v, "from": []})["from"].append(f"dialup:{i['name']}")
        for r in routes or []:
            if r["prefix"].endswith("/0"):
                continue
            out.setdefault(r["prefix"], {"net": r["prefix"], "from": []})["from"].append(
                f"route:{r['table']}"
            )
        return list(out.values())


def _fits(o: Tunnel, x: Tunnel) -> bool:
    """Passen die Netze hinter `o` zu den lokalen Selektoren von `x` (ohne 0.0.0.0/0)?"""
    behind = [v for v in o.remote if not v.endswith("/0")]
    theirs = [v for v in x.selectors("src") if not v.endswith("/0")]
    return bool(behind and theirs) and any(coverage(behind, v) != "none" for v in theirs)


# -- Weg (`paths.trace`) --------------------------------------------------------------------


def pair_id(t: Tunnel, peer: dict | None) -> str:
    """Kennung des Tunnels im Netzwerkplan: beide Enden gleich, gleich welche Richtung."""
    ends = [t.ref]
    if peer and peer.get("tunnel"):
        ends.append(f"{peer['source_system_id']}|{peer['context']}|{peer['tunnel']}")
    return "~".join(sorted(ends))


def step(t: Tunnel, inst: dict | None, dst, peers: list[dict], labels) -> dict:
    """Tunnel als Schritt des Wegs (am Ausgang des Hops): Name, Art, Zustand, Gegenstelle, ob
    das Ziel in den Netzen der Gegenstelle liegt, und die gelesene Gegenstelle (`labels(key)` ->
    Bezeichnung des Knotens)."""
    gw = (inst or {}).get("remote_gw") if t.kind == "dialup" else t.remote_gw
    status = ((inst or {}).get("status") or t.status) if t.kind == "dialup" else t.status
    out: dict = {"name": t.name, "kind": t.kind, "status": status, "remote_gw": gw,
                 "underlay": t.interface, "ike": t.ike_version}  # fmt: skip
    if inst is not None:
        out["instance"] = inst.get("name")
    covered = t.covers(dst)
    if covered is not None:
        out["covered"] = covered
    p2 = t.phase2_for(dst)
    if p2 is not None:
        out["phase2"] = p2["name"]
        if p2.get("status"):
            out["phase2_status"] = p2["status"]
    first = peers[0] if peers else None
    if first is not None:
        key = (first["source_system_id"], first["context"])
        out["peer"] = {"key": f"{key[0]}|{key[1]}", "source_system_id": key[0],
                       "context": key[1], "label": labels(key), "tunnel": first.get("tunnel"),
                       "how": first["how"]}  # fmt: skip
        # Kopplung (1.23.0): automatisch bzw. von Hand; nicht eindeutig: Zahl der Vorschlaege
        for k in ("pair", "open"):
            if first.get(k):
                out["peer"][k] = first[k]
    out["id"] = pair_id(t, first)
    return {k: v for k, v in out.items() if v not in (None, "")}


def how_text(v: dict) -> str:
    """Wie der Weg am naechsten Knoten ankam: „über VPN vpn-b (Gegenstelle 203.0.113.2)“."""
    gw = f" ({L('Gegenstelle', 'peer')} {v['remote_gw']})" if v.get("remote_gw") else ""
    peer = v.get("peer") or {}
    tail = ""
    if peer and not peer.get("tunnel") and peer.get("open"):
        tail = L(" – Tunnel dort nicht eindeutig, in „VPN-Tunnel“ koppeln",
                 " – tunnel there not unique, couple it in “VPN tunnels”")  # fmt: skip
    elif peer and not peer.get("tunnel"):
        tail = L(" – dort kein passender Tunnel gelesen", " – no matching tunnel read there")
    elif peer.get("how") == "reverse":
        tail = L(" – erkannt am Tunnel der Gegenstelle (NAT davor?)",
                 " – recognized by the tunnel of the peer (NAT in front?)")  # fmt: skip
    elif peer.get("how") == "vip":
        tail = L(" – erkannt an der VIP vor der Gegenstelle",
                 " – recognized by the VIP in front of the peer")  # fmt: skip
    elif peer.get("how") == "spoke":
        tail = L(" – erkannt am statischen Tunnel der Gegenstelle",
                 " – recognized by the static tunnel of the peer")  # fmt: skip
    elif peer.get("how") == "dialup":
        tail = L(" – erkannt an der verbundenen Dial-up-Gegenstelle",
                 " – recognized by the connected dial-up peer")  # fmt: skip
    elif peer.get("pair") == "manual":
        tail = L(f" – von Hand gekoppelt mit {peer['tunnel']}",
                 f" – coupled manually with {peer['tunnel']}")  # fmt: skip
    return L(f"über VPN {v['name']}{gw}{tail}", f"via VPN {v['name']}{gw}{tail}")


def end_text(v: dict, names: list[str] | None = None) -> str:
    """Ende an einer Gegenstelle ohne gelesenes Geraet (ADR 0063: sichtbar, gezaehlt)."""
    known = f" ({', '.join(names)})" if names else ""
    if v.get("kind") == "dialup" and not v.get("remote_gw"):
        text = L(f"weiter über Dial-up-VPN {v['name']} – keine Gegenstelle verbunden",
                 f"onward via dial-up VPN {v['name']} – no peer connected")  # fmt: skip
    else:
        what = L("Dial-up-VPN", "dial-up VPN") if v.get("kind") == "dialup" else "VPN"
        inst = f" ({v['instance']})" if v.get("instance") else ""
        text = L(f"weiter über {what} {v['name']}{inst} zur Gegenstelle {v.get('remote_gw')}"
                 f"{known} – kein gelesenes Gerät, der Weg dahinter ist nicht geprüft",
                 f"onward via {what} {v['name']}{inst} to the peer {v.get('remote_gw')}{known} – "
                 "no device read, the path behind it is not checked")  # fmt: skip
    return text + _notes(v)


def _notes(v: dict) -> str:
    out = ""
    if v.get("covered") == "none":
        out += L(" – Ziel in keinem Phase-2-Selektor", " – destination in no phase 2 selector")
    elif v.get("covered") == "partial":
        out += L(" – Ziel nur teilweise in den Phase-2-Selektoren",
                 " – destination only partly in the phase 2 selectors")  # fmt: skip
    if v.get("status") == "down" or v.get("phase2_status") == "down":
        out += L(" – Tunnel down", " – tunnel down")
    return out


def note(v: dict, label: str) -> str | None:
    """Hinweis am Weg, wenn der Tunnel nicht traegt (Selektor, Zustand)."""
    extra = _notes(v)
    return L(f"VPN {v['name']} an {label}{extra}", f"VPN {v['name']} at {label}{extra}") \
        if extra else None  # fmt: skip


def hop(g, key, found: tuple[Tunnel, dict | None], dst, egress: dict, nxt: list) -> list:
    """Tunnel am Ausgang eines Hops (`paths._trace`): den Schritt am Ausgang festhalten und den
    naechsten Knoten bestimmen - der Nachbar ueber die Next-Hop-IP (BGP ueber IPsec), sonst die
    gelesene FortiGate der Gegenstelle mit ihrem Tunnel als Eingang. Leer = Gegenstelle nicht
    gelesen (Ende `vpn`)."""
    t, inst = found
    index = g.vpn()

    def owners(ip: str) -> list[tuple[int, str | None, str]]:
        return [(k[0], k[1], i or "") for k, i, _z in g.owners().get(ip, [])]

    peers = []
    if not nxt:
        peers = [p for p in index.far(t, inst, owners, dst)
                 if (p["source_system_id"], p["context"]) in g.nodes]  # fmt: skip
    else:  # Next-Hop gehoert einer gelesenen Firewall (Adresse im Tunnel): sie ist die Gegenstelle
        link = index.pairing().partner(t, inst, dst)
        x = link.other(t) if link is not None else None
        peers = [{"source_system_id": k[0], "context": k[1], "tunnel": i, "how": "gw",
                  **({"pair": link.origin} if x is not None and x.key == k else {})}
                 for k, i, _how in nxt]  # fmt: skip
    v = step(t, inst, dst, peers, lambda k: g.nodes[k].label if k in g.nodes else None)
    egress["vpn"] = v
    how = how_text(v)
    if nxt:
        return [(k, i, how) for k, i, _how in nxt]
    return [((p["source_system_id"], p["context"]), p.get("tunnel"), how) for p in peers]


# -- Ansicht --------------------------------------------------------------------------------


def _labels(session: Session, tenant_id: int) -> dict[int, str]:
    stmt = select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
    return {sid: name for sid, name in session.execute(stmt)}


def _check(t: Tunnel, routes: list[dict]) -> list[dict]:
    """Routen ueber den Tunnel gegen die Selektoren der Gegenstelle: je Route `full`,
    `partial`, `none` bzw. None (Selektoren nicht aufloesbar, ohne Phase 2)."""
    out = []
    for r in routes:
        out.append({**r, "covered": t.covers(r["prefix"])})
    return out


def summary_of(t: Tunnel, index: Index, routes: list[dict], labels: dict[int, str]) -> dict:
    """Tunnel fuer Liste und Detail."""
    peers = index.peers(t)  # Kopplungen (1.23.0), sonst nach der Adresse der Gegenstelle
    checked = _check(t, routes)
    p2 = list(t.phase2)
    up = sum(1 for p in p2 if p.get("status") == "up")
    return {
        "id": t.id,
        "ref": t.ref,
        "source_system_id": t.source_system_id,
        "source": labels.get(t.source_system_id, f"#{t.source_system_id}"),
        "node": t.node,
        "context": t.context,
        "name": t.name,
        "kind": t.kind,
        "interface": t.interface,
        "remote_gw": t.remote_gw,
        "local_gw": t.local_gw,
        "ike_version": t.ike_version,
        "status": t.status,
        "phase2_total": len(p2),
        "phase2_up": up,
        "instances": len(t.instances),
        "routes": len(routes),
        "uncovered": sum(1 for r in checked if r["covered"] == "none"),
        "partial": sum(1 for r in checked if r["covered"] == "partial"),
        "peers": [
            {
                **p,
                "label": labels.get(p["source_system_id"], "?")
                + (f" · VDOM {p['context']}" if p.get("context") not in (None, "root") else ""),
            }
            for p in peers
        ],
        # Vorschlaege fuer eine Kopplung von Hand (1.23.0): nicht eindeutig bzw. gespiegelt
        "suggestions": len(index.pairing().suggestions(t)),
        "proposal": t.attrs.get("proposal"),
    }


def listing(session: Session, tenant_id: int, q: str | None = None, status: str | None = None,
            system: int | None = None, keep=None) -> dict:  # fmt: skip
    """Tunnel des Mandanten fuer die Ansicht „VPN-Tunnel“: je Tunnel Zustand, Gegenstelle
    (gelesen bzw. nicht), Phase 2 und Routen; dazu, wo IPsec nicht lesbar war. `keep(sid, vdom)`:
    Ausschnitt eines Kunden."""
    from app.models import TopoContext

    index = Index(session, tenant_id)
    labels = _labels(session, tenant_id)
    routes = index.routes()
    needle = (q or "").strip().lower()
    items = []
    for t in index.tunnels:
        if keep is not None and not keep(t.source_system_id, t.context):
            continue
        if system is not None and t.source_system_id != system:
            continue
        if status and (t.status or "unknown") != status:
            continue
        item = summary_of(t, index, routes.get(t.id, []), labels)
        if needle:
            text = " ".join(str(x) for x in (item["source"], t.node, t.context, t.name,
                                              t.remote_gw, t.interface, *t.remote))  # fmt: skip
            if needle not in text.lower():
                continue
        items.append(item)
    counts = {s: sum(1 for x in items if (x["status"] or "unknown") == s)
              for s in (*STATES, "unknown")}  # fmt: skip
    missing = []
    ctx_stmt = (
        select(TopoContext)
        .where(TopoContext.tenant_id == tenant_id)
        .order_by(TopoContext.source_system_id, TopoContext.context)
    )
    for c in session.scalars(ctx_stmt):
        gone = [m for m in c.missing or [] if is_vpn_missing(m)]
        if gone and (keep is None or keep(c.source_system_id, c.context)):
            missing.append({"source_system_id": c.source_system_id,
                            "source": labels.get(c.source_system_id), "context": c.context,
                            "missing": gone})  # fmt: skip
    orphans = [_orphan(r, labels) for r in index.pairing().orphans
               if keep is None or any(keep(e[0], e[1]) for e in r.ends)]  # fmt: skip
    return {"tunnels": items, "total": len(items), "counts": counts, "missing": missing,
            "orphans": orphans}  # fmt: skip


def _orphan(r, labels: dict[int, str]) -> dict:
    """Kopplung von Hand, deren Tunnel ein Lauf nicht mehr liefert (1.23.0): sichtbar, loesbar."""
    return {"pair_id": r.id, "mode": r.mode,
            "ends": [{"source_system_id": e[0], "source": labels.get(e[0], f"#{e[0]}"),
                      "context": e[1], "tunnel": e[2]} for e in r.ends]}  # fmt: skip


def detail(session: Session, tenant_id: int, tunnel_id: int, keep=None) -> dict | None:
    """Ein Tunnel mit Phase 2, Dial-up-Gegenstellen, Routen ueber den Tunnel (mit Selektor),
    Netzen dahinter und der gelesenen Gegenstelle."""
    index = Index(session, tenant_id)
    t = next((x for x in index.tunnels if x.id == tunnel_id), None)
    if t is None:
        return None
    labels = _labels(session, tenant_id)
    routes = index.routes().get(t.id, [])
    out = summary_of(t, index, routes, labels)
    checked = _check(t, routes)
    out |= {
        "phase2": list(t.phase2),
        "dialup": list(t.instances),
        "route_list": checked[:_ROUTES_SHOWN],
        "behind": index.behind(t, routes),
        "unresolved": t.unresolved,
        "attrs": t.attrs,
        **_pairing_detail(t, index, labels, keep),
    }
    return out


def _where(labels: dict[int, str], x: Tunnel) -> str:
    vdom = f" · VDOM {x.context}" if x.context not in (None, "root") else ""
    return labels.get(x.source_system_id, f"#{x.source_system_id}") + vdom


def _pairing_detail(t: Tunnel, index: Index, labels: dict[int, str], keep=None) -> dict:
    """Kopplung im Detail (1.23.0): Vorschlaege, von Hand geloeste automatische Kopplungen und
    die Tunnel anderer FortiGates, mit denen sich der Tunnel koppeln laesst."""
    from app.store import vpn_kopplung

    pairing = index.pairing()

    def ref(x: Tunnel) -> dict:
        return {"tunnel_id": x.id, "ref": x.ref, "source_system_id": x.source_system_id,
                "context": x.context, "tunnel": x.name, "kind": x.kind,
                "label": _where(labels, x)}  # fmt: skip

    seen = keep or (lambda _sid, _ctx: True)
    suggestions = [{**ref(h.tunnel), "why": h.why, "exact": h.exact,
                    "text": vpn_kopplung.why_text(h.why, h.exact)}
                   for h in pairing.suggestions(t)
                   if seen(h.tunnel.source_system_id, h.tunnel.context)]  # fmt: skip
    separated = []
    for ends, pid in pairing.separated.items():
        if vpn_kopplung.end_of(t) in ends:
            other = next(pairing.by_end[e] for e in ends if e != vpn_kopplung.end_of(t))
            separated.append({**ref(other), "pair_id": pid})
    taken = {vpn_kopplung.end_of(k.other(t)) for k in pairing.of(t)}
    choices = [ref(x) for x in index.tunnels
               if x.source_system_id != t.source_system_id
               and not (t.kind == "dialup" and x.kind == "dialup")
               and vpn_kopplung.end_of(x) not in taken
               and seen(x.source_system_id, x.context)]  # fmt: skip
    return {"suggestion_list": suggestions, "separated": separated, "choices": choices}


# -- Pruefung -------------------------------------------------------------------------------


def problems(session: Session, tenant_id: int) -> list[dict]:
    """Befunde der Tunnel (`checks/analyse/vpn.py`): `down` (Tunnel bzw. Phase 2 down, nicht
    Dial-up), `partial` (Selektor deckt eine Route ueber den Tunnel nur teilweise), `none`
    (Route ueber den Tunnel ohne passenden Selektor). Zustand bzw. Selektoren unbekannt: nichts."""
    index = Index(session, tenant_id)
    if not index:
        return []
    labels = _labels(session, tenant_id)
    routes = index.routes()
    out = []
    for t in index.tunnels:
        base = {"tunnel": t.name, "ref": t.ref, "source": labels.get(t.source_system_id),
                "source_system_id": t.source_system_id, "vdom": t.context, "node": t.node,
                "remote_gw": t.remote_gw}  # fmt: skip
        if t.kind != "dialup" and t.status in ("down", "partial"):
            down = [p["name"] for p in t.phase2 if p.get("status") == "down"]
            out.append({**base, "problem": "down", "status": t.status, "phase2_down": down})
        if not t.phase2 or t.unresolved:
            continue
        found: dict[str, list[str]] = {"partial": [], "none": []}
        for r in _check(t, routes.get(t.id, [])):
            if r["covered"] in found and r["prefix"] not in found[r["covered"]]:
                found[r["covered"]].append(r["prefix"])
        for problem, prefixes in found.items():
            if prefixes:
                out.append({**base, "problem": problem, "prefixes": prefixes,
                            "selectors": t.remote[:20]})  # fmt: skip
    return out
