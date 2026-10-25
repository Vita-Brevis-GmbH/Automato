"""Zonen je Hop in der Weg-Suche (0.57.0, ADR 0024 Nr. 13).

Zu einem Weg (`paths.trace`) die logischen Zonen: die Zone der Quelle und des Ziels (laengstes
Praefix wie in der Zonenmatrix), je Hop die Zone seines Knotens (Zonenmodell, `zone_struct`) und
die Uebergaenge dazwischen („DC-EINET“). Dazu die **Absicht** zwischen Quell- und Zielzone samt
Dienst und das Urteil gegenueber dem Weg:

- gesperrt, der Weg erlaubt → **gegen die Absicht**;
- eingeschraenkt mit Diensten: der angegebene Dienst ist erlaubt bzw. nicht (erlaubt der Weg
  ihn trotzdem → gegen die Absicht); ohne Dienst in der Suche nur der Hinweis;
- erlaubt, der Weg sperrt → Hinweis (Absicht nicht umgesetzt).

Nur lesend; ohne Zonen bleibt der Weg wie bisher.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ZonePolicy
from app.store import customers, zone_services, zone_struct
from app.store import zones as zone_store
from app.store.topology import endpoint, span

INTENT_TEXT = {"allow": "erlaubt", "limited": "eingeschränkt", "deny": "gesperrt"}


def _zones_of(index: zone_store.ZoneIndex, text: str | None) -> list[int]:
    if not text or not text.strip():
        return []
    try:
        x = endpoint(text)
    except ValueError:
        return []
    lo, hi = span(x)
    return sorted(z for z in index.zones_of(x.version, lo, hi) if isinstance(z, int))


def annotate(session: Session, tenant_id: int, result: dict, src: str | None, dst: str,
             service: tuple[str, int | None] | None, service_name: str | None = None,
             graph=None) -> None:  # fmt: skip
    """Zonen je Weg und Hop (`path.zones`, `hop.zone`) und die Absicht (`result.zones`)."""
    items = zone_store.load(session, tenant_id)
    if not items:
        result["zones"] = None
        return
    data = customers._Data(session, tenant_id)
    st = zone_struct.build(session, tenant_id, items, data, graph)
    nets = zone_store.derive(session, tenant_id, items, data, st)
    index = zone_store.ZoneIndex(nets)
    color = zone_store.colors(session, tenant_id, items)
    info = {
        z.id: {"id": z.id, "name": z.name, "code": z.code, "kind": z.kind,
               "position": z.position, "color": color.get(z.id, {}).get("color")}
        for z in items
    }  # fmt: skip
    src_ids, dst_ids = _zones_of(index, src), _zones_of(index, dst)

    for path in result.get("paths") or []:
        chain: list[dict] = []
        if len(src_ids) == 1:
            _add(chain, info.get(src_ids[0]), "src")
        for hop in path.get("hops") or []:
            zid = st.node_zone.get((hop.get("source_system_id"), hop.get("context")))
            hop["zone"] = info.get(zid) if zid is not None else None
            _add(chain, hop["zone"], "hop", hop.get("label"))
        if len(dst_ids) == 1:
            _add(chain, info.get(dst_ids[0]), "dst")
        path["zones"] = [{k: v for k, v in e.items() if k != "position"} for e in chain]

    result["zones"] = {
        "src": [info[z] for z in src_ids],
        "dst": [info[z] for z in dst_ids],
        "intents": _intents(
            session, tenant_id, src_ids, dst_ids, info, result, service, service_name
        ),
    }


_NONE = {"id": None, "name": "ohne Zone", "code": None, "kind": "none", "position": 0,
         "color": None}  # fmt: skip


def _add(chain: list[dict], z: dict | None, role: str, label: str | None = None) -> None:
    """Zone an die Kette des Wegs haengen - gleiche Zone wie davor: nur den Hop dazu; sonst mit
    dem Uebergang dorthin."""
    last = chain[-1] if chain else None
    if last is not None and last["id"] == (z["id"] if z else None):
        if label:
            last["hops"].append(label)
        return
    entry = {**(z or _NONE), "role": role, "hops": [label] if label else []}
    if last is not None and (last["id"] is not None or entry["id"] is not None):
        entry["transition"] = zone_store.transition_name(last, entry)
    chain.append(entry)


def _intents(session: Session, tenant_id: int, src_ids: list[int], dst_ids: list[int],
             info: dict, result: dict, service, service_name) -> list[dict]:  # fmt: skip
    intents = {
        (p.src_zone_id, p.dst_zone_id): p
        for p in session.scalars(select(ZonePolicy).where(ZonePolicy.tenant_id == tenant_id))
    }
    verdicts = [(p.get("policy") or {}).get("verdict") for p in result.get("paths") or []
                if p.get("kind") == "active"]  # fmt: skip
    allowed = "allow" in verdicts
    open_ = bool({"depends", "unknown"} & set(verdicts))  # abhaengig bzw. nicht gelesen
    out = []
    for s in src_ids:
        for d in dst_ids:
            p = intents.get((s, d))
            if p is None:
                continue
            verdict, text = _judge(session, tenant_id, p, allowed, open_, verdicts, service,
                                   service_name)  # fmt: skip
            out.append({
                "src": s, "dst": d, "src_name": info[s]["name"], "dst_name": info[d]["name"],
                "intent": p.intent, "note": p.note, "services": list(p.services or []),
                "verdict": verdict, "text": text,
            })  # fmt: skip
    return out


def _judge(session: Session, tenant_id: int, p, allowed: bool, open_: bool, verdicts: list,
           service, service_name) -> tuple[str, str]:  # fmt: skip
    """(Urteil, Text): ok, violation (gegen die Absicht), warn (abhaengig bzw. nicht umgesetzt),
    info (nicht pruefbar)."""
    way = "der Weg erlaubt" if allowed else ("der Weg ist offen" if open_ else "")
    if p.intent == "deny":
        if allowed:
            return "violation", "gegen die Absicht – gesperrt, der Weg erlaubt"
        if open_:
            return "warn", "gesperrt – der Weg ist abhängig bzw. nicht ganz gelesen"
        return "ok", "gesperrt – der Weg sperrt auch"
    if p.intent == "limited":
        if not p.services:
            return "info", "eingeschränkt – ohne Dienste in der Absicht nicht prüfbar"
        listed = ", ".join(p.services)
        if service is None:
            return "info", f"eingeschränkt auf {listed} – Dienst angeben, um zu prüfen"
        allow = zone_services.allowed(session, tenant_id, list(p.services))
        port = service[1]
        name = (service_name or "").strip() or None
        fits = allow.covers(service[0], port, port, name)  # ohne Port: alle Ports des Protokolls
        shown = name or (f"{service[0]}/{port}" if port else service[0])
        if fits:
            return "ok", f"eingeschränkt auf {listed} – {shown} ist erlaubt"
        if allowed:
            return "violation", f"gegen die Absicht – {shown} nicht in {listed}, {way}"
        return "warn", f"eingeschränkt auf {listed} – {shown} gehört nicht dazu"
    if verdicts and not allowed and not open_:
        return "warn", "erlaubt – der Weg sperrt aber (Absicht nicht umgesetzt)"
    return "ok", "erlaubt"
