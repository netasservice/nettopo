"""Tests for the ingest -> parse -> model wiring (PROJECT_SPEC.md section 4).

Uses `tests/fixtures/captures/`: two source devices (`sw1-access`, IOS; `sw2-dist`,
IOS-XE) that are CDP/LLDP neighbors of each other and of a third, non-source device
(`core-rtr`) that only appears in CDP output. Both source devices also carry VLAN 10
`show spanning-tree` output (`sw2-dist` as root) for the STP wiring tests below.

`tests/fixtures/captures_nxos/` is a separate directory for neighbor identity
resolution. One Nexus is uplinked to two access switches and named differently by each
report: `acc-sw3` sees it as `nxos-core1(FDO21120U5D)` over CDP (NX-OS appends the
chassis serial to its device id) and as plain `nxos-core1` over LLDP, while `acc-sw4`
sees it as `NXOS-CORE1.example.com`. `acc-sw3` likewise sees one host as `esxi-host03`
over CDP and `esxi-host03.example.com` over LLDP.

`tests/fixtures/captures_portchannel/` is a third directory for port-channel data: two
source switches joined by a two-member LACP bundle both sides call `Po1`, plus a host on
an unbundled port.

`tests/fixtures/captures_arista/` is a fourth directory for LLDP-only neighbors: one IOS
source whose LLDP output names an Arista chassis switch (letter capabilities `B,R`, the
model only in the System Description) and a generic bridge advertising just `B`.
"""

from __future__ import annotations

from pathlib import Path

from nettopo.ingest.files import FileDataSource
from nettopo.ingest.model_builder import build_network_model
from nettopo.model.entities import DeviceRole, InterfaceType, NetworkModel

FIXTURES = Path(__file__).parent / "fixtures" / "captures"
NXOS_FIXTURES = Path(__file__).parent / "fixtures" / "captures_nxos"
PORT_CHANNEL_FIXTURES = Path(__file__).parent / "fixtures" / "captures_portchannel"
ARISTA_FIXTURES = Path(__file__).parent / "fixtures" / "captures_arista"
STP_PORT_CHANNEL_FIXTURES = Path(__file__).parent / "fixtures" / "stp_portchannel"
HSRP_FIXTURES = Path(__file__).parent / "fixtures" / "hsrp_topology"
BGP_FIXTURES = Path(__file__).parent / "fixtures" / "bgp_topology"


def _build_model() -> NetworkModel:
    return build_network_model(FileDataSource(FIXTURES))


def _build_nxos_model() -> NetworkModel:
    return build_network_model(FileDataSource(NXOS_FIXTURES))


def _build_port_channel_model() -> NetworkModel:
    return build_network_model(FileDataSource(PORT_CHANNEL_FIXTURES))


def test_every_source_device_is_registered_and_marked_as_source() -> None:
    model = _build_model()
    assert model.devices["sw1-access"].is_source is True
    assert model.devices["sw2-dist"].is_source is True


def test_source_devices_get_platform_and_os_from_show_version() -> None:
    model = _build_model()
    assert model.devices["sw1-access"].os == "ios"
    assert model.devices["sw2-dist"].os == "ios-xe"


def test_neighbor_fqdn_is_resolved_to_the_matching_source_hostname() -> None:
    model = _build_model()
    assert "sw2-dist.example.com" not in model.devices
    remote_devices = {link.remote_device for link in model.links}
    assert "sw2-dist" in remote_devices


def test_non_source_neighbor_is_kept_as_a_non_source_device() -> None:
    model = _build_model()
    core_rtr = model.devices["core-rtr.example.com"]
    assert core_rtr.is_source is False


def test_the_same_physical_link_seen_from_both_ends_is_deduplicated() -> None:
    model = _build_model()
    sw1_to_sw2 = [
        link
        for link in model.links
        if {link.local_device, link.remote_device} == {"sw1-access", "sw2-dist"}
    ]
    assert len(sw1_to_sw2) == 1


def test_interfaces_and_vlans_are_populated_per_device() -> None:
    model = _build_model()
    assert "Gi1/0/1" in model.devices["sw1-access"].interfaces
    assert 10 in model.vlans


def test_device_role_is_inferred_from_a_neighbors_reported_capabilities() -> None:
    # core-rtr is only ever seen as a CDP neighbor (never a source device), reported
    # with "Capabilities: Router".
    model = _build_model()
    assert model.devices["core-rtr.example.com"].role is DeviceRole.ROUTER


def test_an_lldp_only_neighbor_is_classified_from_its_system_description() -> None:
    # arista-core1 is only ever seen over LLDP: its letter capabilities `B,R` first read
    # as a router, and the System Description standing in for a platform then names the
    # DCS-7504N chassis, which settles it as a multilayer switch.
    model = build_network_model(FileDataSource(ARISTA_FIXTURES))
    arista = model.devices["arista-core1"]
    assert arista.role is DeviceRole.L3_SWITCH
    assert arista.platform is not None and "DCS-7504N" in arista.platform


def test_an_lldp_bridge_capability_alone_reads_as_a_switch() -> None:
    # mgmt-bridge1 advertises only `B` and a description matching no platform family.
    model = build_network_model(FileDataSource(ARISTA_FIXTURES))
    assert model.devices["mgmt-bridge1"].role is DeviceRole.SWITCH


def test_a_non_source_devices_platform_comes_from_a_neighbors_report() -> None:
    # core-rtr has no capture of its own, so its platform can only come from the
    # "Platform:" line of the CDP report that names it.
    model = _build_model()
    assert model.devices["core-rtr.example.com"].platform == "cisco ISR4331/K9"


def test_a_source_devices_own_show_version_outranks_a_neighbors_report() -> None:
    # sw2-dist's CDP reports sw1-access as "cisco WS-C2960X-24TS-L"; sw1-access's own
    # `show version` is the authority on what it is.
    model = _build_model()
    assert model.devices["sw1-access"].platform == "cisco WS-C2960X-24TS-L"
    assert model.devices["sw1-access"].is_source is True


def test_mgmt_ip_is_filled_in_from_a_neighbors_report_for_source_devices_too() -> None:
    # Unlike platform, nothing parses a device's management address out of its own
    # capture, so a source device has no self-reported value to protect: sw2-dist's only
    # possible mgmt_ip is the one sw1-access's CDP advertises for it.
    model = _build_model()
    assert model.devices["sw2-dist"].mgmt_ip == "192.168.1.2"
    assert model.devices["core-rtr.example.com"].mgmt_ip == "10.0.0.254"


def test_a_cdp_reported_platform_outranks_an_lldp_reported_one(tmp_path: Path) -> None:
    # The two reports reach the builder in filename order, so `a-sw` (LLDP only) is read
    # before `b-sw` (CDP only): CDP must still win, not whichever arrived first.
    (tmp_path / "a-sw.txt").write_text(
        "a-sw#show lldp neighbors detail\n"
        "------------------------------------------------\n"
        "Local Intf: Gi1/0/1\n"
        "Chassis id: 00de.fb12.9999\n"
        "Port id: Gi0/1\n"
        "Port Description: Gi0/1\n"
        "System Name: edge-rtr\n"
        "\n"
        "System Description:\n"
        "Cisco IOS Software\n"
        "\n"
        "Time remaining: 101 seconds\n"
        "System Capabilities: B,R\n"
        "Enabled Capabilities: B,R\n"
        "MED Information:\n"
        "    Model: LLDP-INVENTORY-MODEL\n"
        "\n"
        "Total entries displayed: 1\n"
    )
    (tmp_path / "b-sw.txt").write_text(
        "b-sw#show cdp neighbors detail\n"
        "-------------------------\n"
        "Device ID: edge-rtr\n"
        "Entry address(es):\n"
        "  IP address: 10.0.0.1\n"
        "Platform: cisco ISR4451-X/K9,  Capabilities: Router\n"
        "Interface: GigabitEthernet1/0/2,  Port ID (outgoing port): GigabitEthernet0/2\n"
        "Holdtime : 151 sec\n"
        "\n"
        "Total cdp entries displayed : 1\n"
    )
    model = build_network_model(FileDataSource(tmp_path))

    assert model.devices["edge-rtr"].platform == "cisco ISR4451-X/K9"


def test_stp_bridges_are_populated_per_vlan_from_both_devices() -> None:
    model = _build_model()
    vlan10 = model.stp[10]
    assert set(vlan10.bridges) == {"sw1-access", "sw2-dist"}
    assert vlan10.bridges["sw2-dist"].base_priority == 24576


def test_stp_root_device_is_the_bridge_that_reports_itself_as_root() -> None:
    model = _build_model()
    assert model.stp[10].root_device == "sw2-dist"


def test_stp_ports_are_keyed_by_device_and_interface() -> None:
    model = _build_model()
    vlan10 = model.stp[10]
    assert ("sw1-access", "Gi1/0/24") in vlan10.ports
    assert vlan10.ports[("sw1-access", "Gi1/0/24")].role.value == "root"


def test_both_ends_of_a_source_to_source_link_get_a_role() -> None:
    # Both sw1-access and sw2-dist are source devices, so the physical link between
    # them is discovered from both ends and then deduplicated to one `Link`, which
    # keeps only one direction's remote_capabilities. Role inference must still see
    # both directions' capability reports before that deduplication happens, or one
    # of the two devices would be silently left at DeviceRole.UNKNOWN.
    model = _build_model()
    assert model.devices["sw1-access"].role is DeviceRole.SWITCH
    assert model.devices["sw2-dist"].role is DeviceRole.SWITCH


def test_source_device_serial_comes_from_its_own_show_version() -> None:
    model = _build_model()
    assert model.devices["sw1-access"].serial == "FOC2134X0ABC"


def test_a_neighbor_named_with_and_without_its_serial_is_one_device() -> None:
    model = _build_nxos_model()
    assert set(model.devices) == {"acc-sw3", "acc-sw4", "nxos-core1", "esxi-host03"}


def test_one_neighbor_named_differently_by_two_switches_keeps_both_of_its_links() -> None:
    # Merging the spellings must not merge the adjacencies: the Nexus has one uplink to
    # each access switch, out of a different port on each side.
    model = _build_nxos_model()
    uplinks = {
        (link.local_device, link.local_interface, link.remote_interface)
        for link in model.links
        if link.remote_device == "nxos-core1"
    }
    assert uplinks == {
        ("acc-sw3", "Gi1/0/49", "Eth1/1"),
        ("acc-sw4", "Gi1/0/49", "Eth1/2"),
    }


def test_the_serial_a_neighbor_advertises_in_its_name_is_kept_as_data() -> None:
    model = _build_nxos_model()
    assert model.devices["nxos-core1"].serial == "FDO21120U5D"


def test_cdp_and_lldp_reporting_one_adjacency_produce_a_single_link() -> None:
    model = _build_nxos_model()
    (uplink,) = [
        link
        for link in model.links
        if link.local_device == "acc-sw3" and link.remote_device == "nxos-core1"
    ]
    assert uplink.local_interface == "Gi1/0/49"
    assert uplink.remote_interface == "Eth1/1"
    assert uplink.discovery == "cdp"


def test_a_non_source_neighbor_seen_short_and_as_an_fqdn_is_one_device() -> None:
    model = _build_nxos_model()
    assert "esxi-host03.example.com" not in model.devices
    # `VMware ESXi` is a hypervisor, so the platform overrides the `Host` capability its
    # neighbors report -- which is the whole point of classifying on the chassis.
    assert model.devices["esxi-host03"].role is DeviceRole.SERVER


def test_port_channel_membership_is_recorded_on_both_the_bundle_and_its_members() -> None:
    device = _build_port_channel_model().devices["po-sw1"]

    assert device.interfaces["Po1"].po_members == ["Gi1/0/1", "Gi1/0/2"]
    assert device.interfaces["Gi1/0/1"].po_id == 1
    assert device.interfaces["Gi1/0/2"].po_id == 1


def test_an_interface_only_named_by_the_bundle_table_is_still_created(tmp_path: Path) -> None:
    # A capture need not carry `show interfaces`/`show ip interface brief` for every
    # port the bundle table names.
    (tmp_path / "sw9.txt").write_text(
        "sw9#show etherchannel summary\n"
        "Group  Port-channel  Protocol    Ports\n"
        "------+-------------+-----------+---------------------------------------\n"
        "4      Po4(SU)         LACP      Gi1/0/4(P)\n"
    )
    model = build_network_model(FileDataSource(tmp_path))

    interfaces = model.devices["sw9"].interfaces
    assert interfaces["Po4"].type is InterfaceType.PORT_CHANNEL
    assert interfaces["Gi1/0/4"].type is InterfaceType.PHYSICAL


def test_a_neighbors_chassis_address_reaches_the_device_it_belongs_to() -> None:
    model = build_network_model(FileDataSource(STP_PORT_CHANNEL_FIXTURES))
    assert model.devices["core-rtr"].chassis_id == "aaaa.bbbb.9999"


def test_the_root_address_is_recorded_even_when_no_captured_bridge_is_the_root() -> None:
    stp_vlan = build_network_model(FileDataSource(STP_PORT_CHANNEL_FIXTURES)).stp[10]
    assert stp_vlan.root_device is None
    assert stp_vlan.root_mac == "aaaa.bbbb.9999"


def test_hsrp_groups_are_keyed_by_vlan_and_group_and_merge_both_devices() -> None:
    model = build_network_model(FileDataSource(HSRP_FIXTURES))
    vlan10 = model.hsrp[(10, 10)]

    assert vlan10.virtual_ip == "10.0.10.1"
    assert set(vlan10.members) == {"gw-a", "gw-b"}
    assert vlan10.members["gw-a"].role.value == "active"
    assert vlan10.members["gw-b"].priority == 100


def test_several_hsrp_groups_on_one_svi_stay_separate() -> None:
    model = build_network_model(FileDataSource(HSRP_FIXTURES))
    assert [key for key in sorted(model.hsrp) if key[0] == 40] == [(40, 40), (40, 41)]
    assert model.hsrp[(40, 41)].virtual_ip == "10.0.40.254"


def test_every_bgp_session_is_collected_as_each_device_reported_it() -> None:
    """`model.bgp` is a flat list: a session needs no merging, unlike an HSRP group."""
    model = build_network_model(FileDataSource(BGP_FIXTURES))
    assert [(peer.local_device, peer.peer_ip) for peer in model.bgp] == [
        ("core-r1", "10.0.12.2"),
        ("core-r1", "198.51.100.1"),
        ("core-r1", "203.0.113.9"),
        ("core-r2", "10.0.12.1"),
    ]


def test_a_bgp_summary_settles_the_devices_own_as_number() -> None:
    model = build_network_model(FileDataSource(BGP_FIXTURES))
    assert model.devices["core-r1"].asn == 65001
    assert model.devices["core-r2"].asn == 65001


def test_a_bgp_summary_settles_the_devices_own_router_id() -> None:
    """The header line names the reporting router, so each device gets its own."""
    model = build_network_model(FileDataSource(BGP_FIXTURES))
    assert model.devices["core-r1"].router_id == "10.255.0.1"
    assert model.devices["core-r2"].router_id == "10.255.0.2"


def test_mac_and_arp_tables_reach_the_model_across_devices(tmp_path: Path) -> None:
    """The access switch learns the MAC; the gateway, a different capture, holds the ARP."""
    fixtures = Path(__file__).parent / "fixtures"
    (tmp_path / "acc1.txt").write_text(
        "acc1#show mac address-table\n"
        + (fixtures / "mac_table" / "ios_show_mac_address_table.txt").read_text()
    )
    (tmp_path / "core1.txt").write_text(
        "core1#show ip arp\n" + (fixtures / "arp" / "ios_show_ip_arp.txt").read_text()
    )
    model = build_network_model(FileDataSource(tmp_path))

    assert {entry.device for entry in model.mac_entries} == {"acc1"}
    assert len(model.mac_entries) == 5
    assert {entry.device for entry in model.arp_entries} == {"core1"}
    assert len(model.arp_entries) == 4


def test_a_port_seen_only_in_the_mac_table_becomes_an_interface(tmp_path: Path) -> None:
    fixtures = Path(__file__).parent / "fixtures"
    (tmp_path / "acc1.txt").write_text(
        "acc1#show mac address-table\n"
        + (fixtures / "mac_table" / "ios_show_mac_address_table.txt").read_text()
    )
    interfaces = build_network_model(FileDataSource(tmp_path)).devices["acc1"].interfaces
    assert set(interfaces) == {"Gi1/0/5", "Gi1/0/6", "Gi1/0/48", "Po1"}
    assert interfaces["Po1"].type is InterfaceType.PORT_CHANNEL
