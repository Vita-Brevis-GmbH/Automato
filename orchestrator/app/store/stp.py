"""Spanning Tree der Switches (Automato 1.19.0, Anwender 2026-11-17 Prioritaet 5).

Der Switch-Collector liest je VLAN bzw. MST-Instanz Root, eigene Bridge, Ports mit Rolle und
Zustand und die Topologieaenderungen (`collectors/cisco_switch/stp.py`, IOS/IOS-XE und NX-OS);
andere Plattformen stehen mit Status `unsupported` da und werden gezaehlt. Hier:

- **Einspielen** (`replace`, aus dem Ingest): je Lauf des Quellsystems ersetzt; der Verlauf der
  Zaehler der Topologieaenderungen (`tc_history`) wird je Switch und Instanz mitgenommen - daraus
  die Aenderungen der letzten 24 h (soweit die Laeufe zurueckreichen).
- **Baeume** (`Model`): alle Instanzen derselben VLAN- bzw. MST-Nummer mit derselben Root-MAC
  bilden einen Baum (einen L2-Bereich dieser VLAN). Root = die Switches, die sich selbst als Root
  melden, sonst die Switches mit dieser Bridge-MAC (vPC peer-switch: beide), sonst nur die MAC.
- **Soll-Root** (`StpTarget`, Laufzeitdaten je Mandant): je VLAN-Bereich und/oder Standort ein
  Root (und ein Ersatz); es gilt der genaueste Eintrag (Standort und VLANs vor nur Standort vor
  nur VLANs vor allen). Die Plattform funktioniert ohne Soll - dann meldet die Pruefung nur, wer
  Root ist.
- **Ports und Nachbarn**: blockierte Ports mit der Gegenstelle aus der Verkabelung (CDP/LLDP,
  `store/cabling`), bei Port-Channels ueber die Mitglieder - Grundlage fuer „unerwartet
  blockiert“ (`store/stp_findings`).
- **Datenstand** (ADR 0049): Teile `inventory.stp`/`stp_devices` wirken nicht auf Doku und Wege
  (`data_state.SCOPES`); die blockierten VLANs am Port (0.65.0) bleiben in den Interfaces.

Rein lesend gegenueber den Geraeten; kein Schreibweg.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import Site, SourceSystem, StpDevice, StpInstance, StpSetting, StpTarget
from app.store import cabling
from app.store import sites as site_store

TC_PER_DAY = 10  # Vorgabe: ab so vielen Topologieaenderungen in 24 h ein Befund (je Mandant)
TC_PER_DAY_MAX = 100_000
HISTORY_DAYS = 8  # Verlauf der Zaehler, der bleibt
HISTORY_MAX = 400  # Eintraege je Instanz (haeufige Laeufe)
TC_MIN_HOURS = 1.0  # kuerzester Abstand zweier Laeufe fuer eine Aussage
STATUSES = ("read", "off", "unsupported", "unreadable")
MODES = ("pvst", "rapid-pvst", "mst")
BLOCKED_STATES = frozenset({"blocking", "broken", "discarding"})
_NOT_BRIDGE = ("firewall", "esxi", "router", "controller", "phone", "host")
_FIREWALL = re.compile(r"fortigate|fortios|palo ?alto|pan-os|\basa\b|firepower|check ?point|"
                       r"sophos|\bsrx\d*", re.I)  # fmt: skip
_VMWARE = re.compile(r"vmware|\besx", re.I)


def _cut(value, size: int) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text[:size] or None


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)  # SQLite liefert ohne Zone
    return value


# -- Einspielen ---------------------------------------------------------------------------------


def _history(old: list | None, count: int | None, now: datetime) -> list | None:
    """Verlauf der Zaehler: alte Eintraege (ab HISTORY_DAYS weg), neuer Stand angehaengt; ist
    der Zaehler kleiner als zuletzt (Neustart des Switches bzw. der Instanz), beginnt er neu."""
    if count is None:
        return None
    keep: list[list] = []
    border = now - timedelta(days=HISTORY_DAYS)
    for entry in old or []:
        try:
            at, value = datetime.fromisoformat(entry[0]), int(entry[1])
        except (TypeError, ValueError, IndexError):
            continue
        if _aware(at) >= border:
            keep.append([_aware(at).isoformat(), value])
    if keep and count < keep[-1][1]:
        keep = []
    keep.append([now.isoformat(), count])
    return keep[-HISTORY_MAX:]


def _errdisabled(items: list[dict]) -> list[dict] | None:
    out = []
    for e in items or []:
        port = _cut(e.get("port"), 200) if isinstance(e, dict) else None
        if port:
            out.append({"port": port, "reason": _cut(e.get("reason"), 200),
                        "vlans": _cut(e.get("vlans"), 500)})  # fmt: skip
    return out or None


def replace(session: Session, run, inv) -> None:
    """Spanning Tree des Quellsystems ersetzen (Ingest). Ohne `stp_devices` (aeltere Agenten,
    andere Quellen) bleibt der gespeicherte Stand."""
    sid, tid = run.source_system_id, run.tenant_id
    if sid is None or tid is None or inv.stp_devices is None:
        return
    now = _now()
    previous = {(r.device, r.instance): r.tc_history for r in session.scalars(
        select(StpInstance).where(StpInstance.source_system_id == sid))}  # fmt: skip
    for model in (StpInstance, StpDevice):
        session.execute(delete(model).where(model.source_system_id == sid)
                        .execution_options(synchronize_session=False))  # fmt: skip
    base = {"tenant_id": tid, "source_system_id": sid, "read_at": now}
    rows: list = []
    seen: set[str] = set()
    for d in inv.stp_devices:
        name = _cut(d.device, 200)
        if not name or name in seen:
            continue
        seen.add(name)
        status = d.status if d.status in STATUSES else "unreadable"
        rows.append(StpDevice(**base, device=name, status=status, mode=_cut(d.mode, 32),
                              region=_cut(d.region, 200), revision=d.revision,
                              errdisabled=_errdisabled(d.errdisabled), note=d.note))  # fmt: skip
    done: set[tuple[str, str]] = set()
    for i in inv.stp or []:
        name, inst = _cut(i.device, 200), _cut(i.instance, 32)
        if not name or not inst or (name, inst) in done:
            continue
        done.add((name, inst))
        tc_at = now - timedelta(seconds=i.tc_ago) if i.tc_ago is not None else None
        rows.append(StpInstance(
            **base, device=name, instance=inst, vlan=i.vlan, mst=i.mst, vlans=i.vlans,
            protocol=_cut(i.protocol, 16), root_priority=i.root_priority,
            root_mac=_cut(i.root_mac, 17), root_cost=i.root_cost, root_port=_cut(i.root_port, 200),
            is_root=bool(i.is_root), bridge_priority=i.bridge_priority,
            bridge_mac=_cut(i.bridge_mac, 17), tc_count=i.tc_count, tc_last_at=tc_at,
            tc_from=_cut(i.tc_from, 200),
            tc_history=_history(previous.get((name, inst)), i.tc_count, now),
            ports=[p.model_dump() for p in i.ports] or None,
        ))  # fmt: skip
    session.add_all(rows)
    session.flush()


# -- Einstellungen und Soll -------------------------------------------------------------------


def tc_per_day(session: Session, tenant_id: int) -> int:
    row = session.get(StpSetting, tenant_id)
    return row.tc_per_day if row is not None else TC_PER_DAY


def vlan_text(text: str | None) -> tuple[str | None, set[int] | None]:
    """VLAN-Bereich eines Soll-Eintrags: `10, 20-30` -> (`10,20-30`, Menge); leer = alle (None).
    Ungueltig -> ValueError."""
    raw = (text or "").replace(" ", "").strip(",")
    if not raw:
        return None, None
    found = cabling.vlan_set(raw)
    if not found or any(v < 1 or v > 4094 for v in found):
        raise ValueError(L("VLANs: Nummern 1–4094, z. B. 10,20-30",
                           "VLANs: numbers 1–4094, e.g. 10,20-30"))  # fmt: skip
    return ranges(found), found


def ranges(vlans: set[int]) -> str:
    ordered, out, i = sorted(vlans), [], 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        out.append(str(ordered[i]) if i == j else f"{ordered[i]}-{ordered[j]}")
        i = j + 1
    return ",".join(out)


# -- Modell -------------------------------------------------------------------------------------


@dataclass
class Tree:
    """Ein Spanning Tree: gleiche VLAN- bzw. MST-Nummer, gleiche Root-MAC."""

    kind: str  # vlan | mst
    number: int
    root_mac: str | None
    members: list[StpInstance] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.kind}|{self.number}|{self.root_mac or '-'}"

    @property
    def label(self) -> str:
        return f"VLAN {self.number}" if self.kind == "vlan" else f"MST {self.number}"

    @property
    def vlans(self) -> set[int]:
        if self.kind == "vlan":
            return {self.number}
        out: set[int] = set()
        for m in self.members:
            out |= cabling.vlan_set(m.vlans) or set()
        return out

    @property
    def root_priority(self) -> int | None:
        return next((m.root_priority for m in self.members if m.root_priority is not None), None)


@dataclass
class Neighbor:
    name: str
    port: str | None
    kind: str | None  # switch | firewall | esxi | router | phone | host | None (unbekannt)
    read: bool = False

    @property
    def bridge(self) -> bool | None:
        if self.kind is None:
            return None
        return self.kind == "switch"

    def out(self) -> dict:
        return {"device": self.name, "port": self.port, "kind": self.kind, "read": self.read}


class Model:
    """Spanning Tree eines Mandanten: Switches, Instanzen, Baeume, Soll und Nachbarn."""

    def __init__(self, session: Session, tenant_id: int):
        self.session, self.tenant_id = session, tenant_id
        self.devices = list(
            session.scalars(
                select(StpDevice).where(StpDevice.tenant_id == tenant_id).order_by(StpDevice.device)
            )
        )
        self.instances = list(session.scalars(
            select(StpInstance).where(StpInstance.tenant_id == tenant_id)
            .order_by(StpInstance.device, StpInstance.mst, StpInstance.vlan)))  # fmt: skip
        self.targets = list(
            session.scalars(
                select(StpTarget).where(StpTarget.tenant_id == tenant_id).order_by(StpTarget.id)
            )
        )
        self.sources = dict(session.execute(
            select(SourceSystem.id, SourceSystem.name).where(SourceSystem.tenant_id == tenant_id)
        ).all())  # fmt: skip
        self.site_index = site_store.SiteIndex(session, tenant_id)
        self.site_of: dict[int, int | None] = {
            sid: (s.id if s is not None else None)
            for sid, s in site_store.source_sites(session, tenant_id).items()
        }
        self.by_mac: dict[str, set[str]] = defaultdict(set)
        self.by_device: dict[tuple[int, str], list[StpInstance]] = defaultdict(list)
        for i in self.instances:
            if i.bridge_mac:
                self.by_mac[i.bridge_mac].add(i.device)
            self.by_device[(i.source_system_id, i.device)].append(i)
        trees: dict[str, Tree] = {}
        for i in self.instances:
            kind, number = ("mst", i.mst) if i.mst is not None else ("vlan", i.vlan)
            if number is None:
                continue
            tree = Tree(kind, number, i.root_mac)
            trees.setdefault(tree.key, tree).members.append(i)
        self.trees = dict(sorted(trees.items(), key=lambda kv: (kv[1].kind != "vlan",
                                                                kv[1].number, kv[0])))  # fmt: skip
        self.device_rows = {d.device: d for d in self.devices}
        self._cab: cabling.Cabling | None = None
        self._ends: dict | None = None

    # -- Standorte und Root ---------------------------------------------------------------------

    def site_ids(self, tree: Tree) -> set[int | None]:
        return {self.site_of.get(m.source_system_id) for m in tree.members}

    def site_label(self, site_id: int | None) -> str | None:
        site = self.site_index.by_id.get(site_id) if site_id is not None else None
        return f"{site.code} · {site.name}" if site is not None else None

    def root_names(self, tree: Tree) -> list[str]:
        own = sorted({m.device for m in tree.members if m.is_root})
        if own:
            return own
        return sorted(self.by_mac.get(tree.root_mac or "", ()))

    def root_label(self, tree: Tree) -> str:
        names = self.root_names(tree)
        if names:
            return " / ".join(names)
        return f"MAC {tree.root_mac}" if tree.root_mac else L("unbekannt", "unknown")

    # -- Soll -----------------------------------------------------------------------------------

    def target_vlans(self, t: StpTarget) -> set[int] | None:
        return cabling.vlan_set(t.vlans) if t.vlans else None

    def target_for(self, tree: Tree) -> StpTarget | None:
        """Genauester Soll-Eintrag fuer den Baum: Standort und VLANs vor nur Standort vor nur
        VLANs vor allen; bei Gleichstand der kleinere VLAN-Bereich, dann der aeltere Eintrag.
        Ein Eintrag fuer einen geloeschten Standort gilt nicht (in der Ansicht markiert)."""
        vlans, sites = tree.vlans, self.site_ids(tree)
        best: tuple | None = None
        for t in self.targets:
            if t.site_id is not None and (t.site_id not in self.site_index.by_id
                                          or t.site_id not in sites):  # fmt: skip
                continue
            tv = self.target_vlans(t)
            if tv is not None and not (tv & vlans):
                continue
            score = (2 if t.site_id is not None else 0) + (1 if tv is not None else 0)
            rank = (-score, len(tv) if tv is not None else 5000, t.id)
            if best is None or rank < best[0]:
                best = (rank, t)
        return best[1] if best else None

    def target_state(self, tree: Tree, t: StpTarget | None) -> str:
        """ok | backup (Root auf dem Ersatz) | mismatch | none (ohne Soll)."""
        if t is None:
            return "none"
        names = {cabling.device_key(n) for n in self.root_names(tree)}
        if cabling.device_key(t.root) in names:
            return "ok"
        if t.backup and cabling.device_key(t.backup) in names:
            return "backup"
        return "mismatch"

    # -- Nachbarn -------------------------------------------------------------------------------

    @property
    def cab(self) -> cabling.Cabling:
        if self._cab is None:
            self._cab = cabling.build(self.session, self.tenant_id)
        return self._cab

    def _index(self) -> dict:
        if self._ends is not None:
            return self._ends
        from app.models import InvNeighbor

        cab = self.cab
        ends: dict[tuple[str, str], list[cabling.End]] = defaultdict(list)
        for link in cab.links.values():
            for end, other in ((link.a, link.b), (link.b, link.a)):
                if end.port:
                    ends[(end.device.key, cabling.port_key(end.port))].append(other)
        members: dict[tuple[str, str], list[str]] = defaultdict(list)
        for i in cab.ports:
            if i.lag:
                members[(cabling.device_key(i.device), cabling.port_key(i.lag))].append(i.name)
        hints: dict[tuple[str, str, str], str] = {}
        for n in self.session.scalars(select(InvNeighbor)
                                      .where(InvNeighbor.tenant_id == self.tenant_id)):  # fmt: skip
            key = (cabling.device_key(n.device), cabling.port_key(n.port),
                   cabling.device_key(n.remote_name))  # fmt: skip
            hints[key] = " ".join(x for x in (n.remote_platform, n.remote_description) if x)
        self._ends = {"ends": ends, "members": members, "hints": hints}
        return self._ends

    def neighbors(self, device: str, port: str) -> list[Neighbor]:
        """Gegenstellen eines Ports laut Verkabelung - bei einem Port-Channel die seiner
        Mitglieder; je Geraet einmal."""
        idx = self._index()
        dkey = cabling.device_key(device)
        ports = [port, *idx["members"].get((dkey, cabling.port_key(port)), [])]
        out: dict[str, Neighbor] = {}
        for p in ports:
            pkey = cabling.port_key(p)
            for other in idx["ends"].get((dkey, pkey), []):
                if other.device.key in out:
                    continue
                hint = idx["hints"].get((dkey, pkey, other.device.key), "")
                out[other.device.key] = Neighbor(other.device.name, other.port,
                                                 self._kind(other.device, hint),
                                                 other.device.read)  # fmt: skip
        return list(out.values())

    @staticmethod
    def _kind(dev: cabling.Device, hint: str) -> str | None:
        """Art der Gegenstelle fuer den Spanning Tree: Switch (Bridge) oder nicht - aus dem
        gelesenen Geraet, sonst aus Plattform/Beschreibung und Faehigkeiten aus CDP/LLDP."""
        if dev.kind in ("switch", "leaf", "spine"):
            return "switch"
        if dev.kind in ("firewall", "esxi", "router", "controller"):
            return dev.kind
        text = f"{dev.remote_platform or ''} {hint}"
        if _VMWARE.search(text):
            return "esxi"
        if _FIREWALL.search(text):
            return "firewall"
        caps = {c.lower() for c in dev.caps}
        if caps & {"phone", "telephone", "t"}:
            return "phone"
        if caps & {"switch", "bridge", "b", "trans-bridge", "source-route-bridge"}:
            return "switch"
        if caps & {"router", "r"}:
            return "router"
        if caps & {"host", "station", "s"}:
            return "host"
        return None

    # -- Ports --------------------------------------------------------------------------------

    def unexpected(self, inst: StpInstance, port: dict) -> list[str]:
        """Gruende, warum ein blockierter Port nicht zum normalen Betrieb passt (leer = erwartet
        bzw. nicht beurteilbar). Blockieren ist der Zweck von Spanning Tree - erwartet ist es auf
        einem redundanten Weg zwischen zwei Switches. Unerwartet:
        - `guard`: von einer Schutzfunktion gesperrt (Root/Loop Guard, Bridge Assurance,
          PVST-/Typ-Inkonsistenz - `*_Inc`);
        - `backup`: Backup-Rolle - der Switch hoert seine eigenen BPDUs (Hub bzw. Schleife);
        - `peer_link`: vPC-Peer-Link blockiert;
        - `edge`: Edge-Port (PortFast) blockiert - dort sollte kein Switch haengen;
        - `no_bridge`: Gegenstelle laut Verkabelung kein Switch (Firewall, ESXi-Host, Router,
          Telefon, Server) - BPDUs kommen dann ueber ein Geraet, das nicht bridgen soll."""
        state, role = port.get("state"), port.get("role")
        if state not in BLOCKED_STATES and not port.get("inconsistent"):
            return []
        reasons: list[str] = []
        ptype = (port.get("type") or "").lower()
        if port.get("inconsistent"):
            reasons.append("guard")
        if role == "backup":
            reasons.append("backup")
        if "peer-link" in ptype:
            reasons.append("peer_link")
        if re.search(r"\bedge\b", ptype):
            reasons.append("edge")
        nbs = self.neighbors(inst.device, port.get("port") or "")
        if nbs and any(n.bridge is False for n in nbs):
            reasons.append("no_bridge")
        return reasons

    def port_rows(self, inst: StpInstance) -> list[dict]:
        out = []
        for p in inst.ports or []:
            row = dict(p)
            blocked = p.get("state") in BLOCKED_STATES
            nbs = self.neighbors(inst.device, p.get("port") or "") if blocked else []
            row["blocked"] = blocked
            row["neighbors"] = [n.out() for n in nbs]
            row["unexpected"] = self.unexpected(inst, p) if blocked or p.get("inconsistent") else []
            out.append(row)
        return out

    # -- Topologieaenderungen -----------------------------------------------------------------

    @staticmethod
    def tc_window(inst: StpInstance) -> dict | None:
        """Aenderungen in den letzten 24 h aus dem Verlauf der Zaehler: Basis ist der juengste
        Stand, der mindestens 24 h zurueckliegt, sonst der aelteste. Liegen die Laeufe weiter
        auseinander als 24 h, wird auf 24 h umgerechnet; kuerzer wird nicht hochgerechnet (die
        Aussage waechst mit den Laeufen). Unter `TC_MIN_HOURS` bzw. ohne zweiten Stand: None."""
        points = []
        for entry in inst.tc_history or []:
            try:
                points.append((_aware(datetime.fromisoformat(entry[0])), int(entry[1])))
            except (TypeError, ValueError, IndexError):
                continue
        if len(points) < 2:
            return None
        points.sort()
        last_at, last = points[-1]
        base = points[0]
        for at, value in points:
            if at <= last_at - timedelta(hours=24):
                base = (at, value)
        hours = (last_at - base[0]).total_seconds() / 3600
        delta = last - base[1]
        if hours < TC_MIN_HOURS or delta < 0:
            return None
        per_day = delta * 24 / hours if hours > 24 else float(delta)
        return {"delta": delta, "hours": round(hours, 1), "per_day": round(per_day, 1)}

    # -- Ausgabe ------------------------------------------------------------------------------

    def tree_brief(self, tree: Tree) -> dict:
        t = self.target_for(tree)
        blocked = unexpected = 0
        for m in tree.members:
            for p in m.ports or []:
                if p.get("state") in BLOCKED_STATES:
                    blocked += 1
                    if self.unexpected(m, p):
                        unexpected += 1
        names = self.root_names(tree)
        return {
            "key": tree.key, "kind": tree.kind, "number": tree.number, "label": tree.label,
            "vlans": ranges(tree.vlans) if tree.kind == "mst" else str(tree.number),
            "root": {"names": names, "mac": tree.root_mac, "priority": tree.root_priority,
                     "label": self.root_label(tree), "read": bool(names)},
            "target": self.target_out(t, self.target_state(tree, t)) if t else None,
            "target_state": self.target_state(tree, t),
            "sites": sorted({self.site_label(s) or "–" for s in self.site_ids(tree)}),
            "site_ids": sorted(s for s in self.site_ids(tree) if s is not None),
            "switches": len({m.device for m in tree.members}),
            "blocked": blocked, "unexpected": unexpected,
            "protocols": sorted({m.protocol for m in tree.members if m.protocol}),
        }  # fmt: skip

    def member_out(self, m: StpInstance) -> dict:
        window = self.tc_window(m)
        return {
            "device": m.device, "source_system_id": m.source_system_id,
            "source": self.sources.get(m.source_system_id), "instance": m.instance,
            "site": self.site_label(self.site_of.get(m.source_system_id)),
            "protocol": m.protocol, "is_root": m.is_root, "bridge_priority": m.bridge_priority,
            "bridge_mac": m.bridge_mac, "root_cost": m.root_cost, "root_port": m.root_port,
            "tc_count": m.tc_count, "tc_last_at": _iso(m.tc_last_at), "tc_from": m.tc_from,
            "tc_window": window, "read_at": _iso(m.read_at), "ports": self.port_rows(m),
        }  # fmt: skip

    def tree_out(self, tree: Tree) -> dict:
        """Baum mit Switches, Ports und Kanten fuer die Darstellung: je Switch der Root-Port zur
        Gegenstelle (Weg zur Root) und blockierte Ports (Reserve bzw. unerwartet)."""
        members = [self.member_out(m) for m in tree.members]
        in_tree = {cabling.device_key(m["device"]): m["device"] for m in members}
        edges: list[dict] = []
        for m in members:
            for p in m["ports"]:
                kind = ("root" if p.get("role") == "root" else
                        "blocked" if p["blocked"] else None)  # fmt: skip
                if kind is None:
                    continue
                nbs = p["neighbors"] or [n.out() for n in self.neighbors(m["device"], p["port"])]
                for n in nbs:
                    edges.append({"from": m["device"], "port": p["port"], "kind": kind,
                                  "to": in_tree.get(cabling.device_key(n["device"]), n["device"]),
                                  "to_port": n["port"],
                                  "in_tree": cabling.device_key(n["device"]) in in_tree,
                                  "unexpected": p["unexpected"]})  # fmt: skip
                if not nbs and kind == "root":
                    edges.append({"from": m["device"], "port": p["port"], "kind": kind,
                                  "to": None, "to_port": None, "in_tree": False,
                                  "unexpected": []})  # fmt: skip
        return {**self.tree_brief(tree), "members": members, "edges": edges}

    def target_out(self, t: StpTarget, state: str | None = None) -> dict:
        known = t.site_id is None or t.site_id in self.site_index.by_id
        return {"id": t.id, "site_id": t.site_id, "site": self.site_label(t.site_id),
                "site_missing": not known, "vlans": t.vlans, "root": t.root, "backup": t.backup,
                "note": t.note, "changed_by": t.changed_by, "changed_at": _iso(t.changed_at),
                "state": state}  # fmt: skip

    def device_out(self, d: StpDevice) -> dict:
        own = self.by_device.get((d.source_system_id, d.device), [])
        blocked = sum(1 for i in own for p in i.ports or [] if p.get("state") in BLOCKED_STATES)
        windows = [(i, self.tc_window(i)) for i in own]
        busiest = max((w for w in windows if w[1] is not None), default=None,
                      key=lambda w: w[1]["per_day"])  # fmt: skip
        return {
            "device": d.device, "source_system_id": d.source_system_id,
            "source": self.sources.get(d.source_system_id),
            "site": self.site_label(self.site_of.get(d.source_system_id)),
            "status": d.status, "mode": d.mode, "region": d.region, "revision": d.revision,
            "note": d.note, "read_at": _iso(d.read_at), "instances": len(own),
            "root_of": sorted({i.instance for i in own if i.is_root}),
            "blocked": blocked, "errdisabled": [
                {**e, "bpdu_guard": bpdu_guard(e.get("reason"))} for e in d.errdisabled or []],
            "tc_count": sum(i.tc_count or 0 for i in own),
            "tc_busiest": {"instance": busiest[0].instance, **busiest[1]} if busiest else None,
        }  # fmt: skip


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value is not None else None


def bpdu_guard(reason: str | None) -> bool:
    """err-disabled durch BPDU-Guard (`bpduguard`, `BPDUGuard errDisable`)."""
    return "bpdu" in (reason or "").lower()


def summary(model: Model, tc_limit: int) -> dict:
    """Kennzahlen: Switches je Status (nicht unterstuetzt bzw. nicht lesbar gezaehlt), Modi,
    Baeume, blockierte und unerwartet blockierte Ports, err-disabled, Soll-Eintraege."""
    status = {s: 0 for s in STATUSES}
    modes: dict[str, int] = defaultdict(int)
    for d in model.devices:
        status[d.status] = status.get(d.status, 0) + 1
        if d.status == "read" and d.mode:
            modes[d.mode] += 1
    briefs = [model.tree_brief(t) for t in model.trees.values()]
    err = [e for d in model.devices for e in d.errdisabled or []]
    return {
        "switches": len(model.devices), "status": status, "modes": dict(modes),
        "trees": len(briefs), "instances": len(model.instances),
        "blocked": sum(b["blocked"] for b in briefs),
        "unexpected": sum(b["unexpected"] for b in briefs),
        "errdisabled": len(err), "bpdu_guard": sum(1 for e in err if bpdu_guard(e.get("reason"))),
        "targets": len(model.targets),
        "target_states": {s: sum(1 for b in briefs if b["target_state"] == s)
                          for s in ("ok", "backup", "mismatch", "none")},
        "tc_per_day": tc_limit, "tc_default": TC_PER_DAY,
        "unsupported": sorted(d.device for d in model.devices if d.status == "unsupported"),
        "unreadable": sorted(d.device for d in model.devices if d.status == "unreadable"),
    }  # fmt: skip


def site_choices(session: Session, tenant_id: int) -> list[dict]:
    rows = session.scalars(select(Site).where(Site.tenant_id == tenant_id).order_by(Site.code))
    return [{"id": s.id, "code": s.code, "name": s.name, "kind": s.kind} for s in rows]
