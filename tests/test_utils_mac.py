"""Tests for the central MAC-address normalizer."""

from __future__ import annotations

import pytest

from nettopo.utils.mac import normalize_mac


@pytest.mark.parametrize(
    "raw",
    [
        "0050.56a1.0001",
        "0050.56A1.0001",
        "00:50:56:a1:00:01",
        "00-50-56-A1-00-01",
        "005056a10001",
        "  0050.56a1.0001  ",
    ],
)
def test_every_spelling_normalizes_to_the_cisco_dotted_form(raw: str) -> None:
    assert normalize_mac(raw) == "0050.56a1.0001"


@pytest.mark.parametrize("raw", ["Incomplete", "INCOMPLETE", "", "0050.56a1.000", "zz50.56a1.0001"])
def test_anything_that_is_not_a_mac_returns_none(raw: str) -> None:
    assert normalize_mac(raw) is None


def test_normalization_is_idempotent() -> None:
    once = normalize_mac("00:50:56:A1:00:01")
    assert once is not None
    assert normalize_mac(once) == once
