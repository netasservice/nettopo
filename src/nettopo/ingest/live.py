"""Collects captures from live devices over SSH (PROJECT_SPEC.md section 4).

The `DataSource` implementation `ingest/base.py` was designed for. It runs the same
`show` commands a hand-saved capture would contain and renders them in the same
prompt-prefixed format, so everything downstream -- parsing, the model, the views -- is
unaware that the text came off a device rather than off disk.

This is the only module in nettopo that imports netmiko, and the only one that opens a
socket. `cli.py` imports it lazily so that `import nettopo.cli` neither reaches netmiko
nor requires it to be installed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from netmiko import ConnectHandler

from nettopo.ingest.base import Capture, DataSource
from nettopo.ingest.credentials import Credentials
from nettopo.parsing.version import detect_os, parse_version
from nettopo.utils.command_sections import format_command_section

logger = logging.getLogger("nettopo")

# Every command sent to a device is matched against this first. Collection is read-only,
# and that is enforced at the point of sending rather than only asserted in the docs: an
# edit to the tables below that slipped in `clear counters` fails loudly and locally
# instead of quietly resetting a production counter.
_SHOW_ONLY = re.compile(r"^show\s+\S", re.IGNORECASE)

# Cisco prefixes every rejection with `%` -- `% Invalid input detected`, `% BGP not
# active`. Ordinary `show` output does not start with one, so this is a reliable test for
# "the device understood the question and declined to answer it".
_REJECTED = re.compile(r"^\s*%")

# The command set each view needs (PROJECT_SPEC.md section 4), keyed by the
# ntc-templates platform so that IOS-XE needs no entry of its own.
_IOS_COMMANDS: tuple[str, ...] = (
    "show version",
    "show cdp neighbors detail",
    "show lldp neighbors detail",
    "show ip interface brief",
    "show interfaces",
    "show vlan brief",
    "show spanning-tree",
    "show standby brief",
    "show etherchannel summary",
    "show ip bgp summary",
    "show interfaces status",
    "show interfaces description",
    "show mac address-table",
    "show ip arp",
)

# NX-OS differs in exactly three places, all verified rather than assumed:
#   - the bundle table is `show port-channel summary`. `parsing/etherchannel.py` picks its
#     template from the prompt line rather than the platform, so this needs no plumbing.
#   - `show standby brief` is dropped. NX-OS spells it `show hsrp brief`, ntc-templates
#     ships no template for it and `parsing/hsrp.py` has no parser, so collecting it would
#     only add a section nothing reads.
#   - the interface status and description tables are spelled `show interface ...`, the
#     NX-OS form and the only one its ntc-templates index entries match.
# `show spanning-tree` stays even though ntc-templates has no NX-OS template for it:
# `parsing/spanning_tree.py` parses that command with its own regexes.
_NXOS_COMMANDS: tuple[str, ...] = (
    "show version",
    "show cdp neighbors detail",
    "show lldp neighbors detail",
    "show ip interface brief",
    "show interfaces",
    "show vlan brief",
    "show spanning-tree",
    "show port-channel summary",
    "show ip bgp summary",
    "show interface status",
    "show interface description",
    "show mac address-table",
    "show ip arp",
)

_VERSION_COMMAND = "show version"

_COMMANDS_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    "cisco_ios": _IOS_COMMANDS,
    "cisco_nxos": _NXOS_COMMANDS,
}

# `detect_os()` -> ntc-templates platform. IOS-XE parses under `cisco_ios` because
# ntc-templates ships no `cisco_xe` platform (verified against 9.2.0): that string is a
# netmiko device_type only, and must never reach `Capture.platform_hint`.
_PLATFORM_BY_OS: dict[str, str] = {
    "ios": "cisco_ios",
    "ios-xe": "cisco_ios",
    "nxos": "cisco_nxos",
}

# Every device is connected to as `cisco_ios`. Its session setup (`terminal length 0`) is
# accepted by NX-OS too, so one driver covers the fleet without a second, autodetecting
# connection -- which would mean a second authentication against every device.
_DEVICE_TYPE = "cisco_ios"

# A bare hostname, so a prompt in any other mode (`sw1(config)#`) is rejected rather than
# taken as a device name.
_PROMPT_HOSTNAME = re.compile(r"^(?P<hostname>[\w.\-]+)[#>]\s*$")


class _DeviceConnection(Protocol):
    """The slice of netmiko's connection this module uses.

    Declared rather than typed against `BaseConnection` for two reasons: it documents
    exactly how small the dependency on netmiko is, and netmiko types `send_command` as
    returning `str | list | dict` because of options like `use_textfsm` that nettopo
    never passes. One `cast` at the connect call is honest about that; six scattered
    `type: ignore` comments would not be.
    """

    def check_enable_mode(self) -> bool: ...
    def enable(self) -> None: ...
    def find_prompt(self) -> str: ...
    def send_command(self, command_string: str, *, read_timeout: float) -> str: ...
    def disconnect(self) -> None: ...


class EnableRequired(Exception):
    """The device is in user mode and no enable password was given.

    The one failure a run tolerates: it says nothing about the network's health, only
    about what the operator typed at the prompt.
    """


class Outcome(StrEnum):
    OK = "ok"
    NO_ENABLE = "no-enable"  # skipped; the run continued
    FAILED = "failed"  # this device ended the run
    SKIPPED = "skipped"  # never contacted, because an earlier device ended the run


@dataclass(frozen=True)
class DeviceOutcome:
    """What happened to one inventory entry."""

    target: str
    outcome: Outcome
    capture: Capture | None = None
    platform: str = ""
    commands: int = 0
    detail: str = ""

    @property
    def is_ok(self) -> bool:
        return self.outcome is Outcome.OK


@dataclass(frozen=True)
class CollectionResult:
    outcomes: tuple[DeviceOutcome, ...]  # one per inventory entry, in inventory order

    @property
    def collected(self) -> tuple[DeviceOutcome, ...]:
        return tuple(one for one in self.outcomes if one.outcome is Outcome.OK)

    @property
    def succeeded(self) -> bool:
        return all(one.is_ok for one in self.outcomes)

    def counts_by_status(self) -> dict[str, int]:
        """How many devices ended in each status, keyed by its report name.

        Lets `cli.py` summarize a run without importing `Outcome`, which it could only do
        by importing this module -- and therefore netmiko -- at module scope.
        """
        counts = dict.fromkeys((outcome.value for outcome in Outcome), 0)
        for one in self.outcomes:
            counts[one.outcome.value] += 1
        return counts


class LiveDataSource(DataSource):
    """Collects every device in `targets`, one at a time, stopping at the first error.

    Serial on purpose. Speed is not the goal of this first phase, containment is: whatever
    goes wrong should affect one switch and stop there rather than cascade across a fleet.

    Serial execution is also what makes the account-lockout protection real. A mistyped
    password must never be presented to two hundred devices -- that is a lockout, and on a
    TACACS-backed network it locks the operator out of everything, not just nettopo.
    Because only one device is ever in flight and the run stops at the first error, a wrong
    credential costs exactly one failed authentication attempt. No concurrent design can
    promise that: with five workers, five devices present the bad password before the first
    rejection comes back.
    """

    def __init__(
        self,
        targets: Sequence[str],
        credentials: Credentials,
        *,
        port: int = 22,
        timeout: float = 30.0,
        command_timeout: float = 120.0,
        strict_host_keys: bool = True,
    ) -> None:
        self.targets = tuple(targets)
        self.credentials = credentials
        self.port = port
        self.timeout = timeout
        self.command_timeout = command_timeout
        self.strict_host_keys = strict_host_keys

    def discover(self) -> Iterator[Capture]:
        """The `DataSource` contract: the captures that were collected, successes only."""
        for outcome in self.collect().collected:
            if outcome.capture is not None:
                yield outcome.capture

    def collect(
        self, on_capture: Callable[[str, Capture], object] | None = None
    ) -> CollectionResult:
        """Collect every target in order, calling `on_capture` as each one finishes.

        The callback exists so captures reach disk while the run is still going: the run
        stops at the first error, and one that dies at device 40 of 50 must keep the 39 it
        already paid for. Whatever it returns is ignored; only the write matters here.
        """
        outcomes: list[DeviceOutcome] = []
        stopped = False

        for position, target in enumerate(self.targets, start=1):
            if stopped:
                outcomes.append(
                    DeviceOutcome(
                        target=target,
                        outcome=Outcome.SKIPPED,
                        detail="not attempted; an earlier device ended the run",
                    )
                )
                continue

            outcome = self._collect_target(target, position)
            outcomes.append(outcome)
            if outcome.is_ok and outcome.capture is not None and on_capture:
                on_capture(target, outcome.capture)
            stopped = outcome.outcome is Outcome.FAILED

        return CollectionResult(outcomes=tuple(outcomes))

    def _collect_target(self, target: str, position: int) -> DeviceOutcome:
        """Collect one device, classifying whatever goes wrong.

        `EnableRequired` is caught before the broad handler on purpose: the wrong ordering
        would turn the one tolerated failure into a fatal one and end every run against a
        network whose devices ask for enable.
        """
        try:
            capture, section_count = self._collect_one(target)
        except EnableRequired:
            logger.warning(
                "[%d/%d] %s requires enable and no enable password was given; skipping.",
                position,
                len(self.targets),
                target,
            )
            return DeviceOutcome(
                target=target,
                outcome=Outcome.NO_ENABLE,
                detail="device requires enable; no enable password was given",
            )
        except Exception as exc:  # noqa: BLE001
            # Deliberately broad: an SSH stack raises out of netmiko, paramiko,
            # cryptography and socket, and enumerating those buys nothing when every one
            # of them ends the run anyway. The type name is kept so the report says which.
            logger.error(
                "[%d/%d] %s: %s: %s. Stopping; no further device will be contacted.",
                position,
                len(self.targets),
                target,
                type(exc).__name__,
                exc,
            )
            return DeviceOutcome(
                target=target, outcome=Outcome.FAILED, detail=f"{type(exc).__name__}: {exc}"
            )

        platform = capture.platform_hint or ""
        logger.info(
            "[%d/%d] %s -> %s (%s, %d sections)",
            position,
            len(self.targets),
            target,
            capture.device_hint,
            platform,
            section_count,
        )
        return DeviceOutcome(
            target=target,
            outcome=Outcome.OK,
            capture=capture,
            platform=platform,
            commands=section_count,
        )

    def _collect_one(self, target: str) -> tuple[Capture, int]:
        connection = cast(_DeviceConnection, ConnectHandler(**self._connection_params(target)))
        try:
            self._ensure_privileged(connection, target)
            prompt_hostname = self._prompt_hostname(connection, fallback=target)

            # `show version` first: its output is the only signal available for choosing
            # which platform's command set the rest of the session should use.
            version_output = self._send(connection, target, _VERSION_COMMAND)
            platform = _PLATFORM_BY_OS.get(detect_os(version_output or ""), "cisco_ios")

            sections = []
            if version_output is not None:
                sections.append(
                    format_command_section(prompt_hostname, _VERSION_COMMAND, version_output)
                )
            for command in _COMMANDS_BY_PLATFORM[platform]:
                if command == _VERSION_COMMAND:
                    continue
                output = self._send(connection, target, command)
                if output is not None:
                    sections.append(format_command_section(prompt_hostname, command, output))
        finally:
            connection.disconnect()

        raw_text = "".join(sections)
        capture = Capture(
            device_hint=_device_identity(raw_text, platform, fallback=prompt_hostname),
            raw_text=raw_text,
            # This is what `Capture.platform_hint` exists for: a live source knows each
            # device's platform, where a directory of files can only guess at one default.
            platform_hint=platform,
        )
        return capture, len(sections)

    def _connection_params(self, target: str) -> dict[str, object]:
        return {
            "device_type": _DEVICE_TYPE,
            "host": target,
            "port": self.port,
            "username": self.credentials.username,
            "password": self.credentials.password,
            "secret": self.credentials.enable_password or "",
            "conn_timeout": self.timeout,
            "auth_timeout": self.timeout,
            "banner_timeout": self.timeout,
            # netmiko 4's default shortens its inter-command delays; real chassis under
            # load need the conservative path.
            "fast_cli": False,
            # netmiko's own default silently adds an unknown host key. A tool whose
            # purpose is typing device credentials into a socket must not do that: a
            # man in the middle at first contact harvests a login and an enable secret
            # in one exchange.
            "ssh_strict": self.strict_host_keys,
            "system_host_keys": self.strict_host_keys,
            # `session_log` is deliberately never set: it writes the whole session,
            # authentication included, to a plaintext file.
        }

    def _ensure_privileged(self, connection: _DeviceConnection, target: str) -> None:
        """Enter enable mode if the device is not already privileged.

        Handles both kinds of device in one run: one that logs straight into privileged
        mode is left alone, one that asks for enable gets it, and one that asks for it
        when no enable password was given is skipped rather than collected half-blind.
        """
        if connection.check_enable_mode():
            return
        if not self.credentials.enable_password:
            raise EnableRequired(target)
        connection.enable()

    @staticmethod
    def _prompt_hostname(connection: _DeviceConnection, *, fallback: str) -> str:
        """The device's own name, as its prompt spells it.

        Used verbatim in the synthesized prompt lines, so a capture records what the device
        actually showed, and as the fallback identity when `show version` yields no
        hostname. A prompt in any other mode (`sw1(config)#`) is not a device name, so
        anything that is not a bare hostname falls back to the inventory entry.
        """
        raw = connection.find_prompt().strip()
        match = _PROMPT_HOSTNAME.match(raw)
        if match is None:
            logger.debug("%s: unexpected prompt %r; using the inventory name.", fallback, raw)
            return fallback
        return match.group("hostname")

    def _send(self, connection: _DeviceConnection, target: str, command: str) -> str | None:
        """Run one command, or return None if the device had nothing to say for it.

        A command the platform rejects is not an error: a switch with no BGP configured,
        or one with CDP disabled, is a healthy device answering correctly, and every
        parser already treats a missing command as ordinary input. Ending the run there
        would mean no capture set ever completes.
        """
        _assert_read_only(command)
        output = connection.send_command(command, read_timeout=self.command_timeout)

        if _REJECTED.match(output):
            logger.debug("%s: '%s' was rejected by the device; skipping it.", target, command)
            return None
        if not output.strip():
            logger.debug("%s: '%s' returned nothing; skipping it.", target, command)
            return None
        return output


def _assert_read_only(command: str) -> None:
    """Refuse to send anything but a `show` command.

    Collection never changes a device. The command tables are literals in this module, so
    this can only fire on a future edit to them -- which is the point.
    """
    if not _SHOW_ONLY.match(command):
        raise ValueError(f"refusing to send a non-show command: {command!r}")


def _device_identity(raw_text: str, platform: str, *, fallback: str) -> str:
    """The name this device will be known by, decided the same way the model decides it.

    `model_builder` identifies a source device by its parsed `show version` hostname,
    falling back to the capture's `device_hint`. Deriving the same answer here -- rather
    than using the prompt and hoping the two agree -- buys two things that matter:

    - the capture file is named after the node the diagram will actually draw, so
      "why does my diagram say `sw1`?" is answerable by looking at the directory; and
    - duplicate detection keys on the name that causes the merge. The two normally agree,
      since a prompt and `show version` both come from the configured hostname, but when
      they diverge it is the parsed one the model acts on -- so warning on the prompt
      would quietly miss real collisions.
    """
    version_info = parse_version(raw_text, platform=platform)
    hostname = version_info.hostname if version_info else None
    return hostname or fallback
