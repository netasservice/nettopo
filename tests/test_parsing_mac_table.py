"""Tests for `show mac address-table` parsing (PROJECT_SPEC.md section 4)."""

from __future__ import annotations

from pathlib import Path

from nettopo.model.entities import MacEntry
from nettopo.parsing.mac_table import parse_mac_table

FIXTURES = Path(__file__).parent / "fixtures" / "mac_table"


def _capture(fixture: str, command: str = "show mac address-table") -> str:
    return f"sw1#{command}\n{(FIXTURES / fixture).read_text()}"


def test_ios_keeps_only_endpoint_entries() -> None:
    entries = parse_mac_table("sw1", _capture("ios_show_mac_address_table.txt"))
    assert entries == [
        MacEntry("sw1", "0050.56a1.0001", 10, "Gi1/0/5", "dynamic"),
        MacEntry("sw1", "0050.56a1.0002", 10, "Gi1/0/6", "dynamic"),
        MacEntry("sw1", "001b.5432.aaaa", 30, "Gi1/0/6", "dynamic"),
        MacEntry("sw1", "0019.e7aa.0001", 10, "Gi1/0/48", "dynamic"),
        MacEntry("sw1", "0019.e7aa.0002", 10, "Po1", "dynamic"),
    ]


def test_a_multi_port_line_no_longer_loses_the_whole_ios_table() -> None:
    """The shipped IOS template raises on `Gi1/0/1, Gi1/0/2`; that line must be skipped."""
    entries = parse_mac_table("sw1", _capture("ios_show_mac_address_table.txt"))
    assert entries
    assert all(entry.mac != "0100.5e00.0001" for entry in entries)


def test_nxos_drops_gateway_peer_link_and_supervisor_entries() -> None:
    entries = parse_mac_table(
        "nx1", _capture("nxos_show_mac_address_table.txt"), platform="cisco_nxos"
    )
    assert entries == [
        MacEntry("nx1", "0050.56b2.0001", 10, "Eth1/5", "dynamic"),
        MacEntry("nx1", "0050.56b2.0002", 20, "Eth1/6", "dynamic"),
        MacEntry("nx1", "0019.e7bb.0001", 10, "Po10", "dynamic"),
    ]


def test_the_legacy_hyphenated_command_is_recognized() -> None:
    capture = _capture("ios_show_mac_address_table.txt", command="show mac-address-table")
    assert len(parse_mac_table("sw1", capture)) == 5


def test_a_capture_without_the_command_yields_nothing() -> None:
    assert parse_mac_table("sw1", "sw1#show version\nCisco IOS Software\n") == []


def test_an_unreadable_table_is_skipped_with_a_warning() -> None:
    capture = "sw1#show mac address-table\nVlan    Mac Address       Type        Ports\n%garbage\n"
    assert parse_mac_table("sw1", capture, platform="cisco_unknown") == []
