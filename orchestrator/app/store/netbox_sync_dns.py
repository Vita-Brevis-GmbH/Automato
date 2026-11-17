"""NetBox-Sync, Abschnitt „DNS-Namen aus dem AD“ (1.19.0, ADR 0045 Nachtrag).

Anwender 2026-11-17 (Prioritaet 7 „DNS aus AD“): der Sync setzt `dns_name` der **bestehenden**
IP-Adressen in NetBox aus den A-/AAAA-Records der gelesenen AD-DNS-Zonen (`store/ad_dns.py`).

- **Keine neuen IP-Adressen.** Zugeordnet wird wie im ganzen Sync ueber die Adresse
  (`netbox_sync._key_of`); steht eine Adresse mehrfach in NetBox (mehrere VRFs), wird nicht
  geraten (Eintrag „mehrdeutig“). IPs, die in NetBox einem anderen Mandanten von Automato
  gehoeren, bleiben unberuehrt (gezaehlt).
- **Name je IP** (`ad_dns.Model.decide`): ein A-Record -> sein Name; mehrere -> der passende PTR
  entscheidet, sonst kein Vorschlag, nur „mehrdeutig“ (`ambiguous`) mit den Kandidaten.
- **Aktion:** `dns_name` in NetBox leer -> `update` (wird uebernommen); abweichend -> `confirm`:
  die Vorschau zeigt beide Werte, geschrieben wird nur, was der Betreiber je Eintrag bestaetigt
  (`confirmed` beim Uebernehmen) - ohne Bestaetigung bleibt NetBox, wie es ist; gleich (ohne
  Gross-/Kleinschreibung) -> `unchanged`.
- **Nur das Feld `dns_name`** (PATCH, `patches`), auch an von Hand gepflegten IP-Adressen - kein
  Tag, kein anderes Feld. Fuer IPs mit A-Record entscheidet dieser Abschnitt allein: das
  `dns_name` der anderen Abschnitte (Netzinventar, Firewall-Objekte) faellt fuer sie weg; legt
  ein anderer Abschnitt die IP in diesem Sync erst an, bekommt sie den Namen gleich mit (ohne
  eindeutigen Namen keinen).
- Nur wenn diese NetBox das fuehrende IPAM ist bzw. keines eingelesen ist (wie Firewall-Objekte):
  sonst gehoeren die DNS-Namen ins fuehrende IPAM (DDI-Spiegel).
- **Keine stillen Grenzen:** A-Records auf IPs, die NetBox nicht kennt, neue IPs ohne
  eindeutigen Namen, fremde Mandanten, nicht gelesene Quellen und Zonen ohne Eintraege stehen als
  Hinweis an der Vorschau.

Gespeichert wird deutsch (ADR 0028); `WARNINGS_EN` uebersetzt die Hinweise bei der Anzeige.
"""

import ipaddress
from typing import Any

from sqlalchemy.orm import Session

from app.models import SourceSystem, Tenant
from app.store import ad_dns, references
from app.store import netbox_sync as nb

SECTION = "dns"
KIND = "dns_name"
FIELD = "dns_name"
_P = "DNS-Namen aus dem AD: "


def _sort(ip: str) -> tuple:
    a = ipaddress.ip_address(ip)
    return (a.version, int(a))


def _when(dt) -> str:
    return dt.strftime("%d.%m.%Y %H:%M") if dt else "?"


def _owner(obj: dict) -> str | None:
    t = obj.get("tenant")
    return t.get("slug") if isinstance(t, dict) else None


def _vrf(obj: dict) -> str | None:
    v = obj.get("vrf")
    return v.get("name") if isinstance(v, dict) else None


def section_items(
    session: Session,
    tenant: Tenant,
    target: SourceSystem,
    wants: nb.Wants,
    state: nb.State,
    tenants: list[str] | None = None,
) -> list[dict]:
    """Eintraege des Abschnitts (nach dem Lesen von NetBox, vor dem Diff): je bestehender IP mit
    A-Record ein Eintrag `dns_name`; Hinweise in `wants.warnings`. Nimmt `dns_name` aus dem Soll
    der anderen Abschnitte fuer diese IPs heraus (der Abschnitt entscheidet allein)."""
    primary = references.context(session, tenant.id).primary
    if primary is not None and primary.source.id != target.id:
        wants.warnings.append(
            f"{_P}führendes IPAM ist „{primary.source.name}“ – die DNS-Namen gehören dorthin,"
            " nicht übernommen."
        )
        return []
    model = ad_dns.Model(session, tenant.id)
    _coverage(wants, model)
    if not model.read:
        return []
    others = set(tenants or []) - {nb.tenant_slug(tenant)}
    several = {key.split(" #", 1)[0] for key in state.get("ip_dup", {})}
    items: list[dict] = []
    unknown: list[str] = []
    later: list[str] = []
    foreign: list[str] = []
    for ip in sorted(model.a, key=_sort):
        have, want = state["ip_address"].get(ip), wants.get("ip_address", ip)
        if have is None and want is None:
            unknown.append(ip)
            continue
        if have is None:  # legt ein anderer Abschnitt an: Name gleich mit, sonst keiner
            d = model.decide(ip)
            name = nb._dns(d.name) if d.name else ""
            if name:
                want.fields[FIELD] = name
            else:
                want.fields.pop(FIELD, None)
                later.append(ip)
            continue
        if _owner(have.obj) in others:
            foreign.append(ip)
            continue
        if want is not None:
            want.fields.pop(FIELD, None)
        items.append(_item(ip, have, model.decide(ip), ip in several))
    if unknown:
        wants.warnings.append(
            f"{_P}{len(unknown)} A-Records zeigen auf IPs, die NetBox nicht kennt – keine neuen"
            f" IP-Adressen (z. B. {nb._examples(unknown)})."
        )
    if later:
        wants.warnings.append(
            f"{_P}{len(later)} IPs legt ein anderer Abschnitt in diesem Sync an – ohne DNS-Namen,"
            f" mehrere A-Records (z. B. {nb._examples(later)})."
        )
    if foreign:
        wants.warnings.append(
            f"{_P}{len(foreign)} IPs gehören in NetBox einem anderen Mandanten – unverändert"
            f" (z. B. {nb._examples(foreign)})."
        )
    return items


def _coverage(wants: nb.Wants, model: ad_dns.Model) -> None:
    """Was nicht geprueft werden kann: keine Quelle, Quelle nicht gelesen bzw. letzter Lauf
    fehlgeschlagen, Zonen ohne Eintraege (leer bzw. erst nach dem letzten Lauf eingetragen)."""
    if not model.sources:
        wants.warnings.append(
            f"{_P}keine AD-DNS-Quelle – Quellsystem „AD-DNS“ mit Zonen anlegen und lesen."
        )
        return
    for s in model.sources:
        zones = ", ".join(s.zones) or "–"
        if not s.read:
            why = "letzter Lauf fehlgeschlagen" if s.failed is not None else "noch nicht gelesen"
            wants.warnings.append(f"{_P}{s.name}: {why} – Zonen nicht geprüft: {zones}.")
            continue
        if s.failed is not None:
            wants.warnings.append(
                f"{_P}letzter Lauf von {s.name} fehlgeschlagen – es gilt der Stand vom"
                f" {_when(s.read_at)}."
            )
        if s.empty:
            wants.warnings.append(
                f"{_P}{s.name}: Zonen ohne Einträge bzw. noch nicht gelesen: {', '.join(s.empty)}."
            )


def _item(ip: str, have: nb.Have, d: ad_dns.Decision, several: bool) -> dict:
    current = str(have.obj.get(FIELD) or "")
    vrf = _vrf(have.obj)
    info: dict[str, Any] = {"names": list(d.names), "ptr": list(d.ptr)}
    if vrf:
        info["vrf"] = vrf
    base = {"kind": KIND, "key": ip, "label": f"DNS-Name {ip}" + (f" (VRF {vrf})" if vrf else ""),
            "id": have.id, "manual": not have.tagged}  # fmt: skip
    name = nb._dns(d.name) if d.name else ""
    have_name = ad_dns.norm_name(current)
    if several or not name:
        if not several and have_name and have_name in d.names:
            return {**base, "action": "unchanged"}  # NetBox hat einen der A-Records
        why = "netbox_several" if several else (d.why or "invalid")
        return {**base, "action": "ambiguous", "dns": {**info, "why": why},
                "changes": [{"field": FIELD, "old": current, "new": None}]}  # fmt: skip
    if have_name == name.lower():
        return {**base, "action": "unchanged"}
    action = "confirm" if current.strip() else "update"
    return {**base, "action": action, "dns": {**info, "by": d.by},
            "changes": [{"field": FIELD, "old": current, "new": name}]}  # fmt: skip


def confirmed(items: list[dict], keys) -> list[dict]:
    """Bestaetigte Abweichungen (Auswahl je Eintrag in der Vorschau) werden zu `update`; nicht
    bestaetigte bleiben `confirm` und werden nicht geschrieben."""
    keys = set(keys or ())
    if not keys:
        return items
    return [
        {**i, "action": "update", "confirmed": True}
        if i["kind"] == KIND and i["action"] == "confirm" and i["key"] in keys
        else i
        for i in items
    ]


def patches(items: list[dict]) -> list[dict]:
    """PATCH je IP nur mit `dns_name` - kein Tag, kein anderes Feld (auch an von Hand gepflegten
    IP-Adressen)."""
    return [{"id": i["id"], FIELD: i["changes"][0]["new"]} for i in items if i.get("id")]


# Hinweise der Vorschau (deutsch gespeichert) -> englisch (ADR 0028, `netbox_sync.show_warning`)
WARNINGS_EN = (
    (
        _P + "führendes IPAM ist „{name}“ – die DNS-Namen gehören dorthin, nicht übernommen.",
        "DNS names from the AD: the primary IPAM is “{name}” – the DNS names belong there, not "
        "applied.",
    ),
    (
        _P + "keine AD-DNS-Quelle – Quellsystem „AD-DNS“ mit Zonen anlegen und lesen.",
        "DNS names from the AD: no AD DNS source – create a source system “AD DNS” with zones and "
        "collect it.",
    ),
    (
        _P + "{src}: letzter Lauf fehlgeschlagen – Zonen nicht geprüft: {zones}.",
        "DNS names from the AD: {src}: last run failed – zones not checked: {zones}.",
    ),
    (
        _P + "{src}: noch nicht gelesen – Zonen nicht geprüft: {zones}.",
        "DNS names from the AD: {src}: not collected yet – zones not checked: {zones}.",
    ),
    (
        _P + "letzter Lauf von {src} fehlgeschlagen – es gilt der Stand vom {when}.",
        "DNS names from the AD: last run of {src} failed – the data of {when} applies.",
    ),
    (
        _P + "{src}: Zonen ohne Einträge bzw. noch nicht gelesen: {zones}.",
        "DNS names from the AD: {src}: zones without entries or not collected yet: {zones}.",
    ),
    (
        _P + "{n} A-Records zeigen auf IPs, die NetBox nicht kennt – keine neuen IP-Adressen "
        "(z. B. {examples}).",
        "DNS names from the AD: {n} A records point to IPs that NetBox does not know – no new IP "
        "addresses (e.g. {examples}).",
    ),
    (
        _P + "{n} IPs legt ein anderer Abschnitt in diesem Sync an – ohne DNS-Namen, mehrere "
        "A-Records (z. B. {examples}).",
        "DNS names from the AD: {n} IPs are created by another section in this sync – without a "
        "DNS name, several A records (e.g. {examples}).",
    ),
    (
        _P + "{n} IPs gehören in NetBox einem anderen Mandanten – unverändert (z. B. {examples}).",
        "DNS names from the AD: {n} IPs belong to another tenant in NetBox – unchanged "
        "(e.g. {examples}).",
    ),
)
