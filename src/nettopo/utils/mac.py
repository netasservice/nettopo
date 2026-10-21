"""The central MAC-address normalizer.

`show mac address-table` and `show ip arp` are joined on the MAC address, often across
devices, and nothing guarantees every source spells it the same way: Cisco prints
`0050.56a1.0001`, while other vendors and LLDP use `00:50:56:a1:00:01` or
`00-50-56-A1-00-01`. Every parser that reads a MAC routes it through `normalize_mac()`
so that the join compares one spelling.
"""

from __future__ import annotations

import re

_SEPARATORS = re.compile(r"[.:\-]")
_HEX_DIGITS = re.compile(r"^[0-9a-f]{12}$")


def normalize_mac(raw: str) -> str | None:
    """Return `raw` in lowercase Cisco dotted form (`0050.56a1.0001`), or None.

    None means "not a MAC address": the ARP table prints `Incomplete` (IOS) or
    `INCOMPLETE` (NX-OS) for an unresolved entry, and that placeholder must not be
    matched against anything.
    """
    digits = _SEPARATORS.sub("", raw.strip()).lower()
    if not _HEX_DIGITS.match(digits):
        return None
    return f"{digits[0:4]}.{digits[4:8]}.{digits[8:12]}"
