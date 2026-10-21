"""Parser for `show ip arp` (PROJECT_SPEC.md section 4).

Only the default VRF is read. NX-OS `show ip arp vrf all` prints one header per context,
which the shipped template rejects, and the port table only needs the IP of an endpoint
it already found in a MAC table -- an address in another VRF would still be found
whenever that VRF's gateway capture includes a plain `show ip arp` for it.

Unresolved entries (`Incomplete` on IOS, `INCOMPLETE` on NX-OS) carry no MAC and are
dropped.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from nettopo.model.entities import ArpEntry
from nettopo.parsing._textfsm import TEMPLATE_ERRORS, run_template
from nettopo.utils.command_sections import extract_command_output
from nettopo.utils.interfaces import normalize
from nettopo.utils.mac import normalize_mac

logger = logging.getLogger("nettopo")

_COMMAND_PATTERN = re.compile(r"show\s+ip\s+arp\s*$", re.IGNORECASE)


def parse_arp(local_device: str, raw_text: str, *, platform: str = "cisco_ios") -> list[ArpEntry]:
    """Parse `local_device`'s ARP table into resolved IP-to-MAC entries."""
    output = extract_command_output(raw_text, _COMMAND_PATTERN)
    if not output:
        return []

    entries = []
    for record in _run(platform=platform, data=output):
        mac = normalize_mac(str(record.get("mac_address", "")))
        if mac is None:
            continue
        interface = str(record.get("interface", ""))
        entries.append(
            ArpEntry(
                device=local_device,
                ip=str(record["ip_address"]),
                mac=mac,
                interface=normalize(interface) if interface else None,
            )
        )
    return entries


def _run(*, platform: str, data: str) -> list[dict[str, Any]]:
    """Run the template, downgrading an unusable table to "no ARP entries known"."""
    try:
        return run_template(platform=platform, command="show ip arp", data=data)
    except TEMPLATE_ERRORS as exc:
        logger.warning("Skipping ARP table: %s", exc)
        return []
