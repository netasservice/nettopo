"""The per-port endpoint table behind `ports.csv` (PROJECT_SPEC.md section 8).

Answers "what is plugged into each port" by joining four things the model holds
separately: interface state, the MAC address table, CDP/LLDP links, and the ARP tables.
It builds rows only; `csv_export.py` writes them. This is a join, not a drawing, which
is why it lives in `export/` rather than among the views (which return a `Diagram`).

Rules:

- Every interface of every captured device gets at least one row, sorted by device and
  then in natural interface order.
- An **uplink** -- a port whose CDP/LLDP neighbor is network gear (router, multilayer
  switch, switch, firewall), a port-channel, or a port-channel member -- gets exactly one
  row showing that neighbor. The MACs it learned are hidden: they belong to endpoints
  behind the neighbor, and each one already has a row on its own access port.
- Any other port gets one row per MAC learned on it (deduplicated per VLAN), with the
  neighbor, if any, repeated on each. A neighbor whose role is phone, AP, host, server
  or **unknown** does not make a port an uplink, so the PC behind an IP phone still
  shows up.
- The IP of a MAC is looked up in the ARP tables of *every* captured device, since the
  gateway holding the ARP entry is rarely the access switch that learned the MAC.
- A row without a MAC shows the port's access VLAN in the VLAN column, or `trunk`.
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import astuple, dataclass, fields

from nettopo.model.entities import Device, DeviceRole, Interface, InterfaceType, NetworkModel
from nettopo.utils.interfaces import interface_sort_key

_NETWORK_ROLES = frozenset(
    {DeviceRole.ROUTER, DeviceRole.L3_SWITCH, DeviceRole.SWITCH, DeviceRole.FIREWALL}
)


@dataclass(frozen=True)
class PortRow:
    device: str
    interface: str
    status: str | None
    description: str | None
    speed: str | None
    duplex: str | None
    mac: str | None
    vlan: int | str | None
    ip: str | None
    neighbor: str | None
    neighbor_interface: str | None
    neighbor_platform: str | None

    def as_tuple(self) -> tuple[object, ...]:
        return astuple(self)


PORT_TABLE_HEADER: tuple[str, ...] = tuple(field.name for field in fields(PortRow))


@dataclass(frozen=True)
class _Neighbor:
    device: str
    interface: str


def build_port_rows(model: NetworkModel) -> list[PortRow]:
    """One row per learned MAC on each port of each captured device (rules above)."""
    neighbors_by_port = _neighbors_by_port(model)
    macs_by_port = _macs_by_port(model)
    ips_by_mac = _ips_by_mac(model)

    rows: list[PortRow] = []
    for device in sorted(model.devices.values(), key=lambda device: device.hostname):
        if not device.is_source:
            continue
        for interface in sorted(
            device.interfaces.values(), key=lambda interface: interface_sort_key(interface.name)
        ):
            neighbors = _port_neighbors(model, device, interface, neighbors_by_port)
            port = _PortContext(model, device, interface, neighbors)
            learned = macs_by_port.get((device.hostname, interface.name), [])
            if _is_uplink(model, interface, neighbors) or not learned:
                rows.append(port.row(mac=None, vlan=_configured_vlan(interface), ip=None))
                continue
            for mac, vlan in learned:
                rows.append(port.row(mac=mac, vlan=vlan, ip=_join_ips(ips_by_mac.get(mac))))
    return rows


@dataclass(frozen=True)
class _PortContext:
    """The columns every row of one port shares, so each MAC row only adds its own."""

    model: NetworkModel
    device: Device
    interface: Interface
    neighbors: list[_Neighbor]

    def row(self, *, mac: str | None, vlan: int | str | None, ip: str | None) -> PortRow:
        return PortRow(
            device=self.device.hostname,
            interface=self.interface.name,
            status=_status(self.interface),
            description=self.interface.description,
            speed=self.interface.speed,
            duplex=self.interface.duplex,
            mac=mac,
            vlan=vlan,
            ip=ip,
            neighbor=_join(neighbor.device for neighbor in self.neighbors),
            neighbor_interface=_join(neighbor.interface for neighbor in self.neighbors),
            # The neighbor's own device record, not `Link.remote_platform`: only one
            # direction of each link survives ingestion, so on a link stored from the
            # other end `remote_platform` describes *this* device.
            neighbor_platform=_join(
                self.model.devices[neighbor.device].platform or "" for neighbor in self.neighbors
            ),
        )


def _neighbors_by_port(model: NetworkModel) -> dict[tuple[str, str], list[_Neighbor]]:
    """Each port's neighbors, from both ends of every link.

    A link is stored once, from whichever end reported it, so a port can appear as either
    its local or its remote end.
    """
    neighbors: dict[tuple[str, str], list[_Neighbor]] = defaultdict(list)
    for link in model.links:
        neighbors[(link.local_device, link.local_interface)].append(
            _Neighbor(link.remote_device, link.remote_interface)
        )
        neighbors[(link.remote_device, link.remote_interface)].append(
            _Neighbor(link.local_device, link.local_interface)
        )
    return neighbors


def _port_neighbors(
    model: NetworkModel,
    device: Device,
    interface: Interface,
    neighbors_by_port: dict[tuple[str, str], list[_Neighbor]],
) -> list[_Neighbor]:
    """A port's neighbors; for a port-channel, those of its member ports.

    CDP and LLDP run on the physical members, so a bundle has no links of its own. Each
    member's far end is reported as the remote bundle when the remote device is captured
    too, which collapses N member adjacencies into one `dist-sw1 Po1`.
    """
    if not interface.po_members:
        return neighbors_by_port.get((device.hostname, interface.name), [])

    collapsed: list[_Neighbor] = []
    for member in interface.po_members:
        for neighbor in neighbors_by_port.get((device.hostname, member), []):
            remote_bundle = model.port_channel_name(neighbor.device, neighbor.interface)
            candidate = _Neighbor(neighbor.device, remote_bundle or neighbor.interface)
            if candidate not in collapsed:
                collapsed.append(candidate)
    return collapsed


def _is_uplink(model: NetworkModel, interface: Interface, neighbors: list[_Neighbor]) -> bool:
    if interface.type is InterfaceType.PORT_CHANNEL or interface.po_id is not None:
        return True
    return any(model.devices[neighbor.device].role in _NETWORK_ROLES for neighbor in neighbors)


def _macs_by_port(model: NetworkModel) -> dict[tuple[str, str], list[tuple[str, int]]]:
    unique: dict[tuple[str, str], set[tuple[str, int]]] = defaultdict(set)
    for entry in model.mac_entries:
        unique[(entry.device, entry.interface)].add((entry.mac, entry.vlan))
    return {
        port: sorted(learned, key=lambda mac_vlan: (mac_vlan[1], mac_vlan[0]))
        for port, learned in unique.items()
    }


def _ips_by_mac(model: NetworkModel) -> dict[str, set[str]]:
    ips: dict[str, set[str]] = defaultdict(set)
    for entry in model.arp_entries:
        ips[entry.mac].add(entry.ip)
    return ips


def _join_ips(ips: set[str] | None) -> str | None:
    if not ips:
        return None
    return ";".join(sorted(ips, key=ipaddress.ip_address))


def _status(interface: Interface) -> str | None:
    if interface.admin_up is False:
        return "admin-down"
    if interface.oper_up is None:
        return None
    return "up" if interface.oper_up else "down"


def _configured_vlan(interface: Interface) -> int | str | None:
    if interface.vlan is not None:
        return interface.vlan
    return "trunk" if interface.mode == "trunk" else None


def _join(values: Iterable[str]) -> str | None:
    """`;`-join `values` (kept positional, so the neighbor columns line up), or None."""
    return ";".join(values) or None
