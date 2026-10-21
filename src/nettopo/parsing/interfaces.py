"""Parser for interface state (PROJECT_SPEC.md section 4).

Four commands describe interfaces, each partially: `show ip interface brief` (state and
IP), `show interfaces status` (access VLAN or trunk, speed, duplex, a truncated name),
`show interfaces description` (the full description) and `show interfaces` (everything,
but rarely captured because it is long). They are merged here by normalized name, from
the least to the most authoritative, so whichever subset a capture holds yields the
best answer it can.
"""

from __future__ import annotations

import re

from nettopo.model.entities import Interface, InterfaceType
from nettopo.parsing._textfsm import run_template
from nettopo.utils.command_sections import extract_command_output
from nettopo.utils.interfaces import normalize, svi_vlan

_IP_BRIEF_PATTERN = re.compile(r"show\s+ip\s+int\w*\s+br\w*\s*$", re.IGNORECASE)
_INTERFACES_PATTERN = re.compile(r"show\s+int\w*\s*$", re.IGNORECASE)
_STATUS_PATTERN = re.compile(r"show\s+int\w*\s+stat\w*\s*$", re.IGNORECASE)
_DESCRIPTION_PATTERN = re.compile(r"show\s+int\w*\s+desc\w*\s*$", re.IGNORECASE)

_UNASSIGNED = {"unassigned", ""}

# The ntc-templates index matches IOS commands spelled either way, but NX-OS only in the
# singular -- so the singular is the one spelling both platforms accept.
_STATUS_COMMAND = "show interface status"
_DESCRIPTION_COMMAND = "show interface description"

# NX-OS prints `--` for an empty column, and its description template mis-captures the
# `mgmt0` row as `--     --      --`; either way it means "no value".
_EMPTY_COLUMN = re.compile(r"^[-\s]*$")

# `show interfaces status` Status column. Anything not listed here (`notconnect`, NX-OS's
# truncated `notconnec`, `err-disabled`, `sfpAbsent`, `suspended`, ...) means the port is
# enabled but not passing traffic.
_STATUS_ADMIN_DOWN = {"disabled"}
_STATUS_OPER_UP = {"connected", "up"}


def parse_interfaces(raw_text: str, *, platform: str = "cisco_ios") -> dict[str, Interface]:
    """Parse interface state from every interface command the capture holds.

    Merged by normalized interface name, least authoritative first: `show ip interface
    brief`, then `show interfaces status` (fills only what is still unknown), then
    `show interfaces description`, then `show interfaces`, the richest source, which wins
    on overlapping fields. An interface listed by any one of them is kept.
    """
    interfaces: dict[str, Interface] = {}

    for record in _parse_section(raw_text, platform, _IP_BRIEF_PATTERN, "show ip interface brief"):
        interface = _get_or_create(interfaces, record["interface"])
        interface.admin_up = record["status"] != "administratively down"
        interface.oper_up = record["proto"] == "up"
        if record["ip_address"] not in _UNASSIGNED:
            interface.ip_address = record["ip_address"]

    for record in _parse_section(raw_text, platform, _STATUS_PATTERN, _STATUS_COMMAND):
        _apply_status(_get_or_create(interfaces, record["port"]), record)

    for record in _parse_section(raw_text, platform, _DESCRIPTION_PATTERN, _DESCRIPTION_COMMAND):
        _apply_description(_get_or_create(interfaces, record["port"]), record)

    for record in _parse_section(raw_text, platform, _INTERFACES_PATTERN, "show interfaces"):
        interface = _get_or_create(interfaces, record["interface"])
        # An empty description here must not wipe one `show interfaces description` set.
        interface.description = record.get("description") or interface.description
        interface.admin_up = record["link_status"] != "administratively down"
        interface.oper_up = _oper_up(record.get("protocol_status", ""))
        if record.get("ip_address"):
            interface.ip_address = record["ip_address"]
            interface.prefix_len = _to_int(record.get("prefix_length"))

    for interface in interfaces.values():
        if interface.vlan is None:
            interface.vlan = svi_vlan(interface.name)

    return interfaces


def interface_type(name: str) -> InterfaceType:
    """Classify an already-normalized interface name by its canonical prefix."""
    if "." in name:
        return InterfaceType.SUBINTERFACE
    if name.startswith("Vl"):
        return InterfaceType.SVI
    if name.startswith("Po"):
        return InterfaceType.PORT_CHANNEL
    if name.startswith("Lo"):
        return InterfaceType.LOOPBACK
    if name.startswith("Tu"):
        return InterfaceType.TUNNEL
    if name.startswith("Mgmt"):
        return InterfaceType.MGMT
    return InterfaceType.PHYSICAL


def _apply_status(interface: Interface, record: dict[str, str]) -> None:
    """Merge one `show interfaces status` row, which only fills what is still unknown.

    The Name column is truncated to about 18 characters, so it is a description of last
    resort; the state columns lose to `show ip interface brief`, which distinguishes
    "admin down" from "down" more reliably than a Status of `notconnect` does.
    """
    vlan = record.get("vlan_id", "")
    if vlan.isdigit():
        interface.vlan = int(vlan)
        interface.mode = "access"
    elif vlan == "trunk":
        interface.mode = "trunk"

    interface.speed = _value(record.get("speed"))
    interface.duplex = _value(record.get("duplex"))
    interface.description = interface.description or _value(record.get("name"))

    status = record.get("status", "")
    if interface.admin_up is None:
        interface.admin_up = status not in _STATUS_ADMIN_DOWN
    if interface.oper_up is None:
        interface.oper_up = status in _STATUS_OPER_UP


def _apply_description(interface: Interface, record: dict[str, str]) -> None:
    """Merge one `show interfaces description` row.

    Only the IOS template has Status/Protocol columns; the NX-OS one reports the
    description alone, so state is left to the other commands there.
    """
    interface.description = _value(record.get("description")) or interface.description
    if record.get("status"):
        interface.admin_up = record["status"] != "admin down"
    if record.get("protocol"):
        interface.oper_up = record["protocol"] == "up"


def _value(raw: str | None) -> str | None:
    if raw is None or _EMPTY_COLUMN.match(raw):
        return None
    return raw.strip()


def _get_or_create(interfaces: dict[str, Interface], raw_name: str) -> Interface:
    name = normalize(raw_name)
    return interfaces.setdefault(name, Interface(name=name, type=interface_type(name)))


def _parse_section(
    raw_text: str, platform: str, pattern: re.Pattern[str], command: str
) -> list[dict[str, str]]:
    output = extract_command_output(raw_text, pattern)
    if not output:
        return []
    return run_template(platform=platform, command=command, data=output)


def _oper_up(protocol_status: str) -> bool | None:
    if not protocol_status:
        return None
    return protocol_status.split()[0] == "up"


def _to_int(value: str | None) -> int | None:
    return int(value) if value else None
