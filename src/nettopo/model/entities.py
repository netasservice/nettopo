"""Dataclasses and enums for the normalized network model (PROJECT_SPEC.md section 6).

`model` depends on nothing but `nettopo.utils` and must never import `render`, `views`,
or N2G (layering rule, PROJECT_SPEC.md section 3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DeviceRole(Enum):
    ROUTER = "router"
    L3_SWITCH = "l3_switch"
    SWITCH = "switch"
    FIREWALL = "firewall"
    AP = "ap"
    PHONE = "phone"
    SERVER = "server"
    HOST = "host"
    UNKNOWN = "unknown"


class InterfaceType(Enum):
    PHYSICAL = "physical"
    SVI = "svi"
    PORT_CHANNEL = "port_channel"
    LOOPBACK = "loopback"
    SUBINTERFACE = "subinterface"
    MGMT = "mgmt"
    TUNNEL = "tunnel"
    UNKNOWN = "unknown"


class StpRole(Enum):
    ROOT = "root"
    DESIGNATED = "designated"
    ALTERNATE = "alternate"
    BACKUP = "backup"
    DISABLED = "disabled"


class StpState(Enum):
    FWD = "forwarding"
    BLK = "blocking"
    LRN = "learning"
    LIS = "listening"
    DIS = "disabled"
    BKN = "broken"  # the port is up but STP refuses to use it (PVID/type inconsistency)


class HsrpRole(Enum):
    ACTIVE = "active"
    STANDBY = "standby"
    LISTEN = "listen"
    INIT = "init"
    SPEAK = "speak"
    LEARN = "learn"


class BgpType(Enum):
    IBGP = "ibgp"
    EBGP = "ebgp"


@dataclass
class Interface:
    name: str  # normalized: "Gi1/0/1", "Vl10", "Po1"
    type: InterfaceType = InterfaceType.UNKNOWN
    description: str | None = None
    admin_up: bool | None = None
    oper_up: bool | None = None
    ip_address: str | None = None
    prefix_len: int | None = None
    vlan: int | None = None  # access VLAN, or the SVI number
    mode: str | None = None  # "access" | "trunk"
    speed: str | None = None  # as `show interfaces status` prints it: "a-1000", "10G"
    duplex: str | None = None  # "a-full", "full", "auto"
    trunk_vlans: list[int] = field(default_factory=list)
    po_id: int | None = None  # if this port is a member of a port-channel
    po_members: list[str] = field(default_factory=list)  # if this IS the port-channel (MLAG/LAG)


@dataclass
class Device:
    hostname: str  # canonical correlation key
    is_source: bool = False  # we have this device's own capture
    platform: str | None = None  # raw: "cisco C9300-48P"; own `show version`, or CDP/LLDP
    model: str | None = None  # parsed: "C9300-48P"
    os: str | None = None  # "ios" | "ios-xe" | "nxos"
    serial: str | None = None  # own `show version`, or the suffix NX-OS advertises
    role: DeviceRole = DeviceRole.UNKNOWN
    mgmt_ip: str | None = None  # as a neighbor advertises it over CDP/LLDP
    chassis_id: str | None = None  # base MAC, as an LLDP neighbor advertises it
    asn: int | None = None  # for BGP
    router_id: str | None = None  # BGP router ID, from its own `show ip bgp summary`
    interfaces: dict[str, Interface] = field(default_factory=dict)  # keyed by normalized name


@dataclass
class Link:
    local_device: str
    local_interface: str
    remote_device: str
    remote_interface: str
    discovery: str = "cdp"  # "cdp" | "lldp"
    remote_platform: str | None = None  # CDP platform, or the LLDP System Description
    remote_mgmt_ip: str | None = None
    remote_chassis_id: str | None = None  # LLDP only; CDP never advertises a chassis MAC
    remote_capabilities: list[str] = field(default_factory=list)  # CDP ["Router"] / LLDP ["B","R"]

    def key(self) -> frozenset[tuple[str, str]]:
        """Direction-independent identity, used to de-duplicate A->B and B->A."""
        return frozenset(
            {
                (self.local_device, self.local_interface),
                (self.remote_device, self.remote_interface),
            }
        )

    def oriented(self, source: str) -> tuple[str, str]:
        """This link's (source-end, target-end) interfaces when drawn from `source`.

        Only one direction of each physical link survives ingestion, and which one depends
        on whose capture reported it, so any view that draws links in its own chosen
        orientation has to re-point the interfaces to match.
        """
        if self.local_device == source:
            return self.local_interface, self.remote_interface
        return self.remote_interface, self.local_interface


@dataclass
class StpBridge:
    device: str
    vlan: int
    base_priority: int  # configured base, e.g. 24576
    sys_id_ext: int  # normally equals the VLAN id
    mac: str
    is_root: bool = False
    root_mac: str = ""  # the elected root's address, whether or not that root is this bridge

    @property
    def effective_priority(self) -> int:
        return self.base_priority + self.sys_id_ext


@dataclass
class StpPort:
    device: str
    vlan: int
    interface: str
    role: StpRole
    state: StpState
    cost: int | None = None
    link_type: str = ""  # the Type column verbatim: "P2p", "P2p Edge", "Shr", "P2p Peer(STP)"

    @property
    def is_edge(self) -> bool:
        """Whether PortFast is enabled, i.e. the port faces a host rather than a switch.

        Read off the Type column as a token rather than a substring: the two spellings
        differ by platform (`P2p Edge` on IOS-XE, `Edge P2p` on NX-OS) and the column
        carries unrelated words that a substring test would confuse it with.
        """
        return "edge" in self.link_type.casefold().split()


@dataclass
class StpVlan:
    vlan: int
    root_device: str | None = None  # None when the root is a device we hold no capture for
    root_mac: str = ""  # the elected root's address, even when `root_device` is unknown
    bridges: dict[str, StpBridge] = field(default_factory=dict)
    ports: dict[tuple[str, str], StpPort] = field(default_factory=dict)  # (device, interface)


@dataclass
class HsrpMember:
    device: str
    interface: str  # the SVI: "Vl10"
    group: int
    priority: int
    role: HsrpRole
    preempt: bool | None = None


@dataclass
class HsrpGroup:
    vlan: int
    group: int
    virtual_ip: str | None = None
    members: dict[str, HsrpMember] = field(default_factory=dict)  # keyed by device


@dataclass
class BgpPeer:
    local_device: str
    local_asn: int
    peer_ip: str
    peer_asn: int
    state: str  # "Established", "Idle", "Active", ...
    type: BgpType
    peer_device: str | None = None  # v1: always None
    vrf: str = "default"


@dataclass
class Vlan:
    vlan_id: int
    name: str | None = None
    status: str | None = None


@dataclass
class MacEntry:
    """One endpoint row of a device's `show mac address-table`."""

    device: str
    mac: str  # normalized by `utils/mac.normalize_mac`
    vlan: int
    interface: str  # normalized; always a real port, never CPU/Router/sup-eth
    entry_type: str  # "dynamic" | "static", as the device prints it, lowercased


@dataclass
class ArpEntry:
    """One resolved row of a device's `show ip arp` (default VRF)."""

    device: str
    ip: str
    mac: str  # normalized by `utils/mac.normalize_mac`
    interface: str | None  # normalized; IOS leaves it blank on some entries


@dataclass
class NetworkModel:
    devices: dict[str, Device] = field(default_factory=dict)  # keyed by hostname
    links: list[Link] = field(default_factory=list)
    vlans: dict[int, Vlan] = field(default_factory=dict)
    stp: dict[int, StpVlan] = field(default_factory=dict)  # keyed by VLAN id
    hsrp: dict[tuple[int, int], HsrpGroup] = field(default_factory=dict)  # (vlan, group)
    bgp: list[BgpPeer] = field(default_factory=list)
    mac_entries: list[MacEntry] = field(default_factory=list)
    arp_entries: list[ArpEntry] = field(default_factory=list)

    def port_channel_name(self, hostname: str, interface_name: str) -> str | None:
        """The port-channel `interface_name` on `hostname` belongs to (or is), if any.

        Which bundle a physical port belongs to is a property of the model, not of any one
        view: both the L2 view (to collapse an adjacency onto its bundle) and the STP view
        (to find the logical port `show spanning-tree` reports the bundle under) need the
        same answer. Only source devices have interfaces populated, so an adjacency to a
        device we hold no capture for resolves from its near end alone.
        """
        device = self.devices.get(hostname)
        if device is None:
            return None
        interface = device.interfaces.get(interface_name)
        if interface is None:
            return None
        if interface.po_id is not None:
            return f"Po{interface.po_id}"
        return interface.name if interface.po_members else None
