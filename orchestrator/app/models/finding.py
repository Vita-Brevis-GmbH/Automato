"""Datenqualitaet / Reconciliation (ADR 0004).

Abweichungen werden read-only sichtbar gemacht und sind quittierbar. Die
schreibende Bereinigung (Objekt im DDI anlegen, Regel anpassen/ablehnen/loeschen)
folgt erst mit dem Schreibpfad. Statusaenderungen laufen ins Audit-Log.

Findings sind **abgeleiteter Zustand** des Mandanten: der Abgleich (`app.store.reconcile`)
berechnet sie nach jedem Lauf neu; Triage-Status und Notiz bleiben erhalten, solange der
Befund besteht (ADR 0010).
"""

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FindingType(enum.StrEnum):
    UNMATCHED_OBJECT = "unmatched_object"  # Name in Regel, nicht im SoT
    INVALID_NAME = "invalid_name"  # passt nicht zur Namenskonvention
    AMBIGUOUS_IP = "ambiguous_ip"  # IP auf mehreren Objekten
    DUPLICATE_VALUE = "duplicate_value"
    NAME_TYPE_MISMATCH = "name_type_mismatch"  # Praefix H_ vs. SoT-Typ Netzwerk
    EPG_NO_ENDPOINTS = "epg_no_endpoints"  # EPG ohne gelernte Endpunkte (ACI)
    NAME_MISMATCH = "name_mismatch"  # Objektname passt nicht zum DNS-Namen im IPAM
    UNUSED_OBJECT = "unused_object"  # Firewall-Objekt in keiner Regel/Gruppe verwendet
    SUBNET_NOT_IN_IPAM = "subnet_not_in_ipam"  # ACI-/FortiGate-Subnetz fehlt im IPAM
    VLAN_MISMATCH = "vlan_mismatch"  # VLAN des Subnetzes weicht vom IPAM ab
    SERVICE_MISMATCH = "service_mismatch"  # Service-Name passt nicht zu Protokoll/Port
    # Standardobjekt des Herstellers (FortiOS-Service, ACI-Filter in common) in einer
    # erlaubenden Regel - nicht nach Konvention benannt, soll nicht verwendet werden (0.36.0)
    PREDEFINED_SERVICE_USED = "predefined_service_used"
    RULE_INCOMPLETE = "rule_incomplete"  # Regel ohne Quelle bzw. Ziel (ACI: nicht wirksam)
    VM_NOT_IN_IPAM = "vm_not_in_ipam"  # IP einer VM (vCenter) fehlt im fuehrenden IPAM
    PORTGROUP_NO_EPG = "portgroup_no_epg"  # Portgruppe nach ACI-Schema ohne passende EPG
    # Portgruppe nach ACI-Schema, nur ueber das VLAN einer anders benannten EPG zugeordnet
    PORTGROUP_NAME_MISMATCH = "portgroup_name_mismatch"
    # Parallele Firewalls (Verbund, 0.18.0)
    PARALLEL_RULE_MISMATCH = "parallel_rule_mismatch"  # gleiche Policy-ID, anderer Inhalt
    PARALLEL_RULE_MISSING = "parallel_rule_missing"  # Policy fehlt auf einer Firewall
    PARALLEL_OBJECT_MISMATCH = "parallel_object_mismatch"  # gleiches Objekt, andere Werte
    # Verbund (0.72.0): Policy-Route/SD-WAN-Regel, Routen eines VDOMs bzw. VIP/IP-Pool anders
    # oder fehlend auf einer parallelen Firewall
    PARALLEL_POLICY_ROUTE_MISMATCH = "parallel_policy_route_mismatch"
    PARALLEL_ROUTE_MISMATCH = "parallel_route_mismatch"
    PARALLEL_NAT_MISMATCH = "parallel_nat_mismatch"
    # ACI (0.45.1): Consumer bzw. Provider ohne Gegenstelle im Scope des Contracts (AP, VRF,
    # Tenant) - der Contract verbindet sie nicht
    ACI_SCOPE_UNPAIRED = "aci_scope_unpaired"
    # ACI (0.79.1): EPG ist Consumer und Provider desselben Contracts - er gilt zwischen solchen
    # EPGs in beide Richtungen, gegenueber sich selbst wirkt er nicht
    ACI_CONTRACT_BOTH_SIDES = "aci_contract_both_sides"
    # Konfiguration vor Gelerntem (0.46.0): das ACI hat eine IP in einer anderen EPG gelernt als
    # konfiguriert (Portgruppe der VM bzw. statischer Endpunkt, gleiche VRF)
    EPG_ASSIGNMENT_CONFLICT = "epg_assignment_conflict"
    # SNAT (0.47.0): Policy mit NAT auf einen IP-Pool, den die Firewall nicht (mehr) hat
    SNAT_POOL_MISSING = "snat_pool_missing"
    # Topologie (0.50.0, `store/topo_model`): Plausibilitaet der gelesenen Netze und Routen
    TOPO_DUPLICATE_IP = "topo_duplicate_ip"  # dieselbe IP auf zwei Knoten im selben Netz
    TOPO_MASK_MISMATCH = "topo_mask_mismatch"  # Gegenstellen mit verschiedener Netzmaske
    TOPO_ROUTE_LOOP = "topo_route_loop"  # A leitet ueber B, B zurueck ueber A
    TOPO_STATIC_UNUSED = "topo_static_unused"  # statische Route ohne Wirkung (nicht in der RIB)
    TOPO_BGP_DOWN = "topo_bgp_down"  # BGP-Sitzung nicht aufgebaut
    # Logische Zonen (0.51.0, `store/zones`): erlaubende Regel zwischen Zonen mit Absicht
    # „gesperrt“ bzw. dasselbe Praefix in zwei Zonen
    ZONE_POLICY_VIOLATION = "zone_policy_violation"
    ZONE_OVERLAP = "zone_overlap"
    # Grundschema (0.78.0, ADR 0024): logische Zone, die zu keiner Zone des Grundschemas im
    # Namensprofil passt (Name, Kurzname bzw. Erkennungsmuster)
    ZONE_OUTSIDE_SCHEMA = "zone_outside_schema"
    # Ausgaenge der Zonen (0.105.0, ADR 0035): ein Netz der Zone lernt die Firewall nicht ueber
    # einen Ausgang dieser Zone (anderes Interface, nur Default-Route bzw. keine Route)
    ZONE_NET_EXIT = "zone_net_exit"
    # Verbindungen Ende zu Ende (0.59.0, ADR 0021): erlaubende Regel einer Applikation, deren
    # Verbindung der Weg sperrt, die nicht auf dem Weg liegt bzw. dort nicht greift
    APP_CONNECTION_MISMATCH = "app_connection_mismatch"
    # 0.78.2: Contract Richtung L3Out oeffnet weniger als die Firewall-Regeln (fuehrend)
    APP_CONTRACT_NARROWER = "app_contract_narrower"
    # 0.107.0 (Anwender): Regel einer Applikation auf einem Geraet, ueber das der Weg ihrer
    # Verbindungen nicht geht - sie wird fuer diesen Verkehr nie gebraucht
    APP_RULE_UNUSED = "app_rule_unused"
    # Verkabelung (0.62.0, ADR 0025, `store/cabling`): Verbindung zwischen zwei gelesenen
    # Switches mit abweichender Konfiguration bzw. nur von einer Seite gemeldet
    LINK_SPEED_MISMATCH = "link_speed_mismatch"
    LINK_DUPLEX_MISMATCH = "link_duplex_mismatch"
    LINK_VLAN_MISMATCH = "link_vlan_mismatch"
    LINK_LAG_MISMATCH = "link_lag_mismatch"
    LINK_ONE_SIDED = "link_one_sided"
    # Endpunkte (0.63.0, `store/l2`): VLAN der VMs eines ESXi-Hosts fehlt an seinem Uplink
    UPLINK_VLAN_MISSING = "uplink_vlan_missing"
    # L2-Sicht (0.64.0, `store/l2_topo`): Redundanz und VLAN-Wege
    L2_SINGLE_LINK = "l2_single_link"
    L2_SINGLE_SWITCH = "l2_single_switch"
    L2_SINGLE_POINT = "l2_single_point"
    LAG_DEGRADED = "lag_degraded"
    VLAN_SPLIT = "vlan_split"
    # vPC-Paar (0.65.0): Peer nicht verbunden, vPC nur auf einem Peer, down bzw. inkonsistent
    VPC_MISMATCH = "vpc_mismatch"
    # Datenqualitaet (ADR 0029, `store/data_quality`): Stand eines Quellsystems aelter als die
    # Grenze (AUTOMATO_STALE_DAYS, Zeitplan beachtet) bzw. letzter Lauf nur teilweise gelesen
    # (Geraete nicht erreicht, nicht lesbar, Routing-Kontexte unvollstaendig)
    SOURCE_STALE = "source_stale"
    SOURCE_PARTIAL = "source_partial"
    # von Hand erfasst (0.109.0, z. B. aus „Warum dieser Weg?“): bleibt, bis jemand ihn erledigt
    MANUAL = "manual"
    # Netz einer Regel groesser als die Routen (0.111.0, ADR 0040): das Routing kennt es nicht als
    # Ganzes (Teile genauer geroutet bzw. nur die Default-Route), keine Zone/Anbindung bzw.
    # Zuordnung von Hand sagt, wohin es gehoert
    NET_WIDER_THAN_ROUTES = "net_wider_than_routes"
    # BGP-Filter (0.113.0, ADR 0041, `store/bgp_filters`): Filter erlaubt Netze, der Nachbar
    # liefert nichts davon; erhalten, obwohl der Filter sperrt; eBGP-Nachbar ohne Eingangsfilter;
    # ACI: import-rtctrl eingetragen, aber am L3Out nicht erzwungen (wirkt nicht)
    BGP_EXPECTED_MISSING = "bgp_expected_missing"
    BGP_OUTSIDE_FILTER = "bgp_outside_filter"
    BGP_NO_INBOUND_FILTER = "bgp_no_inbound_filter"
    ACI_IMPORT_NOT_ENFORCED = "aci_import_not_enforced"
    # Stufe 2 (0.114.0): Eingangsfilter passt nicht zu den Zonen hinter dem Nachbarn (Netz der
    # Zonen gesperrt, Ausschluss erlaubt, Regel ausserhalb der Zonen bzw. ohne Filter)
    BGP_ZONE_MISMATCH = "bgp_zone_mismatch"
    # Nachkontrolle des NetBox-Syncs (0.115.0, ADR 0042): nach dem Uebernehmen steht ein
    # geschriebenes Objekt nicht so in NetBox (fehlt, Felder anders, nicht geloescht)
    NETBOX_SYNC_MISMATCH = "netbox_sync_mismatch"


class Severity(enum.StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class FindingStatus(enum.StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class SubjectKind(enum.StrEnum):
    RULE = "rule"
    NETWORK_OBJECT = "network_object"
    EPG = "epg"
    DEVICE_OBJECT = "device_object"
    SUBNET = "subnet"
    VM = "vm"
    PORTGROUP = "portgroup"
    NODE = "node"  # Knoten der Topologie (`<Quellsystem>|<Kontext>`, 0.50.0)
    LINK = "link"  # Verbindung der Verkabelung (`<Geraet>|<Port>~<Geraet>|<Port>`, 0.62.0)
    # bzw. ein Port (`<Geraet>|<Port>`, 0.63.0), Geraet, Geraetepaar bzw. `vlan|<VLAN>` (0.64.0)
    NAT = "nat"  # VIP bzw. IP-Pool (`<Quellsystem>|<VDOM>|<Art>|<Familie>|<Name>`, 0.72.0)
    ZONE = "zone"  # logische Zone (Name, 0.78.0)
    SOURCE = "source"  # Quellsystem (Name; Datenqualitaet, ADR 0029)
    PATH = "path"  # Weg `<Quelle> → <Ziel>` (von Hand erfasst, 0.109.0)
    NETBOX = "netbox"  # Objekt in der Ziel-NetBox (`<NetBox>|<Art>|<Schluessel>`, 0.115.0)


class Finding(Base):
    __tablename__ = "finding"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    type: Mapped[FindingType] = mapped_column(Enum(FindingType, native_enum=False, length=32))
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, native_enum=False, length=8), default=Severity.WARNING
    )
    status: Mapped[FindingStatus] = mapped_column(
        Enum(FindingStatus, native_enum=False, length=16), default=FindingStatus.OPEN, index=True
    )
    subject_kind: Mapped[SubjectKind] = mapped_column(
        Enum(SubjectKind, native_enum=False, length=16)
    )
    subject_id: Mapped[str] = mapped_column(String(300))
    # Quellsystem, auf dessen Daten der Befund beruht (None = mandantenweit)
    source_system_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
