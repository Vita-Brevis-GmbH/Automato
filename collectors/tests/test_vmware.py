"""VMware-vCenter-Collector (ADR 0017): Normalisierung, Nur-Lese-Schutz, Abruf ohne vCenter."""

from types import SimpleNamespace

import pytest
from sot_gateway.readonly import ReadOnlyViolation

from collectors.vmware import client as vc
from collectors.vmware import tasks
from collectors.vmware.normalize import normalize


def test_normalisierung_vms_portgruppen_vlans_und_ips():
    inv = normalize(tasks._sample())
    vms = {v.name: v for v in inv.vms}
    web = vms["web01-007-ka"]
    assert (web.datacenter, web.cluster, web.host) == (
        "DC-A",
        "clu01-chzrh1",
        "esx01-chzrh1.example.ch",
    )
    (nic,) = web.nics
    assert (nic.portgroup, nic.switch, nic.vlan) == ("TE_ka|AP_007|EG_web01", "DVS-ACI-A", 1107)
    assert nic.ips == ["10.20.0.5"]  # link-local faellt weg
    db = vms["db01-007-ka"]
    backup = db.nics[1]
    assert (backup.portgroup, backup.switch, backup.vlan, backup.ips) == (
        "PG_backup",
        "vSwitch1",
        950,
        ["10.95.0.9"],
    )
    assert vms["tpl-ubuntu"].template is True
    pgs = {p.name: p for p in inv.portgroups}
    assert pgs["TE_ka|AP_007|EG_web01"].vm_count == 1 and pgs["TE_ka|AP_007|EG_web01"].kind == "dvs"
    assert pgs["DVS-ACI-A-Uplinks"].vlan_ranges == "0-4094"
    assert pgs["PG_backup"].kind == "standard" and pgs["PG_backup"].vlan == 950
    assert pgs["VM Network"].vlan is None


def test_gleichnamige_standard_portgruppen_mit_anderem_vlan():
    """Dev-Befund 0.43.0: PG_app auf zwei Hosts mit VLAN 950 bzw. 951 - zwei Netze, jede NIC
    mit dem VLAN ihres Hosts."""

    def host(moid, vlan):
        pg = {"name": "PG_app", "vlan": vlan, "vswitch": "vSwitch1"}
        return {"moid": moid, "name": f"{moid}.example.ch", "portgroups": [pg]}

    def vm(name, moid):
        return {"name": name, "host": moid,
                "nics": [{"key": 1, "label": "NIC 1", "network_name": "PG_app"}]}  # fmt: skip

    inv = normalize({"HostSystem": [host("h1", 950), host("h2", 951)],
                     "VirtualMachine": [vm("a", "h1"), vm("b", "h2")]})  # fmt: skip
    assert sorted((p.name, p.vlan) for p in inv.portgroups) == [("PG_app", 950), ("PG_app", 951)]
    assert {v.name: v.nics[0].vlan for v in inv.vms} == {"a": 950, "b": 951}


class _Stub:
    def __init__(self):
        self.calls = []

    def InvokeMethod(self, mo, info, args, outerStub=None):  # noqa: N802 - pyVmomi-Schnittstelle
        self.calls.append(info.wsdlName)
        return "ok"


def test_nur_lesen_blockiert_schreibende_methoden_vor_dem_senden():
    stub = _Stub()
    vc.guard(stub)
    read = SimpleNamespace(name="RetrievePropertiesEx", wsdlName="RetrievePropertiesEx")
    assert stub.InvokeMethod(None, read, []) == "ok"
    blocked = [("PowerOff", "PowerOffVM_Task"), ("Reconfigure", "ReconfigVM_Task"),
               ("Destroy", "Destroy_Task"), ("CreateSnapshot", "CreateSnapshot_Task")]  # fmt: skip
    for name, wsdl in blocked:  # "Destroy" heisst in Python auch View.DestroyView
        with pytest.raises(ReadOnlyViolation):
            stub.InvokeMethod(None, SimpleNamespace(name=name, wsdlName=wsdl), [])
    assert stub.calls == ["RetrievePropertiesEx"]


def test_abruf_mit_propertycollector_ueber_nachgebaute_verbindung():
    from pyVmomi import vim

    stub = _Stub()
    vc.guard(stub)
    view = vim.view.ContainerView("session[1]view-1", stub)

    class VirtualDisk(SimpleNamespace):  # Name wie in pyVmomi (vim.vm.device.VirtualDisk)
        pass

    nic = SimpleNamespace(
        key=4000,
        macAddress="00:50:56:aa:00:01",
        deviceInfo=SimpleNamespace(label="Network adapter 1"),
        connectable=SimpleNamespace(connected=True),
        backing=SimpleNamespace(port=SimpleNamespace(portgroupKey="dvportgroup-31")),
    )
    vm_props = [
        SimpleNamespace(name="name", val="web01-007-ka"),
        SimpleNamespace(
            name="config.hardware.device", val=[nic, VirtualDisk(capacityInKB=41943040)]
        ),
        SimpleNamespace(name="runtime.powerState", val="poweredOn"),
        SimpleNamespace(name="runtime.host", val=vim.HostSystem("host-11")),
        SimpleNamespace(
            name="guest.net",
            val=[
                SimpleNamespace(
                    deviceConfigId=4000,
                    network="x",
                    macAddress=None,
                    connected=True,
                    ipAddress=["10.20.0.5"],
                )
            ],
        ),  # fmt: skip
    ]
    trunk = [SimpleNamespace(start=100, end=199), SimpleNamespace(start=300, end=300)]
    pg_props = [
        SimpleNamespace(name="name", val="Uplinks"),
        SimpleNamespace(name="key", val="dvportgroup-33"),
        SimpleNamespace(
            name="config.defaultPortConfig", val=SimpleNamespace(vlan=SimpleNamespace(vlanId=trunk))
        ),
    ]
    page = SimpleNamespace(
        objects=[
            SimpleNamespace(obj=vim.VirtualMachine("vm-101"), propSet=vm_props),
            SimpleNamespace(
                obj=vim.dvs.DistributedVirtualPortgroup("dvportgroup-33"), propSet=pg_props
            ),
        ],
        token=None,
    )
    pc = SimpleNamespace(RetrievePropertiesEx=lambda specSet, options: page)
    content = SimpleNamespace(
        rootFolder=vim.Folder("group-d1"),
        viewManager=SimpleNamespace(CreateContainerView=lambda *a: view),
        propertyCollector=pc,
    )
    client = vc.VCenterClient("https://vcenter.example.ch", "ro", "x")
    client._si = SimpleNamespace(RetrieveContent=lambda: content)
    data = client.gather()
    (vm,) = data["VirtualMachine"]
    assert vm["nics"][0]["portgroup_key"] == "dvportgroup-31" and vm["disk_gb"] == 40.0
    assert vm["host"] == "host-11" and vm["guest_net"][0]["ips"] == ["10.20.0.5"]
    assert data["DistributedVirtualPortgroup"][0]["vlan_ranges"] == "100-199,300"
    assert stub.calls == ["DestroyView"]  # View wieder entfernt, sonst nichts aufgerufen
    assert client.probe() == 1  # Pruefung liest wie der Lauf, nur die VM-Namen
    assert stub.calls == ["DestroyView", "DestroyView"]


def _no_permission():
    from pyVmomi import vim

    root = vim.Folder("group-d1")
    missing = vim.fault.NoPermission.EntityPrivileges(entity=root, privilegeIds=["System.View"])
    return vim.fault.NoPermission(
        object=root,
        privilegeId="System.View",
        msg="Permission to perform this operation was denied.",
        missingPrivileges=[missing],
    )


def test_vsphere_fehler_als_satz_mit_hinweis():
    from pyVmomi import vim

    from collectors.common import diagnose

    msg = diagnose.device_error(_no_permission(), "vmware")
    assert msg.startswith(
        "Keine Berechtigung im vCenter: dem Konto fehlt System.View auf vim.Folder:group-d1"
        " (oberste Ebene des Inventars)."
    )
    assert "Read-only" in msg and "weitergeben" in msg and "\n" not in msg
    login = diagnose.device_error(vim.fault.InvalidLogin(msg="Cannot complete login"), "vmware")
    assert login.startswith("Anmeldung abgelehnt (vCenter: Benutzer oder Passwort falsch)")


def test_ohne_zugangsdaten_beispiel_nur_zentral(monkeypatch):
    from collectors.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_token", None)
    assert "VirtualMachine" in tasks.gather(None)
    monkeypatch.setattr(settings, "agent_token", "agent-x")
    with pytest.raises(RuntimeError, match="keine Zugangsdaten"):
        tasks.gather(None)
    ok, msg = tasks.check(None)
    assert not ok and "vCenter" in msg


class _FakeVCenter:
    """Ersatz fuer VCenterClient: Beispieldaten statt vCenter, Anmeldung wahlweise abgelehnt,
    Lesen wahlweise ohne Berechtigung (`fault`) bzw. ohne sichtbare VMs (`vms`)."""

    fail = False
    fault = None
    vms = 3
    last = None

    def __init__(self, url, username, password, verify_tls=True):
        self.host = url.removeprefix("https://")
        self.seen = {"url": url, "username": username, "verify_tls": verify_tls}
        _FakeVCenter.last = self

    def __enter__(self):
        if _FakeVCenter.fail:
            raise PermissionError("Anmeldung abgelehnt")
        return self

    def __exit__(self, *exc):
        return False

    def gather(self):
        if _FakeVCenter.fault is not None:
            raise _FakeVCenter.fault
        return tasks._sample()

    def probe(self):
        if _FakeVCenter.fault is not None:
            raise _FakeVCenter.fault
        return _FakeVCenter.vms

    def ping(self):
        return "2026-10-21T08:00:00Z"


def _target(**kw):
    from collectors.common.dto import Target

    base = {"name": "vc-01", "platform": "vmware", "base_url": "https://vc.example.ch",
            "username": "ro", "password": "x", "verify_tls": False}  # fmt: skip
    return Target(**{**base, **kw})


def test_lauf_und_pruefung_mit_zugangsdaten(monkeypatch):
    _FakeVCenter.fail = False
    monkeypatch.setattr(tasks, "VCenterClient", _FakeVCenter)
    sent = {}
    monkeypatch.setattr(
        tasks, "submit_result", lambda run_id, result, via_agent=False: sent.update(via=via_agent)
    )
    result = tasks.collect_once("r1", _target(), via_agent=True)
    assert result.status == "completed" and {v.name for v in result.virtual.vms} >= {"web01-007-ka"}
    assert _FakeVCenter.last.seen == {
        "url": "https://vc.example.ch",
        "username": "ro",
        "verify_tls": False,
    }
    assert sent == {"via": True}
    ok, msg = tasks.check(_target())
    assert ok and "vc.example.ch" in msg and "3 VMs sichtbar" in msg

    _FakeVCenter.vms = 0  # angemeldet, aber Rolle reicht nicht bis zu den VMs
    ok, msg = tasks.check(_target())
    assert not ok and "keine VM sichtbar" in msg
    _FakeVCenter.vms = 3

    _FakeVCenter.fault = _no_permission()  # Anmeldung geht, Lesen nicht (Dev-Befund 0.11.0)
    ok, msg = tasks.check(_target())
    assert not ok and msg.startswith("Keine Berechtigung im vCenter")
    result = tasks.collect_once("r3", _target(), via_agent=True)
    assert result.status == "failed" and "System.View" in result.message
    assert "dynamicType" not in result.message  # kein mehrzeiliges Fault-Abbild
    _FakeVCenter.fault = None

    _FakeVCenter.fail = True
    ok, msg = tasks.check(_target())
    assert not ok and msg
    result = tasks.collect_once("r2", _target(), via_agent=True)
    assert result.status == "failed" and result.virtual is None


# -- ESXi-Hosts: vmnics und Nachbarn aus CDP/LLDP (1.8.0) -------------------------------------


def test_hosts_mit_vmnics_und_nachbarn_aus_den_beispieldaten():
    """Anwender: „Wird in VMware die Ports und Nachbarschaft nicht ausgelesen von den Hosts?“"""
    inv = normalize(tasks._sample())
    hosts = {h.name: h for h in inv.hosts}
    assert set(hosts) == {"esx01-chzrh1.example.ch", "esx02.example.ch"}
    esx01 = hosts["esx01-chzrh1.example.ch"]
    assert (esx01.vendor, esx01.model, esx01.serial, esx01.version, esx01.mgmt_ip) == (
        "Dell Inc.", "PowerEdge R650", "SN-ESX01-A", "8.0.2", "10.0.10.11")  # fmt: skip
    nics = {p.name: p for p in esx01.pnics}
    vmnic0 = nics["vmnic0"]
    assert (vmnic0.mac, vmnic0.speed, vmnic0.duplex, vmnic0.status) == (
        "b4:96:91:00:11:00", "1000", "full", "up")  # fmt: skip
    nb = vmnic0.neighbor
    # 1.9.0: CDP-Systemname („sw-acc-02“) vor der Geraete-ID („sw-acc-02.example.ch“)
    assert (nb.protocol, nb.remote_name, nb.remote_port, nb.remote_ip, nb.remote_caps) == (
        "cdp", "sw-acc-02", "GigabitEthernet1/0/5", "10.0.0.12", ["switch"])  # fmt: skip
    assert nics["vmnic2"].status == "down" and nics["vmnic2"].neighbor is None  # ohne Link
    lldp = {p.name: p for p in hosts["esx02.example.ch"].pnics}["vmnic0"]
    assert lldp.speed == "10G"  # wie die Switches (NX-OS)
    assert (lldp.neighbor.protocol, lldp.neighbor.remote_name, lldp.neighbor.remote_port) == (
        "lldp", "sw-core-01", "Ethernet1/3")  # fmt: skip
    # Systembeschreibung nur die erste Zeile
    assert lldp.neighbor.remote_platform == "Cisco Nexus Operating System (NX-OS) Software 10.3(4a)"
    assert tasks.summary(tasks._sample(), inv) == (
        "ESXi-Hosts: 2 gelesen, 3 vmnics mit Nachbarn (CDP/LLDP)"
    )


def test_nachbar_cdp_vor_lldp_und_lldp_port_als_mac():
    from collectors.vmware.normalize import hosts_of

    cdp = {"dev_id": "sw-acc-01(FOC123)", "port_id": "Gi1/0/1", "address": "10.0.0.99",
           "mgmt_addr": None, "platform": "cisco C9300", "caps": ["switch", "router"]}  # fmt: skip
    lldp = {
        "chassis_id": "00:11:22:33:44:55",
        "port_id": "00:11:22:33:44:66",
        "params": {"Port Description": "Ethernet1/7", "System Capabilities": "Bridge, Router"},
    }
    host = {"name": "10.0.10.21", "pnics": [
        {"name": "vmnic0", "speed_mb": 25000, "duplex": True, "cdp": cdp, "lldp": lldp},
        {"name": "vmnic1", "speed_mb": 2500, "duplex": False, "cdp": {"dev_id": None},
         "lldp": lldp},
        {"name": "vmnic2", "speed_mb": 1000, "lldp": {"chassis_id": None, "port_id": None,
                                                      "params": {}}},
    ]}  # fmt: skip
    (h,) = hosts_of({"HostSystem": [host]})
    assert h.mgmt_ip == "10.0.10.21"  # Host per IP im vCenter: Name als Verwaltungs-Adresse
    n0, n1, n2 = (p.neighbor for p in h.pnics)
    assert (n0.protocol, n0.remote_name, n0.remote_ip, n0.remote_caps) == (
        "cdp", "sw-acc-01(FOC123)", "10.0.0.99", ["switch", "router"])  # fmt: skip
    # LLDP ohne Systemname: Chassis-ID; Port-ID ist die MAC des Ports -> Port-Beschreibung
    assert (n1.protocol, n1.remote_name, n1.remote_port, n1.remote_caps) == (
        "lldp", "00:11:22:33:44:55", "Ethernet1/7", ["bridge", "router"])  # fmt: skip
    assert n2 is None
    assert [(p.speed, p.duplex) for p in h.pnics] == [("25G", "full"), ("2500", "half"),
                                                     ("1000", None)]  # fmt: skip


def test_cdp_systemname_vor_geraete_id():
    """Anwender: „Die Gegenstelle vom VMware Host wird nicht korrekt erkannt, obwohl im CDP zwar
    die Geräte-ID nicht den Switchnamen steht, sondern eine ID. Aber im Systemname wäre der
    korrekte Name zu finden“ - der Systemname gilt vor der Geraete-ID; ein Systemname allein
    ergibt ebenfalls einen Nachbarn."""
    from pyVmomi import vim

    from collectors.vmware.normalize import neighbor_of

    nic = vim.host.PhysicalNic
    both = vc.hint_plain(nic.NetworkHint(device="vmnic0", connectedSwitchPort=nic.CdpInfo(
        devId="ab12cd34ef56", systemName="sw-acc-01.example.ch", portId="Gi1/0/7",
        mgmtAddr="10.0.0.11")))  # fmt: skip
    assert both["cdp"]["dev_id"] == "ab12cd34ef56"
    nb = neighbor_of({"name": "vmnic0", **both})
    assert (nb.protocol, nb.remote_name, nb.remote_port, nb.remote_ip) == (
        "cdp", "sw-acc-01.example.ch", "Gi1/0/7", "10.0.0.11")  # fmt: skip
    only_name = vc.hint_plain(nic.NetworkHint(device="vmnic1", connectedSwitchPort=nic.CdpInfo(
        systemName="sw-acc-01.example.ch", portId="Gi1/0/8")))  # fmt: skip
    assert only_name["cdp"] is not None and only_name["cdp"]["dev_id"] is None
    nb = neighbor_of({"name": "vmnic1", **only_name})
    assert (nb.protocol, nb.remote_name, nb.remote_port) == ("cdp", "sw-acc-01.example.ch",
                                                             "Gi1/0/8")  # fmt: skip
    # ohne Geraete-ID und ohne Systemname kein CDP-Nachbar (dann LLDP bzw. keiner)
    assert vc._cdp(nic.CdpInfo(portId="Gi1/0/9")) is None
    assert neighbor_of({"cdp": {"dev_id": "ab12cd34ef56", "system_name": " "}}).remote_name == (
        "ab12cd34ef56"
    )


def test_lesende_abfrage_query_network_hint_erlaubt():
    """QueryNetworkHint ist lesend und braucht laut vSphere-API nur System.Read (Rolle
    Read-only) - der Nur-Lese-Schutz laesst sie durch, Schreibendes am Netz nicht."""
    from pyVmomi import vim

    assert "QueryNetworkHint" in vc.READ_METHODS
    ns = vim.host.NetworkSystem("networkSystem-11")
    assert ns._GetMethodInfo("QueryNetworkHint").privId == "System.Read"
    for method in ("UpdateNetworkConfig", "UpdatePhysicalNicLinkSpeed", "RefreshNetworkSystem"):
        assert method not in vc.READ_METHODS
    stub = _Stub()
    vc.guard(stub)
    with pytest.raises(ReadOnlyViolation):
        vim.host.NetworkSystem("networkSystem-11", stub).RefreshNetworkSystem()
    assert vim.host.NetworkSystem("networkSystem-11", stub).QueryNetworkHint() == "ok"
    assert stub.calls == ["QueryNetworkHint"]


def test_host_eigenschaften_als_einfaches_dict():
    from pyVmomi import vim

    info = SimpleNamespace(vendor="Dell Inc.", model="PowerEdge R650", serialNumber=None,
                           otherIdentifyingInfo=[SimpleNamespace(
                               identifierType=SimpleNamespace(key="ServiceTag"),
                               identifierValue="ABC1234")])  # fmt: skip

    def vnic(dev, pg, ip):
        return SimpleNamespace(device=dev, portgroup=pg,
                               spec=SimpleNamespace(ip=SimpleNamespace(ipAddress=ip)))  # fmt: skip

    props = {
        "name": "esx01.example.ch",
        "hardware.systemInfo": info,
        "config.product.version": "8.0.2",
        "config.network.vnic": [vnic("vmk1", "vMotion", "10.0.20.11"),
                                vnic("vmk0", "Management Network", "10.0.10.11")],
        "config.network.pnic": [
            SimpleNamespace(device="vmnic0", mac="b4:96:91:00:11:00",
                            linkSpeed=SimpleNamespace(speedMb=10000, duplex=True)),
            SimpleNamespace(device="vmnic1", mac="b4:96:91:00:11:01", linkSpeed=None),
        ],
        "runtime.connectionState": "connected",
        "configManager.networkSystem": vim.host.NetworkSystem("networkSystem-11"),
    }  # fmt: skip
    host = vc.to_plain("HostSystem", "host-11", props)
    assert (host["vendor"], host["serial"], host["version"], host["mgmt_ip"]) == (
        "Dell Inc.", "ABC1234", "8.0.2", "10.0.10.11")  # fmt: skip
    assert host["network_system"] == "networkSystem-11" and host["connection"] == "connected"
    assert host["pnics"] == [
        {"name": "vmnic0", "mac": "b4:96:91:00:11:00", "speed_mb": 10000, "duplex": True},
        {"name": "vmnic1", "mac": "b4:96:91:00:11:01", "speed_mb": None, "duplex": None},
    ]


class _HintStub:
    """Verbindung mit Netzwerk-Hinweisen je Host: Liste = Antwort, Ausnahme = Fehler."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def InvokeMethod(self, mo, info, args, outerStub=None):  # noqa: N802 - pyVmomi-Schnittstelle
        self.calls.append((info.wsdlName, mo._moId))
        answer = self.answers[mo._moId]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _plain_host(n, state="connected"):
    return {"moid": f"host-{n}", "name": f"esx{n:02d}.example.ch", "connection": state,
            "network_system": f"networkSystem-{n}",
            "pnics": [{"name": "vmnic0"}, {"name": "vmnic1"}]}  # fmt: skip


def test_abfrage_der_nachbarn_scheitert_je_host_ohne_abbruch():
    from pyVmomi import vim, vmodl

    nic = vim.host.PhysicalNic
    hint = nic.NetworkHint(
        device="vmnic0",
        connectedSwitchPort=nic.CdpInfo(
            devId="sw-acc-01", portId="GigabitEthernet1/0/10", mgmtAddr="10.0.0.11",
            hardwarePlatform="cisco C9300-48P",
            deviceCapability=nic.CdpDeviceCapability(networkSwitch=True, router=False)),
    )  # fmt: skip
    lldp = nic.NetworkHint(
        device="vmnic1",
        lldpInfo=nic.LldpInfo(chassisId="00:11:22:33:44:55", portId="Ethernet1/3",
                              timeToLive=120, parameter=[
                                  vmodl.KeyAnyValue(key="System Name", value="sw-core-01"),
                                  vmodl.KeyAnyValue(key="Management Address", value="10.0.0.2")]),
    )  # fmt: skip
    stub = _HintStub(
        {
            "networkSystem-1": [hint, lldp],
            "networkSystem-2": vim.fault.NoPermission(privilegeId="System.Read"),
        }
    )
    vc.guard(stub)
    client = vc.VCenterClient("https://vcenter.example.ch", "ro", "x")
    client._si = SimpleNamespace(_stub=stub)
    hosts = [_plain_host(1), _plain_host(2), _plain_host(3, "disconnected")]
    client.neighbors(hosts)
    assert stub.calls == [
        ("QueryNetworkHint", "networkSystem-1"),
        ("QueryNetworkHint", "networkSystem-2"),
    ]  # getrennt: nicht gefragt
    ok, denied, gone = hosts
    assert ok["pnics"][0]["cdp"]["dev_id"] == "sw-acc-01" and ok["pnics"][0]["lldp"] is None
    assert ok["pnics"][0]["cdp"]["caps"] == ["switch"]
    assert ok["pnics"][1]["lldp"]["params"] == {"System Name": "sw-core-01",
                                                "Management Address": "10.0.0.2"}  # fmt: skip
    assert "neighbors_error" not in ok
    assert denied["neighbors_error"] == "NoPermission" and "cdp" not in denied["pnics"][0]
    assert gone["neighbors_error"] == "disconnected"
    inv = normalize({"HostSystem": hosts})
    assert [h.name for h in inv.hosts] == [h["name"] for h in hosts]  # alle Hosts, auch ohne
    nbs = {(h.name, p.name): p.neighbor for h in inv.hosts for p in h.pnics}
    assert nbs[("esx01.example.ch", "vmnic1")].remote_ip == "10.0.0.2"
    assert nbs[("esx02.example.ch", "vmnic0")] is None
    assert tasks.summary({"HostSystem": hosts}, inv) == (
        "ESXi-Hosts: 3 gelesen, 2 vmnics mit Nachbarn (CDP/LLDP) · Hinweise: "
        "esx02.example.ch: CDP/LLDP nicht lesbar (NoPermission); "
        "esx03.example.ch: CDP/LLDP nicht lesbar (disconnected)"
    )


def test_haengt_das_vcenter_werden_die_uebrigen_hosts_nicht_mehr_gefragt():
    stub = _HintStub({f"networkSystem-{n}": TimeoutError("timed out") for n in range(1, 6)})
    vc.guard(stub)
    client = vc.VCenterClient("https://vcenter.example.ch", "ro", "x")
    client._si = SimpleNamespace(_stub=stub)
    hosts = [_plain_host(n) for n in range(1, 6)]
    client.neighbors(hosts)
    assert len(stub.calls) == vc.MAX_TRANSPORT_ERRORS
    assert [h["neighbors_error"] for h in hosts] == ["TimeoutError"] * 3 + [vc.NOT_QUERIED] * 2
    summary = tasks.summary({"HostSystem": hosts}, normalize({"HostSystem": hosts}))
    assert summary.endswith("CDP/LLDP von 2 weiteren Hosts nicht abgefragt (vCenter antwortet "
                            "nicht)")  # fmt: skip


def test_nur_lesen_auch_beim_abfragen_der_nachbarn(monkeypatch):
    """Ein blockierter Aufruf ist ein Fehler im Code - er bricht den Lauf ab statt Hinweis."""
    stub = _HintStub({"networkSystem-1": []})
    monkeypatch.setattr(vc, "READ_METHODS", vc.READ_METHODS - {"QueryNetworkHint"})
    vc.guard(stub)
    client = vc.VCenterClient("https://vcenter.example.ch", "ro", "x")
    client._si = SimpleNamespace(_stub=stub)
    with pytest.raises(ReadOnlyViolation):
        client.neighbors([_plain_host(1)])
    assert stub.calls == []


def test_lauf_meldet_hinweise_und_hosts(monkeypatch):
    sample = tasks._sample()
    sample["HostSystem"][1]["neighbors_error"] = "NoPermission"
    for nic in sample["HostSystem"][1]["pnics"]:
        nic["cdp"] = nic["lldp"] = None
    monkeypatch.setattr(tasks, "gather", lambda target: sample)
    monkeypatch.setattr(tasks, "submit_result", lambda run_id, result, via_agent=False: None)
    result = tasks.collect_once("r1", _target(), via_agent=True)
    assert result.status == "completed"  # Hinweis statt Abbruch
    assert result.message == (
        "ESXi-Hosts: 2 gelesen, 2 vmnics mit Nachbarn (CDP/LLDP) · Hinweise: "
        "esx02.example.ch: CDP/LLDP nicht lesbar (NoPermission)"
    )
    assert {h.name for h in result.virtual.hosts} == {"esx01-chzrh1.example.ch",
                                                      "esx02.example.ch"}  # fmt: skip
