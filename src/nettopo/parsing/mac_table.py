"""Parser for `show mac address-table` (PROJECT_SPEC.md section 4).

Only endpoint entries are kept: a MAC learned on (or statically pinned to) one real port
in one numbered VLAN. Everything else the table lists belongs to the switch itself, not
to anything plugged into it, and is dropped:

- entries pointing at `CPU`, `Router`, `Switch`, `Drop`, `sup-eth1(R)` or NX-OS's
  `vPC Peer-Link` -- none of which is an interface name;
- entries pointing at an SVI, which is the switch's own routed MAC;
- entries in VLAN `All` (IOS) or `-` (NX-OS gateway/control-plane rows).

A Catalyst line listing several ports (`20 0100.5e00.0001 STATIC Gi1/0/1, Gi1/0/2`) is
a multicast or flood entry, never an endpoint -- and the shipped IOS template cannot
parse it at all: it raises `TextFSMError` (not a `ParsingException`) and returns no
records for the whole table. Those lines are removed before the template sees them.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from nettopo.model.entities import InterfaceType, MacEntry
from nettopo.parsing._textfsm import TEMPLATE_ERRORS, run_template
from nettopo.parsing.interfaces import interface_type
from nettopo.utils.command_sections import extract_command_output
from nettopo.utils.interfaces import looks_like_interface, normalize
from nettopo.utils.mac import normalize_mac

logger = logging.getLogger("nettopo")

# Accepts the current spelling and the legacy `show mac-address-table`.
_COMMAND_PATTERN = re.compile(r"show\s+mac[\s-]+addr\w*(?:-table)?\s*$", re.IGNORECASE)
_MULTI_PORT_LINE = re.compile(r"\S,\s*\S")


def parse_mac_table(
    local_device: str, raw_text: str, *, platform: str = "cisco_ios"
) -> list[MacEntry]:
    """Parse `local_device`'s MAC address table into endpoint entries."""
    output = extract_command_output(raw_text, _COMMAND_PATTERN)
    if not output:
        return []

    entries = []
    for record in _run(platform=platform, data=_drop_multi_port_lines(output)):
        entry = _to_entry(local_device, record)
        if entry is not None:
            entries.append(entry)
    return entries


def _drop_multi_port_lines(output: str) -> str:
    return "\n".join(line for line in output.splitlines() if not _MULTI_PORT_LINE.search(line))


def _run(*, platform: str, data: str) -> list[dict[str, Any]]:
    """Run the template, downgrading an unusable table to "no MACs known"."""
    try:
        return run_template(platform=platform, command="show mac address-table", data=data)
    except TEMPLATE_ERRORS as exc:
        logger.warning("Skipping MAC address table: %s", exc)
        return []


def _to_entry(local_device: str, record: dict[str, Any]) -> MacEntry | None:
    # IOS and NX-OS templates name the same two columns differently, and IOS reports its
    # port column as a list -- of exactly one port, once multi-port lines are dropped.
    raw_mac = record.get("destination_address") or record.get("mac_address") or ""
    raw_port = record.get("destination_port") or record.get("ports") or ""
    if isinstance(raw_port, list):
        raw_port = raw_port[0]

    mac = normalize_mac(str(raw_mac))
    vlan = str(record.get("vlan_id", ""))
    if mac is None or not vlan.isdigit() or not looks_like_interface(str(raw_port)):
        return None

    interface = normalize(str(raw_port))
    if interface_type(interface) is InterfaceType.SVI:
        return None

    return MacEntry(
        device=local_device,
        mac=mac,
        vlan=int(vlan),
        interface=interface,
        entry_type=str(record.get("type", "")).lower(),
    )
