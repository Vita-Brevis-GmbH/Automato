"""Weg erklärt (0.109.0, Anwender: „weshalb dieser Pfad gewählt wird … dass ich es als Mensch
verstehen kann“): zu einem Ergebnis der Weg-Suche (`paths.trace`) Hop für Hop in Sätzen, warum
der Weg so geht.

- **Start:** wo die Quelle angeschlossen ist (Netz, Interface) bzw. warum der Weg dort beginnt.
- **Je Hop:** wie der Hop erreicht wurde, Eingang und Routing-Tabelle (VRF), Ziel-NAT bzw. Load
  Balancer, Policy-Routen (geht vor, nur möglich, keine passt), alle Routen, die das Ziel
  enthalten – gewählt bzw. verworfen mit Grund (kürzeres Präfix, höhere Distanz, nicht aktiv,
  nur BGP-Tabelle) –, dem der Next-Hop gehört; mit der Lesezeit des Geräts.
- **Ende:** warum der Weg dort endet, mit einem Hinweis, was zu prüfen ist (Gerät einlesen,
  Anbindung erfassen, Route fehlt, Schleife).
- **Erwartet über …** (`expect`): ein Gerät, über das der Verkehr gehen müsste – auf dem Weg?
  Sonst wo der Weg davon abbiegt (Gerät auf dem Weg, das direkt mit ihm verbunden ist), welche
  Route dort gewinnt bzw. fehlt, und wie das erwartete Gerät selbst Ziel und Quelle routet.

Nur lesend: nutzt dieselben gelesenen Routen, Interfaces und Policy-Routen wie die Weg-Suche
(`paths.Graph`) und ändert nichts am Weg.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.i18n import L
from app.models import CollectorRun, RunStatus, SourceSystem, TopoPolicyRoute, TopoRoute
from app.store import paths
from app.store.topology import endpoint, ip_hex, span, table_of

MAX_ROUTES = 8  # Routen je Hop in der Erklärung
OK, INFO, WARN, STOP = "ok", "info", "warn", "stop"


def _step(kind: str, status: str, text: str, hint: str | None = None,
          items: list[dict] | None = None) -> dict:  # fmt: skip
    out = {"kind": kind, "status": status, "text": text}
    if hint:
        out["hint"] = hint
    if items:
        out["items"] = items
    return out


def _ip(value: str | None):
    if not value:
        return None
    try:
        return endpoint(value)
    except ValueError:
        return None


class Explainer:
    """Erklärung zu einem Ergebnis der Weg-Suche; Graph und Lesezeiten einmal geladen."""

    def __init__(self, session: Session, tenant_id: int, graph: paths.Graph):
        self.session = session
        self.tenant_id = tenant_id
        self.g = graph
        self._read: dict[int, datetime] | None = None

    # -- Lesezeit der Geräte ------------------------------------------------------------

    def read_at(self, sid: int) -> datetime | None:
        if self._read is None:
            rows = self.session.execute(
                select(CollectorRun.source_system_id, func.max(CollectorRun.finished_at))
                .where(CollectorRun.tenant_id == self.tenant_id,
                       CollectorRun.status == RunStatus.COMPLETED)
                .group_by(CollectorRun.source_system_id)
            ).all()  # fmt: skip
            self._read = {sid: at for sid, at in rows if sid is not None and at is not None}
        at = self._read.get(sid)
        return at if at is None or at.tzinfo else at.replace(tzinfo=UTC)

    def _source(self, sid: int) -> dict:
        at = self.read_at(sid)
        days = get_settings().stale_days
        stale = at is not None and days > 0 and at < datetime.now(UTC) - timedelta(days=days)
        return {"read_at": at.isoformat() if at else None, "stale": stale or at is None}

    # -- Routen eines Knotens -------------------------------------------------------------

    def candidates(self, key: paths.Key, ip, table) -> tuple[list[TopoRoute], int]:
        """Alle gelesenen Routen des Knotens, die das Ziel (ganz) enthalten - RIB, BGP- und
        statische Tabelle, längstes Präfix zuerst; dazu die Zahl in anderen VRFs."""
        lo, hi = (ip_hex(v) for v in span(ip))
        rows = list(
            self.session.scalars(
                select(TopoRoute)
                .where(
                    TopoRoute.source_system_id == key[0],
                    TopoRoute.context == key[1],
                    TopoRoute.family == ip.version,
                    TopoRoute.lo <= lo,
                    TopoRoute.hi >= hi,
                )
                .order_by(TopoRoute.prefix_len.desc(), TopoRoute.distance, TopoRoute.id)
                .limit(400)
            )
        )
        mine = [r for r in rows if table_of(r) == table]
        return mine, len(rows) - len(mine)

    def route_items(self, key: paths.Key, ip, table, route: dict | None,
                    policy: dict | None) -> tuple[list[dict], int, int]:  # fmt: skip
        """Routen zum Ziel mit „gewählt“ bzw. dem Grund, warum nicht. -> (Zeilen, weitere,
        in anderen VRFs)."""
        rows, other_vrfs = self.candidates(key, ip, table)
        rib = [r for r in rows if r.table == "rib"]
        seen: set[tuple] = set()
        items: list[dict] = []
        for r in rows:
            hops = tuple(sorted((h.get("ip") or "", h.get("interface") or "")
                                for h in r.next_hops or []))  # fmt: skip
            sig = (r.prefix, r.protocol, r.distance, hops)
            if r.table != "rib" and any(
                (x.prefix, x.protocol, x.distance) == sig[:3] and x.best for x in rib
            ):
                continue  # dieselbe Route wie in der Routing-Tabelle
            if (r.table, *sig) in seen:
                continue
            seen.add((r.table, *sig))
            chosen, why = self._judge(r, route, policy)
            items.append(
                {
                    "prefix": r.prefix,
                    "protocol": r.protocol,
                    "distance": r.distance,
                    "metric": r.metric,
                    "table": r.table,
                    "next_hops": [
                        {
                            "ip": h.get("ip"),
                            "interface": h.get("interface"),
                            "owner": self.g.owner_label(h.get("ip")),
                        }
                        for h in r.next_hops or []
                    ],
                    "chosen": chosen,
                    "why": why,
                }  # fmt: skip
            )
        items.sort(key=lambda x: not x["chosen"])  # gewählte zuerst, sonst wie gelesen
        return items[:MAX_ROUTES], max(0, len(items) - MAX_ROUTES), other_vrfs

    def _judge(self, r: TopoRoute, route: dict | None, policy: dict | None) -> tuple[bool, str]:
        if r.table == "bgp":
            attrs = r.attrs or {}
            path = L(f", AS-Pfad {attrs['as_path']}", f", AS path {attrs['as_path']}") \
                if attrs.get("as_path") else ""  # fmt: skip
            return False, L(f"nur in der BGP-Tabelle, nicht bevorzugt{path}",
                            f"only in the BGP table, not preferred{path}")  # fmt: skip
        if r.table == "static" and not r.best:
            return False, L(
                "statische Route nicht aktiv (abgeschaltet bzw. Next-Hop nicht " "erreichbar)",
                "static route not active (disabled or next-hop not reachable)",
            )
        if r.table == "rib" and not r.best:
            return False, L(
                "in der Routing-Tabelle, aber nicht aktiv", "in the routing table, but not active"
            )
        if route is None:
            return False, L("nicht aktiv", "not active")
        same = r.prefix == route["prefix"]
        dist, best = r.distance or 0, route.get("distance") or 0
        if same and dist == best and r.table in ("rib", "static"):
            if policy is not None:
                return False, L(
                    "gewählte Route – die Policy-Route geht aber vor",
                    "chosen route – but the policy route takes precedence",
                )
            return True, L(
                "gewählt: längstes passendes Präfix", "chosen: longest matching prefix"
            ) + (L(", kleinste Distanz", ", lowest distance") if r.distance is not None else "")
        if r.prefix_len < route["prefix_len"]:
            return False, L(
                f"kürzeres Präfix /{r.prefix_len} – {route['prefix']} ist genauer (das längste "
                "passende Präfix gewinnt)",
                f"shorter prefix /{r.prefix_len} – {route['prefix']} is more specific (the "
                "longest matching prefix wins)",
            )
        if same and dist > best:
            return False, L(
                f"Distanz {dist} höher als {best} (die kleinere Distanz gewinnt)",
                f"distance {dist} higher than {best} (the lower distance wins)",
            )
        return False, L("nicht aktiv", "not active")

    # -- Weg -----------------------------------------------------------------------------

    def start_steps(self, result: dict, src) -> list[dict]:
        out = []
        for s in result.get("start") or []:
            key = paths.parse_key(s.get("key"))
            via = s.get("via") or ""
            if via == L("gewählt", "selected"):  # Startpunkt gewählt (Weg-Suche bzw. Regel-Gerät)
                via = L("als Startpunkt gewählt", "chosen as start point") + (
                    L(f" – die Quelle {src} ist dort nicht angeschlossen, Eingang offen",
                      f" – the source {src} is not connected there, incoming interface open")
                    if src is not None else "")  # fmt: skip
            text = L(f"Start bei {s['label']}: {via}", f"Start at {s['label']}: {via}")
            if src is not None and key is not None:
                conn = self._connected(key, src)
                if conn is not None:
                    prefix, iface = conn
                    where = L(f" an Interface {iface}", f" on interface {iface}") if iface else ""
                    text = L(
                        f"Start bei {s['label']}: die Quelle {src} liegt im angeschlossenen "
                        f"Netz {prefix}{where}",
                        f"Start at {s['label']}: the source {src} is in the connected network "
                        f"{prefix}{where}",
                    )
            out.append(_step("start", OK, text))
        if not out:
            out.append(_step("start", STOP, L("Kein Startpunkt gefunden", "No start point found"),
                             L("Kein gelesenes Gerät hat die Quelle angeschlossen – Startpunkt "
                               "wählen bzw. das Gerät mit diesem Netz einlesen.",
                               "No device read has the source connected – choose a start point "
                               "or collect the device with this network.")))  # fmt: skip
        for n in result.get("notes") or []:
            out.append(_step("note", INFO, n))
        return out

    def _connected(self, key: paths.Key, ip) -> tuple[str, str | None] | None:
        rows = sorted(self.g._routes(ip, key, connected=True), key=lambda r: -r.prefix_len)
        if not rows:
            return None
        r = rows[0]
        iface = next((h.get("interface") for h in r.next_hops or [] if h.get("interface")), None)
        return r.prefix, iface

    def hop(self, path: dict, i: int, dst) -> dict:
        hop = path["hops"][i]
        part = (hop.get("parts") or [hop])[0]
        key = paths.parse_key(part.get("key"))
        node = self.g.nodes.get(key) if key else None
        table = part.get("vrf", 0)
        cur = _ip(((part.get("nat") or {}).get("to") or [None])[0]) or _ip(part.get("dst")) or dst
        steps: list[dict] = []
        ingress = part.get("ingress") or {}
        port = ingress.get("interface")
        zone = ingress.get("zone")
        via = part.get("via") or ""
        if i > 0:
            prev = path["hops"][i - 1]
            prev = (prev.get("parts") or [prev])[0]
            nh = (prev.get("egress") or {}).get("next_hop")
            if nh and port:
                text = L(
                    f"Erreicht über {via}: {nh} ist die Adresse von Interface {port}",
                    f"Reached via {via}: {nh} is the address of interface {port}",
                )
            else:
                text = L(f"Erreicht: {via}", f"Reached: {via}")
            steps.append(_step("reached", OK, text))
        if hop.get("parts"):
            names = ", ".join(dict.fromkeys(p["label"] for p in hop["parts"]))
            steps.append(
                _step(
                    "group",
                    INFO,
                    L(
                        f"Verbund paralleler Firewalls: {names} – gleiche Entscheidung, hier "
                        f"erklärt an "
                        f"{part['label']}",
                        f"Parallel firewalls: {names} – same decision, explained here at "
                        f"{part['label']}",
                    ),
                )
            )
        if port or table:
            where = f"{port}" + (f" (Zone {zone})" if zone else "") if port else ""
            vrf = (
                L(f" – Routing-Tabelle der VRF {table}", f" – routing table of VRF {table}")
                if table
                else L(" – Standard-Routing-Tabelle", " – default routing table")
            )
            text = L(f"Eingang {where}{vrf}", f"Incoming {where}{vrf}") if port else vrf.strip(" –")
            steps.append(_step("table", INFO, text))
        if part.get("nat"):
            n = part["nat"]
            steps.append(
                _step(
                    "nat",
                    INFO,
                    L(
                        f"Ziel-NAT (VIP {n['name']}): {n.get('ext')} → {', '.join(n['to'])} "
                        f"– ab hier "
                        f"wird {n['to'][0]} geroutet",
                        f"Destination NAT (VIP {n['name']}): {n.get('ext')} → "
                        f"{', '.join(n['to'])} – "
                        f"from here {n['to'][0]} is routed",
                    ),
                )
            )
        if part.get("lb"):
            lb = part["lb"]
            to = ", ".join(lb.get("to") or []) or "–"
            steps.append(
                _step(
                    "lb",
                    INFO,
                    L(
                        f"Load Balancer {lb['name']}: weiter zum Server {to}",
                        f"Load balancer {lb['name']}: onward to server {to}",
                    ),
                )
            )
        if key is not None:
            steps += self._policy_steps(key, part)
        if part.get("backup"):
            b = part["backup"]
            steps.append(
                _step(
                    "backup",
                    WARN,
                    L(
                        f"Backup-Weg: hier über {b['prefix']} ({b['reason']}) statt der "
                        f"aktiven Route",
                        f"Backup path: here via {b['prefix']} ({b['reason']}) instead of the "
                        f"active route",
                    ),
                )
            )
        if key is not None and cur is not None and not part.get("leak"):
            steps.append(self._route_step(key, node, part, cur, table))
        if part.get("leak"):
            steps.append(_step("leak", OK, part["leak"].get("text") or ""))
        last = i == len(path["hops"]) - 1
        if not last:
            steps.append(self._next_step(part, path["hops"][i + 1]))
        sid = key[0] if key else part.get("source_system_id")
        return {"label": hop["label"], "key": part.get("key"), "source": part.get("source"),
                "source_system_id": sid, **(self._source(sid) if sid else {}),
                "steps": steps}  # fmt: skip

    def _policy_steps(self, key: paths.Key, part: dict) -> list[dict]:
        follow = part.get("policy")
        maybe = [p for p in part.get("policy_routes") or [] if not p.get("certain")]
        if follow:
            to = " · ".join(x for x in [follow.get("gateway"), *(follow.get("output_device") or [])]
                            if x)  # fmt: skip
            return [
                _step(
                    "policy",
                    OK,
                    L(
                        f"Policy-Route {_policy_name(follow)} passt sicher (Quelle, Ziel, "
                        f"Eingang) und geht "
                        f"vor dem Routing: weiter über {to}",
                        f"Policy route {_policy_name(follow)} matches for certain (source, "
                        f"destination, "
                        f"incoming) and takes precedence over routing: onward via {to}",
                    ),
                )
            ]
        out = []
        if maybe:
            names = ", ".join(_policy_name(p) for p in maybe[:3])
            out.append(_step("policy", WARN, L(
                f"Policy-Route {names} passt vielleicht (Quelle bzw. Ziel über Objekte, Eingang "
                "unbekannt) – nicht angewendet, das Routing entscheidet",
                f"Policy route {names} may match (source or destination via objects, incoming "
                "unknown) – not applied, the routing decides"),
                L("Prüfen, ob diese Policy-Route für den Verkehr gilt – sonst geht er anders.",
                  "Check whether this policy route applies to the traffic – otherwise it takes "
                  "another path.")))  # fmt: skip
        else:
            total = (
                self.session.scalar(
                    select(func.count(TopoPolicyRoute.id)).where(
                        TopoPolicyRoute.source_system_id == key[0],
                        TopoPolicyRoute.context == key[1],
                        TopoPolicyRoute.enabled.is_(True),
                    )
                )
                or 0
            )
            if total:
                out.append(
                    _step(
                        "policy",
                        INFO,
                        L(
                            f"{total} Policy-Route{'n' if total != 1 else ''} geprüft – keine "
                            "passt auf Quelle, Ziel und Eingang",
                            f"{total} policy route{'s' if total != 1 else ''} checked – none "
                            "matches source, destination and incoming",
                        ),
                    )
                )
        return out

    def _route_step(self, key: paths.Key, node, part: dict, cur, table) -> dict:
        route = part.get("route")
        items, more, other = self.route_items(key, cur, table, route, part.get("policy"))
        extra = []
        if more:
            extra.append(L(f"{more} weitere", f"{more} more"))
        if other:
            extra.append(L(f"{other} in anderen VRFs", f"{other} in other VRFs"))
        tail = f" ({', '.join(extra)})" if extra else ""
        if route is not None:
            via = ", ".join(h.get("ip") or h.get("interface") or "?"
                            for h in route.get("next_hops") or []) or "–"  # fmt: skip
            ecmp = ""
            if len(route.get("next_hops") or []) > 1:
                ecmp = L(
                    " – mehrere gleichwertige Next-Hops (ECMP)", " – several equal next-hops (ECMP)"
                )
            text = L(f"Route für {cur}: {route['prefix']} ({route.get('protocol') or '?'}) über "
                     f"{via}{ecmp}{tail}",
                     f"Route for {cur}: {route['prefix']} ({route.get('protocol') or '?'}) via "
                     f"{via}{ecmp}{tail}")  # fmt: skip
            return _step("routes", OK, text, items=items)
        if part.get("policy"):
            return _step("routes", INFO, L(f"Routen für {cur}{tail}", f"Routes for {cur}{tail}"),
                         items=items)  # fmt: skip
        if node is not None and node.missing:
            missing = ", ".join(node.missing[:3])
            return _step("routes", STOP, L(f"Keine Route bekannt – Routing-Tabelle nicht (ganz) "
                                           f"lesbar: {missing}",
                                           f"No route known – routing table not (fully) "
                                           f"readable: {missing}"),
                         L("Lauf des Geräts prüfen (Meldung im Lauf) und neu einlesen.",
                           "Check the device's run (message in the run) and collect again."),
                         items)  # fmt: skip
        if part.get("kind") == "vrf":
            return _step("routes", INFO, L(f"Routen für {cur}{tail}", f"Routes for {cur}{tail}"),
                         items=items)  # fmt: skip
        return _step("routes", STOP, L(
            f"Keine Route enthält {cur} – auch keine Default-Route{tail}",
            f"No route contains {cur} – not even a default route{tail}"),
            L("Auf diesem Gerät fehlt eine Route für das Ziel: Routing des Geräts prüfen bzw. "
              "neu einlesen.",
              "This device lacks a route for the destination: check the device's routing or "
              "collect again."), items)  # fmt: skip

    def _next_step(self, part: dict, nxt: dict) -> dict:
        egress = part.get("egress") or {}
        nh = egress.get("next_hop")
        iface = egress.get("interface")
        zone = egress.get("zone")
        target = (nxt.get("parts") or [nxt])[0]
        out = (f" über {iface}" if iface else "") + (f" (Zone {zone})" if zone else "")
        out_en = (f" via {iface}" if iface else "") + (f" (zone {zone})" if zone else "")
        via = target.get("via") or ""
        if nh:
            owner = next((i for k, i, _z in self.g.owners().get(nh, [])
                          if paths.parse_key(target.get("key")) == k), None)  # fmt: skip
            where = L(f", Interface {owner}", f", interface {owner}") if owner else ""
            return _step(
                "next_hop",
                OK,
                L(
                    f"Next-Hop {nh}{out} gehört {target['label']}{where} – dort geht es weiter",
                    f"Next-hop {nh}{out_en} belongs to {target['label']}{where} – it "
                    f"continues there",
                ),
            )
        return _step("next_hop", OK, L(f"Weiter{out} zu {target['label']}: {via}",
                                       f"Onward{out_en} to {target['label']}: {via}"))  # fmt: skip

    def end_step(self, path: dict) -> dict:
        end = path.get("end") or {}
        kind, text = end.get("kind"), end.get("text") or ""
        last = path["hops"][-1] if path.get("hops") else {}
        part = (last.get("parts") or [last])[0] if last else {}
        egress = part.get("egress") or {}
        nh = egress.get("next_hop")
        label = part.get("label", "")
        if kind in ("connected", "fabric"):
            return _step("end", OK, text)
        if kind == "exit":
            return _step(
                "end",
                OK,
                text,
                L(
                    "Der Weg verlässt das gelesene Netz über eine erfasste Anbindung.",
                    "The path leaves the network read via a captured exit.",
                ),
            )
        if kind == "external":
            return _step(
                "end",
                INFO,
                text,
                L(
                    f"Der Verkehr geht über die Default-Route von {label} nach aussen. "
                    f"Gehört das Ziel "
                    "nicht ins Internet, fehlt auf diesem Gerät eine genauere Route.",
                    f"The traffic leaves via the default route of {label}. If the "
                    f"destination is not "
                    "on the internet, this device lacks a more specific route.",
                ),
            )
        if kind == "unknown" and nh:
            where = self._attached(nh)
            names = self.g.names(nh) or ([self.g.network(nh)] if self.g.network(nh) else [])
            known = L(f" Im IPAM: {', '.join(names)}.", f" In the IPAM: {', '.join(names)}.") \
                if names else ""  # fmt: skip
            return _step(
                "end",
                STOP,
                text,
                L(
                    f"Kein gelesenes Gerät hat die Adresse {nh}.{known}{where} Möglich: das "
                    f"Gerät mit "
                    "dieser Adresse ist nicht als Quellsystem erfasst bzw. nicht eingelesen, seine "
                    "Interface-Adresse fehlt im Inventar – oder es ist ein Router nach "
                    "aussen, dann als "
                    "Anbindung erfassen.",
                    f"No device read has the address {nh}.{known}{where} Possible: the device with "
                    "this address is not captured as a source system or not collected, its "
                    "interface "
                    "address is missing in the inventory – or it is a router to the outside, then "
                    "capture it as an exit.",
                ),
            )
        if kind == "no_route":
            return _step(
                "end",
                STOP,
                text,
                L(
                    f"Auf {label} fehlt eine Route für das Ziel – Routing prüfen bzw. neu "
                    f"einlesen.",
                    f"{label} lacks a route for the destination – check the routing or collect "
                    "again.",
                ),
            )
        if kind == "loop":
            return _step(
                "end",
                STOP,
                text,
                L(
                    "Die Routen zeigen im Kreis: zwei Geräte schicken das Ziel einander zu. "
                    "Meist fehlt "
                    "auf einem davon eine genauere Route, oder eine Default-Route zeigt zurück.",
                    "The routes go in a circle: two devices send the destination to each other. "
                    "Usually one of them lacks a more specific route, or a default route "
                    "points back.",
                ),
            )
        return _step("end", WARN if kind == "max_hops" else INFO, text)

    def _attached(self, ip: str) -> str:
        """Welches gelesene Gerät das Netz des Next-Hops angeschlossen hat (dort hängt er)."""
        addr = _ip(ip)
        if addr is None:
            return ""
        labels = []
        for key, iface, _table in self.g.connected_nodes(addr):
            node = self.g.nodes.get(key)
            if node is not None:
                labels.append(node.label + (f" ({iface})" if iface else ""))
        if not labels:
            return ""
        return L(f" Sein Netz ist angeschlossen an {', '.join(labels[:3])}.",
                 f" Its network is connected to {', '.join(labels[:3])}.")  # fmt: skip

    # -- Erwartet über … ----------------------------------------------------------------

    def expect(self, result: dict, src, dst, sid: int) -> dict:
        name = self.session.get(SourceSystem, sid)
        device = name.name if name else f"#{sid}"
        nodes = [k for k in self.g.nodes if k[0] == sid]
        out: dict = {"source_system_id": sid, "device": device, "on_path": False, "steps": []}
        steps = out["steps"]
        if not nodes:
            steps.append(
                _step(
                    "expect",
                    STOP,
                    L(
                        f"Von {device} ist kein Routing gelesen – es kann nicht auf dem Weg "
                        f"liegen.",
                        f"No routing of {device} has been read – it cannot be on the path.",
                    ),
                    L(
                        "Gerät einlesen (Läufe) bzw. prüfen, ob es Routing liefert.",
                        "Collect the device (runs) or check whether it delivers routing.",
                    ),
                )
            )
            return out
        all_paths = result.get("paths") or []
        for p in all_paths:
            for i, h in enumerate(p.get("hops") or []):
                if any(paths.parse_key(x.get("key")) in nodes for x in h.get("parts") or [h]):
                    backup = p.get("kind") == "backup"
                    out["on_path"] = not backup
                    steps.append(
                        _step(
                            "expect",
                            INFO if backup else OK,
                            L(
                                f"{'Nur ein Backup-Weg' if backup else 'Der Weg'} geht über "
                                f"{device} "
                                f"(Hop {i + 1}).",
                                f"{'Only a backup path' if backup else 'The path'} goes via "
                                f"{device} "
                                f"(hop {i + 1}).",
                            ),
                        )
                    )
                    if not backup:
                        return out
                    break
        active = [p for p in all_paths if p.get("kind") != "backup"] or all_paths
        mine = [(ip, k, iface) for ip, owners in self.g.owners().items()
                for k, iface, _z in owners if k[0] == sid]  # fmt: skip
        if not mine:
            steps.append(
                _step(
                    "expect",
                    STOP,
                    L(
                        f"Von {device} sind keine Interface-Adressen bekannt – kein Next-Hop "
                        f"kann zu ihm "
                        "führen.",
                        f"No interface addresses of {device} are known – no next-hop can "
                        f"lead to it.",
                    ),
                    L(
                        "Inventar bzw. Interfaces des Geräts einlesen.",
                        "Collect the device's inventory or interfaces.",
                    ),
                )
            )
        elif active:
            steps += self._turn(active[0], mine, device, dst)
        steps += self._own_view(nodes, device, src, dst)
        return out

    def _turn(self, path: dict, mine: list[tuple], device: str, dst) -> list[dict]:
        """Wo der Weg vom erwarteten Gerät abbiegt: der erste Hop, der direkt mit ihm verbunden
        ist bzw. eine Route über es kennt - und warum er sie nicht nimmt."""
        for i, hop in enumerate(path.get("hops") or []):
            part = (hop.get("parts") or [hop])[0]
            key = paths.parse_key(part.get("key"))
            if key is None:
                continue
            table = part.get("vrf", 0)
            cur = _ip(((part.get("nat") or {}).get("to") or [None])[0]) or _ip(part.get("dst")) \
                or dst  # fmt: skip
            near = []
            for ip, _k, xiface in mine:
                addr = _ip(ip)
                if addr is None:
                    continue
                rows = [
                    r for r in self.g._routes(addr, key, connected=True) if table_of(r) == table
                ]
                if rows:
                    r = max(rows, key=lambda x: x.prefix_len)
                    iface = next((h.get("interface") for h in r.next_hops or []
                                  if h.get("interface")), None)  # fmt: skip
                    near.append((ip, xiface, r.prefix, iface))
            own_ips = {ip for ip, *_ in mine}
            rows, _other = self.candidates(key, cur, table) if cur is not None else ([], 0)
            via_x = [r for r in rows if any(h.get("ip") in own_ips for h in r.next_hops or [])]
            if not near and not via_x:
                continue
            route = part.get("route")
            chosen = (f"{route['prefix']} ({route.get('protocol') or '?'}) über "
                      + (", ".join(h.get("ip") or h.get("interface") or "?"
                                   for h in route.get("next_hops") or []) or "–")) \
                if route else L("keine Route", "no route")  # fmt: skip
            chosen_en = chosen.replace(" über ", " via ")
            out = []
            if near:
                ip, xiface, prefix, iface = near[0]
                at = L(f" an {iface}", f" on {iface}") if iface else ""
                xi = L(f" (Interface {xiface})", f" (interface {xiface})") if xiface else ""
                out.append(
                    _step(
                        "expect",
                        INFO,
                        L(
                            f"{part['label']} (Hop {i + 1}) ist direkt mit {device} "
                            f"verbunden: Netz "
                            f"{prefix}{at}, {device} hat dort {ip}{xi}.",
                            f"{part['label']} (hop {i + 1}) is directly connected to "
                            f"{device}: network "
                            f"{prefix}{at}, {device} has {ip} there{xi}.",
                        ),
                    )
                )
            target = near[0][0] if near else None
            if via_x:
                r = via_x[0]
                judged, why = self._judge(r, route, part.get("policy"))
                out.append(_step("expect", WARN, L(
                    f"Eine Route über {device} gibt es: {r.prefix} ({r.protocol or '?'}"
                    + (f", Distanz {r.distance}" if r.distance is not None else "") + ") – "
                    f"gewählt ist aber {chosen}: {why}.",
                    f"There is a route via {device}: {r.prefix} ({r.protocol or '?'}"
                    + (f", distance {r.distance}" if r.distance is not None else "") + ") – but "
                    f"{chosen_en} is chosen: {why}."),
                    L(f"Damit der Verkehr über {device} geht, muss diese Route auf {part['label']} "
                      "gewinnen (genaueres Präfix bzw. kleinere Distanz) oder die gewählte weg.",
                      f"For the traffic to go via {device}, this route must win on "
                      f"{part['label']} (more specific prefix or lower distance) or the chosen one "
                      "must go.")))  # fmt: skip
            else:
                need = f"{cur}" if cur is not None else L("das Ziel", "the destination")
                out.append(_step("expect", STOP, L(
                    f"Gewählt ist {chosen} – eine Route für {need} über {device}"
                    + (f" ({target})" if target else "") + f" fehlt auf {part['label']}.",
                    f"{chosen_en} is chosen – a route for {need} via {device}"
                    + (f" ({target})" if target else "") + f" is missing on {part['label']}."),
                    L(f"Damit der Verkehr über {device} geht, bräuchte {part['label']} eine Route "
                      f"für {need} über {target or device}, die genauer ist als die gewählte "
                      "bzw. eine kleinere Distanz hat.",
                      f"For the traffic to go via {device}, {part['label']} would need a route for "
                      f"{need} via {target or device} that is more specific than the chosen one "
                      "or has a lower distance.")))  # fmt: skip
            return out
        neighbors = self._neighbors_of(mine)
        hint = L(f"{device} ist verbunden mit: {', '.join(neighbors)} – der Weg müsste über eines "
                 "dieser Geräte führen.",
                 f"{device} is connected to: {', '.join(neighbors)} – the path would have to "
                 f"go via "
                 "one of these devices.") if neighbors else L(
            f"Kein gelesenes Gerät hat ein Netz mit {device} gemeinsam – Nachbarn von {device} "
            "einlesen.",
            f"No device read shares a network with {device} – collect the neighbors of "
            f"{device}.")  # fmt: skip
        return [_step("expect", STOP, L(
            f"Kein Gerät auf dem Weg ist direkt mit {device} verbunden oder kennt eine Route über "
            "es.",
            f"No device on the path is directly connected to {device} or knows a route via it."),
            hint)]  # fmt: skip

    def _neighbors_of(self, mine: list[tuple]) -> list[str]:
        own = {k for _ip, k, _i in mine}
        out: list[str] = []
        for ip, _k, _i in mine:
            addr = _ip(ip)
            if addr is None:
                continue
            for key, _iface, _t in self.g.connected_nodes(addr):
                node = self.g.nodes.get(key)
                if key not in own and node is not None and node.label not in out:
                    out.append(node.label)
        return out[:5]

    def _own_view(self, nodes: list, device: str, src, dst) -> list[dict]:
        """Wie das erwartete Gerät selbst Ziel und Quelle routet (würde es weiterleiten?)."""
        out = []
        for key in nodes[:3]:
            node = self.g.nodes[key]
            ends = (
                (L("das Ziel", "the destination"), dst),
                (L("die Quelle (Rückweg)", "the source (return path)"), src),
            )
            for what, ip in ends:
                if ip is None:
                    continue
                r = self.g.route(key, ip, 0)
                if r is None:
                    out.append(
                        _step(
                            "own",
                            WARN,
                            L(
                                f"{node.label} kennt keine Route für {what} {ip}.",
                                f"{node.label} knows no route for {what} {ip}.",
                            ),
                        )
                    )
                    continue
                via = ", ".join(
                    (h.get("ip") or h.get("interface") or "?")
                    + (f" ({self.g.owner_label(h.get('ip'))})" if self.g.owner_label(h.get("ip"))
                       else "")
                    for h in r.get("next_hops") or []
                ) or "–"  # fmt: skip
                out.append(
                    _step(
                        "own",
                        INFO,
                        L(
                            f"{node.label} routet {what} {ip} über {r['prefix']} "
                            f"({r.get('protocol') or '?'})"
                            f" → {via}.",
                            f"{node.label} routes {what} {ip} via {r['prefix']} "
                            f"({r.get('protocol') or '?'}) → {via}.",
                        ),
                    )
                )
        return out


def _policy_name(p: dict) -> str:
    kind = L("SD-WAN-Regel", "SD-WAN rule") if p.get("kind") == "sdwan" else ""
    name = p.get("name") or ""
    seq = p.get("seq")
    return " ".join(x for x in [kind, f"#{seq}" if seq is not None else "", name] if x).strip()


def explain(session: Session, tenant_id: int, graph: paths.Graph, result: dict,
            src_text: str | None, dst_text: str | None,
            expect: int | None = None) -> dict:  # fmt: skip
    """Erklärung zu einem Ergebnis von `paths.trace` (siehe Modul): Start, je Weg die Hops mit
    ihren Schritten und das Ende, optional „Erwartet über“ ein Gerät."""
    ex = Explainer(session, tenant_id, graph)
    src = _ip(src_text)
    dst = _ip(dst_text)
    out_paths = []
    for p in result.get("paths") or []:
        hops = [ex.hop(p, i, dst) for i in range(len(p.get("hops") or []))]
        out_paths.append({"kind": p.get("kind"), "hops": hops, "end": ex.end_step(p)})
    out = {"start": ex.start_steps(result, src), "paths": out_paths}
    if expect is not None:
        out["expect"] = ex.expect(result, src, dst, expect)
    return out


__all__ = ["Explainer", "explain"]
