"""Verstaendliche Erklaerung je Befund (Dev-Befund: "genauer zeigen, was nicht ok ist").

`explain(type, subject, detail)` -> (Befund in einem Satz, Hinweis "was tun"). Reine Funktion
ueber dem gespeicherten Detail - die Pruefung selbst steht in `reconcile`/`references`.
Automato aendert nie Firewall, APIC oder DDI; Hinweise beschreiben, was am Quellsystem zu tun
waere (NetBox nur ueber den NetBox-Sync, ADR 0010).

Deutsch/Englisch (0.87.0, ADR 0028): Satz und Hinweis entstehen beim Abruf in der Sprache der
Anfrage (`L`); feste Tabellen halten Paare (de, en). Werte aus dem gespeicherten Detail
(Begruendungen, `text`, Kategorien, Namen) bleiben, wie sie gespeichert sind.
"""

from app.i18n import L

_CATEGORY = {
    "address": ("Adresse", "Address"),
    "address_group": ("Adressgruppe", "Address group"),
    "vip": ("VIP", "VIP"),
    "ippool": ("IP-Pool", "IP-Pool"),
    "service": ("Service", "Service"),
    "service_group": ("Servicegruppe", "Service group"),
}
_CLASS = {  # Englisch mit Artikel: "is a network according to its prefix"
    "host": ("Host (einzelne IP)", "a host (single IP)"),
    "network": ("Netz", "a network"),
    "vip": ("VIP", "a VIP"),
    "range": ("Bereich", "a range"),
    "external": ("extern", "external"),
    "unknown": ("unbekannt", "unknown"),
}


def _pick(table: dict, key, default=None):
    """Eintrag (de, en) einer Tabelle in der Sprache der Anfrage; unbekannt = `default`."""
    pair = table.get(key)
    return L(*pair) if pair else default


def _category(d: dict) -> str:
    return _pick(_CATEGORY, d.get("category")) or L("Objekt", "Object")


def _q(x) -> str:
    return L(f"„{x}“", f"“{x}”")


def _list(items, n: int = 5) -> str:
    items = [str(i) for i in items or [] if i]
    rest = len(items) - n
    more = L(f" (+{rest} weitere)", f" (+{rest} more)") if rest > 0 else ""
    return ", ".join(items[:n]) + more


def _ipam(d: dict) -> str:
    ref = d.get("reference")
    return L(f"im IPAM ({ref})", f"in the IPAM ({ref})") if ref else L("im IPAM", "in the IPAM")


def _unmatched(subject: str, d: dict) -> tuple[str, str]:
    value = d.get("value")
    if not d.get("reference") and d.get("no_reference"):
        return (
            L(
                f"{subject} konnte nicht abgeglichen werden - für den Mandanten ist keine "
                "führende Referenzquelle (DDI/NetBox) eingelesen.",
                f"{subject} could not be reconciled - no primary reference source (DDI/NetBox) "
                "has been collected for the tenant.",
            ),
            L(
                "Referenzquelle als führend einrichten und einlesen.",
                "Set up a reference source as primary and collect it.",
            ),
        )
    ipam = _ipam(d)
    if value:
        # Abgleich: eine IP gilt als bekannt, sobald sie in einem Netz des IPAMs liegt
        net = "/" in value and not value.endswith(("/32", "/128"))
        what = L("als Netz", "as a network") if net else L("als IP", "as an IP")
        msg = L(
            f"{value} ist {ipam} weder {what} noch in einem bekannten Netz erfasst.",
            f"{value} is not recorded {ipam}, neither {what} nor within a known network.",
        )
    elif d.get("ipam_candidates"):
        name = d.get("server_name") or subject
        found = _list(d["ipam_candidates"], 4)
        msg = L(
            f"Der Name {_q(name)} ist {ipam} nicht eindeutig: {found}.",
            f"The name {_q(name)} is ambiguous {ipam}: {found}.",
        )
    else:
        name = d.get("server_name") or subject
        msg = L(
            f"Für den Namen {_q(name)} gibt es {ipam} keinen Eintrag.",
            f"There is no entry {ipam} for the name {_q(name)}.",
        )
    if d.get("ipam_elsewhere"):
        other = d["ipam_elsewhere"]
        msg += L(
            f" Den Server {other['name']} kennt das IPAM unter {other['value']}.",
            f" The IPAM knows the server {other['name']} as {other['value']}.",
        )
    if d.get("firewall_objects"):
        sources = _list(d["sources"], 3) if d.get("sources") else ""
        on = L(f" auf {sources}", f" on {sources}") if d.get("sources") else ""
        objs = _list(d["firewall_objects"], 3)
        msg += L(f" Firewall-Objekt {objs}{on}.", f" Firewall object {objs}{on}.")
    if d.get("rules"):
        n = d["rules"]
        names = _list(d.get("rule_names"), 3)
        msg += L(
            f" Verwendet in {n} Regel{'n' if n != 1 else ''} ({names}).",
            f" Used in {n} rule{'s' if n != 1 else ''} ({names}).",
        )
    hint = L(
        "IP im IPAM erfassen (Host/A-Record) oder Objekt auf der Firewall prüfen. Ist das Ziel "
        "bewusst extern, das Objekt mit H-EXT_/N-EXT_ benennen - dann entfällt der Befund.",
        "Record the IP in the IPAM (host/A record) or check the object on the firewall. If the "
        "destination is intentionally external, name the object H-EXT_/N-EXT_ - then the "
        "finding no longer applies.",
    )
    if d.get("ipam_elsewhere"):
        hint = L(
            "IP auf der Firewall bzw. im IPAM angleichen - eine von beiden ist veraltet.",
            "Align the IP on the firewall or in the IPAM - one of the two is outdated.",
        )
    elif d.get("ipam_candidates"):
        hint = L(
            "Objekt mit dem vollen DNS-Namen benennen (z. B. H_name.domain) oder die IP am "
            "Objekt hinterlegen.",
            "Name the object with the full DNS name (e.g. H_name.domain) or set the IP on the "
            "object.",
        )
    return msg, hint


def _where(nodes: list[dict]) -> str:
    parts = [" ".join(str(n[k]) for k in ("label", "interface", "address") if n.get(k))
             for n in nodes]  # fmt: skip
    return _list(parts, 4)


def _dup_ip(subject: str, d: dict) -> tuple[str, str]:
    net, where = d.get("network"), _where(d.get("nodes") or [])
    return (
        L(
            f"Die IP {subject} ist im Netz {net} mehrfach vergeben: {where}.",
            f"The IP {subject} is assigned more than once in the network {net}: {where}.",
        ),
        L(
            "Eine Adresse je Gerät im selben Netz – Konfiguration der Interfaces prüfen (bei "
            "absichtlich gleichen Firewalls: als Verbund erfassen).",
            "One address per device in the same network – check the interface configuration "
            "(for intentionally identical firewalls: record them as a parallel group).",
        ),
    )


def _mask(subject: str, d: dict) -> tuple[str, str]:
    where = _where(d.get("nodes") or [])
    return (
        L(
            f"Im Netz {subject} verwenden die Gegenstellen verschiedene Netzmasken: {where}.",
            f"In the network {subject} the peers use different netmasks: {where}.",
        ),
        L(
            "Gleiche Maske auf beiden Seiten setzen – sonst erreicht eine Seite einen Teil der "
            "anderen nicht (Broadcast, Adressen ausserhalb).",
            "Set the same mask on both sides – otherwise one side cannot reach part of the other "
            "(broadcast, addresses outside).",
        ),
    )


def _loop(subject: str, d: dict) -> tuple[str, str]:
    a, b = (d.get("labels") or ["?", "?"])[:2]
    prefix, back = d.get("prefix"), d.get("back")
    return (
        L(
            f"Routing-Schleife: {a} leitet {prefix} über {b}, {b} es über {back} zurück zu {a}.",
            f"Routing loop: {a} routes {prefix} via {b}, {b} routes it via {back} back to {a}.",
        ),
        L(
            "Routen beider Seiten prüfen (Default-Route, Filter der BGP-Nachbarn) – Pakete "
            "kreisen sonst bis zum Ablauf der TTL.",
            "Check the routes on both sides (default route, filters of the BGP neighbors) – "
            "otherwise packets circle until the TTL expires.",
        ),
    )


def _static(subject: str, d: dict) -> tuple[str, str]:
    via = _list(d.get("next_hops") or [], 3) or "?"
    prefix, label = d.get("prefix"), d.get("label")
    return (
        L(
            f"Die statische Route {prefix} über {via} auf {label} ist eingeschaltet, steht aber "
            "nicht in der Routing-Tabelle – und keine andere Route deckt das Präfix.",
            f"The static route {prefix} via {via} on {label} is enabled but not in the routing "
            "table – and no other route covers the prefix.",
        ),
        L(
            "Next-Hop bzw. Interface prüfen (nicht erreichbar, Interface aus) – oder die Route "
            "entfernen, wenn sie nicht mehr gebraucht wird.",
            "Check the Next-Hop or interface (unreachable, interface down) – or remove the route "
            "if it is no longer needed.",
        ),
    )


def _bgp(subject: str, d: dict) -> tuple[str, str]:
    name = f" ({_list(d.get('peer_names') or [], 1)})" if d.get("peer_names") else ""
    asn = f", AS {d['remote_as']}" if d.get("remote_as") else ""
    label, peer, state = d.get("label"), d.get("peer"), d.get("state") or "?"
    return (
        L(
            f"BGP-Sitzung von {label} zu {peer}{name}{asn} ist nicht aufgebaut "
            f"(Zustand {state}).",
            f"BGP session from {label} to {peer}{name}{asn} is not established (state {state}).",
        ),
        L(
            "Nachbar prüfen (erreichbar, AS, Passwort, Filter); Wege über ihn fehlen, "
            "Backup-Wege übernehmen.",
            "Check the neighbor (reachable, AS, password, filters); paths via it are missing, "
            "backup paths take over.",
        ),
    )


def _zone_violation(subject: str, d: dict) -> tuple[str, str]:
    all_pairs = d.get("pairs") or []
    denied = [p for p in all_pairs if p.get("intent", "deny") == "deny"]
    limited = [p for p in all_pairs if p.get("intent") == "limited"]
    name = d.get("name") or subject
    on = L(f" auf {d['source']}", f" on {d['source']}") if d.get("source") else ""
    notes = _list(d.get("notes") or [], 2)
    note = L(f" Absicht: {notes}.", f" Intent: {notes}.") if d.get("notes") else ""
    parts = []
    if denied:
        pairs = _list([f"{p.get('src')} → {p.get('dst')}" for p in denied], 4)
        parts.append(
            L(
                f"erlaubt Verkehr zwischen Zonen, die laut Zonenmatrix gesperrt sind: {pairs}",
                f"allows traffic between zones that are blocked according to the zone matrix: "
                f"{pairs}",
            )
        )
    for p in limited[:3]:  # Absicht je Dienst (0.57.0)
        way = f"{p.get('src')} → {p.get('dst')}"
        outside = _list(p.get("outside") or [], 4)
        services = _list(p.get("services") or [], 4)
        parts.append(
            L(
                f"öffnet zwischen {way} {outside} – erlaubt sind nur {services}",
                f"opens {outside} between {way} – only {services} allowed",
            )
        )
    text = "; ".join(parts)
    return (
        L(f"Regel {_q(name)}{on} {text}.{note}", f"Rule {_q(name)}{on} {text}.{note}"),
        L(
            "Regel einschränken bzw. entfernen – oder die Absicht in der Zonenmatrix anpassen "
            "(eingeschränkt bzw. weitere Dienste), wenn der Zugriff gewollt ist.",
            "Restrict or remove the rule – or adjust the intent in the zone matrix (limited or "
            "further services) if the access is intended.",
        ),
    )


def _zone_overlap(subject: str, d: dict) -> tuple[str, str]:
    zones = _list(d.get("zones") or [], 4)
    return (
        L(
            f"Das Netz {subject} gehört zu mehreren logischen Zonen: {zones}.",
            f"The network {subject} belongs to several logical zones: {zones}.",
        ),
        L(
            "Das Netz nur einer Zone zuordnen (Selektor ausschliessen) – sonst zählt jede Regel "
            "dorthin in allen diesen Zonen.",
            "Assign the network to one zone only (exclude it via a selector) – otherwise every "
            "rule to it counts in all these zones.",
        ),
    )


def _zone_net_exit(subject: str, d: dict) -> tuple[str, str]:
    """Netz einer Zone nicht ueber den Uebergang Richtung Zone gelernt (0.105.0, seit 0.106.0 aus
    den Geraeten der Zonen und dem Zonenplan, ADR 0035)."""
    fw = d.get("source") or L("die Firewall", "the firewall")
    ctx = f" (VDOM {d['context']})" if d.get("context") and d["context"] != "root" else ""
    net, zone = d.get("net"), d.get("zone")
    expected = ", ".join(d.get("expected") or [])
    toward = ", ".join(d.get("toward") or [])
    via = ", ".join(d.get("via") or [])
    others = ", ".join(d.get("others") or [])
    if d.get("missing"):
        how = L("gar nicht", "not at all")
    elif d.get("default"):
        how = L(f"nur über die Default-Route ({via})", f"only via the default route ({via})")
    else:
        how = L(f"über {via}", f"via {via}")
    tail = L(f" – {via} führt zur Zone {others}", f" – {via} leads to zone {others}") \
        if others and not d.get("missing") else ""  # fmt: skip
    return (
        L(
            f"{fw}{ctx} lernt das Netz {net} der Zone {zone} {how}, erwartet über {expected} "
            f"Richtung {toward}{tail}.",
            f"{fw}{ctx} learns the network {net} of zone {zone} {how}, expected via {expected} "
            f"towards {toward}{tail}.",
        ),
        L(
            "Routing prüfen bzw. Netze oder Geräte der Zonen anpassen (Netzwerkdesign → "
            "Kategorien).",
            "Check the routing or adjust the networks or devices of the zones (Network design → "
            "Categories).",
        ),
    )


def _zone_outside_schema(subject: str, d: dict) -> tuple[str, str]:
    profile = d.get("profile")
    return (
        L(
            f"Die Zone {subject} passt zu keiner Zone des Grundschemas „{profile}“.",
            f"The zone {subject} matches no zone of the base schema “{profile}”.",
        ),
        L(
            "Zone umbenennen bzw. Kurzname setzen, das Grundschema um die Zone ergänzen "
            "(Namensprofil) oder den Befund quittieren, wenn sie bewusst ausserhalb steht.",
            "Rename the zone or set a short name, add the zone to the base schema (naming "
            "profile) or acknowledge the finding if it is intentionally outside.",
        ),
    )


_STATE = {"off_path": ("nicht auf dem Weg", "not on the path"),
          "other": ("greift auf dem Weg nicht", "does not apply on the path"),
          "decides": ("entscheidet", "decides"), "possible": ("möglich", "possible"),
          "unknown": ("nicht prüfbar", "not verifiable")}  # fmt: skip


_VERDICT = {"allow": ("erlaubt", "allowed"), "deny": ("gesperrt", "blocked"),
            "implicit_deny": ("gesperrt", "blocked")}  # fmt: skip


def _variant(v: dict) -> str:
    via = v.get("via")
    verdict = _pick(_VERDICT, v.get("verdict"), v.get("verdict"))
    if v.get("kind") == "backup":
        return L(f"Backup über {via} {verdict}", f"{verdict} via backup {via}")
    return L(f"über {via} {verdict}", f"{verdict} via {via}")


def _app_narrower(subject: str, d: dict) -> tuple[str, str]:
    """Contract Richtung L3Out enger als die Firewall-Regeln (0.78.2)."""
    name = d.get("name") or subject
    tenant = f" ({d['tenant']})" if d.get("tenant") else ""
    missing = d.get("missing") or []
    shown = ", ".join(missing[:5]) + (f" (+{len(missing) - 5})" if len(missing) > 5 else "")
    fws = [_q(f.get("name")) + (L(f" auf {f['source']}", f" on {f['source']}")
                                if f.get("source") else "")
           for f in d.get("firewall_rules") or []]  # fmt: skip
    via = f" ({', '.join(fws[:3])}{' …' if len(fws) > 3 else ''})" if fws else ""
    systems = d.get("systems") or []
    names = f"{', '.join(systems[:3])}{' …' if len(systems) > 3 else ''}"
    who = L(f" für {names}", f" for {names}") if systems else ""
    cats = "; ".join(d.get("categories") or [])
    app = _q(d.get("application"))
    return (
        L(
            f"Contract {_q(name)}{tenant} der Applikation {app} öffnet{who} weniger als die "
            f"Firewall-Regeln{via}: es fehlt {shown or '–'}"
            f"{f' – Kategorie {cats}' if cats else ''}.",
            f"Contract {_q(name)}{tenant} of the application {app} opens less{who} than the "
            f"firewall rules{via}: missing {shown or '–'}"
            f"{f' – category {cats}' if cats else ''}.",
        ),
        L(
            "Die Firewall ist führend: was sie erlaubt, sperrt der Contract zum L3Out im ACI. Den "
            "Contract um die fehlenden Dienste ergänzen bzw. die Firewall-Regel einschränken "
            "(Applikations-Doku, Kategorien; „Verbindungen Ende zu Ende“ zeigt den Weg).",
            "The firewall is leading: what it allows, the contract to the L3Out blocks in the "
            "ACI. Add the missing services to the contract or restrict the firewall rule "
            "(application documentation, categories; “End-to-end connections” shows the path).",
        ),
    )


def _app_unused(subject: str, d: dict) -> tuple[str, str]:
    """Regel nie gebraucht (0.107.0): ihr Geraet liegt nicht auf dem Weg der Verbindungen."""
    name = d.get("name") or subject
    on = L(f" auf {d['source']}", f" on {d['source']}") if d.get("source") else ""
    conns = d.get("connections") or []
    shown = [f"{c.get('src')} → {c.get('dst')}{' ' + c['service'] if c.get('service') else ''}"
             f" ({L('Weg', 'path')}: {c.get('path') or '–'})" for c in conns[:3]]  # fmt: skip
    count = d.get("count") or len(conns)
    rest = count - len(shown)
    more = L(f" (+{rest} weitere)", f" (+{rest} more)") if rest > 0 else ""
    app, listed = _q(d.get("application")), "; ".join(shown)
    device = d.get("source") or L("das Gerät", "the device")
    return (
        L(
            f"Regel {_q(name)}{on} wird nie gebraucht: der Verkehr von {count} "
            f"Verbindung{'en' if count != 1 else ''} der Applikation {app} geht nicht über "
            f"{device}: {listed}{more}.",
            f"Rule {_q(name)}{on} is never needed: the traffic of {count} "
            f"connection{'s' if count != 1 else ''} of the application {app} does not pass "
            f"{device}: {listed}{more}.",
        ),
        L(
            "Nach dem Routing nimmt der Verkehr einen anderen Weg – die Regel greift dafür nie. "
            "Regel prüfen und entfernen, falls sie nichts anderes braucht, bzw. das Routing "
            "prüfen, wenn der Verkehr über dieses Gerät gehen soll (Weg-Suche). Die Doku nach "
            "Kategorien zeigt die Regel weiter, die Verbindungen Ende zu Ende nicht.",
            "According to the routing the traffic takes another path – the rule never applies to "
            "it. Check the rule and remove it if nothing else needs it, or check the routing if "
            "the traffic should pass this device (path search). The documentation by category "
            "still shows the rule, the end-to-end connections do not.",
        ),
    )


def _app_mismatch(subject: str, d: dict) -> tuple[str, str]:
    """Wege uneinheitlich (0.73.0, ADR 0021): Verbindungen einer Applikation, die über einen
    Weg offen und über einen anderen gesperrt sind. Befunde bis 0.72 (Regel <-> Weg) ohne
    Wege im Detail werden beim nächsten Abgleich ersetzt."""
    name = d.get("name") or subject
    on = L(f" auf {d['source']}", f" on {d['source']}") if d.get("source") else ""
    conns = d.get("connections") or []
    shown = []
    for c in conns[:3]:
        ways = [v for v in c.get("variants") or [] if v.get("verdict") in _VERDICT]
        text = "; ".join(_variant(v) for v in ways[:3]) or c.get("path") or ""
        shown.append(f"{c.get('src')} → {c.get('dst')}"
                     f"{' ' + c['service'] if c.get('service') else ''} ({text})")  # fmt: skip
    count = d.get("count") or len(conns)
    rest = count - len(shown)
    more = L(f" (+{rest} weitere)", f" (+{rest} more)") if rest > 0 else ""
    app, listed = _q(d.get("application")), "; ".join(shown)
    return (
        L(
            f"Regel {_q(name)}{on}: {count} Verbindung{'en' if count != 1 else ''} der "
            f"Applikation {app} {'sind' if count != 1 else 'ist'} je nach Weg offen bzw. "
            f"gesperrt: {listed}{more}.",
            f"Rule {_q(name)}{on}: {count} connection{'s' if count != 1 else ''} of the "
            f"application {app} {'are' if count != 1 else 'is'} open or blocked depending on "
            f"the path: {listed}{more}.",
        ),
        L(
            "Die Wege (aktiv, ECMP, Backup bzw. die Firewalls eines Verbunds) urteilen "
            "verschieden – je nach Lastverteilung bzw. bei einem Ausfall ist die Verbindung "
            "unterbrochen. Regeln auf dem anderen Weg angleichen (Applikations-Doku "
            "„Verbindungen Ende zu Ende“, Weg-Suche).",
            "The paths (active, ECMP, backup or the firewalls of a parallel group) give "
            "different verdicts – depending on load balancing or on a failure the connection is "
            "interrupted. Align the rules on the other path (application documentation "
            "“End-to-end connections”, path search).",
        ),
    )


def _end(e: dict | None) -> str:
    e = e or {}
    return f"{e.get('device')} {e.get('port') or '?'}"


def _link_text(d: dict) -> str:
    # `text` stammt aus dem gespeicherten Detail und bleibt unveraendert
    ends, text = f"{_end(d.get('a'))} ↔ {_end(d.get('b'))}", d.get("text") or ""
    return L(f"Verbindung {ends}: {text}.", f"Link {ends}: {text}.")


def _link(subject: str, d: dict) -> tuple[str, str]:
    """Verkabelung (0.62.0, ADR 0025)."""
    return (
        _link_text(d),
        L(
            "Konfiguration beider Ports angleichen (Speed/Duplex, Modus, Access- bzw. Native "
            "VLAN, erlaubte VLANs, Port-Channel auf beiden Seiten) – Unterschiede führen zu "
            "Fehlern, Paketverlust bzw. Spanning-Tree-Blockaden.",
            "Align the configuration of both ports (speed/duplex, mode, access or native VLAN, "
            "allowed VLANs, Port-Channel on both sides) – differences lead to errors, packet "
            "loss or Spanning Tree blocking.",
        ),
    )


def _link_one_sided(subject: str, d: dict) -> tuple[str, str]:
    return (
        _link_text(d),
        L(
            "CDP bzw. LLDP auf dem anderen Port einschalten – oder prüfen, ob das Kabel dort "
            "steckt, wo die Gegenseite es meldet.",
            "Enable CDP or LLDP on the other port – or check whether the cable is plugged in "
            "where the other side reports it.",
        ),
    )


def _uplink_vlan(subject: str, d: dict) -> tuple[str, str]:
    """Endpunkte (0.63.0): VLAN der VMs fehlt am Uplink des ESXi-Hosts."""
    groups = d.get("portgroups") or {}
    pgs = sorted({p for names in groups.values() for p in names})
    clusters = ", ".join(d.get("clusters") or [])
    where = clusters or L("der VMs", "of the VMs")
    allowed = d.get("allowed") or "?"
    used = ", ".join(pgs[:6]) or L("Portgruppen", "port groups")
    used += " …" if len(pgs) > 6 else ""
    trunk = d.get("mode") == "tagged"
    head = d.get("text") or subject
    return (
        L(
            f"{head}: VMs im Cluster {where} nutzen {used}, der Port lässt nur {allowed} durch "
            f"({'Trunk' if trunk else 'Access'}).",
            f"{head}: VMs in the cluster {where} use {used}, the port only passes {allowed} "
            f"({'trunk' if trunk else 'access'}).",
        ),
        L(
            "VLAN auf dem Port (bzw. Port-Channel) erlauben – sonst verliert eine VM nach vMotion "
            "auf diesen Host bzw. beim Ausfall des anderen Uplinks das Netz. Ist der Port bewusst "
            "nur für einen Teil der VLANs (eigener vSwitch), Befund quittieren.",
            "Allow the VLAN on the port (or Port-Channel) – otherwise a VM loses the network "
            "after vMotion to this host or when the other uplink fails. If the port is "
            "intentionally only for part of the VLANs (separate vSwitch), acknowledge the "
            "finding.",
        ),
    )


_L2_HINT = {
    "l2_single_link": (
        "Zweites Kabel (am besten an einen zweiten Switch bzw. das vPC-Paar) vorsehen – fällt das "
        "Kabel oder ein Port aus, ist das Gerät getrennt.",
        "Provide a second cable (ideally to a second switch or the vPC pair) – if the cable or a "
        "port fails, the device is cut off.",
    ),
    "l2_single_switch": (
        "Kabel auf zwei Switches verteilen (vPC, Stack mit getrennten Mitgliedern) – fällt der "
        "Switch aus, ist das Gerät getrennt.",
        "Spread the cables over two switches (vPC, stack with separate members) – if the switch "
        "fails, the device is cut off.",
    ),
    "l2_single_point": (
        "Bewusster Kern ohne Redundanz? Sonst einen zweiten Weg zwischen den Teilen vorsehen "
        "(zweiter Kern-Switch, Querverbindung). In der Verkabelung → Plan den Ausfall simulieren.",
        "Intended core without redundancy? Otherwise provide a second path between the parts "
        "(second core switch, cross-connection). Simulate the failure in Cabling → Plan.",
    ),
    "lag_degraded": (
        "Mitglied prüfen (Kabel, SFP, Gegenseite, err-disabled) – der Port-Channel läuft ohne "
        "Reserve und mit weniger Bandbreite.",
        "Check the member (cable, SFP, other side, err-disabled) – the Port-Channel runs without "
        "reserve and with less bandwidth.",
    ),
    "vpc_mismatch": (
        "vPC auf beiden Peers gleich konfigurieren (gleiche vPC-Nummer am Port-Channel zum selben "
        "Gerät), Konsistenz prüfen (`show vpc consistency-parameters`) bzw. Peer-Link und "
        "Keepalive wiederherstellen – sonst läuft das Gerät nur über einen Peer.",
        "Configure the vPC identically on both peers (same vPC number on the Port-Channel to the "
        "same device), check the consistency (`show vpc consistency-parameters`) or restore the "
        "peer link and keepalive – otherwise the device only runs via one peer.",
    ),
    "vlan_split": (
        "VLAN auf den Trunks dazwischen erlauben (bzw. bewusst getrennte Netze mit eigener "
        "VLAN-Nummer führen) – sonst erreichen die Geräte der Teile einander und das Gateway "
        "nicht.",
        "Allow the VLAN on the trunks in between (or run intentionally separate networks with "
        "their own VLAN number) – otherwise the devices of the parts cannot reach each other and "
        "the gateway.",
    ),
}


def _l2(subject: str, d: dict, ftype: str) -> tuple[str, str]:
    """L2-Sicht (0.64.0)."""
    return f"{d.get('text') or subject}.", L(*_L2_HINT[ftype])


def _net_wider(subject: str, d: dict) -> tuple[str, str]:
    """Netz der Regel groesser als die Routen (0.111.0, ADR 0040)."""
    value, device = d.get("value") or subject, d.get("source") or L("dem Gerät", "the device")
    via = d.get("route") or "?"
    parts = d.get("parts") or []
    total = d.get("parts_total") or len(parts)
    rules = d.get("rules") or []
    used = (
        L(
            f", verwendet in {len(rules)} Regel{'n' if len(rules) != 1 else ''}",
            f", used in {len(rules)} rule{'s' if len(rules) != 1 else ''}",
        )
        if rules
        else ""
    )
    if total:
        shown = ", ".join(parts[:4]) + (L(" …", " …") if total > 4 else "")
        what = L(f"{total} Teile darin sind genauer geroutet ({shown}), der Rest geht nur über "
                 f"{via}",
                 f"{total} parts of it are routed more specifically ({shown}), the rest only via "
                 f"{via}")  # fmt: skip
    else:
        what = L(f"es geht nur über {via}", f"it only goes via {via}")
    return (
        L(
            f"{device} routet {value} nicht als Ganzes{used}: {what}.",
            f"{device} does not route {value} as a whole{used}: {what}.",
        ),
        L(
            "Unklar, wohin das Netz der Regel gehört: von Hand einer Zone bzw. Anbindung "
            "zuordnen, „nur die Teile“ oder „Weg nach dem Routing stimmt“ wählen – oder das Netz "
            "in einer Zone bzw. bei den Netzen einer Anbindung eintragen.",
            "Unclear where the network of the rule belongs: assign it to a zone or exit by hand, "
            "choose “only the parts” or “path by the routing is right” – or enter the network in "
            "a zone or in the networks of an exit.",
        ),
    )


def _bgp_where(d: dict) -> str:
    """Nachbar bzw. L3Out eines BGP-Befunds (0.113.0)."""
    if d.get("l3out"):
        return L(f"L3Out {d['l3out']} (VRF {d.get('vrf') or '?'})",
                 f"L3Out {d['l3out']} (VRF {d.get('vrf') or '?'})")  # fmt: skip
    asn = f", AS {d['remote_as']}" if d.get("remote_as") else ""
    return L(f"{d.get('label')} vom Nachbarn {d.get('peer')}{asn}",
             f"{d.get('label')} from neighbor {d.get('peer')}{asn}")  # fmt: skip


def _bgp_missing(subject: str, d: dict) -> tuple[str, str]:
    """Filter erlaubt Netze, der Nachbar liefert nichts davon (0.113.0, ADR 0041)."""
    where, nets = _bgp_where(d), _list(d.get("entries") or [], 5)
    total = d.get("total") or len(d.get("entries") or [])
    de_n, en_n = f"Netz{'e' if total != 1 else ''}", f"network{'s' if total != 1 else ''}"
    via = _list(d.get("filters") or [], 3)
    if d.get("l3out"):
        text = L(
            f"{where}: Import Route Control erwartet {total} {de_n}, über die Peers kommt nichts "
            f"davon an ({nets}).",
            f"{where}: import route control expects {total} {en_n}, none of them arrives via the "
            f"peers ({nets}).",
        )
    else:
        text = L(
            f"{where}: der Filter {via} erlaubt {total} {de_n}, angekommen ist nichts davon "
            f"({nets}).",
            f"{where}: the filter {via} permits {total} {en_n}, none of them arrived ({nets}).",
        )
    return (
        text,
        L(
            "Gegenstelle prüfen (kündigt sie die Netze an, eigener Ausgangsfilter) bzw. den "
            "Filter bereinigen; Wege über diesen Nachbarn zu diesen Netzen gibt es nicht.",
            "Check the peer (does it announce the networks, its own outbound filter) or clean up "
            "the filter; there are no paths to these networks via this neighbor.",
        ),
    )


def _bgp_outside(subject: str, d: dict) -> tuple[str, str]:
    """Erhalten, obwohl der Filter sperrt (0.113.0)."""
    where, nets = _bgp_where(d), _list(d.get("prefixes") or [], 5)
    total = d.get("total") or 0
    de_n, en_n = f"Netz{'e' if total != 1 else ''}", f"network{'s' if total != 1 else ''}"
    via = _list(d.get("filters") or [], 3)
    return (
        L(
            f"{where}: {total} erhaltene {de_n} sperrt der Filter {via} nach der gelesenen "
            f"Konfiguration ({nets}).",
            f"{where}: the filter {via} denies {total} received {en_n} according to the "
            f"configuration read ({nets}).",
        ),
        L(
            "Konfiguration und Routing-Tabelle passen nicht zusammen: Filter seit dem Lauf "
            "geändert (Soft-Reset ausstehend) bzw. Bedingung, die die Plattform anders liest.",
            "Configuration and routing table do not match: filter changed since the run (soft "
            "reset pending) or a condition the platform reads differently.",
        ),
    )


def _bgp_open(subject: str, d: dict) -> tuple[str, str]:
    """eBGP-Nachbar ohne Eingangsfilter (0.113.0)."""
    where, n = _bgp_where(d), d.get("received") or 0
    de_n, en_n = f"Netz{'e' if n != 1 else ''}", f"network{'s' if n != 1 else ''}"
    return (
        L(
            f"{where}: eBGP ohne Eingangsfilter – die Firewall übernimmt alles, was der Nachbar "
            f"ankündigt ({n} {de_n}).",
            f"{where}: eBGP without inbound filter – the firewall accepts everything the neighbor "
            f"announces ({n} {en_n}).",
        ),
        L(
            "Prefix-Liste bzw. Route-Map für die erwarteten Netze setzen; ohne Filter entstehen "
            "Wege, die niemand geplant hat.",
            "Set a prefix list or route map for the expected networks; without a filter, paths "
            "nobody planned come about.",
        ),
    )


def _aci_import(subject: str, d: dict) -> tuple[str, str]:
    """import-rtctrl ohne enforce import (0.113.0)."""
    where, nets = _bgp_where(d), _list(d.get("entries") or [], 5)
    return (
        L(
            f"{where}: Subnetze mit Import Route Control ({nets}), am L3Out ist Import aber nicht "
            "erzwungen – sie wirken nicht, der L3Out übernimmt alle Routen.",
            f"{where}: subnets with import route control ({nets}), but import is not enforced on "
            "the L3Out – they have no effect, the L3Out accepts all routes.",
        ),
        L(
            "Am L3Out „Route Control Enforcement: Import“ setzen bzw. die import-rtctrl-Subnetze "
            "entfernen, wenn alles übernommen werden soll.",
            "Set “Route Control Enforcement: Import” on the L3Out or remove the import-rtctrl "
            "subnets if everything is to be accepted.",
        ),
    )


def _bgp_zones(subject: str, d: dict) -> tuple[str, str]:
    """Eingangsfilter passt nicht zu den Zonen dahinter (0.114.0, ADR 0041 Stufe 2)."""
    where, zones = _bgp_where(d), _list(d.get("zones") or [], 4)
    parts_de, parts_en = [], []
    if d.get("open"):
        parts_de.append("kein Eingangsfilter")
        parts_en.append("no inbound filter")
    if d.get("missing"):
        nets = _list(d["missing"], 4)
        parts_de.append(f"gesperrt: {nets}")
        parts_en.append(f"denied: {nets}")
    if d.get("too_open"):
        nets = _list(d["too_open"], 4)
        parts_de.append(f"ausserhalb der Zonen erlaubt: {nets}")
        parts_en.append(f"permitted outside the zones: {nets}")
    if d.get("excluded_allowed"):
        nets = _list(d["excluded_allowed"], 4)
        parts_de.append(f"Ausschluss erlaubt: {nets}")
        parts_en.append(f"exclusion permitted: {nets}")
    return (
        L(
            f"{where}: der Eingangsfilter passt nicht zu den Zonen dahinter ({zones}) – "
            f"{'; '.join(parts_de)}.",
            f"{where}: the inbound filter does not match the zones behind ({zones}) – "
            f"{'; '.join(parts_en)}.",
        ),
        L(
            "Routing → BGP-Nachbarn → Soll/Ist → „Soll aus den Zonen“: Prefix-Liste als "
            "CLI-Vorschlag prüfen und auf der Firewall übernehmen bzw. die Zonen im "
            "Netzwerkdesign anpassen.",
            "Routing → BGP neighbors → expected/actual → “target from the zones”: review the "
            "prefix list proposal (CLI) and apply it on the firewall, or adjust the zones in the "
            "network design.",
        ),
    )


def _bgp_zones_out(subject: str, d: dict) -> tuple[str, str]:
    """Ausgangsfilter passt nicht zu den Zonen diesseits (0.116.0, ADR 0041 Stufe 3)."""
    where = L(f"{d.get('label')} zum Nachbarn {d.get('peer')}",
              f"{d.get('label')} to neighbor {d.get('peer')}")  # fmt: skip
    zones = _list(d.get("zones") or [], 4)
    parts_de, parts_en = [], []
    if d.get("open"):
        parts_de.append("kein Ausgangsfilter")
        parts_en.append("no outbound filter")
    if d.get("missing"):
        nets = _list(d["missing"], 4)
        parts_de.append(f"gesperrt: {nets}")
        parts_en.append(f"denied: {nets}")
    if d.get("announced_outside"):
        nets = _list(d["announced_outside"], 4)
        n = d.get("announced_outside_total") or len(d["announced_outside"])
        parts_de.append(f"{n} Routen ausserhalb der Zonen angekündigt: {nets}")
        parts_en.append(f"{n} routes announced outside the zones: {nets}")
    if d.get("too_open"):
        nets = _list(d["too_open"], 4)
        parts_de.append(f"Filter erlaubt ausserhalb der Zonen: {nets}")
        parts_en.append(f"filter permits outside the zones: {nets}")
    if d.get("excluded_allowed"):
        nets = _list(d["excluded_allowed"], 4)
        parts_de.append(f"Ausschluss erlaubt: {nets}")
        parts_en.append(f"exclusion permitted: {nets}")
    public = ""
    if d.get("public_only"):
        public = L(" (nur öffentliche Netze, Nachbar ausserhalb)",
                   " (public networks only, neighbor outside)")  # fmt: skip
    return (
        L(
            f"{where}: der Ausgangsfilter passt nicht zu den Zonen diesseits ({zones}){public} – "
            f"{'; '.join(parts_de)}.",
            f"{where}: the outbound filter does not match the zones on this side ({zones}){public}"
            f" – {'; '.join(parts_en)}.",
        ),
        L(
            "BGP-Nachbarn öffnen → „Soll aus den Zonen“, Richtung Ausgang: Prefix-Liste als "
            "Vorschlag (FortiOS-CLI) übernehmen bzw. die Zonen im Netzwerkdesign anpassen.",
            "Open the BGP neighbor → “Target from the zones”, direction outbound: apply the prefix "
            "list proposal (FortiOS CLI) or adjust the zones in the network design.",
        ),
    )


def _aci_import_zones(subject: str, d: dict) -> tuple[str, str]:
    """ACI Import Route Control passt nicht zu den Zonen dahinter (0.116.0)."""
    where, zones = _bgp_where(d), _list(d.get("zones") or [], 4)
    parts_de, parts_en = [], []
    if d.get("not_enforced"):
        parts_de.append("Import nicht erzwungen (der L3Out übernimmt alle Routen)")
        parts_en.append("import not enforced (the L3Out accepts all routes)")
    if d.get("missing"):
        nets = _list(d["missing"], 4)
        parts_de.append(f"ohne Import Route Control: {nets}")
        parts_en.append(f"without import route control: {nets}")
    if d.get("too_open"):
        nets = _list(d["too_open"], 4)
        parts_de.append(f"importiert ausserhalb der Zonen: {nets}")
        parts_en.append(f"imported outside the zones: {nets}")
    if d.get("excluded_allowed"):
        nets = _list(d["excluded_allowed"], 4)
        parts_de.append(f"Ausschluss importiert: {nets}")
        parts_en.append(f"exclusion imported: {nets}")
    return (
        L(
            f"{where}: Import Route Control passt nicht zu den Zonen dahinter ({zones}) – "
            f"{'; '.join(parts_de)}.",
            f"{where}: import route control does not match the zones behind ({zones}) – "
            f"{'; '.join(parts_en)}.",
        ),
        L(
            "L3Out öffnen (Routing → L3Outs) → „Soll aus den Zonen“: Subnetze mit „Import Route "
            "Control Subnet“ an der externen EPG als Vorschlag, Enforcement „Import“ setzen.",
            "Open the L3Out (routing → L3Outs) → “Target from the zones”: subnets with “Import "
            "Route Control Subnet” on the external EPG as proposal, set enforcement “import”.",
        ),
    )


def _netbox_sync(subject: str, d: dict) -> tuple[str, str]:
    """Nachkontrolle des NetBox-Syncs (0.115.0, ADR 0042): geschrieben != gelesen."""
    nb = d.get("netbox") or "NetBox"
    what = L(d.get("label") or subject, d.get("label_en") or d.get("label") or subject)
    problem = d.get("problem")
    if problem == "missing":
        text = L(
            f"{nb}: {what} angelegt, steht nach dem Schreiben aber nicht in NetBox.",
            f"{nb}: {what} created, but not in NetBox after writing.",
        )
    elif problem == "still_there":
        text = L(
            f"{nb}: {what} gelöscht, steht nach dem Schreiben aber noch in NetBox.",
            f"{nb}: {what} deleted, but still in NetBox after writing.",
        )
    else:
        changes = d.get("changes") or []
        fields = _list([f"{c.get('field')}: {c.get('old')} → {c.get('new')}" for c in changes], 3)
        text = L(
            f"{nb}: {what} geschrieben, NetBox zeigt danach andere Werte ({fields}).",
            f"{nb}: {what} written, NetBox shows other values afterwards ({fields}).",
        )
    hint = L(
        "Neue Vorschau erstellen und erneut übernehmen. Bleibt die Abweichung, ändert in NetBox "
        "etwas den Wert nach dem Schreiben (Custom Validator, Event Rule, Skript) oder ein Recht "
        "des Tokens fehlt.",
        "Create a new preview and apply again. If the deviation remains, something in NetBox "
        "changes the value after writing (custom validator, event rule, script) or the token "
        "lacks a permission.",
    )
    return text, hint


_TOPO_TEXT = {
    "netbox_sync_mismatch": _netbox_sync,
    "bgp_zone_out_mismatch": _bgp_zones_out,
    "aci_import_zone_mismatch": _aci_import_zones,
    "bgp_zone_mismatch": _bgp_zones,
    "bgp_expected_missing": _bgp_missing,
    "bgp_outside_filter": _bgp_outside,
    "bgp_no_inbound_filter": _bgp_open,
    "aci_import_not_enforced": _aci_import,
    "net_wider_than_routes": _net_wider,
    "topo_duplicate_ip": _dup_ip,
    "topo_mask_mismatch": _mask,
    "topo_route_loop": _loop,
    "topo_static_unused": _static,
    "topo_bgp_down": _bgp,
    "zone_policy_violation": _zone_violation,
    "zone_overlap": _zone_overlap,
    "zone_outside_schema": _zone_outside_schema,
    "zone_net_exit": _zone_net_exit,
    "app_connection_mismatch": _app_mismatch,
    "app_contract_narrower": _app_narrower,
    "app_rule_unused": _app_unused,
    "link_speed_mismatch": _link,
    "link_duplex_mismatch": _link,
    "link_vlan_mismatch": _link,
    "link_lag_mismatch": _link,
    "link_one_sided": _link_one_sided,
    "uplink_vlan_missing": _uplink_vlan,
    **{t: (lambda s, d, t=t: _l2(s, d, t)) for t in _L2_HINT},
}


def explain(ftype: str, subject: str, detail: dict | None) -> tuple[str, str]:
    """(Befund, Hinweis) fuer die Anzeige - in der Sprache der Anfrage."""
    d = detail or {}
    vdom = d.get("vdom") if d.get("vdom") not in (None, "root") else None
    on = ""
    if d.get("source"):
        in_vdom = f" (VDOM {vdom})" if vdom else ""
        on = L(f" auf {d['source']}{in_vdom}", f" on {d['source']}{in_vdom}")
    if ftype == "unmatched_object":
        return _unmatched(subject, d)
    if ftype == "manual":
        return _manual(subject, d)
    if ftype in _TOPO_TEXT:
        return _TOPO_TEXT[ftype](subject, d)
    if ftype in _OBJECT_TEXT:
        return _OBJECT_TEXT[ftype](subject, d, on)
    if ftype in _ACI_TEXT:
        return _ACI_TEXT[ftype](subject, d, on)
    if ftype in _IPAM_TEXT:
        return _IPAM_TEXT[ftype](subject, d, on)
    if ftype in _PARALLEL_TEXT:
        return _PARALLEL_TEXT[ftype](subject, d, on)
    if ftype in _SOURCE_TEXT:
        return _SOURCE_TEXT[ftype](subject, d)
    return subject, ""


def _manual(subject: str, d: dict) -> tuple[str, str]:
    """Von Hand erfasst (0.109.0): Titel und Text so, wie sie eingegeben wurden; dazu der Weg."""
    parts = [d.get("text") or ""]
    if d.get("src") or d.get("dst"):
        svc = f" ({d['service']})" if d.get("service") else ""
        parts.append(L(f"Weg: {d.get('src') or '?'} → {d.get('dst') or '?'}{svc}",
                       f"Path: {d.get('src') or '?'} → {d.get('dst') or '?'}{svc}"))  # fmt: skip
    if d.get("hop"):
        parts.append(L(f"Bei: {d['hop']}", f"At: {d['hop']}"))
    if d.get("by"):
        parts.append(L(f"Erfasst von {d['by']}", f"Captured by {d['by']}"))
    return d.get("title") or subject, " · ".join(x for x in parts if x)


# -- Objekte und Namenskonvention ---------------------------------------------------------


def _invalid_name(subject: str, d: dict, on: str) -> tuple[str, str]:
    prefixes = _list(d["prefixes"], 8) if d.get("prefixes") else ""
    known = L(f" (bekannt: {prefixes})", f" (known: {prefixes})") if d.get("prefixes") else ""
    what = _category(d)
    hint = L(
        "Objekt nach Konvention benennen (z. B. H_ für Hosts, N_ für Netze) oder den Präfix "
        "im Namensprofil ergänzen.",
        "Name the object according to the convention (e.g. H_ for hosts, N_ for networks) or "
        "add the prefix in the naming profile.",
    )
    if d.get("category") == "service":
        hint = L(
            "Service nach Konvention benennen: FT_<sprechender Name> (z. B. FT_https) bzw. "
            "FT_<proto><port> (z. B. FT_tcp8443) – oder den Präfix im Namensprofil ergänzen "
            "(Objekt-Präfix → Typ „Service / ACI-Filter“).",
            "Name the service according to the convention: FT_<descriptive name> (e.g. "
            "FT_https) or FT_<proto><port> (e.g. FT_tcp8443) – or add the prefix in the naming "
            "profile (object prefix → type “Service / ACI filter”).",
        )
    if d.get("convention") in _NAT_CONVENTION:
        return _invalid_nat_name(subject, d, on, prefixes)
    return (
        L(
            f"{what} {_q(subject)}{on} beginnt mit keinem Präfix der Namenskonvention{known}.",
            f"{what} {_q(subject)}{on} does not start with any prefix of the naming "
            f"convention{known}.",
        ),
        hint,
    )


# Namen der VIPs und IP-Pools (0.96.0): Präfix ihres Typs - (Art im Satz, Typ im Namensprofil,
# Beispiel nach dem ersten erwarteten Präfix)
_NAT_CONVENTION = {
    "vip": (("VIP-Präfix", "VIP prefix"), ("„VIP“ bzw. „VIP / DNAT“", "“VIP” or “VIP / DNAT”"),
            ("{p}<FQDN>", "{p}<FQDN>")),
    "virtual_server": (("Präfix für Virtual Server", "prefix for virtual servers"),
                       ("„Virtual Server“", "“Virtual Server”"), ("{p}<FQDN>", "{p}<FQDN>")),
    "snat_pool": (("Pool-Präfix", "pool prefix"), ("„SNAT IP-Pool“", "“SNAT IP pool”"),
                  ("{p}<FQDN> bzw. {p}<Tenant>-<Standort>-<Zweck>",
                   "{p}<FQDN> or {p}<tenant>-<site>-<purpose>")),
}  # fmt: skip


def _invalid_nat_name(subject: str, d: dict, on: str, prefixes: str) -> tuple[str, str]:
    """VIP, Virtual Server bzw. IP-Pool ohne Präfix seines Typs (0.96.0)."""
    prefix, kind, example = (L(*pair) for pair in _NAT_CONVENTION[d["convention"]])
    example = example.replace("{p}", str((d.get("prefixes") or ["…"])[0]))
    what = "Virtual Server" if d.get("virtual_server") else _category(d)
    known = L(f" (erwartet: {prefixes})", f" (expected: {prefixes})") if prefixes else ""
    return (
        L(
            f"{what} {_q(subject)}{on} beginnt mit keinem {prefix} der Namenskonvention{known}.",
            f"{what} {_q(subject)}{on} does not start with any {prefix} of the naming "
            f"convention{known}.",
        ),
        L(
            f"Nach Konvention benennen (z. B. {example}) oder den Präfix im Namensprofil ergänzen "
            f"(Objekt-Präfix → Typ {kind}). NAT / VIPs zeigt den Eintrag mit Kette und Regeln.",
            f"Name it according to the convention (e.g. {example}) or add the prefix in the "
            f"naming profile (object prefix → type {kind}). NAT / VIPs shows the entry with its "
            "chain and rules.",
        ),
    )


def _service_mismatch(subject: str, d: dict, on: str) -> tuple[str, str]:
    rule = d.get("rule") or L("Namensschema", "naming scheme")
    values = _list(d.get("values"), 6)
    expected = _list(d.get("expected"), 6)
    return (
        L(
            f"Service {_q(subject)}{on} definiert {values or 'nichts'}, laut {rule} erwartet: "
            f"{expected}.",
            f"Service {_q(subject)}{on} defines {values or 'nothing'}, expected according to "
            f"{rule}: {expected}.",
        ),
        L(
            "Port/Protokoll am Service oder den Namen korrigieren – der Name soll sagen, was "
            "der Service erlaubt.",
            "Correct the port/protocol of the service or the name – the name should say what "
            "the service allows.",
        ),
    )


def _predefined(subject: str, d: dict, on: str) -> tuple[str, str]:
    rules = d.get("rules") or []
    n, used = len(rules), _list(rules, 4)
    return (
        L(
            f"Standardobjekt {_q(subject)}{on} wird in {n} erlaubenden "
            f"Regel{'n' if n != 1 else ''} verwendet ({used}) – es folgt nicht der "
            "Namenskonvention.",
            f"Predefined object {_q(subject)}{on} is used in {n} accepting "
            f"rule{'s' if n != 1 else ''} ({used}) – it does not follow the naming convention.",
        ),
        L(
            "Stattdessen ein eigenes Objekt nach Konvention verwenden (z. B. FT_https statt "
            "HTTPS); Standardobjekte des Herstellers lassen sich nicht umbenennen.",
            "Use an own object according to the convention instead (e.g. FT_https instead of "
            "HTTPS); predefined objects of the vendor cannot be renamed.",
        ),
    )


def _snat_pool(subject: str, d: dict, on: str) -> tuple[str, str]:
    pools = _list(d.get("pools") or [], 3)
    where = d.get("source") or L("der Firewall", "the firewall")
    vdom = f" (VDOM {d['vdom']})" if d.get("vdom") else ""
    policy = f"{d.get('external_id') or '?'} {_q(d.get('name') or subject)}"
    return (
        L(
            f"Policy {policy} übersetzt die Quelle auf den IP-Pool {pools}, den {where}{vdom} "
            "nicht hat.",
            f"Policy {policy} translates the source to the IP-Pool {pools}, which "
            f"{where}{vdom} does not have.",
        ),
        L(
            "Pool auf der Firewall prüfen bzw. neu einlesen; bis dahin ist die Quelle nach dem "
            "NAT unbekannt (Weg-Suche: Regeln danach nur „möglich“).",
            "Check the pool on the firewall or collect again; until then the source after the "
            "NAT is unknown (path search: rules after it only “possible”).",
        ),
    )


def _name_type(subject: str, d: dict, on: str) -> tuple[str, str]:
    expected = _pick(_CLASS, d.get("expected"), d.get("expected"))
    host = d.get("expected") == "host"
    actual = L("ein Netz", "a network") if host else L("eine einzelne IP", "a single IP")
    value = d.get("value")
    return (
        L(
            f"{_q(subject)}{on} ist laut Präfix {expected}, der Wert {value} ist aber {actual}.",
            f"{_q(subject)}{on} is {expected} according to its prefix, but the value {value} "
            f"is {actual}.",
        ),
        L(
            "Präfix oder Wert korrigieren (H_ = einzelne IP, N_ = Netz).",
            "Correct the prefix or the value (H_ = single IP, N_ = network).",
        ),
    )


def _duplicate(subject: str, d: dict, on: str) -> tuple[str, str]:
    names = d.get("names") or []
    value, n, listed = d.get("value") or subject, len(names), _list(names, 6)
    return (
        L(
            f"Der Wert {value} ist{on} in {n} Adressobjekten definiert: {listed}.",
            f"The value {value} is defined{on} in {n} address objects: {listed}.",
        ),
        L(
            "Auf ein Objekt zusammenführen und die Regeln auf dieses umstellen.",
            "Merge into one object and switch the rules to it.",
        ),
    )


def _unused(subject: str, d: dict, on: str) -> tuple[str, str]:
    what = _category(d)
    return (
        L(
            f"{what} {_q(subject)}{on} wird in keiner Regel und keiner Gruppe verwendet.",
            f"{what} {_q(subject)}{on} is not used in any rule or group.",
        ),
        L(
            "Prüfen und auf der Firewall entfernen (Bereinigung) - Automato ändert die Firewall "
            "nicht.",
            "Check and remove it on the firewall (cleanup) - Automato does not change the "
            "firewall.",
        ),
    )


_OBJECT_TEXT = {
    "invalid_name": _invalid_name,
    "service_mismatch": _service_mismatch,
    "predefined_service_used": _predefined,
    "snat_pool_missing": _snat_pool,
    "name_type_mismatch": _name_type,
    "duplicate_value": _duplicate,
    "unused_object": _unused,
}


# -- ACI, Policies und Portgruppen --------------------------------------------------------


def _epg_conflict(subject: str, d: dict, on: str) -> tuple[str, str]:
    ips = _list(d.get("ips") or [], 4)
    learned = _list(d.get("learned") or [], 3)
    if d.get("vm"):
        who = f"VM {_q(d['vm'])}"
    else:
        epg = _q(d.get("configured"))
        who = L(f"Statischer Endpunkt der EPG {epg}", f"Static endpoint of the EPG {epg}")
    conf, vrf = d.get("configured") or "?", d.get("vrf") or "?"
    via = d.get("via") or L("Konfiguration", "configuration")
    return (
        L(
            f"{who}: {ips} gehört laut {via} zur EPG {conf}, das ACI hat sie in {learned} "
            f"gelernt (VRF {vrf}).",
            f"{who}: {ips} belongs to the EPG {conf} according to {via}, the ACI learned it in "
            f"{learned} (VRF {vrf}).",
        ),
        L(
            "Automato ordnet nach der Konfiguration zu (Portgruppe bzw. statischer Endpunkt). "
            "Prüfen, ob die VM an der richtigen Portgruppe hängt, die IP doppelt vergeben ist "
            "oder der statische Endpunkt veraltet ist.",
            "Automato assigns according to the configuration (port group or static endpoint). "
            "Check whether the VM is connected to the right port group, the IP is assigned "
            "twice or the static endpoint is outdated.",
        ),
    )


def _both_sides(subject: str, d: dict, on: str) -> tuple[str, str]:
    name = d.get("name") or subject
    n = d.get("count") or len(d.get("epgs") or [])
    epgs = _list(d.get("epgs") or [], 5)
    if n == 1:
        which = L(f"die EPG {epgs} ist", f"the EPG {epgs} is")
    else:
        which = L(f"{n} EPGs ({epgs}) sind", f"{n} EPGs ({epgs}) are")
    return (
        L(
            f"Contract {_q(name)}: {which} Consumer und Provider zugleich.",
            f"Contract {_q(name)}: {which} consumer and provider at the same time.",
        ),
        L(
            "Der Contract gilt zwischen diesen EPGs in beide Richtungen; gegenüber sich selbst "
            "wirkt er nicht (Verkehr in der EPG ist ohnehin erlaubt). Prüfen, ob das gewollt ist "
            "oder jede EPG nur Consumer bzw. Provider sein soll. Die Applikations-Doku zeigt die "
            "eigene EPG nicht als Gegenstelle.",
            "The contract applies between these EPGs in both directions; it has no effect "
            "towards itself (traffic within the EPG is allowed anyway). Check whether this is "
            "intended or whether each EPG should only be consumer or provider. The application "
            "documentation does not show the own EPG as a peer.",
        ),
    )


def _scope_unpaired(subject: str, d: dict, on: str) -> tuple[str, str]:
    scope = {"application-profile": "AP", "context": "VRF", "tenant": "Tenant"}.get(
        d.get("scope") or "", d.get("scope") or "?"
    )
    name = _q(d.get("name") or subject)
    epgs = _list(d.get("epgs") or [], 5)
    n = d.get("unpaired") or 0
    if not d.get("pairs"):
        what = L(
            f"Contract {name} (Scope {scope}) verbindet keinen Consumer mit einem Provider – sie "
            f"liegen nicht im selben {scope}",
            f"Contract {name} (scope {scope}) connects no consumer with a provider – they are "
            f"not in the same {scope}",
        )
    else:
        what = L(
            f"Contract {name} (Scope {scope}): {n} EPG{'s' if n != 1 else ''} ohne Gegenstelle "
            f"im selben {scope} ({epgs})",
            f"Contract {name} (scope {scope}): {n} EPG{'s' if n != 1 else ''} without a peer "
            f"in the same {scope} ({epgs})",
        )
    reason = L(f" Beispiel: {d['reason']}.", f" Example: {d['reason']}.") if d.get("reason") else ""
    return (
        what + "." + reason,
        L(
            "Für diese EPGs hat der Contract keine Wirkung. Scope prüfen (VRF, Tenant bzw. global "
            "für Verbindungen darüber hinaus – zwischen VRFs zusätzlich geteilte Subnetze bzw. "
            "Route-Leaking) oder die EPG aus dem Contract nehmen.",
            "The contract has no effect for these EPGs. Check the scope (VRF, tenant or global "
            "for connections beyond – between VRFs additionally shared subnets or route "
            "leaking) or remove the EPG from the contract.",
        ),
    )


_SIDE = {"src": ("keine Quelle", "no source"), "dst": ("kein Ziel", "no destination")}


def _rule_incomplete(subject: str, d: dict, on: str) -> tuple[str, str]:
    missing = d.get("missing") or []
    name = d.get("name") or subject
    if d.get("domain") == "aci_contract":
        if len(missing) == 2:
            side = L(
                "wird von keiner EPG konsumiert und von keiner bereitgestellt",
                "is neither consumed nor provided by any EPG",
            )
        elif "src" in missing:
            side = L(
                "wird von keiner EPG konsumiert (kein Consumer)",
                "is not consumed by any EPG (no consumer)",
            )
        else:
            side = L(
                "wird von keiner EPG bereitgestellt (kein Provider)",
                "is not provided by any EPG (no provider)",
            )
        tenant = f" (Tenant {d['tenant']})" if d.get("tenant") else ""
        return (
            L(
                f"Contract {_q(name)}{tenant}{on} {side} – er ist nicht wirksam.",
                f"Contract {_q(name)}{tenant}{on} {side} – it is not effective.",
            ),
            L(
                "Consumer bzw. Provider ergänzen oder den Contract entfernen (am APIC – "
                "Automato liest nur).",
                "Add a consumer or provider or remove the contract (on the APIC – Automato only "
                "reads).",
            ),
        )
    sides = L(" und ", " and ").join(L(*_SIDE[m]) for m in missing if m in _SIDE)
    return (
        L(
            f"Policy {_q(name)}{on} hat {sides}, die Automato lesen kann.",
            f"Policy {_q(name)}{on} has {sides} that Automato can read.",
        ),
        L(
            "Policy auf der Firewall prüfen. Ist sie gültig, nutzt sie ein Feld, das Automato "
            "noch nicht liest – bitte melden.",
            "Check the policy on the firewall. If it is valid, it uses a field that Automato does "
            "not read yet – please report it.",
        ),
    )


def _epg_no_endpoints(subject: str, d: dict, on: str) -> tuple[str, str]:
    return (
        L(
            f"EPG {_q(subject)}{on} hat keine gelernten Endpunkte.",
            f"EPG {_q(subject)}{on} has no learned endpoints.",
        ),
        L("Prüfen, ob die EPG noch gebraucht wird.", "Check whether the EPG is still needed."),
    )


def _aci_path(d: dict) -> str:
    return "/".join(x for x in (d.get("tenant"), d.get("ap"), d.get("epg")) if x)


def _portgroup_no_epg(subject: str, d: dict, on: str) -> tuple[str, str]:
    path, vms = _aci_path(d), d.get("vms") or 0
    count = f"{vms} VM{'s' if vms != 1 else ''}"
    return (
        L(
            f"Portgruppe {_q(subject)}{on} heisst nach ACI-Schema {path}, diese EPG ist aber "
            f"nicht eingelesen ({count} daran).",
            f"Port group {_q(subject)}{on} is named {path} following the ACI schema, but this "
            f"EPG is not collected ({count} on it).",
        ),
        L(
            "Tenant im APIC-Quellsystem mitlesen bzw. prüfen, ob die EPG noch existiert.",
            "Include the tenant in the APIC source system or check whether the EPG still exists.",
        ),
    )


def _portgroup_name(subject: str, d: dict, on: str) -> tuple[str, str]:
    path, vms = _aci_path(d), d.get("vms") or 0
    count = f"{vms} VM{'s' if vms != 1 else ''}"
    vlan, assigned = d.get("vlan"), d.get("assigned")
    return (
        L(
            f"Portgruppe {_q(subject)}{on} heisst nach ACI-Schema {path}, ist über VLAN {vlan} "
            f"aber der EPG {assigned} zugeordnet – der Name passt nicht ({count} daran).",
            f"Port group {_q(subject)}{on} is named {path} following the ACI schema, but is "
            f"assigned to the EPG {assigned} via VLAN {vlan} – the name does not match "
            f"({count} on it).",
        ),
        L(
            "Portgruppe bzw. EPG umbenennen oder das VLAN prüfen; bis dahin gilt die Zuordnung "
            "über das VLAN.",
            "Rename the port group or EPG or check the VLAN; until then the assignment via the "
            "VLAN applies.",
        ),
    )


_ACI_TEXT = {
    "epg_assignment_conflict": _epg_conflict,
    "aci_contract_both_sides": _both_sides,
    "aci_scope_unpaired": _scope_unpaired,
    "rule_incomplete": _rule_incomplete,
    "epg_no_endpoints": _epg_no_endpoints,
    "portgroup_no_epg": _portgroup_no_epg,
    "portgroup_name_mismatch": _portgroup_name,
}


# -- IPAM-Abgleich ------------------------------------------------------------------------


def _name_mismatch(subject: str, d: dict, on: str) -> tuple[str, str]:
    cmp = d.get("compared") or []
    compared = ""
    if len(cmp) == 2:
        compared = L(
            f" Verglichen ohne Präfix und Domain: {cmp[0]} ≠ {cmp[1]}.",
            f" Compared without prefix and domain: {cmp[0]} ≠ {cmp[1]}.",
        )
    ipam, value, dns = _ipam(d), d.get("value"), d.get("dns_name")
    return (
        L(
            f"{_q(subject)}{on} passt nicht zum DNS-Namen {ipam}: {value} heisst dort "
            f"{dns}.{compared}",
            f"{_q(subject)}{on} does not match the DNS name {ipam}: {value} is named {dns} "
            f"there.{compared}",
        ),
        L(
            "Objekt umbenennen oder den DNS-Eintrag im IPAM korrigieren.",
            "Rename the object or correct the DNS entry in the IPAM.",
        ),
    )


def _subnet_not_in_ipam(subject: str, d: dict, on: str) -> tuple[str, str]:
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
    prefix, ipam = d.get("prefix") or subject, _ipam(d)
    return (
        L(
            f"Das Subnetz {prefix}{where} ist{on} konfiguriert, {ipam} aber nicht als Netz "
            "erfasst.",
            f"The subnet {prefix}{where} is configured{on}, but not recorded as a network "
            f"{ipam}.",
        ),
        L(
            "Netz im IPAM anlegen - bei NetBox als führender Quelle über den NetBox-Sync.",
            "Create the network in the IPAM - with NetBox as primary source via the NetBox sync.",
        ),
    )


def _vlan_mismatch(subject: str, d: dict, on: str) -> tuple[str, str]:
    prefix, ipam = d.get("prefix") or subject, _ipam(d)
    vlan, ipam_vlan = d.get("vlan"), d.get("ipam_vlan")
    return (
        L(
            f"{prefix}: VLAN {vlan}{on}, {ipam} VLAN {ipam_vlan}.",
            f"{prefix}: VLAN {vlan}{on}, VLAN {ipam_vlan} {ipam}.",
        ),
        L(
            "VLAN im IPAM oder auf dem Gerät korrigieren.",
            "Correct the VLAN in the IPAM or on the device.",
        ),
    )


def _ambiguous_ip(subject: str, d: dict, on: str) -> tuple[str, str]:
    ipam = _ipam(d)
    return (
        L(
            f"{subject} ist {ipam} mehrfach mit unterschiedlichen Einträgen vorhanden.",
            f"{subject} exists several times {ipam} with different entries.",
        ),
        L("Doppelte Einträge im IPAM bereinigen.", "Clean up the duplicate entries in the IPAM."),
    )


def _vm_not_in_ipam(subject: str, d: dict, on: str) -> tuple[str, str]:
    ips = d.get("ips") or []
    one, listed, ipam = len(ips) == 1, _list(ips, 6), _ipam(d)
    return (
        L(
            f"VM {_q(subject)}{on}: {'Die IP' if one else 'Die IPs'} {listed} "
            f"{'ist' if one else 'sind'} {ipam} nicht erfasst.",
            f"VM {_q(subject)}{on}: {'the IP' if one else 'the IPs'} {listed} "
            f"{'is' if one else 'are'} not recorded {ipam}.",
        ),
        L(
            "IP im IPAM anlegen (Name wie die VM bzw. ihr Hostname) oder die VM-Konfiguration "
            "prüfen.",
            "Create the IP in the IPAM (name like the VM or its host name) or check the VM "
            "configuration.",
        ),
    )


_IPAM_TEXT = {
    "name_mismatch": _name_mismatch,
    "subnet_not_in_ipam": _subnet_not_in_ipam,
    "vlan_mismatch": _vlan_mismatch,
    "ambiguous_ip": _ambiguous_ip,
    "vm_not_in_ipam": _vm_not_in_ipam,
}


# -- Parallele Firewalls (ADR 0020) -------------------------------------------------------


def _group(d: dict) -> str:
    return L(f"Verbund {d.get('group')}", f"parallel group {d.get('group')}")


def _differences(d: dict) -> str:
    """Abweichende Felder `Feld: Wert ≠ Wert` (Feldnamen und Werte wie gespeichert)."""
    values = d.get("values") or {}
    return "; ".join(f"{k}: {v[0]} ≠ {v[1]}" for k, v in list(values.items())[:4] if len(v) == 2)


def _parallel_rule(subject: str, d: dict, on: str) -> tuple[str, str]:
    shown = _differences(d)
    policy = f"{_q(d.get('name') or subject)} (ID {d.get('external_id')}){on}"
    other, group, fields = d.get("other"), _group(d), _list(d.get("fields"), 6)
    return (
        L(
            f"Policy {policy} weicht von der parallelen Firewall {other} ({group}) ab: "
            f"{fields}.{f' {shown}.' if shown else ''}",
            f"Policy {policy} differs from the parallel firewall {other} ({group}): "
            f"{fields}.{f' {shown}.' if shown else ''}",
        ),
        L(
            "Parallele Firewalls sollen dieselben Regeln haben - die Policy angleichen, sonst "
            "hängt der Zugriff davon ab, über welche Firewall der Verkehr läuft.",
            "Parallel firewalls should have the same rules - align the policy, otherwise the "
            "access depends on which firewall the traffic passes.",
        ),
    )


def _parallel_missing(subject: str, d: dict, on: str) -> tuple[str, str]:
    policy = f"{_q(d.get('name') or subject)} (ID {d.get('external_id')}){on}"
    missing, group = _list(d.get("missing_on"), 6), _group(d)
    return (
        L(
            f"Policy {policy} fehlt auf {missing} ({group}).",
            f"Policy {policy} is missing on {missing} ({group}).",
        ),
        L(
            "Policy auf der parallelen Firewall ergänzen bzw. entfernen - sonst gilt sie nur "
            "auf einem Weg.",
            "Add or remove the policy on the parallel firewall - otherwise it only applies on "
            "one path.",
        ),
    )


def _parallel_object(subject: str, d: dict, on: str) -> tuple[str, str]:
    values = d.get("values") or ["–", "–"]
    what = _category(d)
    other, group = d.get("other"), _group(d)
    return (
        L(
            f"{what} {_q(subject)}{on} ist auf der parallelen Firewall {other} ({group}) anders "
            f"definiert: {values[0]} ≠ {values[1]}.",
            f"{what} {_q(subject)}{on} is defined differently on the parallel firewall {other} "
            f"({group}): {values[0]} ≠ {values[1]}.",
        ),
        L(
            "Objekt angleichen - gleichnamige Objekte in Regeln paralleler Firewalls müssen "
            "dasselbe bedeuten.",
            "Align the object - objects of the same name in rules of parallel firewalls must "
            "mean the same.",
        ),
    )


_ROUTE_KIND = {"differs": ("anders", "differs"), "only_lead": ("fehlt", "missing"),
               "only_here": ("nur hier", "only here")}  # fmt: skip


def _parallel_item(ftype: str, d: dict, on: str) -> tuple[str, str]:
    """Policy-Route/SD-WAN-Regel bzw. VIP/IP-Pool im Verbund (0.72.0)."""
    if ftype == "parallel_nat_mismatch":
        what = "IP-Pool" if d.get("nat_type") == "snat_pool" else "VIP"
        what = f"{what} {_q(d.get('name'))}"
        hint = L(
            "VIP bzw. IP-Pool angleichen - sonst wird je nach Firewall anders übersetzt bzw. "
            "der Dienst ist nur über eine Firewall erreichbar.",
            "Align the VIP or IP-Pool - otherwise the translation differs per firewall or the "
            "service is only reachable via one firewall.",
        )
    else:
        what = d.get("label") or L("Policy-Route", "Policy route")
        hint = L(
            "Policy-Route bzw. SD-WAN-Regel angleichen - sonst nimmt der Verkehr je nach "
            "Firewall einen anderen Weg.",
            "Align the policy route or SD-WAN rule - otherwise the traffic takes a different "
            "path depending on the firewall.",
        )
    group = _group(d)
    if d.get("missing_on"):
        missing = _list(d["missing_on"], 6)
        return (
            L(
                f"{what}{on} fehlt auf {missing} ({group}).",
                f"{what}{on} is missing on {missing} ({group}).",
            ),
            hint,
        )
    shown = _differences(d)
    other, fields = d.get("other"), _list(d.get("fields"), 6)
    return (
        L(
            f"{what}{on} weicht von der parallelen Firewall {other} ({group}) ab: "
            f"{fields}.{f' {shown}.' if shown else ''}",
            f"{what}{on} differs from the parallel firewall {other} ({group}): "
            f"{fields}.{f' {shown}.' if shown else ''}",
        ),
        hint,
    )


def _parallel_routes(d: dict) -> tuple[str, str]:
    """Routen eines VDOMs im Verbund (0.72.0): Praefixe mit Art der Abweichung."""
    entries = d.get("entries") or []
    vdom, group = d.get("vdom"), _group(d)
    if entries and entries[0].get("table") == "vdom":
        here = entries[0].get("kind") == "only_here"
        where = d.get("source") if here else d.get("other")
        return (
            L(
                f"VDOM {vdom} gibt es nur auf {where} ({group}) - sein Routing ist nicht "
                "vergleichbar.",
                f"VDOM {vdom} only exists on {where} ({group}) - its routing cannot be compared.",
            ),
            L(
                "VDOM auf beiden Firewalls einrichten bzw. prüfen, ob der Verbund stimmt.",
                "Set up the VDOM on both firewalls or check whether the parallel group is "
                "correct.",
            ),
        )
    items = [f"{e.get('prefix')} ({_pick(_ROUTE_KIND, e.get('kind'), e.get('kind'))})"
             for e in entries]  # fmt: skip
    count = d.get("count") or len(entries)
    source, other, listed = d.get("source"), d.get("other"), _list(items, 5)
    return (
        L(
            f"Routing im VDOM {vdom} auf {source} weicht von der parallelen Firewall {other} "
            f"({group}) ab: {count} Präfix{'e' if count != 1 else ''} – {listed}.",
            f"Routing in VDOM {vdom} on {source} differs from the parallel firewall {other} "
            f"({group}): {count} prefix{'es' if count != 1 else ''} – {listed}.",
        ),
        L(
            "Statische Routen bzw. das dynamische Routing angleichen - sonst hängt der Weg davon "
            "ab, über welche Firewall der Verkehr läuft. Next-Hops zählen nicht (je Firewall "
            "eigene Router).",
            "Align the static routes or the dynamic routing - otherwise the path depends on "
            "which firewall the traffic passes. Next-Hops do not count (own routers per "
            "firewall).",
        ),
    )


_PARALLEL_TEXT = {
    "parallel_rule_mismatch": _parallel_rule,
    "parallel_rule_missing": _parallel_missing,
    "parallel_object_mismatch": _parallel_object,
    "parallel_policy_route_mismatch": lambda s, d, on: _parallel_item(
        "parallel_policy_route_mismatch", d, on
    ),
    "parallel_nat_mismatch": lambda s, d, on: _parallel_item("parallel_nat_mismatch", d, on),
    "parallel_route_mismatch": lambda s, d, on: _parallel_routes(d),
}


# -- Datenqualitaet (ADR 0029) ------------------------------------------------------------

_PLATFORM = {
    "fortigate": ("FortiGate", "FortiGate"),
    "cisco_aci": ("Cisco ACI", "Cisco ACI"),
    "vmware": ("VMware vCenter", "VMware vCenter"),
    "cisco_switch": ("Switches & Router", "Switches & routers"),
    "avi": ("NSX ALB (Avi)", "NSX ALB (Avi)"),
    "infoblox": ("Infoblox DDI", "Infoblox DDI"),
    "netbox": ("NetBox", "NetBox"),
}

_PARTIAL_HINT = {  # Art des Teilfehlers -> was pruefen
    "unreached": (
        "Erreichbarkeit der Geräte prüfen (Adresse, SSH, Zugangsdaten, Host-Schlüssel)",
        "Check the reachability of the devices (address, SSH, credentials, host key)",
    ),
    "unreadable": (
        "Leserechte des Kontos prüfen (Profil mit Lesezugriff auf alle VDOMs, Routing und BGP "
        "bzw. die Objekte am Controller)",
        "Check the read permissions of the account (profile with read access to all VDOMs, "
        "routing and BGP or the objects on the controller)",
    ),
    "contexts": (
        "Leserechte für Routing und BGP prüfen (FortiGate-Profil je VDOM, Border-Leafs am APIC)",
        "Check the read permissions for routing and BGP (FortiGate profile per VDOM, border "
        "leafs on the APIC)",
    ),
    "notes": (
        "Befehle ohne auswertbare Ausgabe: Plattform bzw. Version des Geräts prüfen",
        "Commands without parsable output: check the platform or version of the device",
    ),
}


def _source_who(subject: str, d: dict) -> str:
    what = _pick(_PLATFORM, d.get("platform"))
    return f"{_q(subject)} ({what})" if what else _q(subject)


def _source_stale(subject: str, d: dict) -> tuple[str, str]:
    """Stand eines Quellsystems veraltet: letzter erfolgreicher Lauf aelter als die Grenze."""
    who, limit, sched = _source_who(subject, d), d.get("limit_days"), d.get("schedule")
    rule = L(f"Grenze {limit} Tage", f"limit {limit} days")
    if sched:
        rule += L(f", Zeitplan {sched}", f", schedule {sched}")
    if d.get("never"):
        msg = L(
            f"Quellsystem {who} wurde noch nie erfolgreich eingelesen ({rule}) – Doku, Weg-Suche "
            "und Zonen kennen seine Daten nicht.",
            f"Source system {who} has never been collected successfully ({rule}) – "
            "documentation, path search and zones do not know its data.",
        )
    else:
        age = d.get("age_days")
        msg = L(
            f"Quellsystem {who} wurde zuletzt vor {age} Tagen erfolgreich eingelesen ({rule}) – "
            "Doku, Weg-Suche und Zonen zeigen einen alten Stand.",
            f"Source system {who} was last collected successfully {age} days ago ({rule}) – "
            "documentation, path search and zones show outdated data.",
        )
    if d.get("last_failed"):
        why = (d.get("last_message") or "").rstrip(".")
        msg += L(f" Letzter Lauf fehlgeschlagen{': ' + why if why else ''}.",
                 f" Last run failed{': ' + why if why else ''}.")  # fmt: skip
    return msg, L(
        "Neu einlesen bzw. die Ursache des Fehlschlags beheben (Läufe, Betriebszustand); für "
        "regelmässiges Einlesen einen Zeitplan am Quellsystem setzen. Ein bewusst nicht mehr "
        "gelesenes Quellsystem ausschalten. Grenze: AUTOMATO_STALE_DAYS.",
        "Collect again or fix the cause of the failure (runs, health); set a schedule on the "
        "source system for regular collection. Switch off a source system that is intentionally "
        "no longer read. Limit: AUTOMATO_STALE_DAYS.",
    )


def _source_partial(subject: str, d: dict) -> tuple[str, str]:
    """Letzter Lauf nur teilweise: Teilfehler aus der Laufmeldung (Text wie gespeichert) und
    Routing-Kontexte „nicht lesbar“."""
    parts = d.get("parts") or []
    items = [str(p["text"]) for p in parts if p.get("text")]
    contexts = d.get("contexts") or []
    if contexts:
        n = d.get("context_count") or len(contexts)
        shown_ctx = [
            f"{c.get('node')} · {c.get('context')} ({'; '.join(c.get('missing') or [])})"
            for c in contexts
        ]
        listed = _list(shown_ctx, 3)
        items.append(L(f"Routing nicht lesbar in {n} Kontext{'en' if n != 1 else ''}: {listed}",
                       f"routing not readable in {n} context{'s' if n != 1 else ''}: "
                       f"{listed}"))  # fmt: skip
    shown = "; ".join(items) or "–"
    kinds = [p.get("kind") for p in parts] + (["contexts"] if contexts else [])
    checks = [L(*_PARTIAL_HINT[k]) for k in dict.fromkeys(kinds) if k in _PARTIAL_HINT]
    return (
        L(
            f"Der letzte Lauf von {_q(subject)} hat nur einen Teil gelesen: {shown}.",
            f"The last run of {_q(subject)} only collected part of the data: {shown}.",
        ),
        "; ".join(checks)
        + L(
            " – und neu einlesen. Bis dahin fehlen diese Daten in Weg-Suche, Doku und Zonen.",
            " – and collect again. Until then this data is missing in path search, "
            "documentation and zones.",
        ),
    )


_SOURCE_TEXT = {
    "source_stale": _source_stale,
    "source_partial": _source_partial,
}
