# PROJECT_SPEC.md — Network Diagram CLI

> **Purpose of this document.** This is the master specification used to bootstrap the
> repository and drive implementation. It defines scope, architecture, the data model,
> the CLI surface, the phased delivery plan, testing, and CI/CD. It is meant to be read
> together with **`CLAUDE.md`**, which governs all engineering conventions (documentation
> maintenance, git workflow, English-only rule, SOLID/KISS/DRY/YAGNI, and the mandatory
> OWASP security review). **When `CLAUDE.md` and this spec disagree on conventions,
> `CLAUDE.md` wins.**
>
> Per the documentation-maintenance rule in `CLAUDE.md`, this file must be kept in sync
> with the code: any change to scope, architecture, model, CLI, or dependencies updates
> this spec in the same commit as `README.md`, `CHANGELOG.md`, and `docs/architecture.md`.

---

## 1. What this tool is

A Python CLI that reads **saved `show` command outputs** from Cisco devices (files only —
no live device connections in v1) and generates **network diagrams** in **draw.io format
with Cisco icons**, plus the **intermediate data as CSV tables**.

The tool produces four diagram views from the same parsed data model:

1. **L2** — physical/link-layer topology from CDP/LLDP, with interface labels and MLAG.
2. **STP** — per-VLAN Rapid-PVST spanning-tree state (root, bridge IDs, priorities, port roles/states).
3. **HSRP** — first-hop redundancy per SVI (virtual IP, priority, active/standby/listen).
4. **BGP** — BGP neighbor (session) graph.

### Working package name

Working title: **`nettopo`** (CLI command: `nettopo`). This is a placeholder —
**verify availability on PyPI and GitHub before the first publish and rename if taken.**
The name appears throughout this document; a rename is a single coordinated change across
`pyproject.toml`, the package directory, the console-script entry point, and the docs.

**Verified (Phase 3, 2026-08-07):** `nettopo` is unclaimed on PyPI (`pypi.org/pypi/nettopo/json`
returns 404). No rename needed for the v0.1.0 release.

---

## 2. Scope

### In scope (v1)

- Read-only ingestion of `show` command outputs from a local directory.
- **Live collection over SSH** (`nettopo collect`): read-only, serial, credentials prompted
  for at run time and never stored, one capture file per device plus a CSV report of the
  run. Part of the standard install, so `collect` works out of the box.
- TextFSM parsing via **`ntc-templates`** (no ad-hoc regex parsers for production paths).
- A normalized in-memory **data model** (dataclasses).
- Four views: L2, STP, HSRP, BGP.
- L2 endpoint filtering: full diagram (all neighbors) **and** network-only (routers/switches).
- L2 interface labels and MLAG rendering **imitating current N2G behavior** (port-channel grouping).
- STP and HSRP: **per-VLAN diagrams** and **two grouping modes** (see §6).
- draw.io output with **Cisco icons** per device role.
- **CSV export** of every intermediate table (neighbors, VLANs, STP, HSRP, BGP).
- A **per-port endpoint table** (`ports.csv`): each port's state, description, learned
  MACs, CDP/LLDP neighbor, endpoint VLAN and IP (joined from the ARP tables of every
  captured device).
- Bulk generation into a structured `output/` tree (e.g. `output/stp/`).

### Explicitly out of scope (v1) — YAGNI

- Storing credentials in any form: no vault, no credential file, no cache, and no
  password accepted on the command line. `collect` prompts and forgets. This is what keeps
  the inventory a file with nothing sensitive in it.
- Ansible-style inventories: groups, per-device variables, `group_vars/`, host ranges,
  dynamic inventory scripts, jump hosts. The inventory is a flat list of hostnames or IPs.
- Concurrent collection. `collect` is serial on purpose (see §11): it bounds a mistyped
  password to one failed authentication attempt, which no concurrent design can promise.
- scrapli, and any transport other than SSH.
- Nexus vPC domain/peer-link modeling. MLAG is **only** port-channel member grouping,
  matching what N2G does today.
- BGP route tables, policies, communities, route-reflector modeling. v1 is the
  **session graph only**.
- ARP tables outside the default VRF (`show ip arp vrf ...`). The NX-OS `vrf all` form
  prints one header per context, which the shipped template rejects.
- BGP `peer_device` resolution (peer IP → hostname). Left as `None` in v1, and `bgp.csv`
  reports it empty on every row. The `bgp` **view** does match a peer address against the
  interface addresses the model already holds, so that one session between two captured
  routers is drawn as one link rather than four disconnected boxes — that is a property of
  the drawing, not of the model, and it writes nothing back (see §7).
- Any Lucidchart support. draw.io is the one tool these diagrams target — see
  `docs/architecture.md` for what a Lucid import does to a generated file.
- Any telemetry or network egress. Every command except `collect` must make **zero**
  network connections, and `collect` connects only to the devices its inventory names
  (see §11).

---

## 3. Repository layout

```
nettopo/
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── CLAUDE.md
├── PROJECT_SPEC.md            # this file
├── docs/
│   └── architecture.md        # components, call flows, design decisions
├── examples/
│   ├── campus/                # runnable six-switch capture set behind the README diagrams
│   │   └── diagrams/          # its generated .drawio files and the PNGs the README embeds
│   └── hsrp-quad/             # four routers in one HSRP group: active, standby, two listening
│       └── diagrams/
├── src/
│   └── nettopo/
│       ├── __init__.py
│       ├── cli.py             # CLI entry point (argument parsing, orchestration only)
│       ├── ingest/            # data sources: a capture directory, or live devices
│       │   ├── __init__.py
│       │   ├── base.py        # DataSource interface
│       │   ├── files.py       # FileDataSource + CaptureWriter: the capture dir, both ways
│       │   ├── inventory.py   # the device list `collect` works from (one device per line)
│       │   ├── credentials.py # prompts for one run's credentials; stores nothing
│       │   ├── live.py        # LiveDataSource: SSH collection. The only netmiko importer
│       │   └── model_builder.py  # ingestion -> parsers -> NetworkModel population
│       ├── parsing/           # one parser per show command (TextFSM/ntc-templates)
│       │   ├── __init__.py
│       │   ├── _textfsm.py    # typed wrapper over ntc_templates; the one untyped boundary
│       │   ├── cdp.py
│       │   ├── lldp.py
│       │   ├── spanning_tree.py
│       │   ├── hsrp.py
│       │   ├── interfaces.py  # show ip interface brief / interfaces status / description / show interfaces
│       │   ├── etherchannel.py  # show etherchannel summary / show port-channel summary
│       │   ├── vlan.py
│       │   ├── mac_table.py   # show mac address-table (endpoint entries only)
│       │   ├── arp.py         # show ip arp (default VRF)
│       │   ├── bgp.py
│       │   └── version.py     # platform/model/os detection
│       ├── model/             # the normalized data model + grouping logic
│       │   ├── __init__.py
│       │   ├── entities.py    # dataclasses + enums
│       │   ├── grouping.py    # STP fingerprint function and group-mode logic
│       │   └── platforms.py   # platform string -> DeviceRole
│       ├── views/             # one module per diagram view; consumes the model
│       │   ├── __init__.py
│       │   ├── diagram.py     # Diagram/DiagramNode/DiagramLink every view returns
│       │   ├── l2.py
│       │   ├── stp.py
│       │   ├── hsrp.py
│       │   └── bgp.py
│       ├── render/            # draw.io emission via N2G, styling, icons
│       │   ├── __init__.py
│       │   ├── drawio.py      # thin wrapper over N2G drawio_diagram
│       │   ├── icons.py       # DeviceRole -> Cisco icon, palette, link styles
│       │   └── legend.py      # the diagram's key, drawn from the same styles
│       ├── export/            # CSV writers
│       │   ├── __init__.py
│       │   ├── csv_export.py
│       │   └── port_table.py  # the MAC/ARP/neighbor join behind ports.csv (rows, no I/O)
│       └── utils/
│           ├── __init__.py
│           ├── command_sections.py  # the capture format: split it, and write it
│           ├── hostnames.py   # THE device-name normalizer (central service)
│           ├── interfaces.py  # THE interface-name normalizer (central service)
│           ├── mac.py         # THE MAC-address normalizer (central service)
│           └── paths.py       # capture-dir default, path resolution, filename sanitization
└── tests/
    ├── fixtures/              # anonymized real captures used as parser inputs
    ├── test_examples_*.py     # keeps each examples/ set matching what the README shows
    ├── test_interfaces.py
    ├── test_parsing_*.py
    ├── test_grouping.py
    ├── test_views_*.py
    └── test_no_network.py
```

**Layering rule (Dependency Inversion):** dependencies point inward.
`parsing` → `model`; `views` → `model`; `render`/`export` → `views`+`model`;
`cli` orchestrates. `model` depends on nothing but `utils`. No module in `model`
imports `render`, `views`, or N2G.

---

## 4. Ingestion

Input is a **directory**. Each file is one device's captured output containing several
`show` commands concatenated, each preceded by its device prompt line
(`hostname#show ...`). The prompt line is how a device is identified as a **source device**
(we have its own capture, not just a neighbor mention).

- **Encoding:** read files with `utf-8-sig` so a UTF-8 **BOM** is stripped transparently.
  (A BOM on the first line silently breaks parsing otherwise.)
- **Platform detection:** determine OS/platform per device by parsing `show version`
  when present; otherwise fall back to a CLI `--platform` default (`cisco_ios`).
  ntc-templates needs the platform to select the right template.
- **Interface:** `ingest/base.py` defines `DataSource` with a method that yields
  `(device_hint, raw_text, platform_hint)`. `FileDataSource` implements it over a directory
  and `LiveDataSource` over SSH, so nothing downstream of ingestion knows or cares which one
  produced a capture.

### Live collection (`ingest/live.py`)

Collects the same command set over SSH and renders it in the same on-disk format, so the
rest of the pipeline is unchanged.

- **Format:** netmiko's `send_command` strips the echoed command and the trailing prompt,
  so the collector synthesizes the `hostname#command` line itself via
  `utils/command_sections.format_command_section()` — the inverse of the splitter, kept in
  the same module so the two cannot drift.
- **Identity:** decided the same way `model_builder` decides it — the parsed `show version`
  hostname, falling back to the device's prompt (`find_prompt()`), falling back to the
  inventory entry. Deriving it identically rather than in parallel matters twice over: the
  capture file is named after the node the diagram will draw, and duplicate detection keys
  on the name that actually causes a merge. A prompt in any other mode (`sw1(config)#`) is
  not a device name and is skipped in that chain, though the prompt lines inside the capture
  still record verbatim what the device showed.
- **Platform:** `show version` is sent first and `parsing.version.detect_os()` chooses the
  rest of the command set, which reaches the model as `Capture.platform_hint` — the field's
  reason for existing. IOS-XE maps to `cisco_ios`: ntc-templates ships no `cisco_xe`
  platform, so that netmiko-only string must never reach a template lookup.
- **Command set:** NX-OS differs in exactly three places — `show port-channel summary`
  replaces the etherchannel form, `show standby brief` is dropped because NX-OS spells
  it `show hsrp brief`, for which there is no template and no parser, and the interface
  status/description tables are sent as `show interface ...`, the NX-OS spelling and the
  only one its ntc-templates entries match.
- **Inventory:** a flat list of hostnames or IPs, one device per line in a plain text
  file, with `#` comments. No YAML, no groups, no variables, no credentials — see §2.
- **Failure policy and credentials:** see §11.

**Command set consumed (v1):**

| View | Commands |
|------|----------|
| identity | `show version` |
| L2 | `show cdp neighbors detail`, `show lldp neighbors detail`, `show etherchannel summary` (IOS/IOS-XE) or `show port-channel summary` (NX-OS) |
| L3/VLAN | `show ip interface brief`, `show interfaces`, `show vlan brief` |
| Ports | `show interfaces status` and `show interfaces description` (NX-OS: `show interface ...`) for VLAN/trunk, speed, duplex and the full description; `show mac address-table` for the endpoints each port learned; `show ip arp` for their IPs. The ARP table usually lives on the gateway, not the access switch, so the gateway's capture is what fills the IP column. Default VRF only |
| STP | `show spanning-tree` (per-VLAN Rapid-PVST), **plus** the L2 discovery commands: spanning-tree output carries a device's own bridge and port states but never names the device on the other end of a port, so the STP view takes its links from the CDP/LLDP topology. `show etherchannel summary` is required whenever links are bundled — spanning-tree names the port-channel (`Po1`) while CDP/LLDP name its members (`Gi1/0/1`), and only the bundle table joins the two. `show lldp neighbors detail` additionally lets an out-of-capture root bridge be identified, since its chassis address is the only thing that can be matched against the reported root address |
| HSRP | `show standby brief` — its shipped template captures every column the model needs (interface, group, priority, preempt, state, virtual IP), so the per-group detail form `show standby` is not read |
| BGP | `show ip bgp summary` |

---

## 5. Normalization (central services)

Two identities have to survive being spelled differently by different commands and
different devices: the **interface name** and the **device name**. Each gets exactly one
normalizer in `utils/`, and nothing else may reimplement either.

### 5.1 Interface names — `utils/interfaces.py`

`utils/interfaces.py` is the single source of truth for interface-name normalization and
**every parser must route names through it**. This prevents silent correlation failures
where the same physical port appears as `Gi1/0/1` in one command and
`GigabitEthernet1/0/1` in another and therefore never matches.

**Rule:** normalize to the **short canonical form**.

| Long form | Canonical |
|-----------|-----------|
| GigabitEthernet | `Gi` |
| TenGigabitEthernet | `Te` |
| TwentyFiveGigE | `Twe` |
| FortyGigabitEthernet | `Fo` |
| HundredGigE | `Hu` |
| FastEthernet | `Fa` |
| Ethernet | `Eth` |
| Port-channel | `Po` |
| Vlan | `Vl` |
| Loopback | `Lo` |
| Tunnel | `Tu` |
| Management | `Mgmt` |

The normalizer must be pure, deterministic, idempotent (`normalize(normalize(x)) == normalize(x)`),
and covered by exhaustive unit tests including already-abbreviated inputs and mixed casing.

`looks_like_interface()` lives here too: it answers whether a string is a recognized
interface type followed by a number, which is how `parsing/lldp.py` decides between the
neighbor's "Port Description" and its "Port id" (see §5.2 for why that matters).

### 5.2 Device names — `utils/hostnames.py`

CDP and LLDP rarely agree on a neighbor's name. The same device is reported as
`nxos-core1`, as `nxos-core1(FDO21120U5D)` (NX-OS appends the chassis serial to the name
it advertises), and as `nxos-core1.example.com`. Every spelling that is not correlated
becomes its own `Device`, so one physical switch is drawn as several nodes joined by
parallel links. `utils/hostnames.py` is the single source of truth for that correlation;
`ingest/model_builder.py` routes every CDP/LLDP-reported name through it.

**Rules**, applied to all spellings sharing one short label (the text before the first
dot, case-folded, serial suffix removed):

1. a hostname belonging to a **source device** wins — the rest of the model is already
   keyed by what that device's own `show version` reported;
2. otherwise, if a bare (domainless) spelling or exactly one domain was observed, the
   spellings are one device, named by the **shortest** observed spelling;
3. otherwise (two or more domains, no bare spelling, no source device) they stay
   separate: `sw1.site-a.com` and `sw1.site-b.com` are presumed to be two devices.

The resolver never invents a name — a canonical name is always an observed spelling
minus its serial suffix — so a device only ever seen by its FQDN keeps that FQDN. The
serial the suffix carried is preserved as `Device.serial` rather than discarded.

---

## 6. Data model

Defined in `model/entities.py`. Enums first (no bare strings for controlled vocabularies).

`Device.role` is settled in two passes, both in `ingest/model_builder.py`. CDP/LLDP
capabilities give a first answer, but they cannot tell a router from a multilayer switch —
a Catalyst 9500 advertises `Router Switch`, exactly like a bridging ISR — and a device's own
CDP/LLDP output never reports its own capabilities, so a source device depends entirely on
being described by a neighbor. `model/platforms.py` then overrides that guess by matching
the reported chassis (`cisco C9500-16X`, `N9K-C93180YC-EX`, `cisco ISR4331/K9`,
`VMware ESX`) against a table of product families. Being about the device rather than about
who saw it, that is the more authoritative of the two, and it is what makes the roles
`render/icons.py` draws — `L3_SWITCH`, `FIREWALL`, `AP`, `SERVER` — reachable at all.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class DeviceRole(Enum):
    ROUTER = "router"; L3_SWITCH = "l3_switch"; SWITCH = "switch"
    FIREWALL = "firewall"; AP = "ap"; PHONE = "phone"
    SERVER = "server"; HOST = "host"; UNKNOWN = "unknown"


class InterfaceType(Enum):
    PHYSICAL = "physical"; SVI = "svi"; PORT_CHANNEL = "port_channel"
    LOOPBACK = "loopback"; SUBINTERFACE = "subinterface"
    MGMT = "mgmt"; TUNNEL = "tunnel"; UNKNOWN = "unknown"


class StpRole(Enum):
    ROOT = "root"; DESIGNATED = "designated"
    ALTERNATE = "alternate"; BACKUP = "backup"; DISABLED = "disabled"


class StpState(Enum):
    FWD = "forwarding"; BLK = "blocking"; LRN = "learning"
    LIS = "listening"; DIS = "disabled"


class HsrpRole(Enum):
    ACTIVE = "active"; STANDBY = "standby"; LISTEN = "listen"
    INIT = "init"; SPEAK = "speak"; LEARN = "learn"


class BgpType(Enum):
    IBGP = "ibgp"; EBGP = "ebgp"


@dataclass
class Interface:
    name: str                              # normalized: "Gi1/0/1", "Vl10", "Po1"
    type: InterfaceType = InterfaceType.UNKNOWN
    description: str | None = None
    admin_up: bool | None = None
    oper_up: bool | None = None
    ip_address: str | None = None
    prefix_len: int | None = None
    vlan: int | None = None                # access VLAN, or the SVI number
    mode: str | None = None                # "access" | "trunk"
    speed: str | None = None               # as `show interfaces status` prints it: "a-1000"
    duplex: str | None = None              # "a-full", "full", "auto"
    trunk_vlans: list[int] = field(default_factory=list)
    po_id: int | None = None               # if this port is a member of a port-channel
    po_members: list[str] = field(default_factory=list)  # if this IS the port-channel (MLAG/LAG)


@dataclass
class Device:
    hostname: str                          # canonical correlation key
    is_source: bool = False                # we have this device's own capture
    platform: str | None = None            # raw: "cisco C9300-48P"; own `show version`, or CDP/LLDP
    model: str | None = None               # parsed: "C9300-48P"
    os: str | None = None                  # "ios" | "ios-xe" | "nxos"
    serial: str | None = None              # own `show version`, or the NX-OS name suffix
    role: DeviceRole = DeviceRole.UNKNOWN  # `platform` if it names a family, else capabilities
    mgmt_ip: str | None = None             # as a neighbor advertises it over CDP/LLDP
    asn: int | None = None                 # for BGP
    router_id: str | None = None           # BGP router ID, from its own `show ip bgp summary`
    interfaces: dict[str, Interface] = field(default_factory=dict)  # keyed by normalized name


@dataclass
class Link:
    local_device: str
    local_interface: str
    remote_device: str
    remote_interface: str
    discovery: str = "cdp"                  # "cdp" | "lldp"
    remote_platform: str | None = None     # CDP "Platform:", or the LLDP System Description
    remote_mgmt_ip: str | None = None      # CDP "Management address(es)" / LLDP management TLV
    remote_capabilities: list[str] = field(default_factory=list)  # CDP ["Router","Switch"] / LLDP ["B","R"]

    def key(self) -> frozenset:
        """Direction-independent identity, used to de-duplicate A->B and B->A."""
        return frozenset({
            (self.local_device, self.local_interface),
            (self.remote_device, self.remote_interface),
        })


@dataclass
class StpBridge:
    device: str
    vlan: int
    base_priority: int                     # configured base, e.g. 24576
    sys_id_ext: int                        # normally equals the VLAN id
    mac: str
    is_root: bool = False

    @property
    def effective_priority(self) -> int:
        return self.base_priority + self.sys_id_ext


@dataclass
class StpPort:
    device: str
    vlan: int
    interface: str
    role: StpRole
    state: StpState
    cost: int | None = None


@dataclass
class StpVlan:
    vlan: int
    root_device: str | None = None
    bridges: dict[str, StpBridge] = field(default_factory=dict)
    ports: dict[tuple[str, str], StpPort] = field(default_factory=dict)  # (device, interface)


@dataclass
class HsrpMember:
    device: str
    interface: str                         # the SVI: "Vl10"
    group: int
    priority: int
    role: HsrpRole
    preempt: bool | None = None


@dataclass
class HsrpGroup:
    vlan: int
    group: int
    virtual_ip: str | None = None
    members: dict[str, HsrpMember] = field(default_factory=dict)  # keyed by device


@dataclass
class BgpPeer:
    local_device: str
    local_asn: int
    peer_ip: str
    peer_asn: int
    state: str                             # "Established", "Idle", "Active", ...
    type: BgpType
    peer_device: str | None = None         # v1: always None
    vrf: str = "default"


@dataclass
class Vlan:
    vlan_id: int
    name: str | None = None
    status: str | None = None


@dataclass
class MacEntry:
    device: str
    mac: str                               # normalized: "0050.56a1.0001"
    vlan: int
    interface: str                         # a real port; CPU/Router/sup-eth/SVI entries are dropped
    entry_type: str                        # "dynamic" | "static"


@dataclass
class ArpEntry:
    device: str
    ip: str
    mac: str                               # normalized; Incomplete entries are dropped
    interface: str | None


@dataclass
class NetworkModel:
    devices: dict[str, Device] = field(default_factory=dict)          # keyed by hostname
    links: list[Link] = field(default_factory=list)
    vlans: dict[int, Vlan] = field(default_factory=dict)
    stp: dict[int, StpVlan] = field(default_factory=dict)             # keyed by VLAN id
    hsrp: dict[tuple[int, int], HsrpGroup] = field(default_factory=dict)  # (vlan, group)
    bgp: list[BgpPeer] = field(default_factory=list)
    mac_entries: list[MacEntry] = field(default_factory=list)
    arp_entries: list[ArpEntry] = field(default_factory=list)
```

A port seen only in a MAC table (no interface command covered it) is registered as an
`Interface` anyway, the same on-demand rule port-channel members follow, so the port
table still owes it a row.

`NetworkModel.hsrp` is keyed by `(vlan, group)`, so a group configured on a routed port or
a subinterface has no key to live under: `parsing/hsrp.py` skips it with a warning. That is
a deliberate v1 limit matching the HSRP view's scope (§7, "switches and their SVIs"), not a
parsing gap.

### Grouping (STP) — `model/grouping.py`

Three generation modes, exposed on the CLI as `--group-mode`:

| Mode | Meaning | Fingerprint contents |
|------|---------|----------------------|
| `per-vlan` (default) | One diagram per VLAN. No grouping. | n/a |
| `strict` | Group VLANs whose diagrams would be **identical**, including configured priorities. | Root + sorted `(device, base_priority)` + sorted `(device, interface, role, state)`. |
| `topology` (aggressive) | Group VLANs with the **same topology**, ignoring priority values. | Root + sorted `(device, interface, role, state)`. |

**Design principle:** grouping is by **resulting topology fingerprint**, never by raw
configured priority alone. Equal priority does not guarantee equal topology (differing
port costs or states can change the blocked link), and `strict` vs `topology` differ only
in whether the priority values participate in the fingerprint. The fingerprint function
lives in `grouping.py` and is the most test-critical logic in the project (see §9).

The fingerprint is per `StpVlan`, but the unit being grouped is the **VLAN**, because that
is what a diagram covers.

**Grouping is STP-only.** The HSRP view takes `--vlan`/`--all` and nothing else: grouping
means several VLANs render identically, and two VLANs' HSRP never does — each carries its
own virtual IP and its own SVI address on every router, and the virtual IP is the headline
fact of the diagram. A grouped picture could only drop every VLAN's addresses but one, or
crowd several sets of addresses onto the same nodes. `hsrp.csv` is the lossless view.

Group output naming: `stp_vlan10.drawio` (per-vlan), and for grouped modes a stable,
sorted, filesystem-safe name such as `stp_vlans-10_20_30.drawio`.

---

## 7. Views

Each view is a function `build(model, options) -> Diagram` that reads the model and returns
render-ready nodes/links. Views never parse text and never write files.

- **`l2`** — nodes = devices, links = `Link`s. Options: `--endpoints {all,network-only}`,
  `--link-mode {physical,port-channel}`.
  `network-only` keeps a device if `is_source` is true **or** its capabilities include
  `Router`/`Switch` (CDP) or `R`/`B` (LLDP letter codes) — keeping source devices, which
  never advertise their own capabilities in their own CDP/LLDP output. Interface labels
  on both link ends. `--link-mode`
  selects one link per physical adjacency (default) or one link per port-channel — MLAG
  shown by port-channel grouping, imitating N2G — with the bundle's member interfaces
  carried in the link's tooltip.
- **`stp`** — switches only. Root highlighted; node labels show bridge ID + effective
  priority (with base priority available in CSV); links colored by port state
  (forwarding vs blocking) and labeled with role/state per end. Honors `--group-mode`
  and `--vlan`.
  A port-channel is one logical STP port, so its members collapse into a single link
  labeled with the bundle name, members carried in the tooltip — the same treatment
  `l2 --link-mode port-channel` gives them, but unconditional here because spanning-tree
  itself never reports the members.
  Nodes come in two kinds: a device with a capture is drawn from its own `StpBridge`,
  while a device known only from a neighbor's CDP/LLDP output is drawn faded and labeled
  with its name alone, since it has no bridge data to show. The latter is included only
  through a **non-Edge** STP port — PortFast marks the ports facing hosts, and without
  that filter every phone and access point would land in a spanning-tree diagram.
  When the root bridge is one of those uncaptured devices, it is highlighted only if an
  LLDP chassis address matches the reported root address exactly; otherwise the run warns
  and nothing is highlighted, so the diagram never guesses at a root.
- **`hsrp`** — switches and their SVIs; per group show virtual IP, each member's priority
  and role (active/standby/listen). Honors `--vlan`; there is no `--group-mode` (see §6).
  HSRP is not a topology: `show standby brief` says who shares a virtual IP, never which
  cable joins them, so unlike the STP view this one does not read `model.links` at all.
  Each group is drawn as a **virtual gateway node** — the address hosts point at — with
  one link to each router that offers it, labeled with that router's SVI, role and
  priority, colored green for active and amber for standby; the active router is
  highlighted. Each router node also carries its **own** SVI address in that VLAN, read
  from `Device.interfaces` (i.e. from `show ip interface brief`/`show interfaces`) — `show
  standby brief` names only the active and standby routers by address, so a listening
  member could not otherwise be placed. A diagram covers one VLAN and every group on it. Because a grouped diagram
  renders one representative VLAN (as the STP view does) and the virtual IP is precisely
  what differs between grouped VLANs, the gateway node names the VLAN it was drawn from.
- **`bgp`** — nodes = devices (labeled with ASN and BGP router ID, each line dropped when
  unknown), edges = BGP sessions labeled with state;
  iBGP vs eBGP styled differently (blue and purple link colors). v1 shows the
  neighbor/session graph only: one diagram for the whole network, no VLAN selection, and
  no view-specific options. A peer whose address matches no captured interface is drawn as
  its own node, labeled with that address and its ASN and faded like any other device the
  captures do not cover.

---

## 8. Rendering and export

### draw.io with Cisco icons — `render/`

- `render/drawio.py` wraps **N2G `drawio_diagram`** and is the **only** module that imports
  N2G. If N2G is ever replaced, only this module changes.
- `render/icons.py` owns the whole visual language: it maps `DeviceRole` → Cisco draw.io
  icon (`shape=mxgraph.cisco19.*`, the modern flat set), gives each role its own glyph
  color and its icon's native size, and supplies the link and link-label styles. On this
  shape family `strokeColor` paints the icon itself rather than a border, so the STP root
  bridge is marked with a gold card fill and an uncaptured device by fading the whole node
  — a dashed border, which the old isometric stencils used, was invisible.
- `render/legend.py` draws a diagram's key. Views declare *what* needs explaining
  (`Diagram.legend`); the samples are generated by calling the same `node_style` the
  diagram itself used, so a legend cannot drift from the picture. Legend cells are
  appended after layout and written as bare `mxCell`s, which keeps them out of both the
  spacing pass and any consumer counting the diagram's edges.
- Per-end interface labels are left exactly as N2G writes them: a label cell parented to
  the link's own edge, positioned along it by a relative geometry. draw.io treats such a
  cell as that edge's label, so it moves with the link and the Arrange layouts skip it,
  and keeping the two ends separate is what the STP view needs — merged into one string
  they say nothing about which switch is which. Nothing post-processes the XML.

> **Known limitation:** `mxgraph.cisco19.*` shapes are drawn by draw.io's own code, and
> nothing outside draw.io implements them. Cisco icons are a confirmed requirement and
> draw.io is where these diagrams are read, edited and exported from, so this is simply
> what the tool targets. A Lucidchart export was built and then removed — what its
> importer does to a generated file, and why supporting it was not worth the cost, is
> recorded in `docs/architecture.md`.

### Layout

Use N2G's igraph-backed layout (`kk` default). **`igraph` is a required dependency** —
without it, layout is skipped and nodes overlap.

N2G fits that layout into one fixed-size canvas, so the algorithm decides only the layout's
*shape* — the spacing between nodes comes out the same however long their labels are, which
the STP view's per-end labels (`Gi1/0/3 designated/forwarding`) overflow. `render/drawio.py`
therefore scales the finished layout up until the closest two nodes have room for the
longest label the diagram carries, keeping the STP and L2 views legible under one rule. The
*closest* pair is deliberately what is measured, not a typical gap: scaling to a typical
gap leaves the tightest nodes overlapping. See `docs/architecture.md`, "Node spacing".

### CSV export — `export/csv_export.py`

CSV is a first-class output, not an afterthought: it is both a deliverable and the primary
debugging aid (a wrong diagram is diagnosed by inspecting the CSV to localize the fault to
parsing vs rendering). One table per entity: `devices.csv`, `interfaces.csv`,
`neighbors.csv`, `vlans.csv`, `stp.csv`, `hsrp.csv`, `bgp.csv`. STP CSV includes both base
and effective priority.

### The port table — `export/port_table.py`

`ports.csv` is the one table that is a join rather than an entity: "what is plugged into
each port". `export/port_table.py` builds its rows from the model (no I/O) and
`csv_export.py` writes them. It lives in `export/` rather than `views/` because it is a
table, not a `Diagram`. Columns: `device, interface, status, description, speed, duplex,
mac, vlan, ip, neighbor, neighbor_interface, neighbor_platform`.

- Every interface of every captured device gets at least one row, sorted by device and
  then in natural interface order (`Gi1/0/2` before `Gi1/0/10`).
- **One row per learned MAC.** The port's columns repeat on each of its rows — a CSV cannot
  merge cells, and repeating them keeps every row filterable on its own.
- **Uplinks are reduced to their neighbor.** A port whose CDP/LLDP neighbor is a router,
  multilayer switch, switch or firewall, a port-channel, or a port-channel member gets one
  row with the neighbor and no MACs: those MACs belong to endpoints behind the neighbor,
  each of which already has a row on its own access port. A port-channel reports its
  members' neighbors, named by the remote bundle when that device is captured too.
- A phone, AP, host, server or **unknown** neighbor does not make a port an uplink, so the
  PC behind an IP phone is still listed — with the phone repeated as neighbor on each row.
- `status` is `up`, `down` or `admin-down`. `vlan` is the VLAN the MAC was learned in; a
  row without a MAC shows the port's access VLAN, or `trunk`.
- `ip` comes from the ARP tables of *every* captured device, matched on the normalized
  MAC; several addresses are `;`-joined in numeric order.
- `neighbor_platform` is read from the neighbor's `Device`, not `Link.remote_platform`,
  which describes the other end when the link was stored from the neighbor's side.

### The collection report — `export/collect_report.py`

`collect` writes one more CSV, and it is not part of the output tree above: it goes to the
directory the command was run from, because it records a *run* rather than describing the
network. One row per inventory entry — `target`, `hostname`, `status`, `platform`,
`commands`, `capture_file`, `detail` — so a device that was never reached is as visible as
one that was collected, and it is written even when the run failed outright. It shares
`csv_export`'s table writer, and therefore its formula-injection guard: the `hostname`
column comes off a device prompt.

### Output tree

```
output/
├── csv/
│   ├── devices.csv
│   ├── neighbors.csv
│   ├── ports.csv
│   └── ...
├── l2/
│   ├── l2_full.drawio
│   ├── l2_full_port-channels.drawio
│   └── l2_network-only.drawio
├── stp/
│   └── stp_vlan10.drawio ...
├── hsrp/
│   └── hsrp_vlan10.drawio ...
└── bgp/
    └── bgp.drawio
```

Filenames derived from hostnames/VLANs must be sanitized (see §11, path handling).

---

## 9. CLI design

Console script: `nettopo`. Subcommand per concern. Argument parsing lives only in `cli.py`;
it orchestrates ingest → parse → model → view → render/export and contains no business logic.

**Common options:** `-i/--input <dir>` (default `~/configs`), `-o/--output <dir>` (default
`./output`), `--platform <default>` (default `cisco_ios`), `--log-level`.

`~/configs` is the shared capture directory. The tilde is expanded by
`utils/paths.resolve_input_root()`, not by the shell: argparse hands its default over
verbatim, so an unexpanded default would resolve to a relative directory named `~` and
fail with a misleading "input directory not found".

```
nettopo parse   -i ./captures                      # parse only; write all CSV tables
nettopo l2      -i ./captures [--endpoints all|network-only] [--link-mode physical|port-channel]
nettopo stp     -i ./captures [--vlan N | --group-mode per-vlan|strict|topology] [--all]
nettopo hsrp    -i ./captures [--vlan N] [--all]
nettopo bgp     -i ./captures
nettopo all     -i ./captures                      # every view + every CSV
nettopo collect -I ./devices.txt                   # devices -> capture files + a run report
```

- `--link-mode` default is `physical`; `port-channel` writes a `_port-channels`-suffixed
  filename so both link modes can coexist in one output directory.
- `--vlan N` restricts to one VLAN (single diagram); mutually exclusive with `--group-mode`.
- `--group-mode` is `stp`-only and defaults to `per-vlan`; `hsrp` rejects it (see §6).
- `--all` for `stp`/`hsrp` writes every resulting diagram into `output/<view>/`.
- `collect` shares none of the common options: the others read a capture directory, it
  writes one. Its `-o/--output` therefore defaults to the same `~/configs`, and it adds
  `-I/--inventory` (required), `--report` (default `nettopo-collect-report.csv`, written to
  the working directory, `-` for stdout), `-u/--user`, `--port`, `--timeout`,
  `--command-timeout` and `--host-key-checking` (default `strict`). There is deliberately
  no password option of any kind — see §11.
- `all` takes the common options only. It runs the per-VLAN views over every VLAN with
  `--group-mode per-vlan`, and writes the three L2 diagrams of the §8 output tree
  (physical, port-channels, network-only). A view the captures hold no data for is skipped
  with a warning and the run still exits 0; only an unreadable input or a failed write is
  an error. `nettopo <view>` named explicitly still writes its diagram, empty.

---

## 10. Dependencies

**Runtime:** `n2g`, `textfsm`, `ntc-templates`, `python-igraph`, `netmiko`.

netmiko is a standard dependency so `nettopo collect` works out of the box, but `cli.py`
still imports the SSH backend lazily, inside the `collect` handler: `import nettopo.cli`
— and therefore every other command — must never reach it, which `tests/test_no_network.py`
asserts in a fresh interpreter. The zero-network guarantee in §11 is enforced by that test
suite, not by the shape of the install.

> **Do NOT depend on or install Flask/Werkzeug.** N2G's optional V3D viewer imports Flask
> at module load; with Flask present but an incompatible Werkzeug on Python 3.12, that
> import poisons the startup of **every** N2G command (`ast.Str` / `NameError: app`).
> We never use the V3D viewer, so Flask must stay absent from the environment. If a future
> need arises, isolate it in a separate optional extra and a separate Python 3.11 venv.

**Dev:** `pytest`, `pytest-cov`, `ruff` (lint + format), `mypy` (type checking),
`build`/`twine` (packaging). Pin versions in `pyproject.toml`.

Target Python: **3.11+** (dataclasses with `X | None`, modern typing).

---

## 11. Security review (OWASP-adapted)

Per `CLAUDE.md`, every change is checked against OWASP Top 10 (2021). For a CLI that reads
local files and, in `collect` alone, connects to network devices, the relevant items are:

- **A03 Injection** — parsing is done by the TextFSM engine over template **data**;
  never `eval`/`exec`/`os.system` on parsed content or filenames. CLI parsing via the
  standard argument parser.
- **A08 Software & Data Integrity** — no `pickle`, no YAML loading of any kind: the
  inventory is plain text read line by line, so it cannot construct Python objects.
  TextFSM templates come from the installed `ntc-templates` package, not from
  user-supplied executable code.
- **Path handling (traversal)** — validate and normalize `--input`/`--output`. Output
  filenames are derived from hostnames and VLAN ids: **sanitize** them (strip path
  separators, quotes, whitespace) so a device named `../../etc` can't escape the output
  directory. Refuse to write outside the resolved output root.
- **Sensitive data** — inputs contain hostnames, management IPs, and topology. The tool
  must not transmit them anywhere. **Hard requirement: zero network connections in every
  command except `collect`.** `collect` is the one command that opens a socket, and only to
  the devices its inventory names.

  The exemption is bounded, not taken on trust. `tests/test_no_network.py` monkeypatches
  `socket.socket` to fail and runs every other command end to end, deriving that list from
  `cli._COMMAND_HANDLERS` itself so a command added later fails the suite rather than
  quietly inheriting the guarantee. A second test starts a fresh interpreter and asserts
  that `import nettopo.cli` pulls in neither `netmiko` nor `paramiko`, so the SSH backend
  stays reachable only through `collect`'s own handler.

- **Read-only collection** — `collect` may never change a device. Every command is matched
  against `^show\s+\S` immediately before it is written to the session, so an edit to the
  command tables that introduced a `clear counters` fails loudly rather than silently
  resetting a production counter. The tables are literals; nothing from the inventory or
  from device output is ever concatenated into a command, so there is no injection path
  into them. One caveat is stated rather than hidden: netmiko sends `terminal length 0` /
  `terminal width 511` at session setup and `exit` at teardown. Those are session-local,
  change no configuration and no counters, and are unavoidable for CLI scraping; no netmiko
  write API is ever called.

- **A07 Identification & authentication failures** — no credential is accepted on the
  command line. There is no `--password` and no `--enable-password`, because argv is
  readable by every user on the host through `ps`. Credentials are prompted for with
  `getpass`, live in memory for the run only, and are declared `repr=False` so that logging
  an object holding them cannot spill them. netmiko's `session_log` is never enabled — it
  would write the authentication exchange to a plaintext file. `collect` refuses to run
  without a TTY rather than let `getpass` fall back to an echoing prompt. Zeroization is
  **not** claimed: a Python `str` cannot be reliably wiped, and saying otherwise would be
  worse than saying so.

- **Account lockout** — treated as a security property, not a usability detail. One wrong
  password across a fleet is a lockout, and on a TACACS-backed network it locks the
  operator out of everything. Collection is therefore **serial and stops at the first
  error**, which bounds a mistyped credential to exactly one failed authentication attempt
  — a guarantee no concurrent design can make, and the reason concurrency is out of scope
  (§2). A rejected enable secret counts the same. nettopo never retries an authentication
  and never re-prompts mid-run, because a retry loop is how accounts get locked. Devices the
  stop prevented from being contacted are reported `skipped`, distinctly from the `failed`
  device that ended the run.

- **SSH host keys** — `collect` defaults to strict checking (`ssh_strict`,
  `system_host_keys`), unlike netmiko's own default of silently adding an unknown key. A
  tool whose purpose is typing device credentials into a socket must not accept an unknown
  host key: a machine impersonating a device at first contact harvests a login and an enable
  secret in one exchange. `--host-key-checking none` exists for labs, warns on every
  invocation, and the README documents `ssh-keyscan` as the deliberate way to populate
  `known_hosts`.

- **A10 SSRF** — `collect` connects to hosts named in a user-supplied inventory. The user is
  the operator and the inventory is their own file, so this is intended behavior rather than
  a forged request. The mitigation that matters: **nothing but the inventory can name a
  target.** No host, address or port is ever derived from parsed device output, so a hostile
  CDP/LLDP neighbor entry in a capture can never become a connection. The resolved target
  list is logged before the first connection, only port 22 or an explicit `--port` is
  dialled, and no redirect, proxy or jump host is followed.

If a change touches any of the above and the mitigation isn't obvious from the diff, call it
out in the PR description.

---

## 12. Testing strategy

- **Parsers** — the highest-value tests: deterministic text-in / structure-out. Store
  anonymized real captures in `tests/fixtures/` and assert the produced model objects.
  Cover IOS and IOS-XE output variants.
- **Interface normalizer** — exhaustive table-driven tests, including idempotency and
  already-abbreviated inputs.
- **Grouping fingerprints** — dedicated tests, especially the boundary case: **same
  priority, different topology** (must NOT group under `strict` **or** `topology` when the
  blocked link differs), and **same topology, different priority** (must group under
  `topology` but NOT under `strict`).
- **Views/render** — assert the draw.io XML is well-formed and that expected nodes/links
  exist; do not assert pixel positions. Node *spacing* is the exception: how far apart the
  closest two nodes end up is a property the layout owes the labels, so it is asserted —
  but still never where any individual node is.
- **Collection** — netmiko is replaced by a scripted fake (`tests/conftest.py`), so the
  collector's tests open no socket. The
  highest-value case asserts that collected text survives `build_network_model()` — if the
  synthesized prompt lines did not match the splitter, every capture would parse to nothing.
  The failure policy is tested by how many devices were *contacted*, since a rule about not
  doing something cannot be checked by looking at what was produced: a wrong password must
  cost exactly one authentication attempt, and a missing enable password must not stop the
  run.
- **No-network** — see §11. The command list is derived from `cli._COMMAND_HANDLERS`, so a
  new command cannot inherit the guarantee without a case; a separate test proves importing
  the CLI pulls in no SSH library; and the `core-install` CI job proves the core package
  installs without one.

Coverage target: meaningful coverage on `parsing`, `model`, and `views`; `render` covered
at the well-formed-XML level.

---

## 13. CI/CD (GitHub Actions)

- **On every push / PR:** `ruff check` + `ruff format --check`, `mypy`, `pytest --cov`.
  The pipeline must be green from the very first commit of Phase 0.
- **On tag `v*`:** build sdist+wheel and publish to PyPI via trusted publishing
  (OIDC, no long-lived token in secrets).
- Branch protection on `main`: no direct pushes; PRs require passing checks.

---

## 14. Delivery plan (sequential GitHub issues)

Each phase is one or more issues. Per `CLAUDE.md`: branch per issue
(`feat/…` / `fix/…`), open a PR into `main`, never commit to `main` directly, and update
`README.md` / `CHANGELOG.md` / `docs/architecture.md` / this spec in the same commit as the
change.

- **Phase 0 — Scaffolding.** Repo, `pyproject.toml`, `src/` layout, empty CLI skeleton,
  Actions pipeline (lint + type + test) green, branch protection, issue labels. No logic.
- **Phase 1 — Foundations.** Interface normalizer (`utils/interfaces.py`) with full tests;
  data model dataclasses + enums (`model/entities.py`); grouping fingerprints
  (`model/grouping.py`) with tests. Nothing user-visible yet.
- **Phase 2 — L2 parsing + CSV.** `ingest/files.py`, CDP/LLDP parsers, `version.py`,
  populate the model, `nettopo parse` writing all CSV tables. First tangible output.
- **Phase 3 — L2 view (v0.1 release).** draw.io render wrapper, icons, endpoint filter
  (`all` / `network-only`), interface labels, MLAG. First usable PyPI release.
- **Phase 4 — STP.** `spanning_tree.py` parser, `StpVlan` population, per-VLAN + both
  grouping modes, `output/stp/` bulk generation, STP CSV.
- **Phase 5 — HSRP.** `hsrp.py` parser, `standby brief`, per-VLAN + grouping, `output/hsrp/`,
  HSRP CSV. Structurally analogous to STP.
- **Phase 6 — BGP.** `bgp.py` parser (`bgp summary`), session graph, iBGP/eBGP styling,
  BGP CSV. `peer_device` stays `None`.
- **Phase 7 — Polish.** Per-view layout tuning, docs, `nettopo all`.
- **Phase 8 — Live collection.** `nettopo collect`: inventory, run-time credential
  prompting, the read-only SSH collector, per-device capture files and the run report. The
  `DataSource` interface Phase 0 defined is what makes this an addition rather than a
  rewrite — no parser, model or view changes.

Each phase after Phase 3 is an incremental PyPI release. Publish early; L2 done well is
worth more than a half-finished grand plan.

---

## 15. Documents to keep in sync

Per `CLAUDE.md`, these are updated together whenever behavior/structure/deps change:
`CLAUDE.md`, `README.md`, `CHANGELOG.md`, `docs/architecture.md`, and this
`PROJECT_SPEC.md`. None may describe a state of the project that no longer exists.
