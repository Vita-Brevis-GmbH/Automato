"""Standard-Namensprofil „default" (Vorgabe der Plattform, ADR 0004-Ergänzung).

Eine Quelle für (a) die eingebaute Rückfall-Konvention des Parsers (`app.naming`), (b) das
per Migration angelegte Profil „default" und (c) die Vorlage beim Anlegen neuer Profile.
Enthält nur Konventionsregeln (Präfixe, Codes, Standard-Services) - keine
kundenspezifischen Laufzeitdaten wie GG-Kürzel oder Projektnummern; die pflegt der Kunde
in der App. Bewusst ohne Abhängigkeiten, damit auch Migrationen es importieren können.
"""

DEFAULT_PROFILE = "default"

DEFAULT_DICTIONARY: dict[str, list[tuple[str, str]]] = {
    # Objekt-Präfix -> Objektklasse (Werte siehe app.naming._VALUE_TO_CLASS)
    "object_prefix": [
        ("H_", "host"),
        ("H-EXT_", "host_external"),
        ("N_", "network"),
        ("N-EXT_", "network_external"),
        ("T_", "transfer"),
        ("V-EXT_", "vip_dnat"),
        ("VIP_", "vip"),
        ("IPP_", "snat_pool"),
        ("R_", "range"),
        # Services (FortiGate) und Filter (ACI) - kein Adressobjekt, Pruefung der Dienste (0.36.0)
        ("FT_", "service"),
    ],
    "utm_prefix": [
        ("AV_", "antivirus"),
        ("WF_", "webfilter"),
        ("DF_", "dnsfilter"),
        ("AC_", "application_control"),
        ("IPS_", "ips"),
        ("WAF_", "waf"),
        ("SSL_", "ssl_inspection"),
    ],
    "color_code": [
        ("13", "intern_host"),
        ("16", "intern_network"),
        ("18", "swisscom_host"),
        ("2", "swisscom_network"),
        ("6", "mpls_host"),
        ("4", "mpls_network"),
        ("23", "vpn_host"),
        ("21", "vpn_network"),
        ("9", "extern_host"),
        ("8", "extern_network"),
    ],
    "l3out": [
        ("L3O_inet", "internet"),
        ("L3O_res", "ressourcen"),
        ("L3O_vpn", "vpn"),
        ("L3O_bit", "unternehmensnetz"),
        ("L3O_sra", "remote_access"),
    ],
    "function_code": [
        ("app", "Applikationsserver"),
        ("web", "Webserver"),
        ("dbs", "Datenbankserver"),
        ("fwl", "Firewall"),
        ("fwc", "Firewall-Cluster"),
        ("lba", "Loadbalancer"),
        ("orc", "Orchestrator"),
        ("ddi", "IPAM/DNS/DHCP"),
        ("vho", "VMware-Host"),
    ],
    "site_code": [
        ("000", "RZ01 / Hauptstandort"),
    ],
    "service": [
        ("FT_https", "tcp/443"),
        ("FT_http", "tcp/80"),
        ("FT_ssh", "tcp/22"),
        ("FT_dns", "tcp+udp/53"),
        ("FT_rdp", "tcp/3389"),
        ("FT_mssql", "tcp+udp/1433:1434"),
        ("FT_ldap", "tcp+udp/389"),
        ("FT_ldapssl", "tcp/636"),
        ("FT_smtp", "tcp/25"),
        ("FT_ntp", "tcp+udp/123"),
        ("FT_snmp", "udp/161"),
        ("FT_syslog", "udp/514"),
    ],
}
