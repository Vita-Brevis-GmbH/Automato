"""Verkabelung im Detail (1.19.0, ADR 0031 Nachtrag 1.19.0): Kabelnummern und Transceiver.

- `GET    /cabling/labels`       Kabelnummern je Port-Paar: in Automato erfasst und aus NetBox
                                 (Label der Kabel, Referenzquelle) mit Herkunft (gleich,
                                 verschieden, nur Automato, nur NetBox) und ob CDP/LLDP zum Kabel
                                 in NetBox passt; Filter Herkunft, Suche (viewer).
- `PUT    /cabling/labels`       Kabelnummer eines Port-Paars erfassen bzw. aendern - eine
                                 Erfassung je Paar (operator, Audit `cable_label.set`).
- `DELETE /cabling/labels/{id}`  Erfassung loeschen (operator, Audit `cable_label.deleted`); was
                                 in NetBox steht, bleibt.
- `GET    /cabling/transceivers` Transceiver je Port und Lesestatus je Switch (gelesen, nicht
                                 lesbar, nicht unterstuetzt) - was nicht geht, ist ausgewiesen.

Nur Daten der Plattform: gelesen wird nichts von den Geraeten, geschrieben nur in Automato (nach
NetBox schreibt allein der NetBox-Sync, Vorschau → Uebernehmen). Nicht fuer Kunden-Benutzer
(Infrastruktur, wie die uebrige Verkabelung). Nach dem Speichern rechnen die Befunde der
Pruefung „Kabel“ neu.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.audit import reads
from app.auth.deps import require_operator, require_viewer, tenant_scope
from app.auth.principal import Principal
from app.db import get_session
from app.i18n import L
from app.models import CableLabel, InvDevice, InvTransceiver
from app.store import cable_labels as store
from app.store import cabling

router = APIRouter(prefix="/cabling", tags=["cabling"])
STATES = (store.SAME, store.DIFFERS, store.AUTOMATO, store.NETBOX)


def _tenant(session: Session, principal: Principal, tenant_id: int | None) -> int:
    tid = tenant_scope(principal, tenant_id)
    if tid is None:
        raise HTTPException(status_code=422, detail="tenant_id required")
    reads.maybe_audit_read(session, principal, tid, "cabling")
    return tid


class LabelIn(BaseModel):
    tenant_id: int
    a_device: str = Field(min_length=1, max_length=200)
    a_port: str = Field(min_length=1, max_length=200)
    b_device: str = Field(min_length=1, max_length=200)
    b_port: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=store.LABEL_MAX)
    note: str | None = Field(default=None, max_length=500)


def _row(pair: str, index: store.Index, links: dict[str, cabling.Link],
         seen: dict[str, list[cabling.End]]) -> dict:  # fmt: skip
    """Ein Port-Paar: Enden, Kabelnummer mit Herkunft, Verbindung aus CDP/LLDP, Nachbar-Abgleich
    der Kabel in NetBox."""
    mine, theirs = index.labels.get(pair), index.by_pair.get(pair, [])
    link = links.get(pair)
    if mine is not None:
        a, b = {"device": mine.a_device, "port": mine.a_port}, {"device": mine.b_device,
                                                               "port": mine.b_port}  # fmt: skip
    elif theirs:
        a, b = theirs[0].out()["a"], theirs[0].out()["b"]
    else:  # pragma: no cover - nur Paare mit Nummer oder Kabel
        a = b = {"device": None, "port": None}
    neighbor = store.NEIGHBOR_UNKNOWN
    wrong: list[dict] = []
    for c in theirs:
        state, found = store.neighbor_state(c, seen)
        if state == store.NEIGHBOR_MISMATCH or neighbor == store.NEIGHBOR_UNKNOWN:
            neighbor = state
        wrong += found
    return {
        "pair": pair, "a": a, "b": b, "state": store.state_of(mine, theirs),
        "automato": store.label_out(mine) if mine else None,
        "netbox": [c.out() for c in theirs],
        "link": {"id": link.id, "state": link.state, "protocols": sorted(link.protocols)}
        if link is not None else None,
        "neighbor": neighbor if theirs else None, "wrong": wrong,
    }  # fmt: skip


@router.get("/labels")
def list_labels(
    tenant_id: int | None = None,
    state: str | None = Query(None, pattern="^(same|differs|automato|netbox|mismatch)$"),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Kabelnummern je Port-Paar (in Automato erfasst bzw. Label in NetBox). `state=mismatch`:
    Kabel in NetBox, deren Enden CDP/LLDP anders meldet."""
    tid = _tenant(session, principal, tenant_id)
    index = store.Index(session, tid)
    cab = cabling.build(session, tid)
    seen = store._ends_at(cab)
    pairs = sorted(set(index.labels) | set(index.by_pair))
    rows = [_row(p, index, cab.links, seen) for p in pairs]
    summary = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    neighbors = [r["neighbor"] for r in rows]
    summary |= {"automato_total": len(index.labels), "netbox_total": len(index.cables),
                "netbox_labels": sum(1 for c in index.cables if c.label),
                "mismatch": neighbors.count(store.NEIGHBOR_MISMATCH),
                "unverified": neighbors.count(store.NEIGHBOR_UNKNOWN)}  # fmt: skip
    if state == "mismatch":
        rows = [r for r in rows if r["neighbor"] == store.NEIGHBOR_MISMATCH]
    elif state:
        rows = [r for r in rows if r["state"] == state]
    if q and q.strip():
        needle = q.strip().casefold()

        def texts(r: dict) -> list[str | None]:
            out = [r["a"]["device"], r["a"]["port"], r["b"]["device"], r["b"]["port"]]
            out += [(r["automato"] or {}).get("label"), *(c["label"] for c in r["netbox"])]
            return out

        rows = [r for r in rows if any(needle in (t or "").casefold() for t in texts(r))]
    return {"total": len(rows), "summary": summary, "items": rows[offset : offset + limit]}


def _audit(session: Session, principal: Principal, action: str, row: CableLabel,
           **extra) -> None:  # fmt: skip
    audit.record(session, actor=f"user:{principal.subject}", action=f"cable_label.{action}",
                 subject=row.pair,
                 detail={"tenant_id": row.tenant_id, "label": row.label,
                         "a": f"{row.a_device} {row.a_port}", "b": f"{row.b_device} {row.b_port}",
                         **extra})  # fmt: skip


@router.put("/labels")
def set_label(
    body: LabelIn,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Kabelnummer eines Port-Paars erfassen bzw. aendern (Operator). Das Paar ist ungerichtet
    und normalisiert (Lang-/Kurzform des Ports): eine Erfassung je Paar."""
    tid = _tenant(session, principal, body.tenant_id)
    try:
        row, old = store.set_label(session, tid, body.a_device, body.a_port, body.b_device,
                                   body.b_port, body.label, body.note,
                                   f"user:{principal.subject}")  # fmt: skip
    except store.Invalid as exc:
        raise HTTPException(status_code=422, detail=_invalid(str(exc))) from None
    _audit(session, principal, "set", row, old=old)
    store.refresh_findings(session, tid)
    index = store.Index(session, tid)
    return {**store.label_out(row), "pair": row.pair, "state": store.state_of(
        row, index.by_pair.get(row.pair, [])), "previous": old}  # fmt: skip


_INVALID_EN = {
    "Gerät und Port an beiden Enden angeben": "give device and port at both ends",
    "Kabelnummer angeben": "give a cable number",
    "beide Enden sind derselbe Port": "both ends are the same port",
    f"Kabelnummer höchstens {store.LABEL_MAX} Zeichen": (
        f"cable number at most {store.LABEL_MAX} characters"),
}  # fmt: skip


def _invalid(text: str) -> str:
    return L(text, _INVALID_EN.get(text, text))


@router.delete("/labels/{label_id}")
def delete_label(
    label_id: int,
    tenant_id: int | None = None,
    principal: Principal = Depends(require_operator),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Erfassung loeschen (Operator) - die Nummer in NetBox bleibt, wie sie ist."""
    tid = _tenant(session, principal, tenant_id)
    row = session.get(CableLabel, label_id)
    if row is None or row.tenant_id != tid:
        raise HTTPException(status_code=404, detail="cable label not found")
    _audit(session, principal, "deleted", row)
    session.delete(row)
    session.flush()
    store.refresh_findings(session, tid)
    return {"deleted": label_id}


@router.get("/transceivers")
def transceivers(
    tenant_id: int | None = None,
    device: str | None = Query(None, max_length=200),
    principal: Principal = Depends(require_viewer),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Transceiver je Port und Lesestatus je Switch: gelesen (auch ohne Optik), nicht lesbar
    (Grund im Lauf), nicht unterstuetzt (SNMPv3) bzw. nicht geliefert (aelterer Agent)."""
    tid = _tenant(session, principal, tenant_id)
    key = cabling.device_key(device) if device else None
    rows = [t for t in session.scalars(
        select(InvTransceiver).where(InvTransceiver.tenant_id == tid)
        .order_by(InvTransceiver.device, InvTransceiver.port))
        if key is None or cabling.device_key(t.device) == key]  # fmt: skip
    counts: dict[str, int] = {}
    for t in rows:
        counts[t.device] = counts.get(t.device, 0) + 1
    devices = [
        {"device": name, "status": status, "count": counts.get(name, 0)}
        for name, status in session.execute(
            select(InvDevice.name, InvDevice.transceivers)
            .where(InvDevice.tenant_id == tid, InvDevice.role.in_(("switch", "router")))
            .order_by(InvDevice.name))
        if key is None or cabling.device_key(name) == key
    ]  # fmt: skip
    return {
        "devices": devices,
        "items": [{"device": t.device, "port": t.port, "type": t.type, "vendor": t.vendor,
                   "part": t.part, "serial": t.serial} for t in rows],
    }  # fmt: skip
