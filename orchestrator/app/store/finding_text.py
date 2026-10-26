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


def _zone_violation(subject: str, d: dict) -> tuple[str, str]:
    all_pairs = d.get("pairs") or []
    denied = [p for p in all_pairs if p.get("intent", "deny") == "deny"]
    limited = [p for p in all_pairs if p.get("intent") == "limited"]
    name = d.get("name") or subject
    on = f" auf {d['source']}" if d.get("source") else ""
    note = f" Absicht: {_list(d.get('notes') or [], 2)}." if d.get("notes") else ""
    parts = []
    if denied:
        pairs = _list([f"{p.get('src')} → {p.get('dst')}" for p in denied], 4)
        parts.append(f"erlaubt Verkehr zwischen Zonen, die laut Zonenmatrix gesperrt sind: {pairs}")
    for p in limited[:3]:  # Absicht je Dienst (0.57.0)
        parts.append(
            f"öffnet zwischen {p.get('src')} → {p.get('dst')} {_list(p.get('outside') or [], 4)} "
            f"– erlaubt sind nur {_list(p.get('services') or [], 4)}"
        )
    return (
        f"Regel {_q(name)}{on} {'; '.join(parts)}.{note}",
        "Regel einschränken bzw. entfernen – oder die Absicht in der Zonenmatrix anpassen "
        "(eingeschränkt bzw. weitere Dienste), wenn der Zugriff gewollt ist.",
    )


def _zone_overlap(subject: str, d: dict) -> tuple[str, str]:
    return (
        f"Das Netz {subject} gehört zu mehreren logischen Zonen: {_list(d.get('zones') or [], 4)}.",
        "Das Netz nur einer Zone zuordnen (Selektor ausschliessen) – sonst zählt jede Regel "
        "dorthin in allen diesen Zonen.",
    )


_STATE = {"off_path": "nicht auf dem Weg", "other": "greift auf dem Weg nicht",
          "decides": "entscheidet", "possible": "möglich", "unknown": "nicht prüfbar"}  # fmt: skip


def _app_mismatch(subject: str, d: dict) -> tuple[str, str]:
    """Abweichung Regel <-> Weg einer Applikation (0.59.0, ADR 0021)."""
    name = d.get("name") or subject
    on = f" auf {d['source']}" if d.get("source") else ""
    conns = d.get("connections") or []
    shown = [
        f"{c.get('src')} → {c.get('dst')}{' ' + c['service'] if c.get('service') else ''} "
        f"({c.get('why') if c.get('state') in ('off_path', 'other') else c.get('path')})"
        for c in conns[:3]
    ]
    count = d.get("count") or len(conns)
    more = f" (+{count - len(shown)} weitere)" if count > len(shown) else ""
    return (
        f"Regel {_q(name)}{on} erlaubt für die Applikation {_q(d.get('application'))} "
        f"{count} Verbindung{'en' if count != 1 else ''}, die der Weg nicht öffnet: "
        f"{'; '.join(shown)}{more}.",
        "In der Applikations-Doku unter „Verbindungen Ende zu Ende“ bzw. in der Weg-Suche den "
        "Weg prüfen: eine andere Regel bzw. ein anderes Gerät sperrt, die Regel liegt auf einem "
        "Gerät ausserhalb des Wegs oder das Routing führt anders. Regel bzw. Routing anpassen – "
        "oder die Regel entfernen, wenn sie nicht gebraucht wird.",
    )


def _end(e: dict | None) -> str:
    e = e or {}
    return f"{e.get('device')} {e.get('port') or '?'}"


def _link(subject: str, d: dict) -> tuple[str, str]:
    """Verkabelung (0.62.0, ADR 0025)."""
    text = d.get("text") or ""
    return (
        f"Verbindung {_end(d.get('a'))} ↔ {_end(d.get('b'))}: {text}.",
        "Konfiguration beider Ports angleichen (Speed/Duplex, Modus, Access- bzw. Native VLAN, "
        "erlaubte VLANs, Port-Channel auf beiden Seiten) – Unterschiede führen zu Fehlern, "
        "Paketverlust bzw. Spanning-Tree-Blockaden.",
    )


def _link_one_sided(subject: str, d: dict) -> tuple[str, str]:
    return (
        f"Verbindung {_end(d.get('a'))} ↔ {_end(d.get('b'))}: {d.get('text') or ''}.",
        "CDP bzw. LLDP auf dem anderen Port einschalten – oder prüfen, ob das Kabel dort steckt, "
        "wo die Gegenseite es meldet.",
    )


def _uplink_vlan(subject: str, d: dict) -> tuple[str, str]:
    """Endpunkte (0.63.0): VLAN der VMs fehlt am Uplink des ESXi-Hosts."""
    groups = d.get("portgroups") or {}
    pgs = sorted({p for names in groups.values() for p in names})
    where = ", ".join(d.get("clusters") or []) or "der VMs"
    allowed = d.get("allowed") or "?"
    return (
        f"{d.get('text') or subject}: VMs im Cluster {where} nutzen "
        f"{', '.join(pgs[:6]) or 'Portgruppen'}{' …' if len(pgs) > 6 else ''}, der Port lässt "
        f"nur {allowed} durch ({'Trunk' if d.get('mode') == 'tagged' else 'Access'}).",
        "VLAN auf dem Port (bzw. Port-Channel) erlauben – sonst verliert eine VM nach vMotion "
        "auf diesen Host bzw. beim Ausfall des anderen Uplinks das Netz. Ist der Port bewusst "
        "nur für einen Teil der VLANs (eigener vSwitch), Befund quittieren.",
    )


_L2_HINT = {
    "l2_single_link": "Zweites Kabel (am besten an einen zweiten Switch bzw. das vPC-Paar) "
    "vorsehen – fällt das Kabel oder ein Port aus, ist das Gerät getrennt.",
    "l2_single_switch": "Kabel auf zwei Switches verteilen (vPC, Stack mit getrennten "
    "Mitgliedern) – fällt der Switch aus, ist das Gerät getrennt.",
    "l2_single_point": "Bewusster Kern ohne Redundanz? Sonst einen zweiten Weg zwischen den "
    "Teilen vorsehen (zweiter Kern-Switch, Querverbindung). In der Verkabelung → Plan den "
    "Ausfall simulieren.",
    "lag_degraded": "Mitglied prüfen (Kabel, SFP, Gegenseite, err-disabled) – der Port-Channel "
    "läuft ohne Reserve und mit weniger Bandbreite.",
    "vpc_mismatch": "vPC auf beiden Peers gleich konfigurieren (gleiche vPC-Nummer am "
    "Port-Channel zum selben Gerät), Konsistenz prüfen (`show vpc consistency-parameters`) bzw. "
    "Peer-Link und Keepalive wiederherstellen – sonst läuft das Gerät nur über einen Peer.",
    "vlan_split": "VLAN auf den Trunks dazwischen erlauben (bzw. bewusst getrennte Netze mit "
    "eigener VLAN-Nummer führen) – sonst erreichen die Geräte der Teile einander und das "
    "Gateway nicht.",
}


def _l2(subject: str, d: dict, ftype: str) -> tuple[str, str]:
    """L2-Sicht (0.64.0)."""
    return f"{d.get('text') or subject}.", _L2_HINT[ftype]


_TOPO_TEXT = {
    "topo_duplicate_ip": _dup_ip,
    "topo_mask_mismatch": _mask,
    "topo_route_loop": _loop,
    "topo_static_unused": _static,
    "topo_bgp_down": _bgp,
    "zone_policy_violation": _zone_violation,
    "zone_overlap": _zone_overlap,
    "app_connection_mismatch": _app_mismatch,
    "link_speed_mismatch": _link,
    "link_duplex_mismatch": _link,
    "link_vlan_mismatch": _link,
    "link_lag_mismatch": _link,
    "link_one_sided": _link_one_sided,
    "uplink_vlan_missing": _uplink_vlan,
    **{t: (lambda s, d, t=t: _l2(s, d, t)) for t in _L2_HINT},
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
    if ftype in ("parallel_policy_route_mismatch", "parallel_nat_mismatch"):
        return _parallel_item(ftype, d, on)
    if ftype == "parallel_route_mismatch":
        return _parallel_routes(d)
    return subject, ""


_ROUTE_KIND = {"differs": "anders", "only_lead": "fehlt", "only_here": "nur hier"}


def _parallel_item(ftype: str, d: dict, on: str) -> tuple[str, str]:
    """Policy-Route/SD-WAN-Regel bzw. VIP/IP-Pool im Verbund (0.72.0)."""
    if ftype == "parallel_nat_mismatch":
        what = "IP-Pool" if d.get("nat_type") == "snat_pool" else "VIP"
        what = f"{what} {_q(d.get('name'))}"
        hint = (
            "VIP bzw. IP-Pool angleichen - sonst wird je nach Firewall anders übersetzt bzw. "
            "der Dienst ist nur über eine Firewall erreichbar."
        )
    else:
        what = d.get("label") or "Policy-Route"
        hint = (
            "Policy-Route bzw. SD-WAN-Regel angleichen - sonst nimmt der Verkehr je nach Firewall "
            "einen anderen Weg."
        )
    group = f"Verbund {d.get('group')}"
    if d.get("missing_on"):
        return f"{what}{on} fehlt auf {_list(d['missing_on'], 6)} ({group}).", hint
    values = d.get("values") or {}
    shown = "; ".join(f"{k}: {v[0]} ≠ {v[1]}" for k, v in list(values.items())[:4] if len(v) == 2)
    return (
        f"{what}{on} weicht von der parallelen Firewall {d.get('other')} ({group}) ab: "
        f"{_list(d.get('fields'), 6)}.{f' {shown}.' if shown else ''}",
        hint,
    )


def _parallel_routes(d: dict) -> tuple[str, str]:
    """Routen eines VDOMs im Verbund (0.72.0): Praefixe mit Art der Abweichung."""
    entries = d.get("entries") or []
    if entries and entries[0].get("table") == "vdom":
        here = entries[0].get("kind") == "only_here"
        return (
            f"VDOM {d.get('vdom')} gibt es nur auf "
            f"{d.get('source') if here else d.get('other')} (Verbund {d.get('group')}) - "
            "sein Routing ist nicht vergleichbar.",
            "VDOM auf beiden Firewalls einrichten bzw. prüfen, ob der Verbund stimmt.",
        )
    items = [f"{e.get('prefix')} ({_ROUTE_KIND.get(e.get('kind'), e.get('kind'))})"
             for e in entries]  # fmt: skip
    count = d.get("count") or len(entries)
    return (
        f"Routing im VDOM {d.get('vdom')} auf {d.get('source')} weicht von der parallelen "
        f"Firewall {d.get('other')} (Verbund {d.get('group')}) ab: {count} "
        f"Präfix{'e' if count != 1 else ''} – {_list(items, 5)}.",
        "Statische Routen bzw. das dynamische Routing angleichen - sonst hängt der Weg davon ab, "
        "über welche Firewall der Verkehr läuft. Next-Hops zählen nicht (je Firewall eigene "
        "Router).",
    )
