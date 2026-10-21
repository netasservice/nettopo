"""Wires ingestion -> parsers -> `NetworkModel` population (PROJECT_SPEC.md section 4).

CDP and LLDP rarely agree on what to call a neighbor: the same device is reported as
`nxos-core1` by one protocol, `nxos-core1(FDO21120U5D)` by the other (NX-OS appends the
chassis serial), and `nxos-core1.example.com` by a third device. Left unresolved, every
spelling becomes its own `Device` and one physical switch is drawn as several nodes.
`utils/hostnames.py` owns that correlation; this module feeds it every observed spelling
and rewrites each discovered link onto the canonical name it returns.

It also infers `Device.role` so `render/icons.py` has real data to key off of, from two
signals in increasing order of authority. CDP/LLDP capabilities come first: a device's own
CDP/LLDP output never reports its own capabilities, so this can only come from how its
*neighbors* describe it, and it must run on every raw discovered link, before
deduplication -- when both ends of a link are source devices, deduplication keeps only one
direction's `Link` (see `links_by_key` below), which would silently discard the only
capability report naming whichever device ends up on the discarded side. The reported
chassis then overrides that guess in `_apply_platform_roles`, once every platform string
is settled.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace

from nettopo.ingest.base import Capture, DataSource
from nettopo.model.entities import (
    Device,
    DeviceRole,
    HsrpGroup,
    Interface,
    Link,
    NetworkModel,
    StpVlan,
)
from nettopo.model.platforms import classify_platform
from nettopo.parsing.arp import parse_arp
from nettopo.parsing.bgp import parse_bgp
from nettopo.parsing.cdp import parse_cdp
from nettopo.parsing.etherchannel import PortChannelCapture, parse_port_channels
from nettopo.parsing.hsrp import parse_hsrp
from nettopo.parsing.interfaces import interface_type, parse_interfaces
from nettopo.parsing.lldp import parse_lldp
from nettopo.parsing.mac_table import parse_mac_table
from nettopo.parsing.spanning_tree import parse_spanning_tree
from nettopo.parsing.version import parse_version
from nettopo.parsing.vlan import parse_vlans
from nettopo.utils.hostnames import resolve_device_identities, split_serial_suffix

# One vocabulary for both protocols: CDP reports word capabilities, LLDP the IEEE
# 802.1AB letter codes -- single letters cannot collide with the words, so the two share
# a table. Checked in priority order: routers first, so a device advertising both bridge
# and router (`B, R`, or CDP's "Router Switch") reads as a router, which the platform
# pass then refines to a multilayer switch when the chassis says so. Phone/`T` and the
# AP code `W` outrank bridge/`B` because phones advertise `B, T` and APs `B, W` -- the
# more specific capability must win. `O`, `P` and `C` are deliberately unmapped: a
# repeater or a DOCSIS device is exactly what UNKNOWN is for.
_ROLE_BY_CAPABILITY: tuple[tuple[str, DeviceRole], ...] = (
    ("Router", DeviceRole.ROUTER),
    ("R", DeviceRole.ROUTER),
    ("W", DeviceRole.AP),
    ("Phone", DeviceRole.PHONE),
    ("T", DeviceRole.PHONE),
    ("Switch", DeviceRole.SWITCH),
    ("B", DeviceRole.SWITCH),
    ("Host", DeviceRole.HOST),
    ("S", DeviceRole.HOST),
)

# CDP names a neighbor's port more reliably than LLDP does, so it wins when both report
# the same adjacency out of one local port.
_DISCOVERY_RANK: dict[str, int] = {"cdp": 0, "lldp": 1}


def build_network_model(source: DataSource, *, default_platform: str = "cisco_ios") -> NetworkModel:
    captures = list(source.discover())

    model = NetworkModel()
    hostname_by_hint = _populate_source_devices(model, captures, default_platform)

    discovered = _discover_links(captures, hostname_by_hint, default_platform)
    canonical_by_spelling = resolve_device_identities(
        (link.remote_device for link in discovered), set(model.devices)
    )
    serial_by_hostname = _serials(canonical_by_spelling)
    platform_by_hostname = _best_reported(
        discovered, canonical_by_spelling, lambda link: link.remote_platform
    )
    mgmt_ip_by_hostname = _best_reported(
        discovered, canonical_by_spelling, lambda link: link.remote_mgmt_ip
    )
    chassis_id_by_hostname = _best_reported(
        discovered, canonical_by_spelling, lambda link: link.remote_chassis_id
    )

    # One entry per (local port, neighbor): the two protocols describing the same
    # adjacency collapse here, before the direction-independent pass below collapses the
    # same link as seen from each of its two ends.
    links_by_port: dict[tuple[str, str, str], Link] = {}
    for link in discovered:
        resolved = replace(link, remote_device=canonical_by_spelling[link.remote_device])
        remote = model.devices.setdefault(
            resolved.remote_device, Device(hostname=resolved.remote_device)
        )
        if remote.role is DeviceRole.UNKNOWN:
            remote.role = _infer_role(resolved.remote_capabilities)
        if remote.serial is None:
            remote.serial = serial_by_hostname.get(resolved.remote_device)
        # A source device's own `show version` is authoritative even when it yielded no
        # platform, so a neighbor's guess never overwrites it.
        if remote.platform is None and not remote.is_source:
            remote.platform = platform_by_hostname.get(resolved.remote_device)
        # Unlike platform, no parser reads a device's management address out of its own
        # capture, so this is the only source there is and source devices take it too.
        if remote.mgmt_ip is None:
            remote.mgmt_ip = mgmt_ip_by_hostname.get(resolved.remote_device)
        if remote.chassis_id is None:
            remote.chassis_id = chassis_id_by_hostname.get(resolved.remote_device)

        port_key = (resolved.local_device, resolved.local_interface, resolved.remote_device)
        existing = links_by_port.get(port_key)
        if existing is None or _discovery_rank(resolved) < _discovery_rank(existing):
            links_by_port[port_key] = resolved

    links_by_key: dict[frozenset[tuple[str, str]], Link] = {}
    for link in links_by_port.values():
        links_by_key.setdefault(link.key(), link)

    model.links = list(links_by_key.values())
    _apply_platform_roles(model)
    return model


def _apply_platform_roles(model: NetworkModel) -> None:
    """Let the reported chassis override the role capabilities guessed at.

    Runs last, over every device, because it needs `Device.platform` already settled from
    both sources (`show version` above, neighbor reports in the link loop). It buys two
    things capabilities alone cannot:

    - A multilayer switch stops being drawn as a router. `Router Switch` is what a
      Catalyst 9500 advertises, and `_ROLE_BY_CAPABILITY` has to pick one of the two;
      the chassis name is the tiebreaker that is actually about the device.
    - A source device gets a role from its *own* `show version`. Until now that role
      could only come from how some neighbor described it, since a device's CDP/LLDP
      output never reports its own capabilities.
    """
    for device in model.devices.values():
        role = classify_platform(device.platform)
        if role is not None:
            device.role = role


def _populate_source_devices(
    model: NetworkModel, captures: Sequence[Capture], default_platform: str
) -> dict[str, str]:
    """Register every device we hold a capture for and return its canonical hostname."""
    hostname_by_hint: dict[str, str] = {}
    for capture in captures:
        platform = capture.platform_hint or default_platform
        version_info = parse_version(capture.raw_text, platform=platform)
        hostname = (version_info.hostname if version_info else None) or capture.device_hint
        hostname_by_hint[capture.device_hint] = hostname

        device = model.devices.setdefault(hostname, Device(hostname=hostname))
        device.is_source = True
        if version_info:
            device.platform = version_info.platform
            device.model = version_info.model
            device.os = version_info.os
            device.serial = version_info.serial
        device.interfaces.update(parse_interfaces(capture.raw_text, platform=platform))
        _apply_port_channels(device, parse_port_channels(capture.raw_text, platform=platform))

        for vlan in parse_vlans(capture.raw_text, platform=platform):
            model.vlans.setdefault(vlan.vlan_id, vlan)

        mac_entries = parse_mac_table(hostname, capture.raw_text, platform=platform)
        model.mac_entries.extend(mac_entries)
        # A port can appear in the MAC table without any interface command covering it
        # (a capture need not include every command); the port table still owes it a row.
        for mac_entry in mac_entries:
            _get_or_create_interface(device, mac_entry.interface)
        model.arp_entries.extend(parse_arp(hostname, capture.raw_text, platform=platform))

        for stp_capture in parse_spanning_tree(hostname, capture.raw_text):
            stp_vlan = model.stp.setdefault(stp_capture.vlan, StpVlan(vlan=stp_capture.vlan))
            stp_vlan.bridges[hostname] = stp_capture.bridge
            for port in stp_capture.ports:
                stp_vlan.ports[(hostname, port.interface)] = port
            if stp_capture.bridge.is_root:
                stp_vlan.root_device = hostname
            # Every bridge in a converged VLAN agrees on the root's address, so any one of
            # them settles it -- including when the root is a device we hold no capture
            # for and `root_device` therefore stays None.
            stp_vlan.root_mac = stp_vlan.root_mac or stp_capture.bridge.root_mac

        for hsrp_capture in parse_hsrp(hostname, capture.raw_text, platform=platform):
            key = (hsrp_capture.vlan, hsrp_capture.group)
            hsrp_group = model.hsrp.setdefault(
                key, HsrpGroup(vlan=hsrp_capture.vlan, group=hsrp_capture.group)
            )
            hsrp_group.members[hostname] = hsrp_capture.member
            # Every member of a group advertises the same virtual address, so the first
            # one to report it settles it -- including when the member holding the
            # address is one we have no capture for.
            hsrp_group.virtual_ip = hsrp_group.virtual_ip or hsrp_capture.virtual_ip

        bgp_capture = parse_bgp(hostname, capture.raw_text, platform=platform)
        model.bgp.extend(bgp_capture.peers)
        if bgp_capture.peers:
            # One `show ip bgp summary` covers one router in one AS, so every session it
            # reports shares the local AS number -- any row settles the device's own.
            device.asn = bgp_capture.peers[0].local_asn
        if bgp_capture.router_id is not None:
            device.router_id = bgp_capture.router_id

    return hostname_by_hint


def _apply_port_channels(device: Device, port_channels: Sequence[PortChannelCapture]) -> None:
    """Record each bundle on its port-channel interface and stamp its members with `po_id`.

    Both ends of that relationship are written because the L2 view reads them from
    opposite directions: a link is bundled by its member's `po_id`, and the bundle's own
    interface carries the member list. Either interface may be absent from
    `show interfaces` (a capture need not include every command), so both are created on
    demand rather than assumed present.
    """
    for port_channel in port_channels:
        _get_or_create_interface(device, port_channel.name).po_members = list(port_channel.members)
        for member_name in port_channel.members:
            _get_or_create_interface(device, member_name).po_id = port_channel.po_id


def _get_or_create_interface(device: Device, name: str) -> Interface:
    return device.interfaces.setdefault(name, Interface(name=name, type=interface_type(name)))


def _discover_links(
    captures: Sequence[Capture], hostname_by_hint: dict[str, str], default_platform: str
) -> list[Link]:
    """Parse every capture's CDP and LLDP neighbors, neighbor names still as reported."""
    links: list[Link] = []
    for capture in captures:
        platform = capture.platform_hint or default_platform
        local_device = hostname_by_hint[capture.device_hint]
        links.extend(parse_cdp(local_device, capture.raw_text, platform=platform))
        links.extend(parse_lldp(local_device, capture.raw_text, platform=platform))
    return links


def _serials(canonical_by_spelling: dict[str, str]) -> dict[str, str]:
    """Recover the chassis serial NX-OS advertises inside its name, keyed by hostname."""
    return {
        canonical: serial
        for spelling, canonical in canonical_by_spelling.items()
        if (serial := split_serial_suffix(spelling)[1]) is not None
    }


def _best_reported(
    discovered: Sequence[Link],
    canonical_by_spelling: dict[str, str],
    attribute: Callable[[Link], str | None],
) -> dict[str, str]:
    """Best value neighbors reported for `attribute`, keyed by canonical hostname.

    Facts about a device we hold no capture for can only come from the devices that see
    it, and several of them usually do. Those reports are ranked rather than taken
    first-come, on the same CDP-over-LLDP order the rest of the build uses: CDP carries
    the chassis model and management address the device itself advertises, where LLDP's
    equivalents are optional TLVs many implementations leave empty or fill loosely.
    Resolving the ranking here, over every raw link, keeps the answer independent of the
    order the captures happened to be read in.
    """
    best_by_hostname: dict[str, tuple[int, str]] = {}
    for link in discovered:
        value = attribute(link)
        if not value:
            continue
        hostname = canonical_by_spelling[link.remote_device]
        rank = _discovery_rank(link)
        current = best_by_hostname.get(hostname)
        if current is None or rank < current[0]:
            best_by_hostname[hostname] = (rank, value)
    return {hostname: value for hostname, (_, value) in best_by_hostname.items()}


def _discovery_rank(link: Link) -> int:
    return _DISCOVERY_RANK.get(link.discovery, len(_DISCOVERY_RANK))


def _infer_role(capabilities: Iterable[str]) -> DeviceRole:
    capability_set = set(capabilities)
    for capability, role in _ROLE_BY_CAPABILITY:
        if capability in capability_set:
            return role
    return DeviceRole.UNKNOWN
