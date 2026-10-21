"""Tests for interface parsing (PROJECT_SPEC.md section 4)."""

from __future__ import annotations

from pathlib import Path

from nettopo.model.entities import InterfaceType
from nettopo.parsing.interfaces import parse_interfaces

FIXTURES = Path(__file__).parent / "fixtures" / "interfaces"


def _capture() -> str:
    ip_brief = (FIXTURES / "show_ip_interface_brief.txt").read_text()
    interfaces = (FIXTURES / "show_interfaces.txt").read_text()
    return (
        f"sw1-access#show ip interface brief\n{ip_brief}\nsw1-access#show interfaces\n{interfaces}"
    )


def test_parse_interfaces_merges_both_commands_by_normalized_name() -> None:
    interfaces = parse_interfaces(_capture())
    assert set(interfaces) == {"Vl1", "Vl10", "Gi1/0/1", "Gi1/0/24"}


def test_parse_interfaces_prefers_show_interfaces_description_and_ip() -> None:
    interfaces = parse_interfaces(_capture())
    gi1 = interfaces["Gi1/0/1"]
    assert gi1.description == "Uplink to core-rtr"
    assert gi1.ip_address == "10.10.10.1"
    assert gi1.prefix_len == 24
    assert gi1.admin_up is True
    assert gi1.oper_up is True
    assert gi1.type is InterfaceType.PHYSICAL


def test_parse_interfaces_falls_back_to_ip_brief_for_interfaces_missing_from_show_interfaces() -> (
    None
):
    interfaces = parse_interfaces(_capture())
    vlan10 = interfaces["Vl10"]
    assert vlan10.ip_address == "10.10.10.1"
    assert vlan10.admin_up is True
    assert vlan10.oper_up is True
    assert vlan10.type is InterfaceType.SVI


def test_parse_interfaces_detects_administratively_down() -> None:
    interfaces = parse_interfaces(_capture())
    vlan1 = interfaces["Vl1"]
    assert vlan1.admin_up is False
    assert vlan1.oper_up is False


def test_parse_interfaces_returns_empty_dict_when_no_commands_present() -> None:
    assert parse_interfaces("sw1-access#show version\nCisco IOS Software\n") == {}


def test_parse_interfaces_types_every_interface_kind() -> None:
    capture = (
        "sw1-access#show ip interface brief\n"
        "Interface              IP-Address      OK? Method Status                Protocol\n"
        "Port-channel1          unassigned      YES unset  up                    up\n"
        "Loopback0               10.255.255.1    YES manual up                    up\n"
        "Tunnel0                 unassigned      YES unset  up                    up\n"
        "Management1             unassigned      YES unset  up                    up\n"
        "GigabitEthernet1/0/1.10 unassigned      YES unset  up                    up\n"
    )
    interfaces = parse_interfaces(capture)
    assert interfaces["Po1"].type is InterfaceType.PORT_CHANNEL
    assert interfaces["Lo0"].type is InterfaceType.LOOPBACK
    assert interfaces["Tu0"].type is InterfaceType.TUNNEL
    assert interfaces["Mgmt1"].type is InterfaceType.MGMT
    assert interfaces["Gi1/0/1.10"].type is InterfaceType.SUBINTERFACE


def _section(command: str, fixture: str) -> str:
    return f"sw1-access#{command}\n{(FIXTURES / fixture).read_text()}\n"


def test_interfaces_status_fills_vlan_mode_speed_and_duplex() -> None:
    interfaces = parse_interfaces(
        _section("show interfaces status", "ios_show_interfaces_status.txt")
    )
    access = interfaces["Gi1/0/5"]
    assert (access.vlan, access.mode, access.speed, access.duplex) == (
        10,
        "access",
        "a-1000",
        "a-full",
    )
    assert interfaces["Gi1/0/1"].mode == "trunk"
    assert interfaces["Gi1/0/1"].vlan is None
    routed = interfaces["Gi1/0/24"]
    assert (routed.vlan, routed.mode) == (None, None)


def test_interfaces_status_maps_its_status_column_to_admin_and_oper_state() -> None:
    interfaces = parse_interfaces(
        _section("show interfaces status", "ios_show_interfaces_status.txt")
    )
    assert (interfaces["Gi1/0/5"].admin_up, interfaces["Gi1/0/5"].oper_up) == (True, True)
    assert (interfaces["Gi1/0/7"].admin_up, interfaces["Gi1/0/7"].oper_up) == (True, False)
    assert (interfaces["Gi1/0/8"].admin_up, interfaces["Gi1/0/8"].oper_up) == (False, False)
    assert (interfaces["Gi1/0/9"].admin_up, interfaces["Gi1/0/9"].oper_up) == (True, False)


def test_interfaces_status_does_not_override_ip_brief_state() -> None:
    capture = (
        "sw1-access#show ip interface brief\n"
        "Interface              IP-Address      OK? Method Status                Protocol\n"
        "GigabitEthernet1/0/5   unassigned      YES unset  administratively down down\n"
        + _section("show interfaces status", "ios_show_interfaces_status.txt")
    )
    assert parse_interfaces(capture)["Gi1/0/5"].admin_up is False


def test_the_full_description_beats_the_truncated_status_name() -> None:
    capture = _section("show interfaces status", "ios_show_interfaces_status.txt") + _section(
        "show interfaces description", "ios_show_interfaces_description.txt"
    )
    interfaces = parse_interfaces(capture)
    assert interfaces["Gi1/0/5"].description == "esxi-host03 vmnic0 (prod cluster)"
    assert interfaces["Gi1/0/9"].description is None
    assert interfaces["Vl10"].description == "Users VLAN gateway"


def test_interfaces_description_sets_ios_state() -> None:
    interfaces = parse_interfaces(
        _section("show interfaces description", "ios_show_interfaces_description.txt")
    )
    assert (interfaces["Gi1/0/8"].admin_up, interfaces["Gi1/0/8"].oper_up) == (False, False)
    assert (interfaces["Gi1/0/7"].admin_up, interfaces["Gi1/0/7"].oper_up) == (True, False)
    assert (interfaces["Gi1/0/1"].admin_up, interfaces["Gi1/0/1"].oper_up) == (True, True)


def test_an_empty_show_interfaces_description_keeps_the_one_already_known() -> None:
    capture = (
        _section("show interfaces description", "ios_show_interfaces_description.txt") + _capture()
    )
    assert parse_interfaces(capture)["Vl10"].description == "Users VLAN gateway"


def test_nxos_status_and_description_treat_dashes_as_empty() -> None:
    capture = (
        "nx1#show interface status\n"
        + (FIXTURES / "nxos_show_interface_status.txt").read_text()
        + "\nnx1#show interface description\n"
        + (FIXTURES / "nxos_show_interface_description.txt").read_text()
    )
    interfaces = parse_interfaces(capture, platform="cisco_nxos")
    assert interfaces["Mgmt0"].description is None
    assert interfaces["Eth1/6"].description is None
    assert interfaces["Eth1/5"].description == "server-01 bond0 eth0"
    assert (interfaces["Eth1/5"].vlan, interfaces["Eth1/5"].mode) == (10, "access")
    assert interfaces["Eth1/1"].mode == "trunk"
    assert (interfaces["Eth1/6"].admin_up, interfaces["Eth1/6"].oper_up) == (True, False)
    assert (interfaces["Eth1/7"].admin_up, interfaces["Eth1/7"].oper_up) == (True, False)
    assert (interfaces["Eth1/8"].admin_up, interfaces["Eth1/8"].oper_up) == (False, False)
    assert interfaces["Po10"].description == "vpc-peer-link"


def test_an_svi_carries_its_own_vlan() -> None:
    assert parse_interfaces(_capture())["Vl10"].vlan == 10
