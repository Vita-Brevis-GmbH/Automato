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
- **Vergleich:** die Firewalls des Abschnitts (Verbund = derselbe Verbund, gleich welches
  Mitglied) müssen rückwärts dieselben sein. ACI-Knoten und Router zählen nicht.
- **Ergebnis** je Weg `return` = {state: symmetric | asymmetric | unknown, text, firewalls};
  sind nicht alle Wege symmetrisch, ein Hinweis, welcher der einzig mögliche ist.

Nur lesend, aus den gelesenen Routing-Tabellen."""

from sqlalchemy.orm import Session

from app.i18n import L
from app.store import nat as nat_store
from app.store import paths as paths_store
from app.store.topology import endpoint, is_net

FIREWALLS = ("firewall", "vdom")
MAX_PATHS = 8  # Wege je Suche mit Rückweg


def _ident(h: dict) -> tuple:
    """Firewall eines Hops - im Verbund der Verbund (gleich welches Mitglied)."""
    if h.get("group"):
        return ("group", h["group"], h.get("context"))
    return ("node", h.get("source_system_id"), h.get("context"))


def _firewalls(hops: list[dict]) -> list[tuple[tuple, str]]:
    return [(_ident(h), h.get("label") or "?") for h in hops if h.get("kind") in FIREWALLS]


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
    """Rückwege zum Vergleichen: (Hops vorwärts, Start-Knoten | None, von, an, ohne Firewall).
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
            out.append((hops[j + 1 :], None, frm, str(part["snat"]["to"]), _ident(part)))
    return out


def _same(forward: list[tuple], back: list[tuple]) -> bool:
    return [f for f, _ in forward] == [b for b, _ in reversed(back)]


def annotate(session: Session, tenant_id: int, graph, result: dict, src: str | None,
             dst: str | None) -> None:  # fmt: skip
    """Rückweg je Weg (siehe Modul) in `path.return`; ohne Quelle (IP) nicht prüfbar."""
    found = result.get("paths") or []
    if not found or not dst:
        return
    try:
        source = endpoint(src) if src else None
    except ValueError:
        source = None
    if source is None or is_net(source):
        for p in found:
            p["return"] = {
                "state": "unknown",
                "text": L(
                    "Rückweg nicht geprüft – ohne Quell-IP",
                    "Return path not checked – no source IP",
                ),
            }
        return
    target = str(endpoint(dst)) if not is_net(endpoint(dst)) else None
    if target is None:
        return
    cache: dict[tuple[str, str], list[list[tuple]]] = {}
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


def _check(session, tenant_id, graph, path: dict, source: str, target: str, cache) -> dict:
    texts, firewalls, state = [], [], "symmetric"
    for hops, start, frm, to, skip in _segments(path, source, target):
        forward = _firewalls(hops)
        firewalls += [label for _, label in forward if label not in firewalls]
        if not forward:
            continue
        try:
            endpoint(to)
            if frm is not None:
                endpoint(frm)
        except ValueError:
            state = "unknown" if state == "symmetric" else state
            continue
        key = (start, frm, to)
        if key not in cache:
            back = paths_store.trace(session, tenant_id, to, frm, start, 0, None, graph=graph,
                                     backups=False, with_rules=False,
                                     balancers=False)  # fmt: skip
            cache[key] = [_firewalls(b.get("hops") or []) for b in back.get("paths") or []
                          if b.get("kind") != "backup"]  # fmt: skip
        backs = [[x for x in b if x[0] != skip] for b in cache[key]]
        where = frm or next((label for f, label in forward[::-1]), "?")
        if not backs:
            texts.append(L(f"kein Rückweg von {where} zu {to} gefunden",
                           f"no return path from {where} to {to} found"))  # fmt: skip
            state = "unknown" if state == "symmetric" else state
            continue
        if any(_same(forward, b) for b in backs):
            continue
        state = "asymmetric"
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
    return {"state": state, "text": text, "firewalls": firewalls}
