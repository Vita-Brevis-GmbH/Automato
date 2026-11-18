"""NetBox-Sync, Abschnitt „Reservierungen“ (1.19.0, ADR 0066).

Anwender 2026-11-17: „Reservationen auch ins Netbox“. Die in Automato reservierten VLAN- und
AS-Nummern (`store/reservations`) gehen ueber den bestehenden Schreibweg nach NetBox -
Vorschau -> Uebernehmen (ADR 0010), nur wenn der Abschnitt gewaehlt ist:

- **VLANs** mit Status `reserved` in der passenden VLAN-Gruppe: die an der Reservierung gewaehlte
  (muss es in NetBox geben bzw. der Sync legt sie an), sonst die eine VLAN-Gruppe am Standort der
  Reservierung (in NetBox bzw. vom Sync angelegt, z. B. die des Quellsystems der Switches), ohne
  solche eine eigene Gruppe „Reservierungen <Standort>“ mit dem Standort als Geltungsbereich (fuer
  alle Standorte eine Gruppe ohne Geltungsbereich). Mehrere Gruppen am Standort: Hinweis, nichts
  geraten - die Gruppe an der Reservierung waehlen.
- **AS-Nummern** als ASN-Objekte mit der RIR der vorhandenen NetBox: die RIR, die die ASN gleicher
  Art (privat bzw. oeffentlich) dort schon tragen, sonst die einzige RIR dieser Art bzw. die
  einzige ueberhaupt. Ohne eindeutige RIR: Hinweis, nichts angelegt (eine RIR legt der Sync nie
  an). Bereiche ueber `MAX_ASNS` Nummern: Hinweis (keine stille Grenze).
- **Nie etwas Gelesenes ueberschreiben:** gibt es in NetBox schon ein VLAN mit dieser Nummer in
  der Gruppe bzw. diese ASN (von Hand, aus einem anderen Abschnitt, aus einem anderen Mandanten),
  zeigt die Vorschau es nur an (`note`); will ein anderer Abschnitt das VLAN (gelesen in Gebrauch),
  gewinnt dieser - ein eigenes reserviertes VLAN geht dabei in das gelesene ueber.
- **Eigen** sind VLANs mit Tag `automato`, Status `reserved` und der Beschreibung
  `Reservierung: …`, die Gruppen „Reservierungen …“ (`GROUP_MARK`) und die ASN mit Tag
  `automato` - je des Mandanten. Nur dieser Abschnitt aendert bzw. entfernt sie: Aenderungen nur
  bestaetigt (`confirm` wie Kabelnummern und DNS-Namen, beide Werte in der Vorschau),
  freigegebene Reservierungen sind veraltet bzw. werden mit „Löschen
  zulassen“ entfernt; andere Abschnitte lassen sie stehen, und Ports nehmen nie ein reserviertes
  VLAN (`owned_vlan` in `netbox_sync._vlan_candidates`).

Der Plan rechnet deutsch (Bezeichnungen, Hinweise); die Anzeige uebersetzt (`WARNINGS_EN`).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.models import Site, Tenant
from app.store import boundary
from app.store import reservations as reservation_store

if TYPE_CHECKING:  # nur fuer Typen - netbox_sync importiert dieses Modul
    from app.store.netbox_sync import Ref, State, Wants

SECTION = "reservations"
KINDS = frozenset({"rir", "asn"})  # Arten nur dieses Abschnitts (gelesen nur mit ihm)
MARK = "Reservierung: "  # Beschreibung der reservierten VLANs und ASN
GROUP_MARK = "Automato: Reservierungen"  # Beschreibung der eigenen VLAN-Gruppen
MAX_ASNS = 1024  # AS je Reservierung als einzelne ASN-Objekte

# Hinweise der Vorschau: deutsche Form -> englisch (`netbox_sync.WARNINGS_EN`)
WARNINGS_EN = (
    (
        "Reservierung {what}: Standort #{site} fehlt – nicht übernommen.",
        "Reservation {what}: site #{site} missing – not applied.",
    ),
    (
        "Reservierung {what}: VLAN-Gruppe {group} nicht in NetBox – nicht übernommen.",
        "Reservation {what}: VLAN group {group} not in NetBox – not applied.",
    ),
    (
        "Reservierung {what}: mehrere VLAN-Gruppen am Standort {site} ({groups}) – VLAN-Gruppe an "
        "der Reservierung wählen; nicht übernommen.",
        "Reservation {what}: several VLAN groups at site {site} ({groups}) – choose the VLAN "
        "group at the reservation; not applied.",
    ),
    (
        "Reservierung {what}: {n} AS-Nummern – mehr als {limit} werden nicht als einzelne "
        "ASN-Objekte übernommen.",
        "Reservation {what}: {n} AS numbers – more than {limit} are not applied as single ASN "
        "objects.",
    ),
    (
        "Reservierungen: keine eindeutige RIR in NetBox für AS {asns} ({rirs}) – ASN nicht "
        "angelegt; in NetBox eine RIR anlegen bzw. die ASN dieser Art einer RIR zuordnen.",
        "Reservations: no unique RIR in NetBox for AS {asns} ({rirs}) – ASN not created; create "
        "an RIR in NetBox or assign the ASNs of this kind to one RIR.",
    ),
    (
        "Reservierungen: {n} Nummern in NetBox schon vorhanden bzw. gelesen in Gebrauch – nur "
        "angezeigt (z. B. {examples}).",
        "Reservations: {n} numbers already in NetBox or read in use – only shown "
        "(e.g. {examples}).",
    ),
)


@dataclass
class ReservedVlan:
    """Reserviertes VLAN (Nummer) - Gruppe erst nach dem Lesen von NetBox (`after_read`)."""

    reservation: str  # „VLAN 120–129“ (fuer Hinweise)
    vid: int
    site: str | None  # Slug des Standorts; None = alle Standorte
    site_name: str
    group: str | None  # an der Reservierung gewaehlte VLAN-Gruppe (Slug)
    name: str
    description: str
    tenant: Ref


@dataclass
class ReservedAsn:
    reservation: str
    asn: int
    description: str
    tenant: Ref


@dataclass
class Pending:
    """Was der Abschnitt nach dem Lesen braucht (an `Wants.reserved`)."""

    tslug: str
    tenant_name: str
    vlans: list[ReservedVlan]
    asns: list[ReservedAsn]


def _value(v: Any) -> Any:
    return v.get("value") if isinstance(v, dict) else v


def _description(purpose: str, by: str, at) -> str:
    when = f" am {at.date().isoformat()}" if at else ""
    return f"{MARK}{purpose} · reserviert von {by}{when}"[:200]


def owned_vlan(obj: dict) -> bool:
    """Vom Abschnitt angelegtes reserviertes VLAN (Tag, Status, Beschreibung)."""
    from app.store.netbox_sync import _tagged

    return (_tagged(obj) and _value(obj.get("status")) == "reserved"
            and str(obj.get("description") or "").startswith(MARK))  # fmt: skip


def owned_asn(obj: dict) -> bool:
    """ASN mit Tag `automato`: legt nur dieser Abschnitt an - auch wenn die Beschreibung in NetBox
    von Hand geaendert wurde (dann aendert der Sync sie nur bestaetigt)."""
    from app.store.netbox_sync import _tagged

    return _tagged(obj)


def owned_group(obj: dict) -> bool:
    from app.store.netbox_sync import _tagged

    return _tagged(obj) and str(obj.get("description") or "").startswith(GROUP_MARK)


def owned(kind: str, obj: dict) -> bool:
    """Gehoert das NetBox-Objekt diesem Abschnitt? Dann fuehrt nur er es als veraltet bzw.
    loescht es (`netbox_sync.diff` laesst es aus, `items` nimmt es)."""
    if kind == "vlan":
        return owned_vlan(obj)
    if kind == "vlan_group":
        return owned_group(obj)
    return False


def group_slug(tslug: str, site: str | None) -> str:
    from app.store.netbox_sync import slugify

    return slugify(f"{tslug}-reservierungen{f'-{site}' if site else ''}")


# -- Soll ---------------------------------------------------------------------------------------


def wants(session: Session, w: Wants, tenant: Tenant, sites: dict[int, Site], tenant_ref: Ref,
          tslug: str) -> None:  # fmt: skip
    """Reservierungen des Mandanten merken - Gruppe bzw. RIR stehen erst nach dem Lesen fest."""
    from app.store.netbox_sync import _cut, slugify

    pending = Pending(tslug, tenant.name, [], [])
    for r in reservation_store.rows(session, tenant.id):
        what = reservation_store.label(r.kind, r.low, r.high)
        desc = _description(r.purpose, r.created_by, r.created_at)
        if r.kind == "asn":
            count = r.high - r.low + 1
            if count > MAX_ASNS:
                w.warnings.append(
                    f"Reservierung {what}: {count} AS-Nummern – mehr als {MAX_ASNS} werden nicht "
                    "als einzelne ASN-Objekte übernommen."
                )
                continue
            pending.asns += [ReservedAsn(what, n, desc, tenant_ref)
                             for n in range(r.low, r.high + 1)]  # fmt: skip
            continue
        site = sites.get(r.site_id) if r.site_id is not None else None
        if r.site_id is not None and site is None:
            w.warnings.append(
                f"Reservierung {what}: Standort #{r.site_id} fehlt – nicht übernommen."
            )
            continue
        for vid in range(r.low, r.high + 1):
            pending.vlans.append(ReservedVlan(
                what, vid, slugify(site.code) if site else None,
                (site.name or site.code) if site else "alle Standorte", r.vlan_group,
                _cut(r.purpose, 64), desc, tenant_ref))  # fmt: skip
    w.reserved = pending


# -- Nach dem Lesen ------------------------------------------------------------------------------


def _groups_at(wants: Wants, state: State, site: str) -> list[str]:
    """VLAN-Gruppen mit dem Standort als Geltungsbereich: in NetBox bzw. vom Sync gewollt."""
    out: set[str] = set()
    have_site = state["site"].get(site)
    for slug, have in state["vlan_group"].items():
        o = have.obj
        if (o.get("scope_type") == "dcim.site" and have_site is not None
                and o.get("scope_id") == have_site.id):  # fmt: skip
            out.add(slug)
    for want in wants.items.values():
        if want.kind == "vlan_group" and want.refs.get("scope_id") == ("site", site):
            out.add(want.key)
    return sorted(out)


def _read_vlans(reader, state: State, group_slugs: set[str]) -> None:
    """VLANs der Zielgruppen und die reservierten VLANs in NetBox (nur GET) - soweit der Sync
    sie nicht schon gelesen hat."""
    from app.store.netbox_sync import Have, _chunks, _key_of, _tagged

    ids = [state["vlan_group"][g].id for g in sorted(group_slugs) if g in state["vlan_group"]]
    found: list[dict] = []
    for chunk in _chunks(ids, 50):
        found += reader.read("ipam/vlans", {"group_id": chunk})
    found += [o for o in reader.read("ipam/vlans", {"status": "reserved"}) if owned_vlan(o)]
    for obj in found:
        key = _key_of("vlan", obj)
        if key is not None and key not in state["vlan"]:
            state["vlan"][key] = Have(obj["id"], key, obj, _tagged(obj))


def _rir_of(obj: dict, by_id: dict[int, str]) -> str | None:
    """Slug der RIR einer ASN in NetBox (verschachtelt `{id, slug}` bzw. nur die ID)."""
    rir = obj.get("rir")
    if isinstance(rir, dict):
        return rir.get("slug") or by_id.get(rir.get("id"))
    return by_id.get(rir) if isinstance(rir, int) else None


def _rir(state: State, number: int) -> tuple[str | None, list[str]]:
    """RIR fuer eine AS-Nummer: die der vorhandenen ASN gleicher Art (privat bzw. oeffentlich),
    sonst die einzige RIR dieser Art bzw. die einzige ueberhaupt; dazu die Kandidaten (fuer den
    Hinweis)."""
    private = boundary.private_as(number)
    rirs = state["rir"]
    by_id = {h.id: slug for slug, h in rirs.items()}
    used = sorted({
        slug for h in state["asn"].values()
        if boundary.private_as(int(h.obj.get("asn") or 0)) == private
        and (slug := _rir_of(h.obj, by_id)) in rirs
    })  # fmt: skip
    if len(used) == 1:
        return used[0], []
    same = sorted(s for s, h in rirs.items() if bool(h.obj.get("is_private")) == private)
    if len(same) == 1 and not used:
        return same[0], []
    if len(rirs) == 1:
        return next(iter(rirs)), []
    return None, used or same or sorted(rirs)


def _ours(state: State, pending: Pending, obj: dict) -> bool:
    """Gehoert das Objekt dem Mandanten des Syncs? (Eine Reservierung eines anderen Mandanten in
    derselben Gruppe bleibt, wie sie ist.)"""
    from app.store.netbox_sync import _tenant_id

    our = state["tenant"].get(pending.tslug)
    return our is not None and _tenant_id(obj) == our.id


def after_read(reader, wants: Wants, state: State, sections) -> None:
    """VLANs in ihrer Gruppe und ASN mit ihrer RIR als Soll - was es schon gibt bzw. ein anderer
    Abschnitt will, nur anzeigen (`Wants.reserved_notes`)."""
    pending: Pending | None = getattr(wants, "reserved", None)
    if SECTION not in (sections or ()) or pending is None:
        return
    from app.store.netbox_sync import Want, _cut

    notes: list[dict] = []  # schon in NetBox: nur angezeigt (Konflikt, nichts geschrieben)
    in_use: list[str] = []  # will ein anderer Abschnitt (gelesen in Gebrauch)
    targets: dict[int, str] = {}  # Index des VLANs -> Gruppe
    ambiguous: dict[str, tuple[str, str, list[str]]] = {}
    missing: set[tuple[str, str]] = set()
    for i, v in enumerate(pending.vlans):
        if v.group:
            if v.group in state["vlan_group"] or wants.has("vlan_group", v.group):
                targets[i] = v.group
            else:
                missing.add((v.reservation, v.group))
            continue
        own = group_slug(pending.tslug, v.site)
        if v.site is None:
            targets[i] = own
            continue
        cands = [g for g in _groups_at(wants, state, v.site) if g != own]
        if len(cands) == 1:
            targets[i] = cands[0]
        elif not cands:
            targets[i] = own
        else:
            ambiguous[v.reservation] = (v.reservation, v.site_name, cands)
    for what, group in sorted(missing):
        wants.warnings.append(
            f"Reservierung {what}: VLAN-Gruppe {group} nicht in NetBox – nicht übernommen."
        )
    for what, site_name, cands in ambiguous.values():
        wants.warnings.append(
            f"Reservierung {what}: mehrere VLAN-Gruppen am Standort {site_name} "
            f"({', '.join(cands)}) – VLAN-Gruppe an der Reservierung wählen; nicht übernommen."
        )
    _read_vlans(reader, state, set(targets.values()))
    for i, group in sorted(targets.items()):
        v = pending.vlans[i]
        key = f"{group}/{v.vid}"
        if wants.has("vlan", key) and (wants.get("vlan", key).fields.get("status") != "reserved"):
            used = wants.get("vlan", key).fields.get("name")
            in_use.append(f"VLAN {v.vid} {used} ({group})")
            continue
        have = state["vlan"].get(key)
        if have is not None and not (owned_vlan(have.obj) and _ours(state, pending, have.obj)):
            changes = [{"field": "name", "old": have.obj.get("name"), "new": v.name},
                       {"field": "status", "old": _value(have.obj.get("status")),
                        "new": "reserved"}]  # fmt: skip
            notes.append({"kind": "vlan", "key": key, "id": have.id, "manual": not have.tagged,
                          "label": f"VLAN {v.vid} {v.name} ({group})", "note": "exists",
                          "reservation": v.reservation, "changes": changes})  # fmt: skip
            continue
        if group == group_slug(pending.tslug, v.site) and not v.group:
            _own_group(wants, state, pending, v)
        wants.add(Want(
            "vlan", key, f"VLAN {v.vid} {v.name} ({group})",
            {"vid": v.vid, "name": v.name, "status": "reserved", "description": v.description},
            {"group": ("vlan_group", group), "tenant": v.tenant}))  # fmt: skip
    no_rir: dict[tuple, list[int]] = defaultdict(list)
    for a in pending.asns:
        key = str(a.asn)
        have = state["asn"].get(key)
        if have is not None and not (owned_asn(have.obj) and _ours(state, pending, have.obj)):
            changes = [{"field": "description", "old": have.obj.get("description"),
                        "new": a.description}]  # fmt: skip
            notes.append({"kind": "asn", "key": key, "id": have.id, "manual": not have.tagged,
                          "label": f"ASN {a.asn}", "note": "exists",
                          "reservation": a.reservation, "changes": changes})  # fmt: skip
            continue
        rir, cands = _rir(state, a.asn)
        if rir is None:
            no_rir[tuple(cands)].append(a.asn)
            continue
        wants.add(Want("asn", key, f"ASN {a.asn} {_cut(a.description[len(MARK):], 60)}",
                       {"asn": a.asn, "description": a.description},
                       {"rir": ("rir", rir), "tenant": a.tenant}))  # fmt: skip
    for cands, asns in no_rir.items():
        wants.warnings.append(
            f"Reservierungen: keine eindeutige RIR in NetBox für AS "
            f"{reservation_store.compact(asns)} ({', '.join(cands) or 'keine RIR'}) – ASN nicht "
            "angelegt; in NetBox eine RIR anlegen bzw. die ASN dieser Art einer RIR zuordnen."
        )
    if notes or in_use:
        shown = [n["label"] for n in notes] + in_use
        wants.warnings.append(
            f"Reservierungen: {len(shown)} Nummern in NetBox schon vorhanden bzw. gelesen in "
            f"Gebrauch – nur angezeigt (z. B. {', '.join(shown[:3])})."
        )
    wants.reserved_notes = notes


def _own_group(wants: Wants, state: State, pending: Pending, v: ReservedVlan) -> None:
    """Eigene VLAN-Gruppe „Reservierungen <Standort>“ (bzw. fuer alle Standorte)."""
    from app.store.netbox_sync import Want, _cut

    slug = group_slug(pending.tslug, v.site)
    if wants.has("vlan_group", slug):
        return
    have = state["vlan_group"].get(slug)
    if have is not None and not owned_group(have.obj):
        return  # gleicher Slug von Hand gepflegt: nur referenziert
    wants.add(Want(
        "vlan_group", slug, f"VLAN-Gruppe Reservierungen {v.site_name}",
        {"name": _cut(f"Reservierungen {v.site_name} ({pending.tenant_name})", 100),
         "slug": slug, "description": _cut(f"{GROUP_MARK} {v.site_name}", 200),
         "scope_type": "dcim.site" if v.site else None},
        {"scope_id": ("site", v.site) if v.site else None}))  # fmt: skip


# -- Vorschau: eigene veraltete Objekte und Anzeige ------------------------------------------------


def _ask(item: dict, wants: Wants) -> bool:
    """Aenderung an einem bestehenden reservierten Objekt dieses Abschnitts (VLAN mit Status
    `reserved`, ASN, Gruppe „Reservierungen …“)?"""
    want = wants.get(item["kind"], item["key"])
    if item["action"] != "update" or want is None:
        return False
    if item["kind"] == "vlan":
        return want.fields.get("status") == "reserved"
    if item["kind"] == "vlan_group":
        return str(want.fields.get("description") or "").startswith(GROUP_MARK)
    return item["kind"] == "asn"


def items(wants: Wants, state: State, tslug: str, allow_delete: bool, chosen,
          current: list[dict] | None = None) -> list[dict]:  # fmt: skip
    """Aktionen des Abschnitts, die `netbox_sync.diff` nicht rechnet: eigene reservierte VLANs
    bzw. Gruppen, die keine Reservierung mehr will (veraltet bzw. loeschen), und die nur
    angezeigten Nummern (Konflikt mit `note`). Aenderungen an bestehenden reservierten Objekten
    (`current`, z. B. neuer Zweck - oder in NetBox von Hand geaendert) bekommen `confirm` wie die
    Kabelnummern und DNS-Namen: die Vorschau zeigt beide Werte, geschrieben wird nur Bestaetigtes
    (`POST …/apply` mit `{"confirm": [...]}`)."""
    if SECTION not in chosen:
        return []
    from app.store.netbox_sync import _item, _tenant_id

    for item in current or []:
        if _ask(item, wants):
            item["confirm"] = True

    our = state["tenant"].get(tslug)
    action = "delete" if allow_delete else "obsolete"
    out: list[dict] = []
    gone: set[str] = set()
    for key, have in sorted(state["vlan"].items()):
        if wants.has("vlan", key) or not owned_vlan(have.obj):
            continue
        if our is None or _tenant_id(have.obj) != our.id:
            continue
        gone.add(key)
        out.append(_item("vlan", key, f"VLAN {key}", action, id=have.id))
    for slug, have in sorted(state["vlan_group"].items()):
        if wants.has("vlan_group", slug) or not owned_group(have.obj):
            continue
        if not slug.startswith(f"{tslug}-reservierungen"):
            continue
        left = [k for k in state["vlan"] if k.startswith(f"{slug}/") and k not in gone]
        if not left:
            out.append(_item("vlan_group", slug, f"VLAN-Gruppe {slug}", action, id=have.id))
    for n in getattr(wants, "reserved_notes", []) or []:
        # nur angezeigt: als Konflikt (nichts geschrieben) mit eigenem Schluessel - dasselbe Objekt
        # kann auch eine Aktion eines anderen Abschnitts haben
        extra = {k: v for k, v in n.items() if k not in ("kind", "key", "label")}
        out.append(_item(n["kind"], f"{n['key']} #reserviert", n["label"], "conflict", **extra))
    return out
