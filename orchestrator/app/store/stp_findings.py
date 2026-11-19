"""Befunde des Spanning Tree (1.19.0): Pruefungen `app/checks/analyse/stp_*.py` (ADR 0039) und
ihre Texte (`finding_text.explain`).

- **Root** (`stp_root_mismatch`, `stp_root_info`): je Soll-Eintrag und tatsaechlicher Root ein
  Befund mit den VLANs bzw. MST-Instanzen - Warnung, wenn der Root nicht am Soll ist, Hinweis,
  wenn der Ersatz-Root Root ist; ohne Soll nur „Root ist <Geraet>“ als Info je Root.
- **Unerwartet blockiert** (`stp_port_blocked`): je Port mit den VLANs - Gruende siehe
  `Model.unexpected` (Schutzfunktion, Backup-Rolle, vPC-Peer-Link, Edge-Port, Gegenstelle kein
  Switch). Blockieren auf redundanten Wegen zwischen Switches ist erwartet und kein Befund;
  ohne bekannte Gegenstelle beurteilt die Pruefung nicht (Ansicht: „Nachbar unbekannt“).
- **BPDU-Guard** (`stp_bpdu_guard`): je err-disabled Port mit Grund BPDU-Guard; andere Gruende
  zeigt nur die Ansicht.
- **Gemischte Modi** (`stp_mode_mixed`): je L2-Bereich (Switches, die laut Verkabelung
  verbunden sind) mit mehr als einem Modus (PVST, Rapid-PVST, MST, aus) bzw. mit Ports an einer
  Grenze zu einem anderen Protokoll (`Bound(PVST)`, `Bound(STP)`, `Peer(STP)` …).
- **Viele Topologieaenderungen** (`stp_topology_changes`): je Switch die Instanzen mit mindestens
  der Schwelle je 24 h (Vorgabe `stp.TC_PER_DAY`, je Mandant einstellbar) aus dem Verlauf der
  Zaehler (`Model.tc_window`).

Gespeichert wird nur Struktur (Geraete, VLANs, Gruende); die Saetze entstehen beim Abruf in der
Sprache der Anfrage. Nur lesend.
"""

import re
from collections import defaultdict

from sqlalchemy.orm import Session

from app.i18n import L, german_only
from app.models import FindingType, Severity, SubjectKind
from app.store import cabling, stp
from app.store.reconcile import Issue

KEYS = ("stp_root", "stp_blockiert", "stp_bpdu_guard", "stp_modus", "stp_wechsel")
_BOUNDARY = re.compile(r"\b(Bound\([^)]*\)|Peer\([^)]*\))", re.I)
_LIST = 40  # Eintraege je Befund im Detail


def model(session: Session, tenant_id: int, cx=None) -> stp.Model:
    """Ein Modell je Durchgang (alle Pruefungen des Spanning Tree teilen es)."""
    if cx is None:
        return stp.Model(session, tenant_id)
    found = cx.extra.get("stp")
    if found is None or found.tenant_id != tenant_id:
        found = cx.extra["stp"] = stp.Model(session, tenant_id)
    return found


def _ranges(numbers: set[int]) -> str:
    return stp.ranges(numbers) if numbers else ""


# -- Root ---------------------------------------------------------------------------------------


def root_issues(session: Session, tenant_id: int, cx=None) -> list[Issue]:
    m = model(session, tenant_id, cx)
    groups: dict[tuple, dict] = {}
    for tree in m.trees.values():
        t = m.target_for(tree)
        state = m.target_state(tree, t)
        if state == "ok":
            continue
        label = m.root_label(tree)
        key = (state if state != "none" else "info", t.id if t else None, label)
        g = groups.setdefault(key, {"vlans": set(), "mst": set(), "sites": set(), "sources": set(),
                                    "target": t, "state": state, "root": label,
                                    "root_mac": tree.root_mac,
                                    "read": bool(m.root_names(tree))})  # fmt: skip
        (g["vlans"] if tree.kind == "vlan" else g["mst"]).add(tree.number)
        if tree.kind == "mst":
            g["vlans"] |= tree.vlans
        g["sites"] |= {m.site_label(s) for s in m.site_ids(tree) if s is not None}
        g["sources"] |= {x.source_system_id for x in tree.members}
    out: list[Issue] = []
    for (kind, tid, label), g in sorted(groups.items(), key=lambda kv: str(kv[0])):
        t = g["target"]
        detail = {"root": label, "root_mac": g["root_mac"], "root_read": g["read"],
                  "vlans": _ranges(g["vlans"]), "mst": _ranges(g["mst"]) or None,
                  "sites": sorted(g["sites"]), "state": g["state"]}  # fmt: skip
        src = next(iter(g["sources"])) if len(g["sources"]) == 1 else None
        if kind == "info":
            out.append(Issue(FindingType.STP_ROOT_INFO, SubjectKind.LINK, f"stp-root|{label}", src,
                             Severity.INFO, detail=detail))  # fmt: skip
            continue
        detail |= {"target_id": t.id, "target_root": t.root, "target_backup": t.backup,
                   "target_vlans": t.vlans, "target_site": m.site_label(t.site_id)}  # fmt: skip
        severity = Severity.INFO if kind == "backup" else Severity.WARNING
        out.append(Issue(FindingType.STP_ROOT_MISMATCH, SubjectKind.LINK,
                         f"stp-root|{tid}|{label}", src, severity, detail=detail))  # fmt: skip
    return out


# -- Ports --------------------------------------------------------------------------------------


def blocked_issues(session: Session, tenant_id: int, cx=None) -> list[Issue]:
    m = model(session, tenant_id, cx)
    ports: dict[tuple, dict] = {}
    for inst in m.instances:
        for p in inst.ports or []:
            reasons = m.unexpected(inst, p) if (p.get("state") in stp.BLOCKED_STATES
                                                or p.get("inconsistent")) else []  # fmt: skip
            if not reasons:
                continue
            key = (inst.source_system_id, inst.device, p.get("port"))
            g = ports.setdefault(key, {"instances": [], "reasons": set(), "neighbors": {},
                                       "inconsistent": set(), "states": set()})  # fmt: skip
            g["instances"].append(inst.instance)
            g["reasons"] |= set(reasons)
            g["states"].add(p.get("state"))
            if p.get("inconsistent"):
                g["inconsistent"].add(p["inconsistent"])
            for n in m.neighbors(inst.device, p.get("port") or ""):
                g["neighbors"][n.name] = n.kind
    out: list[Issue] = []
    for (sid, device, port), g in sorted(ports.items(), key=lambda kv: (kv[0][1], kv[0][2] or "")):
        severity = Severity.ERROR if g["reasons"] & {"peer_link", "backup"} else Severity.WARNING
        detail = {
            "device": device,
            "port": port,
            "instances": _instances(g["instances"]),
            "reasons": sorted(g["reasons"]),
            "states": sorted(s for s in g["states"] if s),
            "inconsistent": sorted(g["inconsistent"]),
            "neighbors": [{"device": n, "kind": k} for n, k in sorted(g["neighbors"].items())],
        }
        out.append(Issue(FindingType.STP_PORT_BLOCKED, SubjectKind.LINK, f"{device}|{port}", sid,
                         severity, detail=detail))  # fmt: skip
    return out


def _instances(names: list[str]) -> str:
    """`VLAN0010`, `VLAN0030`, `MST1` -> `VLAN 10,30 · MST 1`."""
    vlans, msts, other = set(), set(), []
    for n in names:
        if n.upper().startswith("VLAN") and n[4:].isdigit():
            vlans.add(int(n[4:]))
        elif n.upper().startswith("MST") and n[3:].isdigit():
            msts.add(int(n[3:]))
        else:
            other.append(n)
    parts = ([f"VLAN {_ranges(vlans)}"] if vlans else []) + ([f"MST {_ranges(msts)}"] if msts
                                                            else [])  # fmt: skip
    return " · ".join(parts + other)


def bpdu_issues(session: Session, tenant_id: int, cx=None) -> list[Issue]:
    m = model(session, tenant_id, cx)
    out: list[Issue] = []
    for d in m.devices:
        for e in d.errdisabled or []:
            if not stp.bpdu_guard(e.get("reason")):
                continue
            nbs = m.neighbors(d.device, e["port"])
            detail = {"device": d.device, "port": e["port"], "reason": e.get("reason"),
                      "vlans": e.get("vlans"),
                      "neighbors": [{"device": n.name, "kind": n.kind} for n in nbs]}  # fmt: skip
            out.append(Issue(FindingType.STP_BPDU_GUARD, SubjectKind.LINK,
                             f"{d.device}|{e['port']}", d.source_system_id, Severity.WARNING,
                             detail=detail))  # fmt: skip
    return out


# -- Modi ---------------------------------------------------------------------------------------


def _areas(m: stp.Model) -> list[list[str]]:
    """L2-Bereiche: Switches mit Spanning Tree, die laut Verkabelung verbunden sind."""
    names = {cabling.device_key(d.device): d.device for d in m.devices
             if d.status in ("read", "off")}  # fmt: skip
    parent = {k: k for k in names}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for link in m.cab.links.values():
        a, b = link.a.device.key, link.b.device.key
        if a in names and b in names:
            parent[find(a)] = find(b)
    groups: dict[str, list[str]] = defaultdict(list)
    for k, name in names.items():
        groups[find(k)].append(name)
    return [sorted(g) for g in groups.values()]


def mode_issues(session: Session, tenant_id: int, cx=None) -> list[Issue]:
    m = model(session, tenant_id, cx)
    rows = m.device_rows
    boundary: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for inst in m.instances:
        for p in inst.ports or []:
            found = _BOUNDARY.search(p.get("type") or "")
            if found:
                boundary[inst.device][p.get("port") or "?"].add(found.group(1))
    out: list[Issue] = []
    for area in _areas(m):
        modes: dict[str, list[str]] = defaultdict(list)
        for name in area:
            d = rows[name]
            modes[d.mode if d.status == "read" and d.mode else "off"].append(name)
        edges = [
            {"device": dev, "port": port, "type": ", ".join(sorted(kinds))}
            for dev in area
            for port, kinds in sorted(boundary.get(dev, {}).items())
        ]
        if len(modes) < 2 and not edges:
            continue
        sources = {rows[n].source_system_id for n in area}
        detail = {"devices": area[:_LIST], "count": len(area),
                  "modes": {k: v[:_LIST] for k, v in sorted(modes.items())},
                  "boundary": edges[:_LIST]}  # fmt: skip
        out.append(Issue(FindingType.STP_MODE_MIXED, SubjectKind.LINK, f"stp-mode|{area[0]}",
                         next(iter(sources)) if len(sources) == 1 else None, Severity.WARNING,
                         detail=detail))  # fmt: skip
    return out


# -- Topologieaenderungen -------------------------------------------------------------------


def tc_issues(session: Session, tenant_id: int, cx=None) -> list[Issue]:
    m = model(session, tenant_id, cx)
    limit = stp.tc_per_day(session, tenant_id)
    per_device: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for inst in m.instances:
        window = m.tc_window(inst)
        if window is None or window["per_day"] < limit:
            continue
        per_device[(inst.source_system_id, inst.device)].append({
            "instance": inst.instance, **window, "count": inst.tc_count, "from": inst.tc_from,
            "last_at": stp._iso(inst.tc_last_at)})  # fmt: skip
    out: list[Issue] = []
    for (sid, device), items in sorted(per_device.items(), key=lambda kv: kv[0][1]):
        items.sort(key=lambda i: -i["per_day"])
        detail = {"device": device, "limit": limit, "instances": items[:_LIST],
                  "count": len(items), "max_per_day": items[0]["per_day"]}  # fmt: skip
        out.append(Issue(FindingType.STP_TOPOLOGY_CHANGES, SubjectKind.LINK, f"stp-tc|{device}",
                         sid, Severity.WARNING, detail=detail))  # fmt: skip
    return out


# -- Neu rechnen nach einer Aenderung am Soll bzw. an der Schwelle -----------------------------


@german_only  # speichert Befunde: immer deutsch (ADR 0028)
def refresh(session: Session, tenant_id: int) -> int:
    """Nur die Befunde des Spanning Tree neu (nach dem Speichern eines Soll-Eintrags bzw. der
    Schwelle) - ausgeschaltete Pruefungen verlieren ihre Befunde wie im Abgleich."""
    from app import checks
    from app.store import reconcile

    mine = [c for c in checks.all_checks() if c.key in KEYS]
    cx = checks.Context()
    issues: list[Issue] = []
    for check in mine:
        if checks.enabled(session, tenant_id, check):
            issues += checks.run(check, session, tenant_id, cx)
    types = frozenset(t for c in mine for t in c.types)
    reconcile._sync_findings(session, tenant_id, issues, types=types)
    return len(issues)


# -- Texte ----------------------------------------------------------------------------------------

_REASON = {
    "guard": (
        "von einer Schutzfunktion gesperrt (Root/Loop Guard, Bridge Assurance, " "Inkonsistenz)",
        "locked by a protection feature (root/loop guard, bridge assurance, inconsistency)",
    ),
    "backup": (
        "Backup-Rolle: der Switch hört seine eigenen BPDUs (Hub bzw. Schleife am Port)",
        "backup role: the switch hears its own BPDUs (hub or loop on the port)",
    ),
    "peer_link": ("vPC-Peer-Link blockiert", "vPC peer link blocked"),
    "edge": ("Edge-Port (PortFast) blockiert", "edge port (PortFast) blocked"),
    "no_bridge": ("Gegenstelle ist kein Switch", "peer is not a switch"),
}
_KIND = {
    "switch": ("Switch", "switch"), "firewall": ("Firewall", "firewall"),
    "esxi": ("ESXi-Host", "ESXi host"), "router": ("Router", "router"),
    "phone": ("Telefon", "phone"), "host": ("Server/Endgerät", "server/end device"),
    "controller": ("Controller", "controller"),
}  # fmt: skip
_MODE = {"pvst": "PVST+", "rapid-pvst": "Rapid-PVST+", "mst": "MST",
         "off": ("aus", "off")}  # fmt: skip


def _mode(key: str) -> str:
    value = _MODE.get(key, key)
    return L(*value) if isinstance(value, tuple) else value


def reason_text(key: str) -> str:
    pair = _REASON.get(key)
    return L(*pair) if pair else key


def kind_text(key: str | None) -> str:
    pair = _KIND.get(key or "")
    return L(*pair) if pair else L("unbekannt", "unknown")


def _where(d: dict) -> str:
    parts = []
    if d.get("mst"):
        parts.append(f"MST {d['mst']}")
    if d.get("vlans"):
        parts.append(f"VLAN {d['vlans']}")
    sites = d.get("sites") or []
    text = " · ".join(parts) or "?"
    return text + (L(f" (Standort {', '.join(sites)})", f" (site {', '.join(sites)})")
                   if sites else "")  # fmt: skip


def explain(ftype: str, subject: str, d: dict) -> tuple[str, str]:
    """(Befund, Hinweis) fuer die Befunde des Spanning Tree - in der Sprache der Anfrage."""
    if ftype == "stp_root_info":
        return (
            L(f"Root ist {d.get('root')} für {_where(d)} – kein Soll-Root festgelegt.",
              f"Root is {d.get('root')} for {_where(d)} – no target root defined."),
            L("Soll-Root unter Analyse → Spanning Tree → Soll-Root festlegen, dann meldet die "
              "Prüfung Abweichungen statt nur den Root.",
              "Define the target root under Analysis → Spanning Tree → Target root; the check "
              "then reports deviations instead of just the root."),
        )  # fmt: skip
    if ftype == "stp_root_mismatch":
        if d.get("state") == "backup":
            return (
                L(f"Root für {_where(d)} ist der Ersatz-Root {d.get('root')} (Soll "
                  f"{d.get('target_root')}).",
                  f"Root for {_where(d)} is the backup root {d.get('root')} (target "
                  f"{d.get('target_root')})."),
                L(f"Prüfen, ob {d.get('target_root')} ausgefallen ist bzw. seine Priorität "
                  "verloren hat.",
                  f"Check whether {d.get('target_root')} failed or lost its priority."),
            )  # fmt: skip
        unread = "" if d.get("root_read") else L(" (nicht gelesen)", " (not read)")
        return (
            L(f"Root für {_where(d)} ist {d.get('root')}{unread}, Soll ist "
              f"{d.get('target_root')}.",
              f"Root for {_where(d)} is {d.get('root')}{unread}, target is "
              f"{d.get('target_root')}."),
            L(f"Bridge-Priorität auf {d.get('target_root')} senken (z. B. `spanning-tree vlan "
              "<VLANs> root primary` bzw. Priorität 4096/8192) und auf den übrigen Switches nicht "
              "tiefer setzen – sonst laufen die Wege über einen unerwarteten Switch.",
              f"Lower the bridge priority on {d.get('target_root')} (e.g. `spanning-tree vlan "
              "<VLANs> root primary` or priority 4096/8192) and do not set it lower on the other "
              "switches – otherwise traffic takes paths via an unexpected switch."),
        )  # fmt: skip
    if ftype == "stp_port_blocked":
        reasons = ", ".join(reason_text(r) for r in d.get("reasons") or [])
        nbs = ", ".join(f"{n['device']} ({kind_text(n.get('kind'))})"
                        for n in d.get("neighbors") or [])  # fmt: skip
        inc = ", ".join(d.get("inconsistent") or [])
        extra = []
        if nbs:
            extra.append(L(f"Gegenstelle {nbs}", f"peer {nbs}"))
        if inc:
            extra.append(inc)
        tail = f" – {'; '.join(extra)}" if extra else ""
        return (
            L(f"{d.get('device')} {d.get('port')} blockiert in {d.get('instances')}: "
              f"{reasons}{tail}.",
              f"{d.get('device')} {d.get('port')} blocks in {d.get('instances')}: "
              f"{reasons}{tail}."),
            L("Blockieren ist nur auf redundanten Wegen zwischen Switches erwartet. Prüfen, warum "
              "BPDUs über diesen Port kommen (Firewall im Transparent-Modus, VM als Bridge, "
              "Schleife über einen Hub) bzw. welche Schutzfunktion greift (`show spanning-tree "
              "inconsistentports`).",
              "Blocking is only expected on redundant paths between switches. Check why BPDUs "
              "arrive on this port (firewall in transparent mode, VM bridging, loop via a hub) or "
              "which protection feature applies (`show spanning-tree inconsistentports`)."),
        )  # fmt: skip
    if ftype == "stp_bpdu_guard":
        nbs = ", ".join(n["device"] for n in d.get("neighbors") or [])
        at = L(f" (Gegenstelle {nbs})", f" (peer {nbs})") if nbs else ""
        return (
            L(f"{d.get('device')} {d.get('port')} ist durch BPDU-Guard gesperrt "
              f"(err-disabled{at}).",
              f"{d.get('device')} {d.get('port')} is locked by BPDU guard (err-disabled{at})."),
            L("An einem Edge-Port kam ein BPDU an – dort hängt ein Switch bzw. eine Bridge. Gerät "
              "entfernen bzw. Port als Uplink konfigurieren, danach den Port freigeben "
              "(`shutdown`/`no shutdown` bzw. errdisable recovery).",
              "A BPDU arrived on an edge port – a switch or bridge is connected there. Remove the "
              "device or configure the port as an uplink, then re-enable the port "
              "(`shutdown`/`no shutdown` or errdisable recovery)."),
        )  # fmt: skip
    if ftype == "stp_mode_mixed":
        modes = "; ".join(f"{_mode(k)}: {', '.join(v)}" for k, v in (d.get("modes") or {}).items())
        edges = ", ".join(f"{e['device']} {e['port']} {e['type']}" for e in d.get("boundary") or [])
        parts = [modes] + ([L(f"Grenzen {edges}", f"boundaries {edges}")] if edges else [])
        return (
            L(f"Gemischte Spanning-Tree-Modi im L2-Bereich mit {d.get('count')} Switches: "
              f"{' – '.join(parts)}.",
              f"Mixed Spanning Tree modes in the L2 area with {d.get('count')} switches: "
              f"{' – '.join(parts)}."),
            L("Einen Modus für den ganzen Bereich wählen (meist Rapid-PVST+ bzw. MST mit gleicher "
              "Region). An Grenzen zwischen Modi konvergiert der Baum langsam, MST simuliert "
              "PVST nur eingeschränkt.",
              "Choose one mode for the whole area (usually Rapid-PVST+ or MST with the same "
              "region). At boundaries between modes the tree converges slowly, MST only partly "
              "simulates PVST."),
        )  # fmt: skip
    if ftype == "stp_topology_changes":
        items = d.get("instances") or []
        top = ", ".join(
            f"{i['instance']} {i['per_day']}" + (L(f" (von {i['from']})", f" (from {i['from']})")
                                                 if i.get("from") else "")
            for i in items[:5])  # fmt: skip
        return (
            L(f"{d.get('device')}: viele Topologieänderungen je 24 h (Schwelle {d.get('limit')}): "
              f"{top}.",
              f"{d.get('device')}: many topology changes per 24 h (limit {d.get('limit')}): "
              f"{top}."),
            L("Dem Port der letzten Änderung folgen (`show spanning-tree detail | include "
              "occurr|from`) – meist ein flatternder Port ohne PortFast bzw. eine instabile "
              "Verbindung; Endgeräte-Ports als Edge (PortFast) konfigurieren.",
              "Follow the port of the last change (`show spanning-tree detail | include "
              "occurr|from`) – usually a flapping port without PortFast or an unstable link; "
              "configure end-device ports as edge (PortFast)."),
        )  # fmt: skip
    return subject, ""
