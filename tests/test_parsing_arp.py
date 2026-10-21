"""Tests for `show ip arp` parsing (PROJECT_SPEC.md section 4)."""

from __future__ import annotations

from pathlib import Path

from nettopo.model.entities import ArpEntry
from nettopo.parsing.arp import parse_arp

FIXTURES = Path(__file__).parent / "fixtures" / "arp"


def _capture(fixture: str) -> str:
    return f"core1#show ip arp\n{(FIXTURES / fixture).read_text()}"


def test_ios_skips_incomplete_entries_and_normalizes_interfaces() -> None:
    entries = parse_arp("core1", _capture("ios_show_ip_arp.txt"))
    assert entries == [
        ArpEntry("core1", "10.10.10.1", "00de.fb12.3456", "Vl10"),
        ArpEntry("core1", "10.10.10.51", "0050.56a1.0001", "Vl10"),
        ArpEntry("core1", "10.10.10.52", "0050.56a1.0002", "Vl10"),
        ArpEntry("core1", "10.10.30.52", "001b.5432.aaaa", "Vl30"),
    ]


def test_nxos_skips_uppercase_incomplete_entries() -> None:
    entries = parse_arp("core1", _capture("nxos_show_ip_arp.txt"), platform="cisco_nxos")
    assert entries == [
        ArpEntry("core1", "10.20.10.51", "0050.56b2.0001", "Vl10"),
        ArpEntry("core1", "10.20.20.52", "0050.56b2.0002", "Vl20"),
    ]


def test_an_arp_vrf_variant_is_not_read_as_the_default_table() -> None:
    capture = "core1#show ip arp vrf MGMT\n" + (FIXTURES / "ios_show_ip_arp.txt").read_text()
    assert parse_arp("core1", capture) == []


def test_an_unreadable_table_is_skipped_with_a_warning() -> None:
    capture = "core1#show ip arp\nProtocol  Address\n"
    assert parse_arp("core1", capture, platform="cisco_unknown") == []
