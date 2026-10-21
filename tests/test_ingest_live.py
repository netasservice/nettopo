"""Tests for live collection (PROJECT_SPEC.md section 4).

Every test here runs against the scripted `fake_netmiko` connection from `conftest.py`;
nothing opens a socket.
"""

from __future__ import annotations

import pytest

from nettopo.ingest.credentials import Credentials
from nettopo.ingest.live import (
    _COMMANDS_BY_PLATFORM,
    _IOS_COMMANDS,
    LiveDataSource,
    Outcome,
    _assert_read_only,
)
from nettopo.ingest.model_builder import build_network_model
from tests.conftest import NXOS_VERSION, FakeConnection, FakeNetmiko, ios_version

CREDENTIALS = Credentials(username="netops", password="secret", enable_password="enable")

CDP_OUTPUT = """-------------------------
Device ID: sw2-dist.example.com
Entry address(es):
  IP address: 10.0.0.2
Platform: cisco WS-C3850-24T,  Capabilities: Router Switch IGMP
Interface: GigabitEthernet1/0/1,  Port ID (outgoing port): GigabitEthernet1/0/24
Holdtime : 143 sec
"""


def _source(targets: list[str], **kwargs: object) -> LiveDataSource:
    return LiveDataSource(targets, CREDENTIALS, **kwargs)  # type: ignore[arg-type]


def test_collected_text_is_ingestible_by_the_normal_pipeline(fake_netmiko: FakeNetmiko) -> None:
    """The load-bearing property: what the collector writes, the parsers can read.

    Everything else about the collector is plumbing. If the synthesized prompt lines did
    not match `_PROMPT_LINE`, every capture would parse to nothing and every diagram would
    come out empty -- so this asserts through `build_network_model`, not on the text.
    """
    fake_netmiko.default_outputs["show cdp neighbors detail"] = CDP_OUTPUT
    source = _source(["10.0.0.1"])

    model = build_network_model(source)

    assert "sw1" in model.devices
    assert model.devices["sw1"].is_source
    assert model.devices["sw1"].model == "WS-C2960X-24TS-L"
    # Interface names arrive normalized, exactly as they would from a file capture --
    # the collector feeds the same pipeline, it does not bypass any of it.
    assert [(link.local_device, link.local_interface) for link in model.links] == [
        ("sw1", "Gi1/0/1")
    ]
    assert model.links[0].remote_device == "sw2-dist.example.com"


def test_the_device_platform_reaches_the_capture(fake_netmiko: FakeNetmiko) -> None:
    """`Capture.platform_hint` is why a live source can do better than one global default."""
    result = _source(["10.0.0.1"]).collect()

    capture = result.outcomes[0].capture
    assert capture is not None
    assert capture.platform_hint == "cisco_ios"


def test_nxos_is_detected_and_never_reported_as_cisco_xe(fake_netmiko: FakeNetmiko) -> None:
    """ntc-templates ships no `cisco_xe` platform, so no code path may produce one."""
    fake_netmiko.default_outputs["show version"] = NXOS_VERSION

    result = _source(["10.0.0.1"]).collect()

    capture = result.outcomes[0].capture
    assert capture is not None
    assert capture.platform_hint == "cisco_nxos"


def test_nxos_uses_the_port_channel_command_and_drops_show_standby(
    fake_netmiko: FakeNetmiko,
) -> None:
    """NX-OS has no `show standby brief` parser, so collecting it would add dead text."""
    fake_netmiko.default_outputs["show version"] = NXOS_VERSION

    _source(["10.0.0.1"]).collect()

    sent = fake_netmiko.devices["10.0.0.1"].commands_sent
    assert "show port-channel summary" in sent
    assert "show etherchannel summary" not in sent
    assert "show standby brief" not in sent
    assert "show interface status" in sent
    assert "show interface description" in sent
    assert "show interfaces status" not in sent


def test_the_identity_comes_from_the_device_not_the_inventory(fake_netmiko: FakeNetmiko) -> None:
    """An inventory of bare IPs must still produce hostname-named captures."""
    fake_netmiko.devices["10.0.0.1"] = FakeConnection(
        host="10.0.0.1", outputs=dict(fake_netmiko.default_outputs), prompt="dist-sw2#"
    )

    result = _source(["10.0.0.1"]).collect()

    capture = result.outcomes[0].capture
    assert capture is not None
    assert capture.device_hint == "dist-sw2"


def test_a_device_that_names_itself_nowhere_falls_back_to_the_inventory_target(
    fake_netmiko: FakeNetmiko,
) -> None:
    """The end of the identity chain: parsed hostname, then prompt, then the inventory.

    `sw1(config)#` is not a device name -- taking it as one would poison the CDP/LLDP
    correlation downstream -- and this device answers no `show version` either, so the
    inventory entry is all that is left.
    """
    fake_netmiko.devices["10.0.0.1"] = FakeConnection(
        host="10.0.0.1", outputs={}, prompt="sw1(config)#"
    )

    result = _source(["10.0.0.1"]).collect()

    capture = result.outcomes[0].capture
    assert capture is not None
    assert capture.device_hint == "10.0.0.1"


def test_the_parsed_hostname_wins_over_the_prompt(fake_netmiko: FakeNetmiko) -> None:
    """Identity is decided the way `model_builder` decides it, not a parallel way.

    The two normally agree -- both come from the configured hostname -- but when they
    diverge it is the parsed one the model acts on, so it must be the one the capture is
    named and de-duplicated by.
    """
    fake_netmiko.devices["10.0.0.1"] = FakeConnection(
        host="10.0.0.1", outputs={"show version": ios_version("real-name")}, prompt="stale#"
    )

    result = _source(["10.0.0.1"]).collect()

    capture = result.outcomes[0].capture
    assert capture is not None
    assert capture.device_hint == "real-name"
    # The prompt lines still record what the device actually showed.
    assert capture.raw_text.startswith("stale#show version")


def test_a_rejected_command_is_skipped_and_the_device_still_completes(
    fake_netmiko: FakeNetmiko,
) -> None:
    """A switch with no BGP is a healthy device answering correctly, not a failed run."""
    result = _source(["10.0.0.1"]).collect()

    outcome = result.outcomes[0]
    assert outcome.outcome is Outcome.OK
    # Only `show version` was scripted; every other command was declined.
    assert outcome.commands == 1
    assert result.succeeded


def test_the_session_is_closed_even_when_a_command_raises(fake_netmiko: FakeNetmiko) -> None:
    fake_netmiko.devices["10.0.0.1"] = FakeConnection(
        host="10.0.0.1", outputs={}, fail_with=RuntimeError("connection reset")
    )

    _source(["10.0.0.1"]).collect()

    assert fake_netmiko.devices["10.0.0.1"].disconnected


def test_an_already_privileged_device_is_not_asked_to_enable(fake_netmiko: FakeNetmiko) -> None:
    fake_netmiko.devices["10.0.0.1"] = FakeConnection(
        host="10.0.0.1",
        outputs=dict(fake_netmiko.default_outputs),
        privileged=True,
        enable_error=AssertionError("enable() must not be called in privileged mode"),
    )

    assert _source(["10.0.0.1"]).collect().succeeded


def test_a_user_mode_device_is_promoted_with_the_enable_password(
    fake_netmiko: FakeNetmiko,
) -> None:
    fake_netmiko.devices["10.0.0.1"] = FakeConnection(
        host="10.0.0.1", outputs=dict(fake_netmiko.default_outputs), privileged=False
    )

    assert _source(["10.0.0.1"]).collect().succeeded
    assert fake_netmiko.devices["10.0.0.1"].privileged


def test_discover_yields_only_the_captures_that_succeeded(fake_netmiko: FakeNetmiko) -> None:
    """The `DataSource` contract: failures are in the result, not in the stream."""
    fake_netmiko.connect_errors["10.0.0.1"] = TimeoutError("unreachable")

    assert list(_source(["10.0.0.1", "10.0.0.2"]).discover()) == []


@pytest.mark.parametrize(
    "command", sorted(set(_IOS_COMMANDS) | set(_COMMANDS_BY_PLATFORM["cisco_nxos"]))
)
def test_every_collected_command_passes_the_read_only_guard(command: str) -> None:
    _assert_read_only(command)


@pytest.mark.parametrize(
    "command",
    ["clear counters", "configure terminal", "reload", "write memory", "no shutdown", ""],
)
def test_the_read_only_guard_refuses_anything_that_is_not_a_show(command: str) -> None:
    """Collection never changes a device; this is enforced, not just documented."""
    with pytest.raises(ValueError, match="non-show command"):
        _assert_read_only(command)
