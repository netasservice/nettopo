# Examples

Runnable capture sets. Unlike `tests/fixtures/`, which holds the smallest input that
exercises one parser, these are networks meant to be run end to end — they are the source
of the diagrams in the [top-level README](../README.md#example-diagrams).

`campus/` is the general-purpose one, exercising the L2, STP and HSRP views and the
per-port `ports.csv` table. The other two
exist for a single shape the campus set cannot show: `hsrp-quad/` for an HSRP group with
more than two members, and `bgp-edge/` for BGP, which the campus switches do not run.

## `campus/`

A six-switch campus, ten devices once the neighbor-only ones are counted:

| Device | Capture | Role in the diagrams |
|---|---|---|
| `core-sw1`, `core-sw2` | yes | C9500 core pair, joined by `Po1` (`Gi1/0/1`, `Gi1/0/2`); root bridge for VLANs 10/20/99 and 30 respectively, and the HSRP gateway pair for VLANs 10/20/30 |
| `dist-sw1`, `dist-sw2` | yes | C9300 distribution, dual-homed to both core switches — the loop that gives each VLAN a blocked port |
| `acc-sw1`, `acc-sw2` | yes | C2960X access, cross-connected to each other, which is the second loop |
| `acc-sw3` | no | Access switch seen only in `dist-sw2`'s CDP output — drawn faded in the STP view |
| `edge-rtr` | no | WAN router on a routed port, so it appears in the L2 view and not in the STP view |
| `esxi-host01`, `esxi-host02` | no | ESXi hosts on Edge/PortFast ports: dropped by `--endpoints network-only` and by the STP view |

Four VLANs (10 users, 20 voice, 30 servers, 99 mgmt) with two distinct spanning trees
between them, which is what makes `--group-mode strict` and `--group-mode topology`
produce different groupings on this set. The core pair runs HSRP on VLANs 10, 20 and 30,
aligned with those trees — each core switch is the active gateway for the VLANs it roots.
The HSRP view never groups, so it writes one diagram per VLAN whatever the priorities are.

The captures also carry the four commands behind `ports.csv` (`show interfaces status`,
`show interfaces description`, `show mac address-table`, `show ip arp`). The access
switches have endpoints that CDP does not report, so the diagrams are unchanged: on
`acc-sw1`, a PC on `Gi1/0/5`, an IP phone with a PC behind it on `Gi1/0/6`, a disabled
spare port, and two VMs on the `esxi-host01` trunk; on `acc-sw2`, a PC and an
err-disabled port. Only the core switches hold ARP tables, so every IP in `ports.csv` is
resolved across devices, and the distribution switches' MAC tables, which learn every
endpoint, are hidden as uplinks.

```bash
nettopo parse -i examples/campus -o ./output
nettopo l2    -i examples/campus -o ./output
nettopo stp   -i examples/campus -o ./output --all --group-mode topology
nettopo hsrp  -i examples/campus -o ./output --all
```

`campus/diagrams/` holds the output of those commands, committed so the README can show
it: the `.drawio` files nettopo writes, plus PNG exports of the five the README embeds.
`FileDataSource` reads only regular files in the capture directory, so this subdirectory
is invisible to ingestion. Both regeneration steps are documented in the
[top-level README](../README.md#example-diagrams).

## `hsrp-quad/`

Four layer-3 switches sharing one HSRP group on VLAN 50 — a VLAN spanning two buildings,
each with a switch pair:

| Device | SVI address | Priority | State |
|---|---|---|---|
| `bldg-a-sw1` | `10.20.50.2` | 150 | Active |
| `bldg-a-sw2` | `10.20.50.3` | 140 | Standby |
| `bldg-b-sw1` | `10.20.50.4` | 110 | Listen |
| `bldg-b-sw2` | `10.20.50.5` | 100 | Listen |

The campus set has the usual two-router gateway pair, where active and standby account for
everyone. This one exists because a group can have any number of members while `show
standby brief` names only those two by address — so the two listening switches are the
case that proves the diagram's addresses come from `show ip interface brief` and not from
the HSRP output.

Deliberately narrow: these captures carry `show version`, `show ip interface brief` and
`show standby brief` and nothing else, because no other command contributes to an HSRP
diagram. `nettopo l2` and `nettopo stp` on this directory produce empty diagrams, which is
correct — there is no neighbor-discovery or spanning-tree data to draw.

```bash
nettopo hsrp -i examples/hsrp-quad -o examples/hsrp-quad/diagrams --all
```

## `bgp-edge/`

Three routers in AS 65001 — two core, one edge — fully meshed over iBGP, with the edge
router holding the two eBGP sessions to the outside:

| Device | Loopback | Sessions |
|---|---|---|
| `core-r1` | `10.255.0.1` | iBGP to `core-r2` and `edge-r1` |
| `core-r2` | `10.255.0.2` | iBGP to `core-r1` and `edge-r1` |
| `edge-r1` | `10.255.0.3` | iBGP to both cores, eBGP to AS 65100 (Established) and AS 65200 (Idle) |

The mesh is what this set is for. Every one of its three sessions appears in two captures,
once from each end, and the diagram has to draw each of them once — which it can only do
by matching each peer address against the loopbacks the routers report in
`show ip interface brief`. The two eBGP peers are the other half: no capture covers them,
so they stay faded boxes labeled with the address and AS the session named them by, and
the `Idle` one shows what a session that never came up looks like.

Deliberately narrow, like `hsrp-quad/`: these captures carry `show version`,
`show ip interface brief` and `show ip bgp summary` and nothing else, because no other
command contributes to a BGP diagram. `nettopo l2` and `nettopo stp` on this directory
produce empty diagrams, which is correct — there is no neighbor-discovery or
spanning-tree data to draw.

```bash
nettopo bgp -i examples/bgp-edge -o examples/bgp-edge/diagrams
```

Every value in these files is synthetic — hostnames, serials, MACs and IPs alike.
