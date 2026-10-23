"""Verstaendliche Erklaerung je Befund (Dev-Befund: "genauer zeigen, was nicht ok ist").

`explain(type, subject, detail)` -> (Befund in einem Satz, Hinweis "was tun"). Reine Funktion
ueber dem gespeicherten Detail - die Pruefung selbst steht in `reconcile`/`references`.
Automato aendert nie Firewall, APIC oder DDI; Hinweise beschreiben, was am Quellsystem zu tun
waere (NetBox nur ueber den NetBox-Sync, ADR 0010).
"""

_CATEGORY = {
    "address": "Adresse",
    "address_group": "Adressgruppe",
    "vip": "VIP",
    "ippool": "IP-Pool",
    "service": "Service",
    "service_group": "Servicegruppe",
}
_CLASS = {
    "host": "Host (einzelne IP)",
    "network": "Netz",
    "vip": "VIP",
    "range": "Bereich",
    "external": "extern",
    "unknown": "unbekannt",
}


def _q(x) -> str:
    return f"„{x}“"


def _list(items, n: int = 5) -> str:
    items = [str(i) for i in items or [] if i]
    more = f" (+{len(items) - n} weitere)" if len(items) > n else ""
    return ", ".join(items[:n]) + more


def _ipam(d: dict) -> str:
    return f"im IPAM ({d['reference']})" if d.get("reference") else "im IPAM"


def _unmatched(subject: str, d: dict) -> tuple[str, str]:
    value = d.get("value")
    if not d.get("reference") and d.get("no_reference"):
        return (
            f"{subject} konnte nicht abgeglichen werden - für den Mandanten ist keine "
            "führende Referenzquelle (DDI/NetBox) eingelesen.",
            "Referenzquelle als führend einrichten und einlesen.",
        )
    if value:
        # Abgleich: eine IP gilt als bekannt, sobald sie in einem Netz des IPAMs liegt
        what = "als Netz" if "/" in value and not value.endswith(("/32", "/128")) else "als IP"
        msg = f"{value} ist {_ipam(d)} weder {what} noch in einem bekannten Netz erfasst."
    elif d.get("ipam_candidates"):
        name = d.get("server_name") or subject
        msg = (
            f"Der Name {_q(name)} ist {_ipam(d)} nicht eindeutig: "
            f"{_list(d['ipam_candidates'], 4)}."
        )
    else:
        name = d.get("server_name") or subject
        msg = f"Für den Namen {_q(name)} gibt es {_ipam(d)} keinen Eintrag."
    if d.get("ipam_elsewhere"):
        other = d["ipam_elsewhere"]
        msg += f" Den Server {other['name']} kennt das IPAM unter {other['value']}."
    if d.get("firewall_objects"):
        on = f" auf {_list(d['sources'], 3)}" if d.get("sources") else ""
        msg += f" Firewall-Objekt {_list(d['firewall_objects'], 3)}{on}."
    if d.get("rules"):
        n = d["rules"]
        msg += f" Verwendet in {n} Regel{'n' if n != 1 else ''} ({_list(d.get('rule_names'), 3)})."
    hint = (
        "IP im IPAM erfassen (Host/A-Record) oder Objekt auf der Firewall prüfen. Ist das Ziel "
        "bewusst extern, das Objekt mit H-EXT_/N-EXT_ benennen - dann entfällt der Befund."
    )
    if d.get("ipam_elsewhere"):
        hint = "IP auf der Firewall bzw. im IPAM angleichen - eine von beiden ist veraltet."
    elif d.get("ipam_candidates"):
        hint = (
            "Objekt mit dem vollen DNS-Namen benennen (z. B. H_name.domain) oder die IP am "
            "Objekt hinterlegen."
        )
    return msg, hint


def _where(nodes: list[dict]) -> str:
    parts = [" ".join(str(n[k]) for k in ("label", "interface", "address") if n.get(k))
             for n in nodes]  # fmt: skip
    return _list(parts, 4)


def _dup_ip(subject: str, d: dict) -> tuple[str, str]:
    return (
        f"Die IP {subject} ist im Netz {d.get('network')} mehrfach vergeben: "
        f"{_where(d.get('nodes') or [])}.",
        "Eine Adresse je Gerät im selben Netz – Konfiguration der Interfaces prüfen (bei "
        "absichtlich gleichen Firewalls: als Verbund erfassen).",
    )


def _mask(subject: str, d: dict) -> tuple[str, str]:
    return (
        f"Im Netz {subject} verwenden die Gegenstellen verschiedene Netzmasken: "
        f"{_where(d.get('nodes') or [])}.",
        "Gleiche Maske auf beiden Seiten setzen – sonst erreicht eine Seite einen Teil der "
        "anderen nicht (Broadcast, Adressen ausserhalb).",
    )


def _loop(subject: str, d: dict) -> tuple[str, str]:
    a, b = (d.get("labels") or ["?", "?"])[:2]
    return (
        f"Routing-Schleife: {a} leitet {d.get('prefix')} über {b}, {b} es über "
        f"{d.get('back')} zurück zu {a}.",
        "Routen beider Seiten prüfen (Default-Route, Filter der BGP-Nachbarn) – Pakete "
        "kreisen sonst bis zum Ablauf der TTL.",
    )


def _static(subject: str, d: dict) -> tuple[str, str]:
    via = _list(d.get("next_hops") or [], 3) or "?"
    return (
        f"Die statische Route {d.get('prefix')} über {via} auf {d.get('label')} ist "
        "eingeschaltet, steht aber nicht in der Routing-Tabelle – und keine andere Route deckt "
        "das Präfix.",
        "Next-Hop bzw. Interface prüfen (nicht erreichbar, Interface aus) – oder die Route "
        "entfernen, wenn sie nicht mehr gebraucht wird.",
    )


def _bgp(subject: str, d: dict) -> tuple[str, str]:
    name = f" ({_list(d.get('peer_names') or [], 1)})" if d.get("peer_names") else ""
    asn = f", AS {d['remote_as']}" if d.get("remote_as") else ""
    return (
        f"BGP-Sitzung von {d.get('label')} zu {d.get('peer')}{name}{asn} ist nicht aufgebaut "
        f"(Zustand {d.get('state') or '?'}).",
        "Nachbar prüfen (erreichbar, AS, Passwort, Filter); Wege über ihn fehlen, Backup-Wege "
        "übernehmen.",
    )


_TOPO_TEXT = {
    "topo_duplicate_ip": _dup_ip,
    "topo_mask_mismatch": _mask,
    "topo_route_loop": _loop,
    "topo_static_unused": _static,
    "topo_bgp_down": _bgp,
}


def explain(ftype: str, subject: str, detail: dict | None) -> tuple[str, str]:
    """(Befund, Hinweis) fuer die Anzeige."""
    d = detail or {}
    vdom = d.get("vdom") if d.get("vdom") not in (None, "root") else None
    on = f" auf {d['source']}{f' (VDOM {vdom})' if vdom else ''}" if d.get("source") else ""
    if ftype == "unmatched_object":
        return _unmatched(subject, d)
    if ftype == "invalid_name":
        known = f" (bekannt: {_list(d['prefixes'], 8)})" if d.get("prefixes") else ""
        what = _CATEGORY.get(d.get("category"), "Objekt")
        hint = (
            "Objekt nach Konvention benennen (z. B. H_ für Hosts, N_ für Netze) oder den Präfix "
            "im Namensprofil ergänzen."
        )
        if d.get("category") == "service":
            hint = (
                "Service nach Konvention benennen: FT_<sprechender Name> (z. B. FT_https) bzw. "
                "FT_<proto><port> (z. B. FT_tcp8443) – oder den Präfix im Namensprofil ergänzen "
                "(Objekt-Präfix → Typ „Service / ACI-Filter“)."
            )
        return (
            f"{what} {_q(subject)}{on} beginnt mit keinem Präfix der Namenskonvention{known}.",
            hint,
        )
    if ftype == "service_mismatch":
        rule = d.get("rule") or "Namensschema"
        return (
            f"Service {_q(subject)}{on} definiert {_list(d.get('values'), 6) or 'nichts'}, laut "
            f"{rule} erwartet: {_list(d.get('expected'), 6)}.",
            "Port/Protokoll am Service oder den Namen korrigieren – der Name soll sagen, was "
            "der Service erlaubt.",
        )
    if ftype == "predefined_service_used":
        rules = d.get("rules") or []
        return (
            f"Standardobjekt {_q(subject)}{on} wird in {len(rules)} erlaubenden "
            f"Regel{'n' if len(rules) != 1 else ''} verwendet ({_list(rules, 4)}) – es folgt "
            "nicht der Namenskonvention.",
            "Stattdessen ein eigenes Objekt nach Konvention verwenden (z. B. FT_https statt "
            "HTTPS); Standardobjekte des Herstellers lassen sich nicht umbenennen.",
        )
    if ftype == "snat_pool_missing":
        pools = _list(d.get("pools") or [], 3)
        where = d.get("source") or "der Firewall"
        vdom = f" (VDOM {d['vdom']})" if d.get("vdom") else ""
        return (
            f"Policy {d.get('external_id') or '?'} {_q(d.get('name') or subject)} übersetzt die "
            f"Quelle auf den IP-Pool {pools}, den {where}{vdom} nicht hat.",
            "Pool auf der Firewall prüfen bzw. neu einlesen; bis dahin ist die Quelle nach dem "
            "NAT unbekannt (Weg-Suche: Regeln danach nur „möglich“).",
        )
    if ftype in _TOPO_TEXT:
        return _TOPO_TEXT[ftype](subject, d)
    if ftype == "epg_assignment_conflict":
        ips = _list(d.get("ips") or [], 4)
        learned = _list(d.get("learned") or [], 3)
        who = (
            f"VM {_q(d['vm'])}"
            if d.get("vm")
            else f"Statischer Endpunkt der EPG {_q(d.get('configured'))}"
        )
        conf = d.get("configured") or "?"
        return (
            f"{who}: {ips} gehört laut {d.get('via') or 'Konfiguration'} zur EPG {conf}, das ACI "
            f"hat sie in {learned} gelernt (VRF {d.get('vrf') or '?'}).",
            "Automato ordnet nach der Konfiguration zu (Portgruppe bzw. statischer Endpunkt). "
            "Prüfen, ob die VM an der richtigen Portgruppe hängt, die IP doppelt vergeben ist "
            "oder der statische Endpunkt veraltet ist.",
        )
    if ftype == "aci_scope_unpaired":
        scope = {"application-profile": "AP", "context": "VRF", "tenant": "Tenant"}.get(
            d.get("scope") or "", d.get("scope") or "?"
        )
        name = d.get("name") or subject
        epgs = _list(d.get("epgs") or [], 5)
        n = d.get("unpaired") or 0
        if not d.get("pairs"):
            what = (f"Contract {_q(name)} (Scope {scope}) verbindet keinen Consumer mit einem "
                    "Provider – sie liegen nicht im selben " + scope)  # fmt: skip
        else:
            what = (f"Contract {_q(name)} (Scope {scope}): {n} EPG{'s' if n != 1 else ''} ohne "
                    f"Gegenstelle im selben {scope} ({epgs})")  # fmt: skip
        reason = f" Beispiel: {d['reason']}." if d.get("reason") else ""
        return (
            what + "." + reason,
            "Für diese EPGs hat der Contract keine Wirkung. Scope prüfen (VRF, Tenant bzw. global "
            "für Verbindungen darüber hinaus – zwischen VRFs zusätzlich geteilte Subnetze bzw. "
            "Route-Leaking) oder die EPG aus dem Contract nehmen.",
        )
    if ftype == "rule_incomplete":
        missing = d.get("missing") or []
        name = d.get("name") or subject
        if d.get("domain") == "aci_contract":
            side = (
                "wird von keiner EPG konsumiert und von keiner bereitgestellt"
                if len(missing) == 2
                else (
                    "wird von keiner EPG konsumiert (kein Consumer)"
                    if "src" in missing
                    else "wird von keiner EPG bereitgestellt (kein Provider)"
                )
            )
            tenant = f" (Tenant {d['tenant']})" if d.get("tenant") else ""
            return (
                f"Contract {_q(name)}{tenant}{on} {side} – er ist nicht wirksam.",
                "Consumer bzw. Provider ergänzen oder den Contract entfernen (am APIC – "
                "Automato liest nur).",
            )
        sides = " und ".join(
            {"src": "keine Quelle", "dst": "kein Ziel"}[m] for m in missing if m in ("src", "dst")
        )
        return (
            f"Policy {_q(name)}{on} hat {sides}, die Automato lesen kann.",
            "Policy auf der Firewall prüfen. Ist sie gültig, nutzt sie ein Feld, das Automato "
            "noch nicht liest – bitte melden.",
        )
    if ftype == "name_type_mismatch":
        expected = _CLASS.get(d.get("expected"), d.get("expected"))
        actual = "ein Netz" if d.get("expected") == "host" else "eine einzelne IP"
        return (
            f"{_q(subject)}{on} ist laut Präfix {expected}, der Wert {d.get('value')} ist aber "
            f"{actual}.",
            "Präfix oder Wert korrigieren (H_ = einzelne IP, N_ = Netz).",
        )
    if ftype == "duplicate_value":
        names = d.get("names") or []
        return (
            f"Der Wert {d.get('value') or subject} ist{on} in {len(names)} Adressobjekten "
            f"definiert: {_list(names, 6)}.",
            "Auf ein Objekt zusammenführen und die Regeln auf dieses umstellen.",
        )
    if ftype == "unused_object":
        what = _CATEGORY.get(d.get("category"), "Objekt")
        return (
            f"{what} {_q(subject)}{on} wird in keiner Regel und keiner Gruppe verwendet.",
            "Prüfen und auf der Firewall entfernen (Bereinigung) - Automato ändert die Firewall "
            "nicht.",
        )
    if ftype == "name_mismatch":
        cmp = d.get("compared") or []
        detail_cmp = (
            f" Verglichen ohne Präfix und Domain: {cmp[0]} ≠ {cmp[1]}." if len(cmp) == 2 else ""
        )
        return (
            f"{_q(subject)}{on} passt nicht zum DNS-Namen {_ipam(d)}: {d.get('value')} heisst dort "
            f"{d.get('dns_name')}.{detail_cmp}",
            "Objekt umbenennen oder den DNS-Eintrag im IPAM korrigieren.",
        )
    if ftype == "subnet_not_in_ipam":
        where = " ".join(
            x
            for x in (
                (d.get("context") or "").upper() or None,
                d.get("context_name"),
                f"EPG {d['epg']}" if d.get("epg") else None,
                f"VRF {d['vrf']}" if d.get("vrf") else None,
                f"VLAN {d['vlan']}" if d.get("vlan") is not None else None,
            )
            if x
        )
        where = f" ({where})" if where else ""
        return (
            f"Das Subnetz {d.get('prefix') or subject}{where} ist{on} konfiguriert, {_ipam(d)} "
            "aber nicht als Netz erfasst.",
            "Netz im IPAM anlegen - bei NetBox als führender Quelle über den NetBox-Sync.",
        )
    if ftype == "vlan_mismatch":
        return (
            f"{d.get('prefix') or subject}: VLAN {d.get('vlan')}{on}, {_ipam(d)} VLAN "
            f"{d.get('ipam_vlan')}.",
            "VLAN im IPAM oder auf dem Gerät korrigieren.",
        )
    if ftype == "ambiguous_ip":
        return (
            f"{subject} ist {_ipam(d)} mehrfach mit unterschiedlichen Einträgen vorhanden.",
            "Doppelte Einträge im IPAM bereinigen.",
        )
    if ftype == "epg_no_endpoints":
        return (
            f"EPG {_q(subject)}{on} hat keine gelernten Endpunkte.",
            "Prüfen, ob die EPG noch gebraucht wird.",
        )
    if ftype == "vm_not_in_ipam":
        ips = d.get("ips") or []
        what = "Die IP" if len(ips) == 1 else "Die IPs"
        return (
            f"VM {_q(subject)}{on}: {what} {_list(ips, 6)} {'ist' if len(ips) == 1 else 'sind'} "
            f"{_ipam(d)} nicht erfasst.",
            "IP im IPAM anlegen (Name wie die VM bzw. ihr Hostname) oder die VM-Konfiguration "
            "prüfen.",
        )
    if ftype == "portgroup_no_epg":
        path = "/".join(x for x in (d.get("tenant"), d.get("ap"), d.get("epg")) if x)
        vms = d.get("vms") or 0
        return (
            f"Portgruppe {_q(subject)}{on} heisst nach ACI-Schema {path}, diese EPG ist aber "
            f"nicht eingelesen ({vms} VM{'s' if vms != 1 else ''} daran).",
            "Tenant im APIC-Quellsystem mitlesen bzw. prüfen, ob die EPG noch existiert.",
        )
    if ftype == "portgroup_name_mismatch":
        path = "/".join(x for x in (d.get("tenant"), d.get("ap"), d.get("epg")) if x)
        vms = d.get("vms") or 0
        return (
            f"Portgruppe {_q(subject)}{on} heisst nach ACI-Schema {path}, ist über VLAN "
            f"{d.get('vlan')} aber der EPG {d.get('assigned')} zugeordnet – der Name passt nicht "
            f"({vms} VM{'s' if vms != 1 else ''} daran).",
            "Portgruppe bzw. EPG umbenennen oder das VLAN prüfen; bis dahin gilt die Zuordnung "
            "über das VLAN.",
        )
    if ftype == "parallel_rule_mismatch":
        values = d.get("values") or {}
        shown = "; ".join(
            f"{k}: {v[0]} ≠ {v[1]}" for k, v in list(values.items())[:4] if len(v) == 2
        )
        return (
            f"Policy {_q(d.get('name') or subject)} (ID {d.get('external_id')}){on} weicht von "
            f"der parallelen Firewall {d.get('other')} (Verbund {d.get('group')}) ab: "
            f"{_list(d.get('fields'), 6)}.{f' {shown}.' if shown else ''}",
            "Parallele Firewalls sollen dieselben Regeln haben - die Policy angleichen, sonst "
            "hängt der Zugriff davon ab, über welche Firewall der Verkehr läuft.",
        )
    if ftype == "parallel_rule_missing":
        return (
            f"Policy {_q(d.get('name') or subject)} (ID {d.get('external_id')}){on} fehlt auf "
            f"{_list(d.get('missing_on'), 6)} (Verbund {d.get('group')}).",
            "Policy auf der parallelen Firewall ergänzen bzw. entfernen - sonst gilt sie nur "
            "auf einem Weg.",
        )
    if ftype == "parallel_object_mismatch":
        values = d.get("values") or ["–", "–"]
        what = _CATEGORY.get(d.get("category"), "Objekt")
        return (
            f"{what} {_q(subject)}{on} ist auf der parallelen Firewall {d.get('other')} "
            f"(Verbund {d.get('group')}) anders definiert: {values[0]} ≠ {values[1]}.",
            "Objekt angleichen - gleichnamige Objekte in Regeln paralleler Firewalls müssen "
            "dasselbe bedeuten.",
        )
    return subject, ""
