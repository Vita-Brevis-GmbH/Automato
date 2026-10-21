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
    """Ersatz fuer VCenterClient: Beispieldaten statt vCenter, Anmeldung wahlweise abgelehnt."""

    fail = False
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
        return tasks._sample()

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
    assert ok and "vc.example.ch" in msg

    _FakeVCenter.fail = True
    ok, msg = tasks.check(_target())
    assert not ok and msg
    result = tasks.collect_once("r2", _target(), via_agent=True)
    assert result.status == "failed" and result.virtual is None
