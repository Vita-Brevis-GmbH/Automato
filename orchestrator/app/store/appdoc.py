"""Applikations-Doku (UC1, systemzentriert) - die 10 Kategorien aus
`docs/usecases/uc1-applikations-doku-spec.md`.

Mittelpunkt ist ein **System** (Host-`network_object`). Für dieses System werden alle
Regeln, an denen es beteiligt ist, nach **Richtung × Scope × Enforcement** in 10
Tabellen eingeordnet. Nutzt `rule.protections` (Schutzmechanismen) und `nat_rule`
(`nat_type` dnat_vip/snat_pool) für die NAT-Spalten.

Model 2 (FortiGate): Tabellen 1–6, 9, 10; die ACI-internen Tabellen 7/8 bleiben leer bis
Model 1. Die Kategorisierung ist bewusst konservativ: nur eindeutig ableitbare Regeln
werden zugeordnet, der Rest landet in Tabelle 10 (unspezifisch). Rein lesend/pure Logik.
"""

import ipaddress
from collections import defaultdict
from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import L
from app.models import (
    Epg,
    EpgEndpoint,
    EpgSubnet,
    NatRule,
    NetworkObject,
    ObjectClass,
    Rule,
    RuleAction,
    RuleDomain,
    RuleEndpoint,
    RuleIndex,
    Site,
    SourceSystem,
    Tenant,
)
from app.models.rule import EndpointKind
from app.store import (
    aci_direction,
    aci_relevance,
    aci_shared,
    appdoc_catalog,
    appdoc_filter,
    references,
    rule_index,
    rule_view,
    sites,
    virtual,
)
from app.store import epgs as epg_store
from app.store import nat as nat_store
from app.store import relevance as relevance_store
from app.store import rules as rules_store

SEP = " · "  # gleichnamige EPGs mehrerer ACI-Tenants: "EG_x · TE_a · AP_1"

# Kategorien (Nummer, Titel, Richtung Zonentyp → Zonentyp) seit 0.104.0 als Katalog
# (`appdoc_catalog`): Vorgaben im Code, angepasst bzw. erweitert im Namensprofil des Mandanten
# (Anwender 2026-10-28: „Konfiguration statt Code“). `TenantData.catalog` ist der des Mandanten;
# die Funktionen hier nehmen ohne Katalog die Vorgaben.
DEFAULT_CATALOG = appdoc_catalog.default()


def table_title(index: int, catalog: appdoc_catalog.Catalog | None = None) -> str:
    """Titel einer Kategorie in der Sprache der Anfrage."""
    return (catalog or DEFAULT_CATALOG).title(index)


# "Bezug": wie die Regel das System erfasst - direkt, ueber ein Netz/einen Bereich bzw. eine
# Gruppe (Objektname), any, vzAny der VRF, ESG oder die EPG des Servers (Dev-Befund 0.10.0).
# Steht in jeder Zeile (Treffer, Erklaerung), seit 0.34.0 aber nicht mehr als Spalte der
# Kategorien - dort sagte er den Anwendern nichts.
VIA = "Bezug"
# Spalten seit 0.34.0 (Dev-Befund 0.33.0): Aktion der Regel und das Geraet, auf dem sie steht
ACTION, DEVICE = "Aktion", "Gerät"
# Zusatzfelder je Zeile (nicht als Spalte, 0.14.0): Regel-ID, Richtung aus Sicht des Systems
# (in = System ist Ziel, out = Quelle) und Art des Bezugs (specific = der Host bzw. die EPG
# selbst, subnet = groesseres Netz, any, vzany = vzAny der VRF, pref_group = Preferred Group)
RULE, DIRECTION, SCOPE = "_rule", "_dir", "_scope"
# Seite der Regel (src | dst) und das Objekt der Regel, ueber das das System erfasst wird -
# wie es in der Regel steht (H_web01, N_srv16 mit 10.20.0.0/16, any, EPG Tenant/AP/EPG), 0.14.1
SIDE, OBJECT, OBJECT_VALUE = "_side", "_obj", "_val"
# Relevanz (0.24.0, ADR 0019): kann die Regel Verkehr zum bzw. vom System ueberhaupt sehen
# (possible | impossible | unknown) und warum nicht - nach dem Routing ihrer Firewall
REL, REL_TEXT = "_rel", "_rel_text"
# EPG (`Tenant/AP/EPG`), ueber die ein ACI-Contract fuer das System gilt - fuer den Filter nach
# EPG in der Applikations-Doku (0.42.3); leer bei Firewall-Regeln und externen EPGs
EPG = "_epg"
# ACI: DNs der Beteiligten eines Treffers - eigener Endpunkt bzw. Gegenstellen im Scope (0.43.2)
PARTIES = "_parties"
# ACI ist zustandslos (0.69.1): Zeile der Rueckrichtung (Provider -> Consumer) mit den Diensten,
# die der Provider aufbauen kann (`aci_direction`); die Seite (`SIDE`) bleibt die des Contracts
REVERSE = "_reverse"
# Kategorien Richtung L3Out (0.78.2, Anwender): die Firewall ist fuehrend - ein Contract zu
# externen EPGs (`L3OUT` = deren Labels) steht nur in den Kategorien, wenn er weniger oeffnet als
# die Firewall-Regeln derselben Kategorie und Richtung (`NARROW` = fehlende Dienste,
# `FW_RULES` = die Firewall-Regeln), sonst nicht (`CAT_HIDDEN`; Treffer, Regeln & Contracts und
# Verbindungen behalten ihn). `CAT_HIDDEN` auch fuer Zugriffe der Applikation auf andere Systeme
# in Kategorie 10 („Unspezifische Zugriffe auf die Applikation“: nur eingehend)
L3OUT, NARROW, FW_RULES, CAT_HIDDEN = "_l3out", "_narrow", "_fw_rules", "_cat_hidden"
UNSPECIFIC = ("subnet", "any", "vzany", "pref_group")
# Ausgehende Firewall-Regeln mit breiter Quelle (0.103.0, Anwender: „Firewall-Regeln, welche die
# Source offen ist (z. B. 10.0.0.0/8), damit der Server irgendwo zugreifen kann, sollen bei dem
# Server nicht zeigen“): das System steht nur ueber ein Netz, einen Bereich bzw. any in der
# Quelle - kein Zugriff dieser Applikation. Nicht in der Doku (Kategorien, Regeln, Verbindungen,
# Doku je System), nur gezaehlt; eingehende breite Regeln (Zugriffe auf das System) und Regeln,
# die das System konkret nennen (Host, Gruppe mit dem Host), bleiben.
OUT_BROAD = "_out_broad"
BROAD = ("subnet", "any")
# L3Outs: die Firewall entscheidet (ADR 0034, Anwender 2026-10-27). Ein Contract zu externen EPGs
# mit einer Firewall hinter dem L3Out ist nur ein Hilfs-Contract (`HELPER`: L3Outs und die
# Firewall-Regeln, an denen er steht) - keine eigene Zeile, an der Firewall-Regel vermerkt
# (`HELPERS`). Spalte `L3OUT_COL`: ueber welchen L3Out der Zugriff ins Fabric kommt (mit den
# Hilfs-Contracts), `L3OUT_IN` = sein Name (Sortierung der unspezifischen Zugriffe),
# `L3OUT_NOTE` = Hinweis am Contract ohne Firewall dahinter, `L3OUT_ALONE` = L3Out gelesen, aber
# ohne Firewall: der Contract regelt allein und steht in den Kategorien
L3OUT_COL, L3OUT_IN, HELPER, HELPERS = "L3Out", "_l3out_in", "_helper", "_helpers"
L3OUT_NOTE, L3OUT_ALONE = "_l3out_note", "_l3out_alone"
# Gegenstellen einer Zeile (0.105.0): Adressen bzw. DNs der EPGs - „innerhalb der Applikation“,
# wenn alle zur Applikation gehoeren (`applications.build_doc`)
CP_REFS = "_cp_refs"
INTERNAL = (PARTIES, HELPER, HELPERS, L3OUT_ALONE, CP_REFS)  # nur intern bzw. fuer die Treffer

# Spalten seit 0.84.0 (Anwender: „heute steht alles in Server Name“): Quelle und Ziel in
# Verkehrsrichtung, je mit Name und IP; die Seite der Applikation (`APP`) bzw. die Gegenstelle
# (`PEER`) kommen je nach Richtung in Source bzw. Destination (`place`). Zugriffe aus dem
# Internet mit der Kette öffentliche IP → VIP der Firewall → Load Balancer → Server in eigenen
# Spalten. Mehrere Einträge einer Zelle stehen je auf einer Zeile („\n“).
SOURCE, DESTINATION, SERVICE = "Source", "Destination", "TCP/UDP"
NAT, SNAT_IP = "NAT", "NAT Externe IPv4"
DNS_NAME, VIP_FW, BALANCER = "Externer DNS Name", "VIP (Firewall)", "Load Balancer"
PROTECTION, DESCRIPTION = "Schutzmechanismen", "Description"
APP, PEER, APP_COL = "_app", "_peer", "_app_col"
# Zonen der Gegenstelle (0.101.0, `appdoc_zones`): woher der Zugriff kommt bzw. wohin er geht
ZONE_COL = "Zone"
# ohne Wert in allen Zeilen einer Kategorie: Spalte ausgeblendet (`visible_columns`)
OPTIONAL = (NAT, SNAT_IP, DNS_NAME, VIP_FW, BALANCER, PROTECTION, L3OUT_COL, ZONE_COL)
_COMMON = [SOURCE, DESTINATION, ZONE_COL, SERVICE, NAT, SNAT_IP, ACTION, PROTECTION, DEVICE,
           L3OUT_COL, DESCRIPTION]  # fmt: skip
# Zugriffe ueber eine VIP der Firewall: die Kette DNS → VIP → Load Balancer vor dem Ziel
_CHAIN = [SOURCE, ZONE_COL, DNS_NAME, VIP_FW, BALANCER, DESTINATION, SERVICE, NAT, SNAT_IP, ACTION,
          PROTECTION, DEVICE, L3OUT_COL, DESCRIPTION]  # fmt: skip
ALL_COLUMNS = list(dict.fromkeys([*_CHAIN, *_COMMON]))  # gleiche Zeile erkennen


def table_columns(rows: list[dict] | None = None) -> list[str]:
    """Spalten einer Kategorie nach ihren Zeilen (0.106.0, Anwender: „je nach Zonenuebergang“):
    mit einer VIP der Firewall die Kette DNS → VIP → Load Balancer → Server, sonst die
    gemeinsamen Spalten; NAT bzw. NAT-IP nur, wenn eine Zeile sie hat (`visible_columns`)."""
    chain = any(r.get(c) for r in rows or [] for c in (DNS_NAME, VIP_FW, BALANCER))
    return _CHAIN if chain else _COMMON


MAX_IPS = 3  # IPs je System in einer Zelle
# Anzeige der Spalten auf Englisch (0.87.0, ADR 0028): die Spaltennamen bleiben Schluessel der
# Zeilen (Python und Oberflaeche vergleichen sie), uebersetzt wird nur `labels` je Tabelle
_COLUMN_EN = {
    SNAT_IP: "NAT external IPv4",
    DNS_NAME: "External DNS name",
    ACTION: "Action",
    PROTECTION: "Protection",
    DEVICE: "Device",
}


def column_labels(columns: list[str]) -> list[str]:
    """Anzeige der Spalten (parallel zu `columns`) in der Sprache der Anfrage."""
    return [L(c, _COLUMN_EN.get(c, c)) for c in columns]


# Marker im Dienst-Text (ACI zustandslos, 0.69.1) - beide Sprachen, damit die Auswertung
# (`app_paths._named`, `_merge_reverse`) jede Fassung erkennt
REVERSE_MARKS = ("(Rückrichtung)", "(reverse direction)")
BOTH_MARKS = ("beide Richtungen", "both directions")


def reverse_mark() -> str:
    """Zusatz am Dienst einer Zeile der Rueckrichtung."""
    return L("(Rückrichtung)", "(reverse direction)")


def both_mark() -> str:
    """Zusatz am Dienst, wenn beide Richtungen in derselben Kategorie stehen."""
    return L("(↔ beide Richtungen)", "(↔ both directions)")


def has_both(text: str | None) -> bool:
    return any(m in (text or "") for m in BOTH_MARKS)


def place(row: dict) -> dict:
    """Source/Destination aus der Seite der Applikation und der Gegenstelle: eingehend
    (System = Ziel) steht die Gegenstelle in Source, ausgehend in Destination."""
    app, peer = row.get(APP) or "", row.get(PEER) or ""
    inbound = row.get(DIRECTION) == "in"
    row[SOURCE], row[DESTINATION] = (peer, app) if inbound else (app, peer)
    row[APP_COL] = DESTINATION if inbound else SOURCE
    return row


def visible_columns(rows: list[dict]) -> list[str]:
    """Spalten einer Kategorie ohne die optionalen, die in keiner Zeile einen Wert haben."""
    return [c for c in table_columns(rows)
            if c not in OPTIONAL or any(r.get(c) for r in rows)]  # fmt: skip


def app_label(name: str, ips: list[str] | None = None) -> str:
    """System der Applikation mit seinen IPs: „web01 (10.20.0.10)“."""
    shown = [i for i in dict.fromkeys(ips or []) if i and i != name]
    if not shown:
        return name
    if all(i.startswith(f"{name}:") for i in shown):  # System ohne Namen: nur IP mit Port
        return ", ".join(shown[:MAX_IPS])
    more = " …" if len(shown) > MAX_IPS else ""
    return f"{name} ({', '.join(shown[:MAX_IPS])}{more})"


def lines(values) -> str:
    """Einträge einer Zelle je auf einer Zeile, ohne doppelte."""
    return "\n".join(dict.fromkeys(v for v in values if v))


def _target(other: Epg | None, inline: str | None, own: Epg) -> str:
    """Gegenstelle in der Doku: EPG mit dem ganzen ACI-Pfad `Tenant/AP/EPG` (Dev-Befund 0.14.0:
    `EG_dmz01` allein ist nicht eindeutig); externe EPG / vzAny mit L3Out bzw. VRF."""
    if other is None:
        return inline or ""
    if other.kind != "epg":
        return epg_store.label(other, own.aci_tenant)
    return epg_store.inventory_label(other)


def epg_system_name(epg: Epg) -> str:
    """Eindeutiger Systemname einer EPG, deren Name es in mehreren ACI-Tenants/APs gibt."""
    return SEP.join(x for x in (epg.name, epg.aci_tenant, epg.ap) if x)


def _label(obj: NetworkObject | None, inline: str | None) -> str:
    if obj is not None:
        return obj.value or obj.fqdn or obj.name
    return inline or "any"


def _peer_label(obj: NetworkObject | None, inline: str | None) -> str:
    """Gegenstelle einer Firewall-Regel mit Name und Wert (0.84.0): „N_partner (192.0.2.0/24)“,
    „H_www (www.example.ch)“; ohne Namen nur der Wert."""
    if obj is not None:
        value = obj.value or obj.fqdn
        if value and obj.name and obj.name != value:
            return f"{obj.name} ({value})"
        return value or obj.name
    return inline or "any"


def categorize(data: "TenantData", direction: str, own_zones: list[int], cp_zones: list[int],
               *, inside: bool = False, same_tenant: bool = False, own_hint: str | None = None,
               cp_hint: str | None = None) -> int:  # fmt: skip
    """Kategorie einer Zeile (0.106.0, ADR 0035): innerhalb der Applikation (ACI im selben AP;
    Server intern ergaenzt `applications.build_doc`), innerhalb des Security Tenants (ACI im
    selben Tenant bzw. alle Zonen der Gegenstelle sind eigene), sonst die Richtung Zonentyp der
    Quelle → Zonentyp des Ziels (eingehend: Gegenstelle → System), sonst unspezifisch.
    Mehrere Zonentypen auf einer Seite: unspezifisch. Eine Seite ohne Zone nimmt den Zonentyp
    nach dem Namen (`own_hint`, `cp_hint`: Firewall-Zone bzw. L3Out, System im DC)."""
    cat, zm = data.catalog, data.zone_map
    if inside:
        return cat.role("aci_ap")
    if same_tenant or (own_zones and cp_zones and set(cp_zones) <= set(own_zones)):
        return cat.role("aci_tenant")
    own_t = zm.type_of(own_zones) if own_zones else own_hint
    cp_t = zm.type_of(cp_zones) if cp_zones else cp_hint
    idx = cat.by_flow(cp_t, own_t) if direction == "in" else cat.by_flow(own_t, cp_t)
    return idx if idx is not None else cat.fallback


def _merge_reverse(rows: list[dict]) -> list[dict]:
    """Doku je System wie die Applikations-Doku (0.72.1): steht die Rueckrichtung eines
    Contracts in derselben Kategorie wie seine Vorwaertsrichtung, eine Zeile „↔ beide
    Richtungen“; sonst bleibt sie eine eigene Zeile „(Rueckrichtung)“."""
    forward = {(r.get(RULE), r.get(SIDE), r.get(OBJECT)): r for r in rows if not r.get(REVERSE)}
    out = []
    for row in rows:
        twin = forward.get((row.get(RULE), row.get(SIDE), row.get(OBJECT)))
        if row.get(REVERSE) and twin is not None:
            if not has_both(twin.get(SERVICE)):
                twin[SERVICE] = f"{twin.get(SERVICE) or ''} {both_mark()}".strip()
            continue
        out.append(row)
    return out


def _envelope(
    session: Session,
    system_name: str,
    tenant_id: int,
    system_ip: str | None,
    site_id: int | None,
    rows_by_table: dict[int, list[dict]],
    vms: list[dict] | None = None,
    full: bool = False,
    catalog: appdoc_catalog.Catalog | None = None,
) -> dict:
    """Doku eines Systems. Regeln, die das System nach dem Routing nicht sehen koennen, stehen
    nicht in den Tabellen, sondern unter `impossible` mit Begruendung (`full` = alle Zeilen,
    fuer die Applikations-Doku, die selbst filtert)."""
    catalog = catalog or appdoc_catalog.for_tenant(session, tenant_id)
    impossible: list[dict] = []
    if not full:
        for rows in rows_by_table.values():
            for row in rows:
                for key in INTERNAL:  # nur fuer die Treffer der Applikations-Doku
                    row.pop(key, None)
        for i in rows_by_table:
            keep = []
            for row in rows_by_table[i]:
                where = appdoc_filter.place(row)  # 0.104.0: Bedeutung der Merkmale an einer Stelle
                if where == appdoc_filter.TABLE:
                    keep.append(row)
                elif where == appdoc_filter.IMPOSSIBLE and not any(
                    (x["rule_id"], x["side"]) == (row.get(RULE), row.get(SIDE)) for x in impossible
                ):
                    impossible.append(
                        {
                            "rule_id": row.get(RULE),
                            "description": row.get(DESCRIPTION) or "",
                            "side": row.get(SIDE),
                            "text": row.get(REL_TEXT) or "",
                        }  # fmt: skip
                    )
            rows_by_table[i] = by_l3out(_merge_reverse(keep))
    for rows in rows_by_table.values():
        for row in rows:
            place(row)
    tables = []
    for c in catalog.ordered():
        rows = rows_by_table.get(c.index, [])
        columns = visible_columns(rows)
        tables.append({"index": c.index, "title": catalog.title(c.index),
                       "enforcement": c.enforcement, "columns": columns,
                       "labels": column_labels(columns), "rows": rows})  # fmt: skip
    tenant = session.get(Tenant, tenant_id)
    site = session.get(Site, site_id) if site_id else None
    return {
        "system": system_name,
        "system_ip": system_ip,
        "tenant_code": tenant.code if tenant else None,
        "site": sites.SiteIndex(session, site.tenant_id).ref(site) if site else None,
        "vms": vms or [],  # VMs dahinter (vCenter, ADR 0017)
        "tables": tables,
        "impossible": impossible,  # routingtechnisch nicht moeglich (ADR 0019)
    }


class TenantData:
    """Regeln, Objekte, Zonen, NAT und EPGs eines Mandanten - je Doku nur die Regeln der Systeme
    (per SQL ausgewaehlt), einmal geladen, auch wenn die Doku mehrerer Systeme (Applikation)
    gebaut wird (Dev-Befund: grosse Regelwerke)."""

    def __init__(self, session: Session, tenant_id: int):
        self.session = session
        self.tenant_id = tenant_id
        self._rules: dict[int, Rule] = {}
        self._objs: dict[int, NetworkObject] = {}
        self._zones: dict[int, str] = {}
        self._nat: dict[int, dict] = {}

    def rules(self, ids: set[int]) -> list[Rule]:
        """Regeln mit Endpunkten/Services in Firewall-Reihenfolge (fehlende nachgeladen)."""
        todo = [i for i in ids if i not in self._rules]
        if todo:
            # NAT je Regel aus dem Regelindex: SNAT (Pool bzw. Interface-IP) und VIPs im Ziel
            for rid, row in self.session.execute(
                select(RuleIndex.rule_id, RuleIndex.row).where(RuleIndex.rule_id.in_(todo))
            ):
                self._nat[rid] = (row or {}).get("nat") or {}
            for r in rules_store.list_rules(
                self.session, self.tenant_id, limit=None, rule_ids=todo
            ):
                self._rules[r.id] = r
            obj_ids = {e.network_object_id for i in todo for e in self._rules[i].endpoints}
            obj_ids -= set(self._objs)
            self._objs.update(rules_store.objects_by_ids(self.session, {i for i in obj_ids if i}))
            zone_ids = {
                z for i in todo for z in (self._rules[i].src_zone_id, self._rules[i].dst_zone_id)
            }
            self._zones.update(rules_store.zone_names(self.session, zone_ids - set(self._zones)))
        found = [self._rules[i] for i in ids if i in self._rules]
        return sorted(found, key=lambda r: (r.source_system_id, r.sequence or 0, r.id))

    def rule_ids(self, domain: RuleDomain, **endpoint) -> set[int]:
        """IDs der Regeln einer Domaene mit einem Endpunkt (network_object_id=… | epg_id=…)."""
        cond = [getattr(RuleEndpoint, k) == v for k, v in endpoint.items()]
        return set(
            self.session.scalars(
                select(RuleEndpoint.rule_id)
                .join(Rule, Rule.id == RuleEndpoint.rule_id)
                .where(Rule.tenant_id == self.tenant_id, Rule.domain == domain, *cond)
            )
        )

    @property
    def objs(self) -> dict:
        return self._objs

    @property
    def zones(self) -> dict:
        return self._zones

    @cached_property
    def nats(self) -> list[NatRule]:
        stmt = select(NatRule).where(NatRule.tenant_id == self.tenant_id)
        return list(self.session.scalars(stmt))

    @cached_property
    def balancers(self) -> nat_store.Balancers:
        """Virtual Services des NSX ALB je VIP-Adresse (0.79.0)."""
        return nat_store.Balancers(self.nats)

    def lbs(self, vips: list[NatRule], ports: list[tuple[int, int]] | None = None) -> list:
        """Virtual Services hinter den VIPs der Regel (0.79.0); seit 0.84.0 ohne
        Port-Weiterleitung nur die mit einem Port der Regel (die VIP leitet jeden Port gleich
        weiter)."""
        out: list[NatRule] = []
        for vip in vips:
            for lb in self.balancers.behind(vip):
                if lb in out:
                    continue
                span = nat_store._ports(lb.ext_port)
                if ports and span is not None and not vip.portforward:
                    if not any(lo <= span[1] and span[0] <= hi for lo, hi in ports):
                        continue
                out.append(lb)
        return out

    def balancer_text(self, vips: list[NatRule], ports: list[tuple[int, int]] | None = None) -> str:
        """Load Balancer hinter den VIPs der Regel, je Zeile „vs-web · 10.30.0.50:443 · Pool
        pool-web“."""
        out = []
        for lb in self.lbs(vips, ports):
            pool = (lb.attrs or {}).get("pool") or (lb.attrs or {}).get("pool_group")
            port = f":{lb.ext_port}" if lb.ext_port else ""
            out.append(f"{lb.name} · {lb.ext_ip}{port}" + (f" · Pool {pool}" if pool else ""))
        return lines(out)

    def server_ports(self, vips: list[NatRule], ip: str | None,
                     ports: list[tuple[int, int]] | None = None) -> list[str]:  # fmt: skip
        """Ports des Servers im Pool der Load Balancer hinter den VIPs (0.84.0)."""
        out: list[str] = []
        for lb in self.lbs(vips, ports):
            for t in lb.targets or []:
                if isinstance(t, dict) and t.get("ip") == ip and t.get("port"):
                    out.append(str(t["port"]))
        return list(dict.fromkeys(out))

    @cached_property
    def nat_by_name(self) -> dict[tuple[int, str | None, str], NatRule]:
        """VIPs und SNAT-Pools je (Firewall, VDOM, Name) - wie im Regelindex."""
        return {(n.source_system_id, n.vdom, n.name.lower()): n for n in self.nats}

    def rule_nat(self, rule: Rule) -> tuple[list[NatRule], str]:
        """NAT einer erlaubenden Firewall-Policy (Dev-Befund 0.33.0): ihre VIPs (DNAT) und ihr
        SNAT - die Pool-Adressen bzw. „Interface-IP“. Sperrende Regeln haben kein NAT."""
        if rule.domain is not RuleDomain.FIREWALL or rule.action is not RuleAction.ALLOW:
            return [], ""
        info = self._nat.get(rule.id) or {}
        key = (rule.source_system_id, rule.vdom)
        vips = [self.nat_by_name.get((*key, str(v).lower())) for v in info.get("vips") or []]
        snat = ""
        if info.get("snat"):
            pools = info.get("pools") or []
            found = [self.nat_by_name.get((*key, str(p).lower())) for p in pools]
            snat = (
                ", ".join(
                    (n.ext_ip if n is not None and n.ext_ip else p)
                    for n, p in zip(found, pools, strict=True)
                )
                if pools
                else L("Interface-IP", "interface IP")
            )
        return [v for v in vips if v is not None], snat

    @cached_property
    def device_names(self) -> dict[int, str]:
        stmt = select(SourceSystem.id, SourceSystem.name).where(
            SourceSystem.tenant_id == self.tenant_id
        )
        return dict(self.session.execute(stmt).all())

    @cached_property
    def vdom_counts(self) -> dict[int, int]:
        """Anzahl VDOMs je Firewall (nur root = die Firewall selbst, ohne VDOM-Angabe)."""
        out: dict[int, set[str]] = {}
        for sid, vdom in self.session.execute(
            select(Rule.source_system_id, Rule.vdom)
            .where(Rule.tenant_id == self.tenant_id, Rule.vdom.is_not(None))
            .distinct()
        ):
            out.setdefault(sid, set()).add(vdom)
        return {sid: len(v) for sid, v in out.items()}

    def device(self, rule: Rule) -> str:
        """Geraet der Regel: Firewall (mit VDOM, wenn es mehrere gibt) bzw. APIC."""
        sid = rule.source_system_id
        if sid not in self.device_names:  # Altbestand ohne Mandant am Quellsystem
            system = self.session.get(SourceSystem, sid)
            self.device_names[sid] = system.name if system else f"#{sid}"
        name = self.device_names[sid]
        if rule.vdom and self.vdom_counts.get(rule.source_system_id, 0) > 1:
            return f"{name} · VDOM {rule.vdom}"
        return name

    @cached_property
    def dns(self):
        return references.context(self.session, self.tenant_id)

    def rule(self, rule_id: int | None) -> Rule | None:
        """Schon geladene Regel (Zeilen der Doku)."""
        return self._rules.get(rule_id) if rule_id is not None else None

    @cached_property
    def has_firewall(self) -> bool:
        """Liest der Mandant Firewall-Regeln? Ohne Firewall bleiben die L3Out-Contracts."""
        return self.session.scalar(select(Rule.id).where(
            Rule.tenant_id == self.tenant_id, Rule.domain == RuleDomain.FIREWALL).limit(1)
        ) is not None  # fmt: skip

    def services(self, ids: set[int]):
        """Dienste je Regel (`zone_services.RuleServices`), einmal geladen."""
        from app.store import zone_services  # spaet: zone_services -> resolve -> ...

        if not hasattr(self, "_services"):
            self._services = zone_services.RuleServices({}, set())
            self._services_loaded: set[int] = set()
        todo = set(ids) - self._services_loaded
        if todo:
            more = zone_services.rule_services(self.session, todo)
            self._services.by_rule.update(more.by_rule)
            self._services.negated |= more.negated
            self._services_loaded |= todo
        return self._services

    @cached_property
    def epgs_by_id(self) -> dict[int, Epg]:
        stmt = select(Epg).where(Epg.tenant_id == self.tenant_id)
        return {e.id: e for e in self.session.scalars(stmt)}

    @cached_property
    def epgs_by_dn(self) -> dict[str, Epg]:
        return {e.dn: e for e in self.epgs_by_id.values() if e.dn}

    @cached_property
    def relevance(self) -> relevance_store.Relevance:
        return relevance_store.Relevance(self.session, self.tenant_id)

    @cached_property
    def aci(self) -> aci_relevance.Classifier:
        """Zuordnung zu externen EPGs (0.28.0); teilt den Graph der Wege."""
        return aci_relevance.Classifier(self.session, self.tenant_id, lambda: self.relevance.graph)

    @cached_property
    def catalog(self) -> appdoc_catalog.Catalog:
        """Kategorien des Mandanten (0.104.0): Vorgaben, angepasst im Namensprofil."""
        return appdoc_catalog.for_tenant(self.session, self.tenant_id)

    @cached_property
    def zone_map(self):
        """Logische Zonen mit ihren Netzen (0.101.0): Zone und Kategorie der Gegenstellen."""
        from app.store import appdoc_zones  # spaet: appdoc_zones -> zones -> customers

        return appdoc_zones.ZoneMap(self.session, self.tenant_id)

    @cached_property
    def leaks(self) -> aci_shared.Leaks:
        """Wege zwischen VRFs im Fabric (Shared Services, Route-Leaking; 0.44.0)."""
        return aci_shared.Leaks(self.session, self.tenant_id, self.aci)


def _proxies(data: TenantData, epg: Epg) -> dict[int, str]:
    """EPG-IDs, deren Contracts fuer die EPG gelten, mit Bezug: die EPG selbst, vzAny ihrer
    VRF (alle EPGs der VRF) und ESGs, die sie per EPG-Selektor einschliessen."""
    out = {epg.id: "direkt"}
    own = vrf_key(data, epg)
    for other in data.epgs_by_id.values():
        # vzAny genau der VRF der EPG (0.44.0: aufgeloeste VRF statt Name und Tenant/common)
        if other.kind == "vzany" and own[1] and vrf_key(data, other) == own:
            out[other.id] = f"vzAny {other.vrf}"
        elif other.kind == "esg" and epg.dn and f"EPG {epg.dn}" in (other.selectors or []):
            out[other.id] = f"ESG {other.name}"
    return out


def action_label(rule: Rule) -> str:
    return "Allow" if rule.action is RuleAction.ALLOW else "Deny"


def _aci_scope(rule: Rule, via: str) -> str:
    raw = rule.raw if isinstance(rule.raw, dict) else {}
    if raw.get("kind") == "preferred_group":
        return "pref_group"
    return "vzany" if via.startswith("vzAny") else "specific"


def _aci_rows(data: TenantData, epg: Epg, system_name: str, via: str | None = None,
              ips: list[str] | None = None) -> list:  # fmt: skip
    """ACI-Contracts einer EPG (direkt, ueber vzAny der VRF, ueber ESGs) als (Tabelle, Zeile).

    Gegenstelle gleiches AP (im selben ACI-Tenant) -> 7, gleicher Tenant -> 8, sonst nach dem
    Zonentyp wie bei Firewall-Regeln (hinter einem L3Out die Zone der Firewall dahinter). `via`
    (Server in der EPG) steht im
    Bezug vor dem Weg der EPG (vzAny, ESG); `ips` = IPs des Systems (Anzeige mit dem Namen)."""
    by_id = data.epgs_by_id
    proxies = _proxies(data, epg)
    zm_own = data.zone_map
    own_zones = (
        list(dict.fromkeys([*zm_own.zones_of_epgs([epg]), *zm_own.own(ips or [])]))
        if zm_own.active else []
    )  # fmt: skip
    ids: set[int] = set()
    for pid in proxies:
        ids |= data.rule_ids(RuleDomain.ACI_CONTRACT, epg_id=pid)
    ids |= data.rule_ids(RuleDomain.ACI_CONTRACT, epg_id=None, inline_value=epg.name)

    def self_via(e) -> str | None:  # aeltere Laeufe ohne EPG-Verknuepfung: ueber den Namen
        if e.epg_id in proxies:
            return proxies[e.epg_id]
        return "direkt" if e.epg_id is None and e.inline_value == epg.name else None

    out = []
    for r in data.rules(ids):
        eps = [e for e in r.endpoints if e.kind.value == "epg"]
        sides: dict[str, str] = {}
        own: dict[str, list[str]] = {}  # Seite -> DNs der eigenen Endpunkte (EPG, vzAny, ESG)
        for e in eps:
            v = self_via(e)
            if v and (e.role.value not in sides or v == "direkt"):
                sides[e.role.value] = v
            if v and e.epg_id in by_id and by_id[e.epg_id].dn:
                own.setdefault(e.role.value, []).append(by_id[e.epg_id].dn)
        for side in sorted(sides, key=lambda x: x != "dst"):  # zuerst eingehend
            cp_role = "src" if side == "dst" else "dst"
            cp = [e for e in eps if e.role.value == cp_role]
            # Consumer und Provider zugleich (0.79.1, Anwender): die eigene EPG ist keine
            # Gegenstelle (Verkehr in der EPG ist ohnehin erlaubt) - nur die anderen
            mutual = any(e.epg_id == epg.id for e in cp)
            cp = [e for e in cp if e.epg_id != epg.id]
            if mutual and not cp:
                continue
            # nur Gegenstellen im Scope des Contracts (Dev-Befund 0.43.0: Contract aus common
            # mit Scope VRF - die Consumer anderer Tenants bzw. VRFs erreicht er nicht)
            kept = [e for e in cp if e.epg_id not in by_id
                    or scope_allows(data, r.aci_scope, epg, by_id[e.epg_id])]  # fmt: skip
            if cp and not kept:
                continue
            # zwischen VRFs nur mit Leak (Shared Service, Route-Leaking; 0.44.0) - sonst
            # oeffnet der Contract im Fabric keinen Weg (wie die Weg-Suche)
            rel, rel_text, blocked = None, "", []
            for e in kept:
                other = by_id.get(e.epg_id)
                if (
                    other is None
                    or other.kind not in ("epg", "esg")
                    or epg.kind not in ("epg", "esg")
                ):
                    continue
                state, why = data.leaks.between(epg, other)
                if state == aci_shared.IMPOSSIBLE:
                    blocked.append(e)
                    rel_text = why
                elif state == aci_shared.UNKNOWN:
                    rel, rel_text = aci_shared.UNKNOWN, why
            if blocked and len(blocked) < len(kept):
                kept = [e for e in kept if e not in blocked]  # die erreichbaren bleiben
            elif blocked:
                rel = aci_shared.IMPOSSIBLE
            cp = kept
            cp_epgs = [by_id[e.epg_id] for e in cp if e.epg_id in by_id]
            cp_names = [_target(by_id.get(e.epg_id), e.inline_value, epg) for e in cp]
            same_ap = any(
                c.ap == epg.ap and c.aci_tenant == epg.aci_tenant and epg.ap is not None
                for c in cp_epgs
            )
            external = [c for c in cp_epgs if c.kind == "external"]
            only_external = bool(external) and len(external) == len(cp_epgs)
            # Zonen der Gegenstellen (0.102.0); eine externe EPG ohne eigene Zone gehoert zur Zone
            # der Firewall hinter ihrem L3Out (0.106.0, Geraete der Zonen)
            zm = data.zone_map
            cp_zones = zm.zones_of_epgs(cp_epgs) if zm.active else []
            if zm.active:
                for c in external:
                    if not zm.zones_of_epgs([c]):
                        cp_zones += [z for z in _l3out_zones(data, c) if z not in cp_zones]
            # ACI intern (7/8) nur im Fabric - nie fuer ein System ausserhalb bzw. ueber einen
            # L3Out (0.83.1); innerhalb des Tenants nicht ueber Scope „global“
            fabric = epg.kind != "external" and not external
            inside = fabric and (same_ap or r.aci_scope == "application-profile")
            in_tenant = (fabric and r.aci_scope != "global"
                         and all(c.aci_tenant == epg.aci_tenant for c in cp_epgs))  # fmt: skip
            # ohne Zone (0.106.0): ueber einen L3Out der Zonentyp nach seinem Namen, im Fabric DC
            if only_external:
                cp_hint = zm.name_type(*(c.l3out for c in external))
            else:
                cp_hint = None if external else zm.dc_type
            own_hint = (zm.name_type(epg.l3out) if epg.kind == "external" else None) or zm.dc_type
            how = sides[side]  # Schluessel: direkt | vzAny <VRF> | ESG <Name>
            shown = L("direkt", "direct") if how == "direkt" else how
            text = shown if via is None else (via if how == "direkt" else f"{how} · {via}")
            if mutual:
                text += L(" · Consumer und Provider", " · Consumer and Provider")
            obj = f"EPG {epg_store.inventory_label(epg)}" if how == "direkt" else how
            targets = lines(cp_names)
            forward = "in" if side == "dst" else "out"
            # ACI ist zustandslos (0.69.1): Filter ohne Ports oeffnen auch den Weg vom Provider
            # zum Consumer - eine eigene Zeile in der Gegenrichtung (z. B. aus dem Internet)
            back = aci_direction.reverse(r)
            # nur Antworten Consumer -> Provider (0.72.1): nur die Rueckrichtung, sonst eine Zeile
            # mit Hinweis
            only = aci_direction.replies_only(r.raw, [s.canonical_name for s in r.services])
            directions = (forward, "out" if forward == "in" else "in")[: 2 if back else 1]
            if only and back:
                directions = directions[1:]
            for direction in directions:
                reverse = direction != forward
                idx = categorize(data, direction, own_zones, cp_zones, inside=inside,
                                 same_tenant=in_tenant, own_hint=own_hint,
                                 cp_hint=cp_hint)  # fmt: skip
                services = ", ".join(back if reverse else rule_view.service_labels(r))
                if only and not reverse:
                    services = L("nur Antworten (Filter nur mit Quellport)",
                                 "replies only (filter with source port only)")  # fmt: skip
                back_text = L("Rückrichtung", "reverse direction")
                out.append(
                    (
                        idx,
                        {
                            APP: app_label(system_name, ips),
                            PEER: targets,
                            ZONE_COL: zm.text(cp_zones) if cp_zones else "",
                            SERVICE: services + (f" {reverse_mark()}" if reverse else ""),
                            ACTION: action_label(r),
                            PROTECTION: ", ".join(r.protections) if r.protections else "",
                            DEVICE: data.device(r),
                            DESCRIPTION: r.name or f"#{r.external_id}",
                            VIA: f"{text} · {back_text}" if reverse else text,
                            RULE: r.id,
                            DIRECTION: direction,
                            SCOPE: _aci_scope(r, how),
                            SIDE: side,
                            OBJECT: obj,
                            OBJECT_VALUE: "",
                            EPG: epg_store.inventory_label(epg) if epg.kind == "epg" else None,
                            # Beteiligte (0.43.2): eigene Endpunkte und Gegenstellen im Scope (DN)
                            # - die Applikations-Doku zeigt nur sie, nicht alle des Contracts
                            PARTIES: {
                                "own": own.get(side, []),
                                "counter": [c.dn for c in cp_epgs if c.dn],
                            },
                            CP_REFS: [c.dn for c in cp_epgs if c.dn],
                            **({REVERSE: back} if reverse else {}),
                            **({REL: rel, REL_TEXT: rel_text} if rel else {}),
                            **(
                                {L3OUT: [aci_relevance.ext_label(c) for c in external]}
                                if only_external
                                else {}
                            ),  # fmt: skip
                        },
                    )
                )
    return out


def _l3out_zones(data: TenantData, ext: Epg) -> list[int]:
    """Zonen der Firewall(s) hinter dem L3Out einer externen EPG (ADR 0034, Geraete der Zonen)."""
    o = data.aci.behind.of(ext.source_system_id, ext.aci_tenant, ext.l3out)
    out: list[int] = []
    for fw in o.firewalls if o is not None else []:
        out += [z for z in data.zone_map.of_firewall(fw.source_system_id, fw.context)
                if z not in out]  # fmt: skip
    return out


def _contract_allowed(data: TenantData, rows: list[dict]):
    """Was die erlaubenden L3Out-Contracts einer Kategorie und Richtung oeffnen
    (`zone_services.Allowed`): vorwaerts ihre Dienste, in der Rueckrichtung die Dienste, die
    der Provider aufbauen kann; Contracts „nur Antworten“ oeffnen vorwaerts nichts."""
    from app.store import zone_services

    out = zone_services.Allowed([])
    # „nur Antworten“ aus der Regel selbst (0.87.0: nicht mehr aus dem Text der Zeile)
    forward = {r[RULE] for r in rows
               if not r.get(REVERSE) and not _replies_only(data.rule(r[RULE]))}  # fmt: skip
    services = data.services(forward)
    for rid in forward:
        for proto, lo, hi, name in services.by_rule.get(rid, []):
            out.names.add((name or "").strip().lower())
            if proto in ("tcp", "udp"):
                out.items.append((proto, lo or zone_services.ALL_PORTS[0],
                                  hi or lo or zone_services.ALL_PORTS[1]))  # fmt: skip
            elif proto in ("icmp", "icmpv6"):
                out.items.append(("icmp", None, None))
            elif proto == "ip" and lo is None:  # ACI-Filter ohne Protokoll: alles
                out.items.append(("any", None, None))
    for r in rows:
        for text in r.get(REVERSE) or []:
            proto, _, port = str(text).lower().partition("/")
            if proto in ("ip", "all", "unspecified", "any"):
                out.items.append(("any", None, None))
            elif proto in ("tcp", "udp") and port in ("", "any"):
                out.items.append((proto, *zone_services.ALL_PORTS))
            elif (parsed := zone_services.parse(str(text))) is not None:
                out.items.append(parsed)
    return out


def _replies_only(rule: Rule | None) -> bool:
    """Contract, der Consumer -> Provider nur Antworten erlaubt (Filter nur mit Quellport)."""
    if rule is None:
        return False
    return aci_direction.replies_only(rule.raw, [s.canonical_name for s in rule.services])


def mark_categories(data: TenantData, items: list[tuple[int, dict]],
                    vrfs: set | None = None) -> list[tuple[int, dict]]:  # fmt: skip
    """Kategorien eines Systems bereinigen (0.78.2, Anwender) - die Zeilen bleiben, nur
    `CAT_HIDDEN` bzw. `NARROW` kommen dazu:

    - Kategorie 10 zeigt nur Zugriffe auf die Applikation (eingehend), nicht ihre Zugriffe auf
      andere Systeme.
    - Contracts Richtung L3Out (Gegenstelle nur externe EPGs) mit einer Firewall hinter dem
      L3Out (ADR 0034, `_mark_l3outs`): nur Hilfs-Contracts - nie eine eigene Zeile, vermerkt an
      den Firewall-Regeln dieser Firewall (Spalte L3Out); oeffnen sie weniger, steht das dort
      (Befund `app_contract_narrower`). `vrfs` = VRFs des Systems (Spalte L3Out der
      Firewall-Regeln).
    - L3Out gelesen, aber keine Firewall dahinter (nicht abgeleitet bzw. „keine“): der Contract
      regelt allein - er steht in den Kategorien, mit Hinweis.
    - L3Out nicht gelesen (Firewall unbekannt) wie 0.78.2, mit Hinweis: die Firewall ist
      fuehrend - je Kategorie und Richtung deckt der Contract die Dienste der erlaubenden
      Firewall-Regeln ab -> nicht in den Kategorien; oeffnet er weniger, steht er da, mit den
      fehlenden Diensten (Befund `app_contract_narrower`). Ohne Firewall-Regeln dort oeffnet die
      Firewall nichts, der Contract steht nicht da; ohne Firewall im Mandanten bleibt alles wie
      bisher."""
    fallback = data.catalog.fallback
    for idx, row in items:
        if idx == fallback and row.get(DIRECTION) == "out":
            row[CAT_HIDDEN] = True
        if outbound_broad(data, row):
            row[OUT_BROAD] = True
    if not data.has_firewall:
        return items
    _mark_l3outs(data, items, vrfs)
    groups: dict[tuple, dict[str, list]] = {}
    for idx, row in items:
        if row.get(CAT_HIDDEN) and idx == fallback:
            continue
        if row.get(REL) == relevance_store.IMPOSSIBLE or row.get(HELPER) or row.get(L3OUT_ALONE):
            continue  # Hilfs-Contract bzw. Contract ohne Firewall dahinter (ADR 0034)
        rule = data.rule(row.get(RULE))
        if rule is None:
            continue
        g = groups.setdefault((idx, row.get(DIRECTION)), {"fw": [], "aci": []})
        if row.get(L3OUT):
            g["aci"].append(row)
        elif rule.domain is RuleDomain.FIREWALL and rule.action is RuleAction.ALLOW:
            g["fw"].append(row)
    for g in groups.values():
        allow = [r for r in g["aci"] if data.rule(r[RULE]).action is RuleAction.ALLOW]
        if not allow:
            continue  # sperrende Contracts (Taboo) bleiben stehen
        fw_ids = list(dict.fromkeys(r[RULE] for r in g["fw"]))
        missing: list[str] = []
        if fw_ids:
            opened = _contract_allowed(data, allow)
            services = data.services(set(fw_ids))
            for rid in fw_ids:
                missing += services.outside(rid, opened) or []
        missing = list(dict.fromkeys(missing))
        for r in allow:
            if not missing:
                r[CAT_HIDDEN] = True
                continue
            r[NARROW], r[FW_RULES] = missing, fw_ids
            shown = _missing_text(missing)
            r[DESCRIPTION] = f"{r.get(DESCRIPTION) or ''} – " + L(
                f"öffnet weniger als die Firewall-Regeln, es fehlt {shown}",
                f"opens less than the firewall rules, missing {shown}")  # fmt: skip
    return items


def outbound_broad(data: TenantData, row: dict) -> bool:
    """Ausgehende Firewall-Regel, die das System nur ueber eine breite Quelle erfasst (0.103.0)."""
    if row.get(DIRECTION) != "out" or row.get(SCOPE) not in BROAD:
        return False
    rule = data.rule(row.get(RULE))
    return rule is not None and rule.domain is RuleDomain.FIREWALL


def _missing_text(missing: list[str]) -> str:
    return ", ".join(missing[:5]) + (f" (+{len(missing) - 5})" if len(missing) > 5 else "")


def _row_l3outs(data: TenantData, row: dict) -> list[tuple[str, object]]:
    """L3Outs der externen EPGs eines Contracts (Gegenstellen im Scope): (Name, L3Out mit der
    Firewall dahinter bzw. None, wenn er nicht gelesen ist)."""
    out: dict[str, object] = {}
    for dn in (row.get(PARTIES) or {}).get("counter") or []:
        e = data.epgs_by_dn.get(dn)
        if e is None or e.kind != "external" or not e.l3out:
            continue
        if out.get(e.l3out) is None:
            out[e.l3out] = data.aci.behind.of(e.source_system_id, e.aci_tenant, e.l3out)
    return list(out.items())


def _fabric_ports(data: TenantData, rule: Rule, direction: str | None) -> list[str]:
    """Interfaces bzw. Zonen einer Firewall-Regel zum Fabric: ausgehend (System = Quelle) ihr
    Eingang, eingehend ihr Ausgang."""
    raw = rule.raw if isinstance(rule.raw, dict) else {}
    key = "srcintf" if direction == "out" else "dstintf"
    names = [str(e["name"]) for e in raw.get(key) or [] if isinstance(e, dict) and e.get("name")]
    zone = data.zones.get(rule.src_zone_id if direction == "out" else rule.dst_zone_id)
    return names or ([zone] if zone else [])


def _mark_l3outs(data: TenantData, items: list[tuple[int, dict]], vrfs: set | None) -> None:
    """L3Outs: die Firewall entscheidet (ADR 0034, Anwender: „Contracts über L3Os immer nur als
    Hilfs-Contract anschauen … die L3O-Contracts zum External EPG haben immer eine Firewall
    hintendran, welche den Traffic regelt“).

    - Erlaubender Contract zu externen EPGs, hinter deren L3Outs eine Firewall steht
      (`l3out_firewall`): `HELPER` und `CAT_HIDDEN` - keine eigene Zeile. Er steht an den
      erlaubenden Firewall-Regeln dieser Firewall in derselben Richtung (`HELPERS`, Spalte L3Out
      „L3O_inet · Contract CT_inet“). Fehlen ihm Dienste einer dieser Regeln, sperrt er real:
      das steht an der Regel („es fehlt …“), am Contract `NARROW` (Befund).
    - Ohne Firewall hinter einem seiner L3Outs: `L3OUT_NOTE` (Hinweis); gelesen ohne Firewall
      `L3OUT_ALONE` (steht in den Kategorien), nicht gelesen wie 0.78.2.
    - Firewall-Regeln einer Firewall hinter einem L3Out der VRFs des Systems: Spalte L3Out mit
      dessen Namen (`L3OUT_IN` fuer die Sortierung)."""
    behind = data.aci.behind
    helpers: dict[tuple, list[dict]] = defaultdict(list)  # (Richtung, L3Out) -> Contracts
    l3outs: dict[int, object] = {}
    for _idx, row in items:
        if not row.get(L3OUT) or row.get(REL) == relevance_store.IMPOSSIBLE:
            continue
        rule = data.rule(row.get(RULE))
        outs = _row_l3outs(data, row) if rule is not None else []
        if not outs:
            continue
        names = [name for name, _o in outs]
        row[L3OUT_IN] = names[0]
        unread = [name for name, o in outs if o is None]
        alone = [name for name, o in outs if o is not None and not o.firewalls]
        if unread or alone:
            # gelesen ohne Firewall: der Contract regelt allein - er steht in den Kategorien;
            # L3Out nicht gelesen: Firewall unbekannt, weiter wie 0.78.2
            notes = []
            if alone:
                listed = ", ".join(alone)
                notes.append(L(f"keine Firewall hinter {listed} – der Contract regelt allein",
                               f"no firewall behind {listed} – the contract alone controls "
                               "access"))  # fmt: skip
            if unread:
                listed = ", ".join(unread)
                notes.append(L(f"Firewall hinter {listed} nicht bekannt (L3Out nicht gelesen)",
                               f"firewall behind {listed} not known (L3Out not read)"))  # fmt: skip
            row[L3OUT_NOTE] = "; ".join(notes)
            row[L3OUT_ALONE] = not unread
            mark = L("ohne Firewall", "no firewall")
            row[L3OUT_COL] = lines(f"{n} ({mark})" if n in alone else n for n in names)
            continue
        row[L3OUT_COL] = lines(names)
        if rule.action is not RuleAction.ALLOW:
            continue  # sperrende Contracts (Taboo) bleiben stehen
        row[CAT_HIDDEN] = True
        row[HELPER] = {"l3outs": names, "for": []}
        for _name, o in outs:
            l3outs[id(o)] = o
            helpers[(row.get(DIRECTION), id(o))].append(row)
    attached: dict[tuple, list[dict]] = defaultdict(list)  # (Richtung, L3Out) -> Firewall-Regeln
    for _idx, row in items:
        rule = data.rule(row.get(RULE))
        if (not vrfs or rule is None or rule.domain is not RuleDomain.FIREWALL
                or row.get(REL) == relevance_store.IMPOSSIBLE):  # fmt: skip
            continue
        direction = row.get(DIRECTION)
        outs = behind.for_rule(rule.source_system_id, rule.vdom, vrfs,
                               _fabric_ports(data, rule, direction))  # fmt: skip
        if not outs:
            continue
        row[L3OUT_IN], row[L3OUT_COL] = outs[0].name, lines(o.name for o in outs)
        if rule.action is RuleAction.ALLOW:
            for o in outs:
                l3outs.setdefault(id(o), o)
                attached[(direction, id(o))].append(row)
    for (direction, oid), contracts in helpers.items():
        fw_rows = attached.get((direction, oid), [])
        if not fw_rows:
            continue  # die Firewall oeffnet hier nichts (0.78.2): nur Treffer
        name = l3outs[oid].name
        opened = _contract_allowed(data, contracts)
        services = data.services({r[RULE] for r in fw_rows})
        listed: dict[int, dict] = {}
        for c in contracts:
            rule = data.rule(c[RULE])
            listed.setdefault(rule.id, {"rule_id": rule.id,
                                        "name": rule.name or f"#{rule.external_id}"})  # fmt: skip
        missing: list[str] = []
        narrow: list[int] = []
        for fw in fw_rows:
            miss = services.outside(fw[RULE], opened) or []
            fw.setdefault(HELPERS, []).append(
                {"l3out": name, "contracts": list(listed.values()), "missing": miss}
            )
            if miss:
                missing += miss
                narrow.append(fw[RULE])
        for c in contracts:
            c[HELPER]["for"] = list(dict.fromkeys([*c[HELPER]["for"], *(r[RULE] for r in fw_rows)]))
            if missing:
                c[NARROW] = list(dict.fromkeys([*(c.get(NARROW) or []), *missing]))
                c[FW_RULES] = list(dict.fromkeys([*(c.get(FW_RULES) or []), *narrow]))
    for _idx, row in items:  # Spalte L3Out der Firewall-Regeln mit den Hilfs-Contracts
        if row.get(HELPERS):
            shown = {h["l3out"]: h for h in row[HELPERS]}
            names = str(row.get(L3OUT_COL) or "").split("\n")
            row[L3OUT_COL] = lines(_helper_text(shown[n]) if n in shown else n for n in names)


def _helper_text(h: dict) -> str:
    """„L3O_inet · Contract CT_inet:SJ“, mit fehlenden Diensten „ – es fehlt tcp/8443“."""
    names = ", ".join(c["name"] for c in h["contracts"])
    text = f"{h['l3out']} · Contract {names}"
    if h["missing"]:
        shown = _missing_text(h["missing"])
        text += L(f" – es fehlt {shown}", f" – missing {shown}")
    return text


def by_l3out(rows: list[dict]) -> list[dict]:
    """Unspezifische Zugriffe nach dem L3Out, ueber den sie hereinkommen (ADR 0034, Anwender):
    die spezifischen wie bisher zuerst, danach die unspezifischen je L3Out (ohne L3Out
    zuletzt) - nur, wenn eine unspezifische einen L3Out hat."""
    broad = [r for r in rows if r.get(SCOPE) in UNSPECIFIC]
    if not any(r.get(L3OUT_IN) for r in broad):
        return rows
    specific = [r for r in rows if r.get(SCOPE) not in UNSPECIFIC]
    broad.sort(key=lambda r: (not r.get(L3OUT_IN), str(r.get(L3OUT_IN) or "").lower()))
    return specific + broad


def vrf_key(data: TenantData, epg: Epg) -> tuple[int | None, str | None]:
    """(APIC, `Tenant:VRF`) einer EPG."""
    return epg.source_system_id, data.aci.vrf_of(epg)


def scope_allows(data: TenantData, scope: str | None, own: Epg, other: Epg) -> bool:
    """Gegenstelle im Scope des Contracts (`aci_relevance.Classifier.scope_allows`)."""
    return data.aci.scope_allows(scope, own, other)


def fabric_epgs(
    data: TenantData, known: set[int], by_ip: set[int], prefer: frozenset | set = frozenset()
) -> set[int]:
    """EPGs eines Systems im ACI (Dev-Befund 0.42.2): die bekannten (Portgruppe der VM) und
    die nur ueber die IP gefundenen (gelernter Endpunkt, damit verknuepfter Server) - diese nur
    in den VRFs der bekannten. Gleiche Adressen gibt es in mehreren ACI-Tenants bzw. VRFs; ein
    Endpunkt mit der IP einer VM in einer fremden VRF ist ein anderes System. Ohne bekannte EPG
    gelten die VRFs in `prefer` (die der Applikation), sofern eine passt, sonst alle."""
    epgs = data.epgs_by_id
    known = {i for i in known if i in epgs and epgs[i].kind == "epg"}
    found = {i for i in by_ip if i in epgs and epgs[i].kind == "epg"} - known
    allowed = {vrf_key(data, epgs[i]) for i in known}
    if not allowed and prefer:
        allowed = {vrf_key(data, epgs[i]) for i in found} & set(prefer)
    if not allowed:
        return known | found
    return known | {i for i in found if vrf_key(data, epgs[i]) in allowed}


def _ext_rows(data: TenantData, ips: list[str], system_name: str, vrfs: set | None = None) -> list:
    """ACI-Contracts eines Systems ausserhalb des Fabrics (0.28.0): ueber die externen EPGs,
    deren Subnetze seine IP erfassen - mit der Relevanz nach der Zuordnung im ACI (zuordnender
    Scope, laengstes Subnetz der VRF, nicht im Fabric, bei Gleichstand das Routing).

    Steht das System in EPGs (`vrfs` = deren VRFs), zaehlen nur externe EPGs dieser VRFs
    (Dev-Befund 0.42.2): 0.0.0.0/0 einer fremden VRF erfasst jede Adresse, ihre Contracts
    gehoeren zu den Systemen dort, nicht zur Applikation."""
    best: dict[int, tuple[Epg, object, tuple[str, str]]] = {}
    for value in dict.fromkeys(ips):
        ip = relevance_store._ip(value)
        if ip is None:
            continue
        for epg in data.aci.externals(ip):
            if vrfs and vrf_key(data, epg) not in vrfs:
                continue
            if data.aci.inside(epg, ip):
                continue  # im Fabric: die Contracts seiner EPG gelten, nicht die externen
            rel = data.aci.check(epg, ip)
            prev = best.get(epg.id)
            if rel is not None and (
                prev is None or aci_relevance.RANK[rel[0]] < aci_relevance.RANK[prev[2][0]]
            ):
                best[epg.id] = (epg, data.aci.subnet_for(epg, ip), rel)
    out = []
    for epg, net, (state, text) in best.values():
        length = net.prefixlen
        scope = "any" if length == 0 else "specific" if length == net.max_prefixlen else "subnet"
        label = aci_relevance.ext_label(epg)
        ext = L("externe EPG", "external EPG")
        for idx, row in _aci_rows(data, epg, system_name, via=f"{ext} {label} ({net})",
                                  ips=ips):  # fmt: skip
            if row[SCOPE] == "specific":
                row[SCOPE] = scope
            if row[OBJECT].startswith("EPG "):  # „EPG …“ in beiden Sprachen
                row[OBJECT] = f"{ext} {label}"
            row |= {OBJECT_VALUE: str(net), REL: state, REL_TEXT: text}
            out.append((idx, row))
    return out


def _fw_rows(
    data: TenantData,
    rule: Rule,
    sides: dict[str, tuple[str, str, str, str]],
    system_name: str,
    dnat_for_sys: list[NatRule],
    rels: dict[str, dict] | None = None,
    ips: list[str] | None = None,
    own_zones: list[int] | None = None,
) -> list:
    """Firewall-Regel als (Tabelle, Zeile) je Seite, auf der das System steht (Ziel =
    eingehend, Quelle = ausgehend); `sides` = Seite -> (Bezug, Art des Bezugs, Objekt der
    Regel, sein Bereich); `rels` = Seite -> Relevanz (Routing); `ips` = IPs des Systems fuer
    die Anzeige mit dem Namen (EPG: keine).

    NAT nur aus der Regel selbst (Dev-Befund 0.33.0): ihre VIPs, sofern sie auf das System
    zeigen, und ihr SNAT - nicht alle VIPs bzw. Pools des Mandanten, und nie bei sperrenden
    Regeln."""
    objs, zones = data.objs, data.zones
    vips, snat = data.rule_nat(rule)
    mine = {n.id for n in dnat_for_sys}
    rule_dnat = [n for n in vips if n.id in mine]
    out = []
    for side in sorted(sides, key=lambda x: x != "dst"):
        direction = "in" if side == "dst" else "out"
        counterpart_eps = [e for e in rule.endpoints if e.role.value != side]
        cp_objs = [
            objs.get(e.network_object_id) if e.network_object_id else None for e in counterpart_eps
        ]
        cp_labels = [
            _peer_label(o, e.inline_value) for o, e in zip(cp_objs, counterpart_eps, strict=True)
        ]
        cp_external = any(o is not None and o.object_class == ObjectClass.EXTERNAL for o in cp_objs)
        cp_zone = zones.get(rule.src_zone_id) if direction == "in" else zones.get(rule.dst_zone_id)
        cp_side = "src" if direction == "in" else "dst"
        # Gegenstelle any (0.25.0): das, was hinter Interface bzw. Zone der Regel liegt
        cp_any = [e.kind is EndpointKind.ANY for e in counterpart_eps]
        behind = data.relevance.behind(rule, cp_side, cp_zone) if any(cp_any) else None
        if behind is not None:
            cp_labels = [
                f"{label} → {behind['text']}" if is_any else label
                for label, is_any in zip(cp_labels, cp_any, strict=True)
            ]
            cp_external = cp_external or behind["kind"] == "internet"
        # logische Zonen der Gegenstelle (0.101.0); seit 0.106.0 entscheidet ihr Zonentyp mit der
        # Richtung im Namenskonzept - nicht ganz in einer Zone: was hinter dem Interface liegt
        zone_ids = _counterpart_zones(data, counterpart_eps, cp_objs, rule, cp_side, cp_zone)
        # ohne Zone (0.106.0): Zonentyp nach dem Namen der Firewall-Zone, extern bzw. ueber eine
        # VIP das Internet; das System selbst im DC
        zm = data.zone_map
        dnat_in = direction == "in" and bool(rule_dnat)
        cp_hint = zm.name_type(cp_zone) or (zm.name_type("internet")
                                            if cp_external or dnat_in else None)  # fmt: skip
        idx = categorize(data, direction, own_zones or [], zone_ids, own_hint=zm.dc_type,
                         cp_hint=cp_hint)  # fmt: skip
        via, scope, obj, value = sides[side]
        own = _ips_in(ips or [], value)
        row = {
            APP: app_label(system_name, own),
            PEER: lines(cp_labels),
            SERVICE: ", ".join(rule_view.service_labels(rule)),
            PROTECTION: ", ".join(rule.protections) if rule.protections else "",
            ACTION: action_label(rule),
            DEVICE: data.device(rule),
            DESCRIPTION: rule.name or f"#{rule.external_id}",
            ZONE_COL: data.zone_map.text(zone_ids) if zone_ids else "",
            CP_REFS: _refs(counterpart_eps, cp_objs),
        }
        if direction == "in" and rule_dnat:
            # Kette (0.84.0): öffentliche IP → VIP der Firewall → Load Balancer → Server
            ports = _rule_ports(rule)
            row |= {
                DNS_NAME: lines(_vip_dns(data, v) for v in rule_dnat),
                VIP_FW: lines(_vip_text(v) for v in rule_dnat),
                BALANCER: data.balancer_text(rule_dnat, ports),
            }
            server = [f"{ip}:{p}" for ip in own for p in data.server_ports(rule_dnat, ip, ports)]
            if server:
                row[APP] = app_label(system_name, server)
        elif snat and direction == "out" and cp_external:
            row[SNAT_IP] = snat  # ins Internet: die externe IP (0.84.0)
        elif snat:
            row[NAT] = f"SNAT {snat}"
        meta = {RULE: rule.id, DIRECTION: direction, SCOPE: scope, SIDE: side}
        rel = (rels or {}).get(side)
        if rel is not None:
            meta |= {REL: rel["state"], REL_TEXT: rel["text"]}
        out.append((idx, {**row, VIA: via, **meta, OBJECT: obj, OBJECT_VALUE: value}))
    return out


def _refs(eps: list, objs: list) -> list[str]:
    """Adressen der Gegenstelle (Host bzw. Netz der Regel, ohne any) - „innerhalb der
    Applikation“ (0.105.0)."""
    out = []
    for e, o in zip(eps, objs, strict=True):
        value = o.value if o is not None and o.value else e.inline_value
        if e.kind is EndpointKind.ANY or not value:
            return []
        out.append(value.split(" ")[0].removesuffix("/32").removesuffix("/128"))
    return out


def _counterpart_zones(data: TenantData, eps: list, objs: list, rule: Rule | None = None,
                       side: str | None = None, fw_zone: str | None = None,
                       ) -> list[int]:  # fmt: skip
    """Logische Zonen der Gegenstelle einer Firewall-Regel (0.101.0) ueber ihre Werte. Liegt sie
    nicht ganz in Zonen (any, grosser Bereich), zaehlt seit 0.106.0, was hinter dem Interface der
    Regel auf ihrer Seite liegt (`relevance.behind`: Anbindung, geroutete Netze, Default-Route) -
    any: diese Zonen; ein grosser Bereich: seine Zonen, die dort liegen (sonst alle darin)."""
    zm = data.zone_map
    if not zm.active:
        return []
    values: list[str] = []
    for e, o in zip(eps, objs, strict=True):
        if o is not None and o.value:
            values.append(o.value)
        elif e.kind is not EndpointKind.ANY and e.inline_value:
            values.append(e.inline_value)
    has_any = any(e.kind is EndpointKind.ANY for e in eps)
    if values and not has_any and all(zm.match(v)[1] for v in values):
        return zm.zones_of(values)
    there = (zm.behind(data.relevance.behind(rule, side, fw_zone))
             if rule is not None and side is not None else [])  # fmt: skip
    inner = zm.zones_of(values)
    if has_any:
        return list(dict.fromkeys([*inner, *there]))
    narrowed = [z for z in inner if z in there]
    return narrowed or inner


def _ips_in(ips: list[str], value: str | None) -> list[str]:
    """IPs des Systems im Objekt der Regel (Host bzw. Netz), sonst alle."""
    try:
        net = ipaddress.ip_network(str(value or "").split(" ")[0], strict=False) if value else None
    except ValueError:
        net = None
    if net is None:
        return list(ips)
    inside = []
    for ip in ips:
        try:
            if ipaddress.ip_address(ip.split("/")[0]) in net:
                inside.append(ip.split("/")[0])
        except ValueError:
            continue
    return inside or list(ips)


def _rule_ports(rule: Rule) -> list[tuple[int, int]]:
    """TCP/UDP-Ports der Regel (leer = alle)."""
    out = []
    for s in rule.services:
        if s.proto.value in ("tcp", "udp") and s.port_start:
            out.append((s.port_start, s.port_end or s.port_start))
    return out


def _vip_text(vip: NatRule) -> str:
    """VIP der Firewall: „V-EXT_shop: 203.0.113.10 → 10.30.0.50“ (mit Ports bei
    Port-Weiterleitung)."""
    ext = vip.ext_ip or "?"
    to = vip.mapped_ip or "?"
    if vip.portforward:
        ext += f":{vip.ext_port}" if vip.ext_port else ""
        to += f":{vip.mapped_port}" if vip.mapped_port else ""
    return f"{vip.name}: {ext} → {to}"


def _vip_dns(data: TenantData, vip: NatRule) -> str:
    """Externer DNS-Name der VIP: aus dem IPAM, sonst aus dem Namen (V-EXT_www.example.ch)."""
    names = references.dns_names(data.dns, vip.ext_ip) if vip.ext_ip else []
    if names:
        return names[0]
    return nat_store.external_name(vip) or ""


def _nat_context(data: TenantData, ips: set[str]) -> list[NatRule]:
    """VIPs mit einem der IPs als internem Ziel (auch Real Server) - seit 0.79.0 auch die VIPs
    der FortiGate, deren Ziel ein Virtual Service des NSX ALB mit diesen Servern ist."""
    return [
        n
        for n in data.nats
        if n.nat_type == "dnat_vip"
        and ips & {*nat_store.internal_ips(n), *data.balancers.servers(n)}
    ]


def _covered_rows(
    session: Session,
    data: TenantData,
    values: list[str],
    system_name: str,
    overlap: bool,
    direct_ids: set[int] | None = None,
    is_direct=None,
    contained: list[str] | None = None,
    show_ips: bool = True,
    own_zones: list[int] | None = None,
) -> list:
    """Firewall-Regeln eines Systems: direkt (Endpunkt) und alle, deren Quelle/Ziel den Wert
    enthaelt (Host-IP) bzw. sich mit ihm ueberschneidet (EPG-Subnetz) - ueber Netze, Bereiche,
    Gruppen und any, gleichwertig (Dev-Befund 0.10.0). `contained`: Werte, die eine Regel ganz
    enthalten muss (Subnetz eines BD, den die EPG mit anderen teilt, 0.43.1)."""
    cover = rule_index.covers(
        session, data.tenant_id, values, overlap=overlap, domain=RuleDomain.FIREWALL
    )
    if contained:
        more = rule_index.covers(session, data.tenant_id, contained, domain=RuleDomain.FIREWALL)
        for rid, c in more.items():
            for role, side in c.sides.items():
                cover.setdefault(rid, rule_index.Cover()).add(role, *side)
        values = [*values, *contained]
    dnat = _nat_context(data, {v.split("/")[0] for v in values})
    # IPs des Systems zur Anzeige mit seinem Namen (0.84.0); eine EPG nennt nur ihren Pfad
    shown = [v.split("/")[0] for v in values] if show_ips else None
    # eigene Zonen des Systems (0.105.0): Gegenstellen darin sind „innerhalb der eigenen Zone“
    zm = data.zone_map
    own = own_zones if own_zones is not None else (zm.own(values) if zm.active else [])
    out = []
    for r in data.rules((direct_ids or set()) | set(cover)):
        sides: dict[str, tuple[str, str, str, str]] = {}
        c = cover.get(r.id)
        if is_direct is not None:
            direct = L("direkt", "direct")  # Bezug (Anzeige)
            for e in r.endpoints:
                role = e.role.value
                if is_direct(e) and role not in sides:
                    o = data.objs.get(e.network_object_id) if e.network_object_id else None
                    if c is not None and role in c.sides:  # Objektname wie auf der Firewall
                        sides[role] = (direct, "specific", c.via(role), c.value(role))
                    else:
                        sides[role] = (direct, "specific", o.name if o else "", _label(o, None))
        for role in c.sides if c else ():
            sides.setdefault(role, (c.via(role), c.scope(role), c.via(role), c.value(role)))
        if any(side[1] != "any" for side in sides.values()):
            # Server -> any: das System steht gezielt auf einer Seite; dass any auf der Gegenseite
            # es auch umfasst, ist kein eigener Zugriff (Dev-Befund 0.14.0)
            sides = {k: v for k, v in sides.items() if v[1] != "any"}
        zones = {"src": data.zones.get(r.src_zone_id), "dst": data.zones.get(r.dst_zone_id)}
        rels = {s: data.relevance.check(r, s, values, zones[s]) for s in sides}
        out += _fw_rows(data, r, sides, system_name, dnat, rels, shown, own)
    return out


def _epg_values(session: Session, data: TenantData, epg: Epg) -> tuple[list[str], list[str]]:
    """Adressen einer EPG fuer ihre Firewall-Regeln (Dev-Befund 0.43.0): `own` - eigene
    Subnetze, Subnetze eines BD nur mit dieser EPG, IPs ihrer VMs (Portgruppe) und Endpunkte;
    eine Regel, die sich damit ueberschneidet, gilt. `shared` - Subnetze eines BD, den sie mit
    anderen EPGs teilt: eine Regel gilt nur, wenn sie das ganze Subnetz enthaelt; ein Host bzw.
    Teilnetz darin gehoert sonst womoeglich zu einer anderen EPG des BD."""
    bd = (epg.source_system_id, epg.aci_tenant, epg.bd)
    others = epg.bd is not None and any(
        e.id != epg.id and e.kind == "epg" and (e.source_system_id, e.aci_tenant, e.bd) == bd
        for e in data.epgs_by_id.values()
    )
    own: list[str] = []
    shared: list[str] = []
    for s in session.scalars(select(EpgSubnet).where(EpgSubnet.epg_id == epg.id)):
        (shared if s.origin == "bd" and others else own).append(s.ip)
    own += session.scalars(select(EpgEndpoint.ip).where(EpgEndpoint.epg_id == epg.id))
    for vm in epg_store.vms_of(session, [epg.id]).get(epg.id, []):
        own += vm["ips"]
    return list(dict.fromkeys(own)), shared


def epg_doc(session: Session, epg: Epg, data: TenantData | None = None, full: bool = False) -> dict:
    """ACI-EPG als System: Contracts (direkt, vzAny der VRF, ESGs) nach scope/AP in die
    Tabellen 7/8/9 (+ L3Out-Tabellen) und die Firewall-Regeln, die ihre Subnetze bzw.
    Endpunkte erfassen (auch ueber groessere Netze und any). Als Name der ganze ACI-Pfad."""
    data = data or TenantData(session, epg.tenant_id)
    name = epg_store.inventory_label(epg)
    rows_by_table: dict[int, list[dict]] = {c.index: [] for c in data.catalog.ordered()}
    own, shared = _epg_values(session, data, epg)
    items = _aci_rows(data, epg, name)
    zm = data.zone_map
    own_zones = [*zm.zones_of_epgs([epg]), *zm.own(own)] if zm.active else []
    items += _covered_rows(session, data, own, name, overlap=True, contained=shared,
                           show_ips=False, own_zones=list(dict.fromkeys(own_zones)))  # fmt: skip
    for idx, row in mark_categories(data, items, {vrf_key(data, epg)}):
        rows_by_table.setdefault(idx, []).append(row)
    host = session.get(NetworkObject, epg.network_object_id) if epg.network_object_id else None
    return _envelope(
        session,
        name,
        epg.tenant_id,
        host.value if host else None,
        host.site_id if host else None,
        rows_by_table,
        epg_store.vms_of(session, [epg.id]).get(epg.id, []),
        full,
        data.catalog,
    )


def build_doc(session: Session, system_name: str, tenant_id: int | None) -> dict | None:
    """Baut die Tabellen für ein System: Host → Firewall-Tabellen (1–6,10), EPG → ACI (7–9)."""
    sys_obj = session.scalars(
        select(NetworkObject).where(
            NetworkObject.name == system_name,
            NetworkObject.object_class == ObjectClass.HOST,
            *([NetworkObject.tenant_id == tenant_id] if tenant_id is not None else []),
        )
    ).first()
    if sys_obj is not None:
        return host_doc(session, sys_obj)
    name, *where = system_name.split(SEP)  # "EG_x · TE_a · AP_1" bei gleichen Namen
    epg = session.scalars(
        select(Epg).where(
            Epg.name == name,
            Epg.kind == "epg",
            *([Epg.aci_tenant == where[0]] if where else []),
            *([Epg.ap == where[1]] if len(where) > 1 else []),
            *([Epg.tenant_id == tenant_id] if tenant_id is not None else []),
        )
    ).first()
    if epg is not None:
        return epg_doc(session, epg)
    return None


def host_doc(
    session: Session,
    sys_obj: NetworkObject,
    data: TenantData | None = None,
    full: bool = False,
    prefer: frozenset | set = frozenset(),
) -> dict:
    """Doku eines Host-Systems: alle Firewall-Regeln, die den Server erfassen - direkt und ueber
    Netze, Bereiche, Gruppen und any -, dazu die ACI-Contracts der EPG(s) des Servers
    (`prefer` = VRFs der Applikation, siehe `fabric_epgs`)."""
    system_name = sys_obj.name
    tenant_id = sys_obj.tenant_id
    sys_ip = sys_obj.value
    data = data or TenantData(session, tenant_id)

    rows_by_table: dict[int, list[dict]] = {c.index: [] for c in data.catalog.ordered()}
    direct = data.rule_ids(RuleDomain.FIREWALL, network_object_id=sys_obj.id)
    items = _covered_rows(
        session,
        data,
        [sys_ip] if sys_ip else [],
        system_name,
        overlap=False,
        direct_ids=direct,
        is_direct=lambda e: e.network_object_id == sys_obj.id,
    )
    ip = sys_ip.split("/")[0] if sys_ip else None
    vms = virtual.Lookup(session, tenant_id, {ip} if ip else set())
    # ACI: die EPG(s), in denen der Server steht (Portgruppe der VM, gelernter Endpunkt,
    # verknuepft)
    epgs = host_epgs(session, data, sys_obj, vms.epg_ids(ip), prefer)
    for epg in epgs:
        items += _aci_rows(
            data, epg, system_name, via=f"EPG {epg_store.label(epg)}", ips=[ip] if ip else None
        )
    # ausserhalb des Fabrics: ueber die externe EPG, der das ACI die IP zuordnet (0.28.0)
    vrfs = {vrf_key(data, e) for e in epgs}
    items += _ext_rows(data, [ip] if ip else [], system_name, vrfs)
    for idx, row in mark_categories(data, items, vrfs):
        rows_by_table.setdefault(idx, []).append(row)
    info = vms.info(ip)
    return _envelope(
        session,
        system_name,
        tenant_id,
        sys_ip,
        sys_obj.site_id,
        rows_by_table,
        [info] if info else [],
        full,
        data.catalog,
    )


def vm_rows(
    session: Session,
    data: TenantData,
    name: str,
    ips: list[str],
    hosts: list[NetworkObject],
    epg_ids: set[int],
    prefer: frozenset | set = frozenset(),
    mapped: set[str] | frozenset = frozenset(),
) -> list:
    """Zeilen einer VM (Applikations-Doku, 0.14.0): Firewall-Regeln ueber ihre IPs - direkt
    ueber das Host-Objekt einer IP, sonst ueber Netze, Bereiche, Gruppen und any - und die
    Contracts ihrer EPGs (Portgruppe; gelernter Endpunkt bzw. verknuepfter Server nur in den
    VRFs der Portgruppen, siehe `fabric_epgs`).

    Vorgabe vor Gelerntem (0.43.1, R12): Fuer IPs an einer NIC mit EPG (`mapped`) gilt die EPG
    der Portgruppe; ein gelernter Endpunkt mit dieser IP in einer anderen EPG (alt, verschoben,
    gleiche Adresse) bringt keine Contracts."""
    host_ids = {h.id for h in hosts}
    direct: set[int] = set()
    for hid in host_ids:
        direct |= data.rule_ids(RuleDomain.FIREWALL, network_object_id=hid)
    out = _covered_rows(
        session,
        data,
        ips,
        name,
        overlap=False,
        direct_ids=direct,
        is_direct=lambda e: e.network_object_id in host_ids,
    )
    free = [h for h in hosts if not h.value or h.value.split("/")[0] not in mapped]
    free_ids = {h.id for h in free}
    by_ip = {e.id for e in data.epgs_by_id.values() if e.network_object_id in free_ids}
    values = list(dict.fromkeys([*ips, *(h.value.split("/")[0] for h in free if h.value)]))
    values = [v for v in values if v not in mapped]
    if values:
        by_ip |= set(session.scalars(select(EpgEndpoint.epg_id).where(EpgEndpoint.ip.in_(values))))
    ids = fabric_epgs(data, set(epg_ids), by_ip, prefer)
    for i in sorted(ids):
        epg = data.epgs_by_id[i]
        out += _aci_rows(data, epg, name, via=f"EPG {epg_store.inventory_label(epg)}", ips=ips)
    vrfs = {vrf_key(data, data.epgs_by_id[i]) for i in ids}
    # eine IP an einer NIC mit EPG (Portgruppe) liegt im Fabric - keine externe EPG (0.83.1)
    outside = [ip for ip in ips if ip not in mapped]
    return mark_categories(data, out + _ext_rows(data, outside, name, vrfs), vrfs)


def host_epgs(
    session: Session,
    data: TenantData,
    sys_obj: NetworkObject,
    vm_epgs: set[int] = frozenset(),
    prefer: frozenset | set = frozenset(),
) -> list[Epg]:
    """EPGs eines Servers: die Portgruppen seiner VM (`vm_epgs`, Vorgabe vor Gelerntem, 0.43.1),
    sonst ueber die IP (gelernter Endpunkt, verknuepft) - in den VRFs von `prefer`
    (`fabric_epgs`)."""
    if vm_epgs:
        return [data.epgs_by_id[i] for i in sorted(fabric_epgs(data, set(vm_epgs), set()))]
    by_ip = {e.id for e in data.epgs_by_id.values() if e.network_object_id == sys_obj.id}
    if sys_obj.value:
        ip = sys_obj.value.split("/")[0]
        by_ip |= set(session.scalars(select(EpgEndpoint.epg_id).where(EpgEndpoint.ip == ip)))
    ids = fabric_epgs(data, set(vm_epgs), by_ip, prefer)
    return [data.epgs_by_id[i] for i in sorted(ids)]
