"""Rückweg je Weg der Weg-Suche (0.134.0).

Anwender 2026-11-05: „Gemäss mir sollte der Hin und Rückweg geprüft werden (dieser muss gleich
sein, ausnahme ACI Nodes und Firewall Verbunde). und dann der einzig mögliche Weg aufzeigen.“

Firewalls sind zustandsbehaftet: die Antwort muss über dieselben Firewalls zurück, sonst
verwirft die Firewall sie (asymmetrisches Routing). Je Weg rechnet die Weg-Suche darum den
Rückweg mit - nur das Routing, ohne Policies (die Antwort erlaubt die Sitzung):

- **Abschnitte:** bis zum Load Balancer antwortet seine VIP an die Quelle, dahinter der Server
  an den Load Balancer (Service Engine im Netz der VIP; ohne SNAT des Load Balancers an die
  Quelle). Ohne Load Balancer antwortet das Ziel nach dem DNAT an die Quelle. Nach einem SNAT
  antwortet es der Adresse des Pools (sie gehört der Firewall mit dem SNAT), von dieser Firewall
  geht die Antwort an die Quelle zurück.
- **Vergleich:** die Firewalls des Abschnitts müssen rückwärts dieselben sein. Parallele
  Firewalls zählen als eine (0.141.1, Anwender: „Berücksichtigen der Parallelen Wege (ACI
  mehrere Leafs, Firewall Verbunde mit Sessionsync)“): derselbe Verbund bzw. Session-Sync-Partner
  (FGSP, `exits._scopes`) - gleich welches Mitglied. ACI-Knoten (alle Leafs einer VRF) und Router
  zählen nicht.
- **Anzeige** (0.141.1, Anwender: „ich sehe die Meldung, aber es zeigt mir nicht den Rückweg
  an, welcher falsch sein soll“): je Abschnitt die Rückwege Hop für Hop (`return.legs`), die
  Firewalls darin markiert, die nicht auf dem Hinweg liegen.
- **Ergebnis** je Weg `return` = {state: symmetric | asymmetric | unknown, text, firewalls,
  legs (nicht symmetrisch)};
  sind nicht alle Wege symmetrisch, ein Hinweis, welcher der einzig mögliche ist.
- **Antwort von aussen** (0.136.0): hängt der Antwortende an keinem gelesenen Gerät (Internet,
  Partner), kommt die Antwort über den Ausgang des Hinwegs zurück, von dort nach dem Routing.
- **Verbindungen der Applikations-Doku** (0.136.0): dieselbe Prüfung je Verbindung, Quelle bzw.
  Ziel auch als Netz (`nets=True`), `connection.return` aus den aktiven Wegen.

Nur lesend, aus den gelesenen Routing-Tabellen."""

from sqlalchemy.orm import Session

from app.i18n import L
from app.store import nat as nat_store
from app.store import paths as paths_store
from app.store.topology import endpoint, is_net

FIREWALLS = ("firewall", "vdom")
MAX_PATHS = 8  # Wege je Suche mit Rückweg


def _ident(h: dict, scope=None) -> tuple:
    """Firewall eines Hops - im Verbund der Verbund, bei Session-Sync-Partnern (FGSP) der Partner-
    Verbund (`scope`, 0.141.1), gleich welches Mitglied."""
    if scope is not None and h.get("source_system_id") is not None:
        return ("fw", scope(h["source_system_id"]), h.get("context"))
    if h.get("group"):
        return ("group", h["group"], h.get("context"))
    return ("node", h.get("source_system_id"), h.get("context"))


def _scope(graph):
    """Quellsystem -> Vertreter seiner parallelen Firewalls (Verbund, Session-Sync) - je Graph
    einmal (`exits._scopes`)."""
    if graph is None:
        return None
    found = graph.__dict__.get("_return_scope")
    if found is None:
        from app.store import exits  # spaet: exits -> paths

        found = graph.__dict__["_return_scope"] = exits._scopes(graph)
    return found


def _firewalls(hops: list[dict], scope=None) -> list[tuple[tuple, str]]:
    return [(_ident(h, scope), h.get("label") or "?") for h in hops if h.get("kind") in FIREWALLS]


def _hops(hops: list[dict], scope=None) -> list[tuple]:
    """Hops eines Rückwegs zum Merken: (Firewall | None, Bezeichnung, Art)."""
    return [(_ident(h, scope) if h.get("kind") in FIREWALLS else None, h.get("label") or "?",
             h.get("kind")) for h in hops]  # fmt: skip


def _snat_hop(hops: list[dict]) -> tuple[int, dict] | None:
    """Letzter Hop des Abschnitts mit SNAT: (Index, Firewall mit dem SNAT)."""
    for i in range(len(hops) - 1, -1, -1):
        for part in hops[i].get("parts") or [hops[i]]:
            if (part.get("snat") or {}).get("to"):
                return i, part
    return None


def _dnat(hops: list[dict], dst: str) -> str:
    """Ziel nach dem letzten DNAT des Abschnitts (VIP), sonst das Ziel."""
    for h in reversed(hops):
        to = (h.get("nat") or {}).get("to") or []
        if to:
            return str(to[0])
    return dst


def _legs(path: dict, source: str, dst: str) -> list[tuple[list, str, str]]:
    """Abschnitte des Wegs am Load Balancer: (Hops, wer antwortet, an wen)."""
    hops = path.get("hops") or []
    cut = next((i for i, h in enumerate(hops) if h.get("lb")), None)
    if cut is None:
        return [(hops, _dnat(hops, dst), source)]
    lb = hops[cut]["lb"]
    front, back = hops[: cut + 1], hops[cut + 1 :]
    vip = nat_store.host_ip(lb.get("ext")) or _dnat(front, dst)
    server = str((lb.get("to") or [""])[0])
    legs = [(front, vip, source)]
    if server and back:  # Server antwortet dem Load Balancer (SNAT) bzw. dem Client
        legs.append((back, server, source if lb.get("snat") == "client" else vip))
    return legs


def _segments(path: dict, source: str, dst: str) -> list[tuple]:
    """Rückwege zum Vergleichen: (Hops vorwärts, Start-Knoten | None, von, an, ohne Firewall -
    der Hop mit dem SNAT).
    Nach einem SNAT antwortet das Ziel der Adresse des Pools - sie gehört der Firewall mit dem
    SNAT, diese gibt die Antwort an den Client weiter (von ihr aus geprüft)."""
    out = []
    for hops, frm, to in _legs(path, source, dst):
        found = _snat_hop(hops)
        if found is None:
            out.append((hops, None, frm, to, None))
            continue
        j, part = found
        out.append((hops[: j + 1], part["key"], None, to, None))
        if hops[j + 1 :]:
            out.append((hops[j + 1 :], None, frm, str(part["snat"]["to"]), part))
    return out


def _exit_entry(hops: list[dict]) -> tuple | None:
    """Eingang der Antwort von aussen (0.136.0): kommt die Antwort von einer Adresse ausserhalb
    der gelesenen Geräte (Internet, Partner), kommt sie über den Ausgang des Hinwegs zurück -
    (Knoten, Interface, Zone, wie erreicht) für `paths.trace(entries=)`."""
    if not hops:
        return None
    last = hops[-1]
    part = (last.get("parts") or [last])[0]
    key = paths_store.parse_key(part.get("key"))
    egress = part.get("egress") or {}
    if key is None or not egress.get("interface"):
        return None
    how = L("Antwort über den Ausgang des Hinwegs", "reply via the exit of the forward path")
    return (key, egress["interface"], egress.get("zone"), how)


def _same(forward: list[tuple], back: list[tuple]) -> bool:
    return _squash([f for f, _ in forward]) == _squash([b for b, _ in reversed(back)])


def _squash(idents: list[tuple]) -> list[tuple]:
    """Aufeinanderfolgende gleiche Firewalls einmal (Partner nacheinander = ein Durchgang)."""
    out: list[tuple] = []
    for x in idents:
        if not out or out[-1] != x:
            out.append(x)
    return out


MAX_SHOWN = 3  # Rückwege je Abschnitt in der Anzeige


def _shown(back: list[tuple], forward: set[tuple]) -> list[dict]:
    """Rückweg zur Anzeige: je Hop Bezeichnung, Art und ob eine Firewall auf dem Hinweg fehlt."""
    return [{"label": label, "kind": kind, "firewall": ident is not None,
             "other": ident is not None and ident not in forward}
            for ident, label, kind in back]  # fmt: skip


def annotate(session: Session, tenant_id: int, graph, result: dict, src: str | None,
             dst: str | None, nets: bool = False, backups: bool = True) -> None:  # fmt: skip
    """Rückweg je Weg (siehe Modul) in `path.return`; ohne Quelle (IP) nicht prüfbar. `nets`
    (Verbindungen der Applikations-Doku, 0.136.0): Quelle bzw. Ziel auch als Netz - die Antwort
    geht an das Netz als Ganzes."""
    found = [p for p in result.get("paths") or [] if backups or p.get("kind") != "backup"]
    if not found or not dst:
        return
    try:
        source = endpoint(src) if src else None
    except ValueError:
        source = None
    if source is None or (is_net(source) and not nets):
        for p in found:
            p["return"] = {
                "state": "unknown",
                "text": L(
                    "Rückweg nicht geprüft – ohne Quell-IP",
                    "Return path not checked – no source IP",
                ),
            }
        return
    try:
        target = endpoint(dst)
    except ValueError:
        return
    if is_net(target) and not nets:
        return
    target = str(target)
    # je Graph (Mandant, Suche bzw. Berechnung der Verbindungen) einmal je Rückweg
    cache = graph.__dict__.setdefault("_return_paths", {}) if graph is not None else {}
    for p in found[:MAX_PATHS]:
        p["return"] = _check(session, tenant_id, graph, p, str(source), target, cache)
    states = [p["return"]["state"] for p in found if p.get("return")]
    good = [p for p in found if (p.get("return") or {}).get("state") == "symmetric"]
    if "asymmetric" in states and good:
        names = ", ".join(
            " → ".join(h.get("label") or "?" for h in p["hops"] if h.get("kind") in FIREWALLS)
            or "–"
            for p in good[:2]
        )
        result["notes"].append(L(
            f"Nur {len(good)} von {len(states)} Wegen haben den Rückweg über dieselben Firewalls"
            f" – möglich: {names}",
            f"Only {len(good)} of {len(states)} paths return via the same firewalls – possible: "
            f"{names}",
        ))  # fmt: skip
    elif "asymmetric" in states:
        result["notes"].append(L(
            "Kein Weg hat den Rückweg über dieselben Firewalls – die Antwort ginge über eine "
            "andere Firewall (asymmetrisches Routing)",
            "No path returns via the same firewalls – the reply would pass another firewall "
            "(asymmetric routing)",
        ))  # fmt: skip


def summary(found: list[dict]) -> dict | None:
    """Rückweg einer Verbindung aus ihren Wegen (0.136.0): über eine andere Firewall, sobald
    ein Weg es ist (ECMP verteilt die Antworten), symmetrisch nur, wenn alle es sind."""
    rets = [p["return"] for p in found if p.get("return")]
    if not rets:
        return None
    bad = [r for r in rets if r["state"] == "asymmetric"]
    if bad:
        text = bad[0]["text"]
        if len(rets) > 1:
            text += L(f" ({len(bad)} von {len(rets)} Wegen)", f" ({len(bad)} of {len(rets)} paths)")
        return _with_legs({"state": "asymmetric", "text": text,
                           "firewalls": bad[0].get("firewalls") or []}, bad[0])  # fmt: skip
    if all(r["state"] == "symmetric" for r in rets):
        return {"state": "symmetric", "text": rets[0]["text"],
                "firewalls": rets[0].get("firewalls") or []}  # fmt: skip
    unsure = next(r for r in rets if r["state"] != "symmetric")
    return _with_legs({"state": "unknown", "text": unsure["text"],
                       "firewalls": unsure.get("firewalls") or []}, unsure)  # fmt: skip


def _with_legs(out: dict, ret: dict) -> dict:
    """Abschnitte nur, wo der Rückweg nicht symmetrisch ist (0.141.1) - nur dort angezeigt."""
    if ret.get("legs"):
        out["legs"] = ret["legs"]
    return out


def _from_last_connected(graph, hops: list[dict], to: str) -> list[dict]:
    """Hops ab dem letzten Knoten am Netz des Empfängers der Antwort (0.136.0): hängt die
    Quelle an mehreren Knoten (Transfernetz, SNAT-Pool), kann der Weg an jedem beginnen - die
    Antwort endet am ersten, der das Netz angeschlossen hat; davor zählt nicht."""
    if graph is None:
        return hops
    near = {k for k, *_ in graph.connected_nodes(endpoint(to))}
    cut = 0
    for i, h in enumerate(hops):
        keys = {paths_store.parse_key(x.get("key")) for x in h.get("parts") or [h]}
        if not keys & near:
            break
        cut = i
    return hops[cut:]


def _check(session, tenant_id, graph, path: dict, source: str, target: str, cache) -> dict:
    texts, firewalls, state, legs = [], [], "symmetric", []
    scope = _scope(graph)
    for hops, start, frm, to, skip in _segments(path, source, target):
        firewalls += [label for _, label in _firewalls(hops) if label not in firewalls]
        skip = _ident(skip, scope) if skip is not None else None  # Firewall mit dem SNAT
        try:
            endpoint(to)
            if frm is not None:
                endpoint(frm)
        except ValueError:
            state = "unknown" if state == "symmetric" else state
            continue
        forward = _firewalls(_from_last_connected(graph, hops, to), scope)
        if not forward:
            continue
        entry = _exit_entry(hops) if start is None else None
        key = (start, frm, to, entry[:2] if entry else None)
        if key not in cache:
            back = paths_store.trace(session, tenant_id, to, frm, start, 0, None, graph=graph,
                                     backups=False, with_rules=False,
                                     balancers=False)  # fmt: skip
            if not back.get("paths") and entry is not None:
                # Antwort von aussen (an keinem gelesenen Geraet): über den Ausgang des Hinwegs
                back = paths_store.trace(session, tenant_id, to, frm, None, 0, None,
                                         graph=graph, backups=False, with_rules=False,
                                         balancers=False, entries=[entry])  # fmt: skip
            cache[key] = [_hops(b.get("hops") or [], scope) for b in back.get("paths") or []
                          if b.get("kind") != "backup"]  # fmt: skip
        full = [[x for x in b if x[0] is None or x[0] != skip] for b in cache[key]]
        backs = [[(x[0], x[1]) for x in b if x[0] is not None] for b in full]
        where = frm or next((label for f, label in forward[::-1]), "?")
        idents = {f for f, _ in forward}
        leg = {
            "from": where,
            "to": to,
            "forward": [label for _, label in forward],
            "paths": [_shown(b, idents) for b in full[:MAX_SHOWN]],
            "more": max(0, len(full) - MAX_SHOWN),
        }
        if not backs:
            texts.append(L(f"kein Rückweg von {where} zu {to} gefunden",
                           f"no return path from {where} to {to} found"))  # fmt: skip
            state = "unknown" if state == "symmetric" else state
            legs.append({**leg, "state": "unknown"})
            continue
        if any(_same(forward, b) for b in backs):
            legs.append({**leg, "state": "symmetric"})
            continue
        state = "asymmetric"
        legs.append({**leg, "state": "asymmetric"})
        other = " → ".join(label for _, label in backs[0]) or L("keine Firewall", "no firewall")
        texts.append(L(f"Antwort von {where} an {to} über {other}",
                       f"reply from {where} to {to} via {other}"))  # fmt: skip
    if state == "symmetric":
        text = L("Rückweg über dieselben Firewalls", "Return path via the same firewalls")
    elif state == "asymmetric":
        text = L("Rückweg über andere Firewalls – ", "Return path via other firewalls – ")
        text += "; ".join(texts)
    else:
        text = L("Rückweg nicht ganz prüfbar – ", "Return path not fully checkable – ")
        text += "; ".join(texts) or L("Adresse unbekannt", "address unknown")
    out = {"state": state, "text": text, "firewalls": firewalls}
    return _with_legs(out, {"legs": legs}) if state != "symmetric" else out
