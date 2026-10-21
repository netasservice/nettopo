"""Tests for the per-port endpoint table behind `ports.csv` (PROJECT_SPEC.md section 8)."""

from __future__ import annotations

from nettopo.export.port_table import PORT_TABLE_HEADER, PortRow, build_port_rows
from nettopo.model.entities import (
    ArpEntry,
    Device,
    DeviceRole,
    Interface,
    InterfaceType,
    Link,
    MacEntry,
    NetworkModel,
)


def _model() -> NetworkModel:
    """`acc1` (captured) uplinked to `dist1` (captured), with a phone + PC and a server.

    - Gi1/0/1: uplink to dist1, stored from dist1's side (reverse direction).
    - Gi1/0/5: a server, no CDP neighbor, one MAC.
    - Gi1/0/6: an IP phone (CDP) with a PC behind it; two MACs in two VLANs.
    - Gi1/0/7: access port, nothing learned.
    - Gi1/0/8: admin-down.
    """
    model = NetworkModel()
    acc1 = Device(hostname="acc1", is_source=True, role=DeviceRole.SWITCH)
    acc1.interfaces = {
        name: Interface(name=name, type=InterfaceType.PHYSICAL, admin_up=True, oper_up=True)
        for name in ("Gi1/0/1", "Gi1/0/5", "Gi1/0/6", "Gi1/0/10")
    }
    acc1.interfaces["Gi1/0/1"].mode = "trunk"
    acc1.interfaces["Gi1/0/5"].vlan = 10
    acc1.interfaces["Gi1/0/5"].description = "server-01"
    acc1.interfaces["Gi1/0/5"].speed = "a-1000"
    acc1.interfaces["Gi1/0/5"].duplex = "a-full"
    acc1.interfaces["Gi1/0/6"].vlan = 10
    acc1.interfaces["Gi1/0/7"] = Interface(
        name="Gi1/0/7", type=InterfaceType.PHYSICAL, admin_up=True, oper_up=False, vlan=20
    )
    acc1.interfaces["Gi1/0/8"] = Interface(
        name="Gi1/0/8", type=InterfaceType.PHYSICAL, admin_up=False, oper_up=False
    )
    acc1.interfaces["Vl10"] = Interface(name="Vl10", type=InterfaceType.SVI, vlan=10)

    dist1 = Device(hostname="dist1", is_source=True, role=DeviceRole.L3_SWITCH, platform="C9500")
    dist1.interfaces["Gi1/0/1"] = Interface(name="Gi1/0/1", type=InterfaceType.PHYSICAL)
    phone = Device(hostname="SEP001B5432AAAA", role=DeviceRole.PHONE, platform="CP-8845")

    model.devices = {"acc1": acc1, "dist1": dist1, phone.hostname: phone}
    model.links = [
        Link("dist1", "Gi1/0/1", "acc1", "Gi1/0/1", remote_platform="cisco C9300"),
        Link("acc1", "Gi1/0/6", phone.hostname, "Port 1", remote_platform="CP-8845"),
    ]
    model.mac_entries = [
        MacEntry("acc1", "0050.56a1.0001", 10, "Gi1/0/5", "dynamic"),
        MacEntry("acc1", "001b.5432.aaaa", 30, "Gi1/0/6", "dynamic"),
        MacEntry("acc1", "0050.56a1.0002", 10, "Gi1/0/6", "dynamic"),
        MacEntry("acc1", "0050.56a1.0002", 10, "Gi1/0/6", "dynamic"),
        MacEntry("acc1", "0019.e7aa.0001", 10, "Gi1/0/1", "dynamic"),
        MacEntry("dist1", "0050.56a1.0001", 10, "Gi1/0/1", "dynamic"),
    ]
    model.arp_entries = [
        ArpEntry("dist1", "10.10.10.51", "0050.56a1.0001", "Vl10"),
        ArpEntry("dist1", "10.10.10.9", "0050.56a1.0001", "Vl10"),
        ArpEntry("dist1", "10.10.10.52", "0050.56a1.0002", "Vl10"),
    ]
    return model


def _rows(model: NetworkModel, device: str, interface: str) -> list[PortRow]:
    return [
        row for row in build_port_rows(model) if row.device == device and row.interface == interface
    ]


def test_header_matches_the_agreed_column_order() -> None:
    assert PORT_TABLE_HEADER == (
        "device",
        "interface",
        "status",
        "description",
        "speed",
        "duplex",
        "mac",
        "vlan",
        "ip",
        "neighbor",
        "neighbor_interface",
        "neighbor_platform",
    )


def test_rows_are_sorted_by_device_then_natural_interface_order() -> None:
    keys = [(row.device, row.interface) for row in build_port_rows(_model())]
    acc1_ports = [interface for device, interface in keys if device == "acc1"]
    assert acc1_ports == [
        "Gi1/0/1",
        "Gi1/0/5",
        "Gi1/0/6",
        "Gi1/0/6",
        "Gi1/0/7",
        "Gi1/0/8",
        "Gi1/0/10",
        "Vl10",
    ]
    assert keys == sorted(keys, key=lambda key: key[0])


def test_only_captured_devices_get_rows() -> None:
    assert {row.device for row in build_port_rows(_model())} == {"acc1", "dist1"}


def test_an_uplink_shows_its_neighbor_and_hides_its_macs() -> None:
    (row,) = _rows(_model(), "acc1", "Gi1/0/1")
    assert row.mac is None
    assert row.neighbor == "dist1"
    assert row.neighbor_interface == "Gi1/0/1"
    assert row.vlan == "trunk"


def test_neighbor_platform_comes_from_the_neighbor_device_not_the_reversed_link() -> None:
    """The link is stored from dist1's side, so its `remote_platform` describes acc1."""
    (row,) = _rows(_model(), "acc1", "Gi1/0/1")
    assert row.neighbor_platform == "C9500"


def test_a_server_port_lists_its_mac_with_every_ip_numerically_sorted() -> None:
    (row,) = _rows(_model(), "acc1", "Gi1/0/5")
    assert (row.mac, row.vlan, row.ip) == ("0050.56a1.0001", 10, "10.10.10.9;10.10.10.51")
    assert (row.description, row.speed, row.duplex, row.status) == (
        "server-01",
        "a-1000",
        "a-full",
        "up",
    )
    assert row.neighbor is None


def test_a_phone_port_lists_every_mac_once_with_the_phone_on_each_row() -> None:
    rows = _rows(_model(), "acc1", "Gi1/0/6")
    assert [(row.mac, row.vlan, row.ip) for row in rows] == [
        ("0050.56a1.0002", 10, "10.10.10.52"),
        ("001b.5432.aaaa", 30, None),
    ]
    assert {(row.neighbor, row.neighbor_interface, row.neighbor_platform) for row in rows} == {
        ("SEP001B5432AAAA", "Port 1", "CP-8845")
    }


def test_a_port_with_nothing_learned_gets_one_row_with_its_access_vlan() -> None:
    (row,) = _rows(_model(), "acc1", "Gi1/0/7")
    assert (row.mac, row.vlan, row.status) == (None, 20, "down")


def test_status_reports_admin_down_and_stays_empty_when_unknown() -> None:
    model = _model()
    assert _rows(model, "acc1", "Gi1/0/8")[0].status == "admin-down"
    assert _rows(model, "acc1", "Vl10")[0].status is None


def test_an_svi_row_carries_its_vlan() -> None:
    (row,) = _rows(_model(), "acc1", "Vl10")
    assert row.vlan == 10


def test_a_neighbor_of_unknown_role_is_treated_as_an_endpoint() -> None:
    model = _model()
    model.devices["SEP001B5432AAAA"].role = DeviceRole.UNKNOWN
    assert len(_rows(model, "acc1", "Gi1/0/6")) == 2


def test_port_channel_and_member_ports_are_uplinks_named_by_the_remote_bundle() -> None:
    model = _model()
    acc1, dist1 = model.devices["acc1"], model.devices["dist1"]
    acc1.interfaces["Gi1/0/1"].po_id = 1
    acc1.interfaces["Gi1/0/2"] = Interface(name="Gi1/0/2", type=InterfaceType.PHYSICAL, po_id=1)
    acc1.interfaces["Po1"] = Interface(
        name="Po1", type=InterfaceType.PORT_CHANNEL, mode="trunk", po_members=["Gi1/0/1", "Gi1/0/2"]
    )
    dist1.interfaces["Gi1/0/1"].po_id = 7
    dist1.interfaces["Gi1/0/2"] = Interface(name="Gi1/0/2", type=InterfaceType.PHYSICAL, po_id=7)
    dist1.interfaces["Po7"] = Interface(
        name="Po7", type=InterfaceType.PORT_CHANNEL, po_members=["Gi1/0/1", "Gi1/0/2"]
    )
    model.links.append(Link("acc1", "Gi1/0/2", "dist1", "Gi1/0/2"))
    model.mac_entries.append(MacEntry("acc1", "0019.e7aa.0002", 10, "Po1", "dynamic"))

    (bundle,) = _rows(model, "acc1", "Po1")
    assert (bundle.mac, bundle.vlan, bundle.neighbor, bundle.neighbor_interface) == (
        None,
        "trunk",
        "dist1",
        "Po7",
    )
    (member,) = _rows(model, "acc1", "Gi1/0/2")
    assert (member.mac, member.neighbor, member.neighbor_interface) == (None, "dist1", "Gi1/0/2")


def test_a_bundle_to_an_uncaptured_device_lists_each_member_far_end() -> None:
    model = _model()
    acc1 = model.devices["acc1"]
    model.devices["fw1"] = Device(hostname="fw1", role=DeviceRole.FIREWALL)
    acc1.interfaces["Gi1/0/5"].po_id = 2
    acc1.interfaces["Gi1/0/7"].po_id = 2
    acc1.interfaces["Po2"] = Interface(
        name="Po2", type=InterfaceType.PORT_CHANNEL, po_members=["Gi1/0/5", "Gi1/0/7"]
    )
    model.links += [Link("acc1", "Gi1/0/5", "fw1", "eth1"), Link("acc1", "Gi1/0/7", "fw1", "eth2")]

    (bundle,) = _rows(model, "acc1", "Po2")
    assert (bundle.neighbor, bundle.neighbor_interface) == ("fw1;fw1", "eth1;eth2")
