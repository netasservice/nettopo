"""Exhaustive table-driven tests for the interface-name normalizer.

Covers the full canonical-form table (PROJECT_SPEC.md section 5), idempotency,
already-abbreviated inputs, and mixed casing.
"""

from __future__ import annotations

import pytest

from nettopo.utils.interfaces import interface_sort_key, looks_like_interface, normalize, svi_vlan

# Long-form input -> expected canonical output, one row per PROJECT_SPEC.md section 5.
LONG_FORM_CASES = [
    ("GigabitEthernet1/0/1", "Gi1/0/1"),
    ("TenGigabitEthernet1/1/1", "Te1/1/1"),
    ("TwentyFiveGigE1/0/1", "Twe1/0/1"),
    ("FortyGigabitEthernet1/0/1", "Fo1/0/1"),
    ("HundredGigE1/0/1", "Hu1/0/1"),
    ("FastEthernet0/1", "Fa0/1"),
    ("Ethernet0/1", "Eth0/1"),
    ("Port-channel1", "Po1"),
    ("Vlan10", "Vl10"),
    ("Loopback0", "Lo0"),
    ("Tunnel0", "Tu0"),
    ("Management0/0/0", "Mgmt0/0/0"),
]

# Inputs already in canonical short form must be returned unchanged.
ALREADY_ABBREVIATED_CASES = [
    "Gi1/0/1",
    "Te1/1/1",
    "Twe1/0/1",
    "Fo1/0/1",
    "Hu1/0/1",
    "Fa0/1",
    "Eth0/1",
    "Po1",
    "Vl10",
    "Lo0",
    "Tu0",
    "Mgmt0/0/0",
]

# Mixed / unusual casing of both long and short forms.
MIXED_CASE_CASES = [
    ("gigabitethernet1/0/1", "Gi1/0/1"),
    ("GIGABITETHERNET1/0/1", "Gi1/0/1"),
    ("gi1/0/1", "Gi1/0/1"),
    ("GI1/0/1", "Gi1/0/1"),
    ("tengigabitethernet1/1/1", "Te1/1/1"),
    ("TE1/1/1", "Te1/1/1"),
    ("port-CHANNEL1", "Po1"),
    ("PO1", "Po1"),
    ("vLAN10", "Vl10"),
    ("MGMT0/0/0", "Mgmt0/0/0"),
    ("mgmt0/0/0", "Mgmt0/0/0"),
]


@pytest.mark.parametrize(("raw", "expected"), LONG_FORM_CASES)
def test_normalizes_long_form_to_canonical(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize("already_canonical", ALREADY_ABBREVIATED_CASES)
def test_already_abbreviated_input_is_unchanged(already_canonical: str) -> None:
    assert normalize(already_canonical) == already_canonical


@pytest.mark.parametrize(("raw", "expected"), MIXED_CASE_CASES)
def test_mixed_casing_is_normalized(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [raw for raw, _ in LONG_FORM_CASES]
    + ALREADY_ABBREVIATED_CASES
    + [raw for raw, _ in MIXED_CASE_CASES],
)
def test_idempotent(raw: str) -> None:
    once = normalize(raw)
    twice = normalize(once)
    assert once == twice


def test_unrecognized_interface_type_is_returned_unchanged() -> None:
    assert normalize("Async0") == "Async0"


@pytest.mark.parametrize(
    "raw",
    [raw for raw, _ in LONG_FORM_CASES]
    + ALREADY_ABBREVIATED_CASES
    + [raw for raw, _ in MIXED_CASE_CASES],
)
def test_every_recognized_interface_name_looks_like_one(raw: str) -> None:
    assert looks_like_interface(raw) is True


@pytest.mark.parametrize(
    "raw",
    [
        "uplink-to-acc-sw3",  # an NX-OS LLDP "Port Description"
        "Port 1",  # an IP phone's port id -- shares a prefix with Port-channel
        "long-haul uplink",  # shares a prefix with Loopback
        "vmnic0",  # a VMware uplink
        "0050.568a.1234",
        "",
    ],
)
def test_free_text_does_not_look_like_an_interface(raw: str) -> None:
    assert looks_like_interface(raw) is False


def test_does_not_mutate_numeric_suffix() -> None:
    assert normalize("GigabitEthernet1/0/48") == "Gi1/0/48"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Vl10", 10),
        ("Vlan10", 10),
        ("vlan999", 999),
        ("Gi1/0/1", None),
        ("Po1", None),
        ("Vl10.5", None),  # a subinterface of an SVI is not itself the SVI
        ("Vlan", None),
        ("", None),
    ],
)
def test_svi_vlan_reads_the_vlan_out_of_an_svi_name(raw: str, expected: int | None) -> None:
    assert svi_vlan(raw) == expected


def test_interface_sort_key_orders_port_numbers_numerically() -> None:
    names = ["Gi1/0/10", "Vl10", "Gi1/0/2", "Gi1/0/1", "Po1", "Gi1/1/1", "Vl2"]
    assert sorted(names, key=interface_sort_key) == [
        "Gi1/0/1",
        "Gi1/0/2",
        "Gi1/0/10",
        "Gi1/1/1",
        "Po1",
        "Vl2",
        "Vl10",
    ]


def test_interface_sort_key_never_compares_text_with_a_number() -> None:
    """A bare `1/1` (some switches print ports this way) must not raise a TypeError."""
    assert sorted(["1/1", "mgmt0", "Eth1/1"], key=interface_sort_key) == ["Eth1/1", "mgmt0", "1/1"]
