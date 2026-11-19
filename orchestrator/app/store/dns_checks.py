"""Befunde zu DNS aus dem AD (1.19.0, ADR 0045 Nachtrag): Pruefungen
`app/checks/analyse/dns_*.py` (ADR 0039) und ihre Texte (`finding_text.explain`).

- **PTR widerspricht A-Record** (`dns_ptr_mismatch`, je IP): der PTR einer IP nennt einen Namen,
  der nicht unter ihren A-Records steht - Warnung, wenn die IP andere A-Records hat bzw. der Name
  per A-Record auf eine andere IP zeigt, sonst Hinweis (PTR auf einen Namen ohne A-Record in den
  gelesenen Zonen, bzw. ein weiterer PTR passt). Liegt der Name ausserhalb der gelesenen Zonen,
  ist das nicht pruefbar (`dns_unchecked`).
- **A-Record auf eine IP, die NetBox nicht kennt** (`dns_ip_unknown`, je AD-DNS-Quelle, Hinweis):
  nur gezaehlt (Anzahl, Beispiele, Anzahl je Zone) - der NetBox-Sync legt keine IP an.
- **Nicht pruefbar** (`dns_unchecked`, je AD-DNS-Quelle, Hinweis): Quelle nicht gelesen bzw. ihr
  letzter Lauf fehlgeschlagen, Zonen ohne Eintraege, A-Records ohne gelesene Rueckwaertszone
  (PTR nicht pruefbar, davon mit mehreren A-Records: der Sync entscheidet nicht), PTR auf Namen
  ausserhalb der gelesenen Zonen, NetBox nicht gelesen (keine stillen Grenzen, ADR 0048).

Gespeichert wird nur Struktur (IPs, Namen, Zaehler); die Saetze entstehen beim Abruf in der
Sprache der Anfrage. Nur lesend.
"""

import ipaddress
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import FindingType, Platform, Severity, SourceSystem, SubjectKind
from app.store import ad_dns, references
from app.store.reconcile import Issue

KEYS = ("dns_ptr", "dns_unbekannt", "dns_ungeprueft")
_LIST = 20  # Beispiele je Befund im Detail


def model(session: Session, tenant_id: int, cx=None) -> ad_dns.Model:
    """Ein Modell je Durchgang (alle Pruefungen zu DNS teilen es)."""
    if cx is None:
        return ad_dns.Model(session, tenant_id)
    found = cx.extra.get("ad_dns")
    if found is None or found.tenant_id != tenant_id:
        found = cx.extra["ad_dns"] = ad_dns.Model(session, tenant_id)
    return found


def _sort(ip: str) -> tuple:
    a = ipaddress.ip_address(ip)
    return (a.version, int(a))


def _net(ip: str) -> str:
    """Netz fuer Beispiele: /24 bzw. /64."""
    a = ipaddress.ip_address(ip)
    return str(ipaddress.ip_network(f"{ip}/{24 if a.version == 4 else 64}", strict=False))


def netbox(session: Session, tenant_id: int) -> tuple[set[str] | None, list[str], list[str]]:
    """(IPs der gelesenen NetBox-Quellen, ihre Namen, Namen der nicht gelesenen) - IPs `None`,
    wenn der Mandant keine gelesene NetBox hat."""
    sources = session.scalars(
        select(SourceSystem)
        .where(
            SourceSystem.tenant_id == tenant_id,
            SourceSystem.platform == Platform.NETBOX,
            SourceSystem.enabled.is_(True),
        )
        .order_by(SourceSystem.id)
    ).all()
    read = [s for s in sources if s.reference_run_id]
    unread = [s.name for s in sources if not s.reference_run_id]
    if not read:
        return None, [], unread
    ips: set[str] = set()
    for s in read:
        ips |= set(references.load_index(session, s).by_ip)
    return ips, [s.name for s in read], unread


# -- PTR gegen A-Record -------------------------------------------------------------------------


def ptr_issues(session: Session, tenant_id: int, cx=None) -> list[Issue]:
    m = model(session, tenant_id, cx)
    names = {s.id: s.name for s in m.sources}
    out: list[Issue] = []
    for ip in sorted(m.ptr, key=_sort):
        ptrs, a = m.ptr[ip], m.a.get(ip, set())
        bad = sorted(p for p in ptrs if p not in a)
        if not bad:
            continue
        elsewhere = {p: sorted(m.hosts[p], key=_sort)[:5] for p in bad if m.hosts.get(p)}
        missing = [p for p in bad if p not in elsewhere and m.forward_read(p)]
        if not elsewhere and not missing:
            continue  # nur Namen ausserhalb der gelesenen Zonen: `dns_unchecked`
        matched = bool(ptrs & a)
        severity = Severity.WARNING if not matched and (a or elsewhere) else Severity.INFO
        sid = min(m.ptr_sources[ip])
        detail = {"ip": ip, "ptr": sorted(ptrs), "a": sorted(a), "elsewhere": elsewhere,
                  "missing": missing, "source": names.get(sid)}  # fmt: skip
        out.append(
            Issue(FindingType.DNS_PTR_MISMATCH, SubjectKind.SOURCE, ip, sid, severity, detail)
        )
    return out


# -- A-Records auf IPs, die NetBox nicht kennt --------------------------------------------------


def unknown_issues(session: Session, tenant_id: int, cx=None) -> list[Issue]:
    m = model(session, tenant_id, cx)
    if not m.read:
        return []
    known, nb_names, _ = netbox(session, tenant_id)
    if known is None:
        return []  # keine NetBox gelesen: `dns_unchecked`
    out: list[Issue] = []
    for s in m.sources:
        if not s.read:
            continue
        ips = sorted((ip for ip, sids in m.a_sources.items() if s.id in sids and ip not in known),
                     key=_sort)  # fmt: skip
        if not ips:
            continue
        zones = Counter(m.zone_of.get(ip, "") for ip in ips)
        detail = {"count": len(ips), "examples": ips[:_LIST], "netbox": nb_names,
                  "zones": dict(zones.most_common(10)), "source": s.name}  # fmt: skip
        out.append(
            Issue(
                FindingType.DNS_IP_UNKNOWN, SubjectKind.SOURCE, s.name, s.id, Severity.INFO, detail
            )
        )
    return out


# -- Nicht pruefbar -----------------------------------------------------------------------------


def unchecked_issues(session: Session, tenant_id: int, cx=None) -> list[Issue]:
    m = model(session, tenant_id, cx)
    if not m.sources:
        return []
    _, _, nb_unread = netbox(session, tenant_id)
    out: list[Issue] = []
    for s in m.sources:
        d: dict = {"source": s.name}
        if not s.read:
            d["state"] = "failed" if s.failed is not None else "never"
            d["zones"] = s.zones
        elif s.failed is not None:
            d["state"] = "stale"
        if s.failed:
            d["message"] = s.failed
        if s.empty:
            d["empty"] = s.empty
        if s.read:
            ips = [ip for ip, sids in m.a_sources.items() if s.id in sids]
            no_rev = sorted((ip for ip in ips if not m.reverse_read(ip)), key=_sort)
            if no_rev:
                nets = list(dict.fromkeys(_net(ip) for ip in no_rev))
                d["no_reverse"] = len(no_rev)
                d["no_reverse_nets"] = nets[:_LIST]
                d["no_reverse_several"] = sum(1 for ip in no_rev if len(m.a.get(ip, ())) > 1)
            outside = sorted({p for ip, sids in m.ptr_sources.items() if s.id in sids
                              for p in m.ptr[ip] - m.a.get(ip, set())
                              if not m.hosts.get(p) and not m.forward_read(p)})  # fmt: skip
            if outside:
                d["ptr_outside"] = len(outside)
                d["ptr_outside_examples"] = outside[:_LIST]
        if nb_unread:
            d["netbox_unread"] = nb_unread
        if len(d) > 1:
            out.append(
                Issue(FindingType.DNS_UNCHECKED, SubjectKind.SOURCE, s.name, s.id, Severity.INFO, d)
            )
    return out


# -- Texte (Sprache der Anfrage) ----------------------------------------------------------------


def _list(values, n: int = 5) -> str:
    values = [str(v) for v in values or []]
    return ", ".join(values[:n]) + (" …" if len(values) > n else "")


def explain(ftype: str, subject: str, d: dict) -> tuple[str, str]:
    if ftype == "dns_ptr_mismatch":
        return _ptr(subject, d)
    if ftype == "dns_ip_unknown":
        return _unknown(subject, d)
    if ftype == "dns_unchecked":
        return _unchecked(subject, d)
    return subject, ""


def _ptr(subject: str, d: dict) -> tuple[str, str]:
    ip = d.get("ip") or subject
    parts = [L(f"Der PTR von {ip} zeigt auf {_list(d.get('ptr'))}",
               f"The PTR of {ip} points to {_list(d.get('ptr'))}")]  # fmt: skip
    if d.get("a"):
        parts.append(
            L(
                f"die A-Records der IP nennen {_list(d['a'])}",
                f"the A records of the IP name {_list(d['a'])}",
            )
        )
    for name, ips in (d.get("elsewhere") or {}).items():
        parts.append(
            L(
                f"{name} zeigt per A-Record auf {_list(ips)}",
                f"{name} points to {_list(ips)} by A record",
            )
        )
    if d.get("missing"):
        parts.append(
            L(
                f"{_list(d['missing'])} ohne A-Record in den gelesenen Zonen",
                f"{_list(d['missing'])} without an A record in the collected zones",
            )
        )
    hint = L(
        "PTR in der Reverse-Zone bzw. A-Record im AD-DNS korrigieren (DNS-Manager) – ein "
        "veralteter PTR führt Werkzeuge in die Irre, die per Rückwärtsauflösung benennen. Der "
        "NetBox-Sync nimmt den Namen des A-Records; bei mehreren A-Records entscheidet der PTR.",
        "Correct the PTR in the reverse zone or the A record in the AD DNS (DNS Manager) – a "
        "stale PTR misleads tools that name by reverse lookup. The NetBox sync takes the name of "
        "the A record; with several A records the PTR decides.",
    )
    return "; ".join(parts) + ".", hint


def _unknown(subject: str, d: dict) -> tuple[str, str]:
    n, src = d.get("count") or 0, d.get("source") or subject
    nb = _list(d.get("netbox")) or "NetBox"
    zones = d.get("zones") or {}
    per_zone = ", ".join(f"{z or '?'}: {c}" for z, c in list(zones.items())[:5])
    at = L(f" (je Zone: {per_zone})", f" (per zone: {per_zone})") if per_zone else ""
    text = L(
        f"{n} A-Records von {src} zeigen auf IPs, die NetBox ({nb}) nicht kennt{at}, z. B. "
        f"{_list(d.get('examples'))}.",
        f"{n} A records of {src} point to IPs that NetBox ({nb}) does not know{at}, e.g. "
        f"{_list(d.get('examples'))}.",
    )
    hint = L(
        "Hinweis, nur gezählt: der NetBox-Sync legt keine IP-Adressen an (Abschnitt „DNS-Namen "
        "aus dem AD“ setzt nur den Namen bestehender IPs). Gehört die IP ins IPAM, in NetBox "
        "erfassen; veraltete Einträge im AD-DNS entfernen; extern bzw. bewusst nicht im IPAM: "
        "kein Fehler.",
        "Note, only counted: the NetBox sync does not create IP addresses (section “DNS names "
        "from the AD” only sets the name of existing IPs). If the IP belongs in the IPAM, add it "
        "in NetBox; remove stale entries in the AD DNS; external or deliberately not in the "
        "IPAM: not an error.",
    )
    return text, hint


def _unchecked(subject: str, d: dict) -> tuple[str, str]:
    src = d.get("source") or subject
    why: list[str] = []
    state = d.get("state")
    msg = f" ({d['message'][:160]})" if d.get("message") else ""
    if state == "never":
        why.append(
            L(
                f"noch nicht gelesen – Zonen {_list(d.get('zones'))} nicht geprüft",
                f"not collected yet – zones {_list(d.get('zones'))} not checked",
            )
        )
    elif state == "failed":
        why.append(
            L(
                f"letzter Lauf fehlgeschlagen{msg} – Zonen {_list(d.get('zones'))} nicht "
                "geprüft",
                f"last run failed{msg} – zones {_list(d.get('zones'))} not checked",
            )
        )
    elif state == "stale":
        why.append(
            L(
                f"letzter Lauf fehlgeschlagen{msg} – es gilt der letzte gelesene Stand",
                f"last run failed{msg} – the last collected data applies",
            )
        )
    if d.get("empty"):
        why.append(
            L(
                f"Zonen ohne Einträge bzw. noch nicht gelesen: {_list(d['empty'])}",
                f"zones without entries or not collected yet: {_list(d['empty'])}",
            )
        )
    if d.get("no_reverse"):
        several = d.get("no_reverse_several") or 0
        more = L(f", davon {several} mit mehreren A-Records (der NetBox-Sync entscheidet dort "
                 "nicht)",
                 f", of which {several} with several A records (the NetBox sync does not decide "
                 "there)") if several else ""  # fmt: skip
        nets = _list(d.get("no_reverse_nets"), 4)
        why.append(
            L(
                f"{d['no_reverse']} A-Records ohne gelesene Rückwärtszone – PTR nicht "
                f"prüfbar (z. B. {nets}){more}",
                f"{d['no_reverse']} A records without a collected reverse zone – PTR not "
                f"checkable (e.g. {nets}){more}",
            )
        )
    if d.get("ptr_outside"):
        ex = _list(d.get("ptr_outside_examples"), 3)
        why.append(
            L(
                f"{d['ptr_outside']} PTR auf Namen ausserhalb der gelesenen Zonen (z. B. " f"{ex})",
                f"{d['ptr_outside']} PTR to names outside the collected zones (e.g. " f"{ex})",
            )
        )
    if d.get("netbox_unread"):
        why.append(
            L(
                f"NetBox {_list(d['netbox_unread'])} nicht gelesen – IPs, die NetBox nicht "
                "kennt, nicht prüfbar",
                f"NetBox {_list(d['netbox_unread'])} not collected – IPs that NetBox does "
                "not know not checkable",
            )
        )
    text = L(f"DNS aus dem AD ({src}) nicht ganz geprüft: ", f"DNS from the AD ({src}) not fully "
             "checked: ") + "; ".join(why) + "."  # fmt: skip
    hint = L(
        "Grenze der Prüfung, kein Fehler: Quelle lesen (Läufe); Zonentransfer auch für die "
        "Reverse-Zonen freigeben und sie am Quellsystem eintragen (je Zeile, z. B. "
        "20.10.in-addr.arpa); Zonen der PTR-Ziele mitlesen; NetBox als Referenzquelle lesen.",
        "Limit of the check, not an error: collect the source (runs); allow the zone transfer "
        "also for the reverse zones and enter them on the source system (one per line, e.g. "
        "20.10.in-addr.arpa); also collect the zones of the PTR targets; collect NetBox as a "
        "reference source.",
    )
    return text, hint
