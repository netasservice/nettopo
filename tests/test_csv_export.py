"""Tests for CSV export (PROJECT_SPEC.md section 8)."""

from __future__ import annotations

import csv
from pathlib import Path

from nettopo.export.csv_export import write_csv_tables
from nettopo.model.entities import (
    BgpPeer,
    BgpType,
    Device,
    HsrpGroup,
    HsrpMember,
    HsrpRole,
    Interface,
    Link,
    MacEntry,
    NetworkModel,
    StpBridge,
    StpPort,
    StpRole,
    StpState,
    StpVlan,
    Vlan,
)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _sample_model() -> NetworkModel:
    model = NetworkModel()
    device = Device(
        hostname="sw1", is_source=True, platform="cisco C9300", os="ios", serial="FOC2134X0ABC"
    )
    device.interfaces["Gi1/0/1"] = Interface(name="Gi1/0/1", description="=cmd|calc")
    model.devices["sw1"] = device
    model.devices["sw2"] = Device(hostname="sw2")
    model.links.append(
        Link(
            local_device="sw1",
            local_interface="Gi1/0/1",
            remote_device="sw2",
            remote_interface="Gi1/0/2",
            remote_mgmt_ip="192.168.1.2",
            remote_capabilities=["Switch", "IGMP"],
        )
    )
    model.vlans[10] = Vlan(vlan_id=10, name="users", status="active")
    return model


def test_write_csv_tables_creates_every_table(tmp_path: Path) -> None:
    csv_dir = write_csv_tables(_sample_model(), tmp_path)
    expected = {
        "devices.csv",
        "interfaces.csv",
        "neighbors.csv",
        "vlans.csv",
        "stp.csv",
        "hsrp.csv",
        "bgp.csv",
        "ports.csv",
    }
    assert {path.name for path in csv_dir.iterdir()} == expected


def test_devices_csv_has_one_row_per_device(tmp_path: Path) -> None:
    csv_dir = write_csv_tables(_sample_model(), tmp_path)
    rows = _read_rows(csv_dir / "devices.csv")
    assert {row["hostname"] for row in rows} == {"sw1", "sw2"}
    sw1 = next(row for row in rows if row["hostname"] == "sw1")
    assert sw1["is_source"] == "True"
    assert sw1["platform"] == "cisco C9300"
    assert sw1["os"] == "ios"
    assert sw1["serial"] == "FOC2134X0ABC"


def test_devices_csv_carries_the_bgp_router_id_when_one_is_known(tmp_path: Path) -> None:
    """A device that speaks no BGP leaves the column empty rather than absent."""
    model = _sample_model()
    model.devices["sw1"].asn = 65001
    model.devices["sw1"].router_id = "10.255.0.1"
    rows = _read_rows(write_csv_tables(model, tmp_path) / "devices.csv")

    assert {(row["hostname"], row["router_id"]) for row in rows} == {
        ("sw1", "10.255.0.1"),
        ("sw2", ""),
    }


def test_neighbors_csv_joins_capabilities_with_semicolons(tmp_path: Path) -> None:
    csv_dir = write_csv_tables(_sample_model(), tmp_path)
    (row,) = _read_rows(csv_dir / "neighbors.csv")
    assert row["remote_capabilities"] == "Switch;IGMP"
    assert row["remote_mgmt_ip"] == "192.168.1.2"


def test_vlans_csv_is_sorted_by_vlan_id(tmp_path: Path) -> None:
    model = _sample_model()
    model.vlans[1] = Vlan(vlan_id=1, name="default", status="active")
    csv_dir = write_csv_tables(model, tmp_path)
    rows = _read_rows(csv_dir / "vlans.csv")
    assert [row["vlan_id"] for row in rows] == ["1", "10"]


def test_empty_stp_hsrp_bgp_tables_are_header_only(tmp_path: Path) -> None:
    csv_dir = write_csv_tables(_sample_model(), tmp_path)
    for name in ("stp.csv", "hsrp.csv", "bgp.csv"):
        lines = (csv_dir / name).read_text().splitlines()
        assert len(lines) == 1


def test_bgp_csv_has_one_row_per_session_sorted_by_device_and_peer(tmp_path: Path) -> None:
    model = _sample_model()
    model.bgp = [
        BgpPeer(
            local_device="sw1",
            local_asn=65001,
            peer_ip="198.51.100.1",
            peer_asn=65100,
            state="Idle",
            type=BgpType.EBGP,
        ),
        BgpPeer(
            local_device="sw1",
            local_asn=65001,
            peer_ip="10.0.12.2",
            peer_asn=65001,
            state="Established",
            type=BgpType.IBGP,
        ),
    ]
    rows = _read_rows(write_csv_tables(model, tmp_path) / "bgp.csv")

    assert [(row["peer_ip"], row["type"], row["state"]) for row in rows] == [
        ("10.0.12.2", "ibgp", "Established"),
        ("198.51.100.1", "ebgp", "Idle"),
    ]
    assert all(row["local_asn"] == "65001" and row["vrf"] == "default" for row in rows)


def test_bgp_csv_leaves_peer_device_empty_on_every_row(tmp_path: Path) -> None:
    """v1 does not resolve a peer IP to a hostname (PROJECT_SPEC.md section 2).

    The BGP *view* matches addresses so it can draw one link between two captured
    routers; this table stays a faithful record of what each device reported.
    """
    model = _sample_model()
    model.bgp = [
        BgpPeer(
            local_device="sw1",
            local_asn=65001,
            peer_ip="10.0.12.2",
            peer_asn=65001,
            state="Established",
            type=BgpType.IBGP,
        )
    ]
    rows = _read_rows(write_csv_tables(model, tmp_path) / "bgp.csv")
    assert all(row["peer_device"] == "" for row in rows)


def test_formula_prefixed_cell_values_are_neutralized(tmp_path: Path) -> None:
    csv_dir = write_csv_tables(_sample_model(), tmp_path)
    rows = _read_rows(csv_dir / "interfaces.csv")
    (row,) = [row for row in rows if row["name"] == "Gi1/0/1"]
    assert row["description"] == "'=cmd|calc"


def test_stp_csv_has_one_row_per_port_with_base_and_effective_priority(tmp_path: Path) -> None:
    model = _sample_model()
    model.stp[10] = StpVlan(
        vlan=10,
        root_device="sw1",
        bridges={
            "sw1": StpBridge(
                device="sw1",
                vlan=10,
                base_priority=24576,
                sys_id_ext=10,
                mac="aaaa.bbbb.0001",
                is_root=True,
            ),
            "sw2": StpBridge(
                device="sw2",
                vlan=10,
                base_priority=32768,
                sys_id_ext=10,
                mac="aaaa.bbbb.0002",
            ),
        },
        ports={
            ("sw1", "Gi1/0/1"): StpPort(
                device="sw1",
                vlan=10,
                interface="Gi1/0/1",
                role=StpRole.DESIGNATED,
                state=StpState.FWD,
                cost=4,
            ),
            ("sw2", "Gi1/0/2"): StpPort(
                device="sw2",
                vlan=10,
                interface="Gi1/0/2",
                role=StpRole.ROOT,
                state=StpState.FWD,
                cost=4,
            ),
        },
    )
    csv_dir = write_csv_tables(model, tmp_path)
    rows = _read_rows(csv_dir / "stp.csv")

    assert len(rows) == 2
    sw2_row = next(row for row in rows if row["device"] == "sw2")
    assert sw2_row["root_device"] == "sw1"
    assert sw2_row["base_priority"] == "32768"
    assert sw2_row["effective_priority"] == "32778"
    assert sw2_row["is_root"] == "False"
    assert sw2_row["interface"] == "Gi1/0/2"
    assert sw2_row["role"] == "root"
    assert sw2_row["state"] == "forwarding"


def test_hsrp_csv_has_one_row_per_member_sorted_by_vlan_and_group(tmp_path: Path) -> None:
    model = _sample_model()
    model.hsrp[(10, 11)] = HsrpGroup(
        vlan=10,
        group=11,
        virtual_ip="10.0.10.254",
        members={
            "sw1": HsrpMember(
                device="sw1",
                interface="Vl10",
                group=11,
                priority=90,
                role=HsrpRole.STANDBY,
                preempt=False,
            )
        },
    )
    model.hsrp[(10, 10)] = HsrpGroup(
        vlan=10,
        group=10,
        virtual_ip="10.0.10.1",
        members={
            "sw2": HsrpMember(
                device="sw2",
                interface="Vl10",
                group=10,
                priority=100,
                role=HsrpRole.STANDBY,
                preempt=True,
            ),
            "sw1": HsrpMember(
                device="sw1",
                interface="Vl10",
                group=10,
                priority=150,
                role=HsrpRole.ACTIVE,
                preempt=True,
            ),
        },
    )
    csv_dir = write_csv_tables(model, tmp_path)
    rows = _read_rows(csv_dir / "hsrp.csv")

    assert [(row["vlan"], row["group"], row["device"]) for row in rows] == [
        ("10", "10", "sw1"),
        ("10", "10", "sw2"),
        ("10", "11", "sw1"),
    ]
    assert rows[0] == {
        "vlan": "10",
        "group": "10",
        "virtual_ip": "10.0.10.1",
        "device": "sw1",
        "interface": "Vl10",
        "priority": "150",
        "role": "active",
        "preempt": "True",
    }
    assert rows[2]["preempt"] == "False"


def test_stp_csv_carries_the_root_address_and_port_type(tmp_path: Path) -> None:
    # Both columns exist to debug a wrong diagram from the CSV alone: the root address
    # says whether the root is outside the captures, the type says which ports are edges.
    model = _sample_model()
    model.stp[10] = StpVlan(
        vlan=10,
        root_mac="aaaa.bbbb.9999",
        bridges={
            "sw1": StpBridge(
                device="sw1",
                vlan=10,
                base_priority=32768,
                sys_id_ext=10,
                mac="aaaa.bbbb.0001",
                root_mac="aaaa.bbbb.9999",
            )
        },
        ports={
            ("sw1", "Gi1/0/1"): StpPort(
                device="sw1",
                vlan=10,
                interface="Gi1/0/1",
                role=StpRole.DESIGNATED,
                state=StpState.FWD,
                cost=4,
                link_type="P2p Edge",
            )
        },
    )
    (row,) = _read_rows(write_csv_tables(model, tmp_path) / "stp.csv")

    assert row["root_device"] == ""
    assert row["root_mac"] == "aaaa.bbbb.9999"
    assert row["link_type"] == "P2p Edge"


def test_interfaces_csv_carries_speed_and_duplex(tmp_path: Path) -> None:
    model = _sample_model()
    model.devices["sw1"].interfaces["Gi1/0/1"].speed = "a-1000"
    model.devices["sw1"].interfaces["Gi1/0/1"].duplex = "a-full"
    (row,) = _read_rows(write_csv_tables(model, tmp_path) / "interfaces.csv")
    assert (row["speed"], row["duplex"]) == ("a-1000", "a-full")


def test_ports_csv_has_one_row_per_learned_mac(tmp_path: Path) -> None:
    model = _sample_model()
    model.devices["sw1"].interfaces["Gi1/0/5"] = Interface(name="Gi1/0/5", vlan=10)
    model.mac_entries = [
        MacEntry("sw1", "0050.56a1.0001", 10, "Gi1/0/5", "dynamic"),
        MacEntry("sw1", "0050.56a1.0002", 10, "Gi1/0/5", "dynamic"),
    ]
    rows = _read_rows(write_csv_tables(model, tmp_path) / "ports.csv")
    assert [(row["interface"], row["mac"], row["vlan"]) for row in rows] == [
        ("Gi1/0/1", "", ""),
        ("Gi1/0/5", "0050.56a1.0001", "10"),
        ("Gi1/0/5", "0050.56a1.0002", "10"),
    ]
    assert rows[0]["neighbor"] == "sw2"


def test_ports_csv_neutralizes_formula_prefixed_cells(tmp_path: Path) -> None:
    rows = _read_rows(write_csv_tables(_sample_model(), tmp_path) / "ports.csv")
    assert rows[0]["description"] == "'=cmd|calc"
