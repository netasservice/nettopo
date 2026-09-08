# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Project moved to the `netasservice` GitHub organization.** All repository, issue
  and wiki links now point at `github.com/netasservice/nettopo`.

## [0.9.1] - 2026-08-20

### Fixed

- **Endpoint icons rendered as empty boxes in older draw.io builds.** Access point,
  IP phone, server and host nodes (and their legend samples) used standalone
  `mxgraph.cisco19` stencils that only exist in recent draw.io releases. All roles now
  use the same `rect;prIcon=` card form as the network gear, which renders in every
  build.
- **LLDP-only neighbors (e.g. Arista EOS switches) were classified as unknown devices.**
  Three gaps closed: the parser now falls back to the LLDP System Description when
  LLDP-MED reports no model, so "Arista Networks EOS ... DCS-7504N" reaches platform
  classification; the capability table understands the IEEE 802.1AB letter codes
  (`R`, `B`, `W`, `T`, `S`) alongside CDP's word forms; and Arista product families
  (DCS/CCS, plus a generic Arista/EOS rule) are recognized. `--endpoints network-only`
  accordingly no longer drops neighbors reported only over LLDP.

## [0.9.0] - 2026-08-20

### Changed

- **`nettopo collect` now works out of the box.** netmiko is a standard dependency and the
  `collect` extra is gone: `pip install nettopo` is the whole install, and
  `pip install 'nettopo[collect]'` is no longer a valid target. The zero-network guarantee
  for every other command is unchanged and still enforced by `tests/test_no_network.py`,
  which fails every socket and asserts that importing the CLI never imports the SSH
  backend — what changed is only that the guarantee no longer rests on the networking
  library being absent from the environment.

### Removed

- **YAML inventories.** An inventory is now only a plain text file with one device per
  line; a `.yaml`/`.yml` inventory is refused with an error that says the support was
  removed. This drops the PyYAML dependency entirely.

## [0.8.0] - 2026-08-19

Phase 8: nettopo can now gather its own captures. `nettopo collect` connects to the devices
in an inventory over SSH, runs the same `show` commands a hand-saved capture would contain,
and writes one file per device — so the workflow becomes `nettopo collect && nettopo all`
with no paths typed at all.

The design is deliberately narrow. Credentials are asked for on the terminal and never
stored, which is why the inventory is a plain list with nothing sensitive in it and needs no
encryption. Collection is read-only and serial. The SSH backend is an optional extra, so a
core install still has no networking library in it.

### Added

- **`nettopo collect --inventory <file>`.** Collects captures from live devices over SSH and
  writes one `.txt` per device, named after the device's own hostname — so an inventory of
  bare IP addresses still produces `sw-core.txt`. Needs the new optional extra:
  `pip install 'nettopo[collect]'`.
- **Inventory** (`ingest/inventory.py`): a flat list of hostnames or IPs, as one device per
  line (`.txt`) or a YAML list (`.yaml`/`.yml`), with `#` comments. No groups, no per-device
  variables, no credentials. YAML is read with `safe_load` only.
- **Run-time credentials** (`ingest/credentials.py`): username, password and enable password
  prompted for and held in memory for that run. Nothing is written to disk, and there is
  deliberately no password option of any kind — argv is readable by every user on the host.
- **Enable handling.** A device already in privileged mode is left alone, one that asks for
  enable gets it, and one that asks for it when no enable password was given is skipped and
  reported while the run continues — so a network where only some devices need enable takes
  one run rather than two.
- **The run report.** Every collection writes `nettopo-collect-report.csv` to the directory
  the command was run from, with one row per inventory entry — including the devices that
  were never reached. Written even when the run failed outright.
- **Duplicate-hostname handling.** Two devices reporting the same name get their captures
  suffixed with their inventory entry — both of them, so neither keeps the clean name by
  arriving first — plus a warning and a `duplicate-hostname` report row. Note this does not
  make the resulting diagram correct: the model is keyed by hostname and will still merge
  them into one node. Only distinct hostnames fix that.
- `utils/command_sections.format_command_section()`, the inverse of the capture splitter,
  kept beside it so the two halves of the format cannot drift.
- `utils/paths.resolve_input_root()` and `DEFAULT_CAPTURE_DIR`.
- A `core-install` CI job that installs the package without the `collect` extra and proves
  the whole diagram pipeline runs with no networking library present.

### Changed

- **Breaking-ish:** `-i`/`--input` is no longer required and defaults to `~/configs` on
  every command. Nothing that worked before stops working — the flag is accepted exactly as
  it was, and omitting it previously exited 2 rather than doing anything useful — but a bare
  `nettopo all` now reads `~/configs` instead of printing usage.
- **The zero-network guarantee is narrowed, and this is a security claim changing:** it was
  "zero network connections", full stop. It is now "zero network connections in every
  command except `collect`". The exemption is bounded rather than taken on trust — the
  socket-denial test derives its command list from the command table itself so a new command
  cannot inherit the guarantee unchecked, a second test proves importing the CLI pulls in
  neither netmiko nor paramiko, and the `core-install` job proves the core package installs
  without either.
- `parsing.version._detect_os()` is now public as `detect_os()`; the collector must classify
  a device before any template can be selected.
- `export/csv_export._write_table()` and `._csv_safe()` are now public as `write_table()` and
  `csv_safe()`, shared with the collection report so its device-reported `hostname` column
  gets the same formula-injection guard.

## [0.7.0] - 2026-08-18

Phase 7: `nettopo all` becomes real, and with it the zero-network guarantee covers the
whole tool in one run. Lucidchart support is dropped: draw.io is now the one tool these
diagrams target.

### Removed

- **Breaking:** the Lucidchart post-process and its `--no-lucidify` flag. `render/lucidify.py`
  is deleted and `render_diagram()` no longer takes `apply_lucidify`; generated `.drawio`
  files now carry N2G's output exactly as it comes, which is what draw.io reads.

  It was removed after the live Lucid import it existed for was finally run — about forty
  test imports into a real account. Lucid's importer rejects any file containing the legend
  outright, ignores the `<object>` label N2G puts hostnames in, draws none of the
  `mxgraph.cisco19.*` icons, refuses every form of embedded SVG/PNG artwork, and drops
  `fillColor` and `opacity` on the classic Cisco stencils it *does* map — which is how the
  STP view marks the root bridge and how uncaptured devices are faded. Supporting it meant
  giving up the legend, the icons or the marks that carry each view's meaning. The findings
  are kept in `docs/architecture.md` so the decision is not re-litigated from scratch.

  Two cosmetic quirks in N2G's output come back with it: `relative="-1"` on target-end
  labels and doubled semicolons in style strings. draw.io reads both correctly, and
  normalizing them existed only to serve an importer that is no longer a target.

- The `examples/` diagrams are regenerated without the post-process. Only those two quirks
  differ; the layout, the styles and the committed PNG exports are unchanged.

### Added

- `nettopo all -i <dir>` runs every view and every CSV export in one invocation, writing
  the complete `output/` tree from `PROJECT_SPEC.md` section 8: the seven CSV tables, the
  three L2 diagrams, one STP and one HSRP diagram per VLAN, and the BGP session graph.
  Ingestion and parsing run once for all five outputs rather than once per command, which
  is the reason it is a command and not a shell loop — nothing downstream of the model
  mutates it, so the views can share one ingest. It drives the same view and render
  helpers the individual commands do, so each file it writes is byte-identical to what
  the corresponding command would have produced.
- `all` takes only the common options (`-i`, `-o`, `--platform`) — no
  `--vlan`, `--endpoints`, `--link-mode` or `--group-mode`. The views run with the
  settings that lose nothing: every VLAN, drawn one diagram per VLAN (`--group-mode
  per-vlan`, which never collapses two VLANs into one drawing), and the three L2 variants
  the output tree names — physical, port-channels collapsed, and network-only. The fourth
  combination (`network-only` over port-channels) is deliberately not written: dropping
  endpoints already removes what makes a dense L2 diagram unreadable, so it would differ
  from `l2_network-only.drawio` only on the occasional bundle between two network devices.
- A view the captures hold no data for is skipped with a `WARNING` naming what was
  skipped, and the run still exits 0 — a capture set covering part of the network (access
  switches that speak no BGP, routers with no spanning tree) is ordinary input, not a
  failed run. No directory is created for a skipped view. Only an unreadable input
  directory or a failed write makes `all` exit non-zero. Commands named explicitly are
  unchanged: `nettopo bgp` on captures with no BGP still writes its (empty) diagram. For
  L2 the emptiness test is the link count, not the node count: captures with no CDP/LLDP
  still yield one node per device, and a page of unconnected boxes is not a topology.
- `tests/test_no_network.py` covers a full `nettopo all` run, which completes the hard
  requirement from `PROJECT_SPEC.md` section 11. The test's earlier cases each covered one
  command; this one drives every view and every export under the same `socket.socket` ban,
  so a socket opened anywhere in the tool now fails it.

### Changed

- `cli.py`'s five handlers share `_load_model()` for the ingest step and `_write_diagram()`
  for the render-and-log step, extracted so `all` could reuse them rather than repeat
  them. No behavior changes for the individual commands; the BGP command's log line still
  counts sessions rather than links.

### Removed

- `cli.py`'s `_run_unimplemented` placeholder handler, which had no remaining caller once
  `all` became real, and the CLI test that asserted `all` exits 1.

## [0.6.0] - 2026-08-14

Supersedes the `0.6.0rc1` pre-release, which was exercised against real captures; the
tree is identical to `v0.6.0rc1` and the entries below cover everything that changed
since `0.5.0`.

Phase 6: BGP. `nettopo bgp` becomes the fifth real command, rendering the BGP neighbor
session graph, and `bgp.csv` stops being header-only. Two things outside BGP change with
it: `nettopo hsrp` drops `--group-mode`, which it could never honor without dropping
VLANs, and multi-line node labels finally render as several lines in every view.

### Added

- Phase 6 BGP parsing: `parsing/bgp.py` reads `show ip bgp summary` through ntc-templates
  and populates `NetworkModel.bgp` — one `BgpPeer` per session, carrying both AS numbers,
  the peer address, the state and whether the session is iBGP or eBGP. Two columns are
  interpreted rather than copied: IOS overloads the last one, printing a prefix count for
  a session that is up and a state word (`Idle`, `Active`, `Idle (Admin)`) for one that is
  not, so a count is recorded as `Established`; and the summary never says iBGP or eBGP,
  which is read off whether the neighbor's AS matches the local one. AS numbers written in
  asdot notation (`1.10`) are converted to the single integer the model stores. IOS and
  IOS-XE print the command identically and share the one template; `tests/fixtures/bgp/`
  carries a fixture for each. The parser returns a `BgpCapture`: the sessions, plus the
  one fact the summary's header states about the reporting router rather than about any
  session — its BGP router ID.
- `Device.asn` is populated, from the local AS number in the device's own summary. It had
  been in the model and in `devices.csv` since Phase 1 with nothing writing it.
- `Device.router_id`, a new field carrying the `BGP router identifier` from the device's
  own summary, exported as a `router_id` column in `devices.csv`. It sits on the device
  rather than on `BgpPeer` because it describes the router, not a session — so `bgp.csv`
  stays one row per session with no repeated column.
- Phase 6 BGP view: `views/bgp.py` builds one render-ready `Diagram` for the whole
  network. Routers are labeled with their AS number over their router ID (`RID
  10.255.0.1`, and any line whose value is unknown is simply left out), sessions with
  their state, and
  colored blue for iBGP or purple for eBGP. Because `show ip bgp summary` names the far
  end of a session by address only, the view matches each peer address against the
  interface addresses the model already holds: where it finds a captured device, the two
  routers' independent reports of one session collapse into a single link, and where it
  finds nothing the peer keeps a faded node of its own labeled with its address and AS —
  never a router ID, which the summary prints only for the device reporting it.
  That match is a property of the drawing — it never writes `BgpPeer.peer_device`, which
  stays `None` as the spec requires. When the two ends disagree about a session's state,
  both are shown rather than one being silently picked.
- `nettopo bgp -i <dir>` is real, writing `output/bgp/bgp.drawio`. The filename is fixed
  and the command takes no view-specific options: BGP sessions do not partition into VLANs
  the way STP and HSRP do, so there is nothing for a `--vlan`/`--all` pair to select
  between.
- `bgp.csv` is populated: one row per session as each device reported it, with
  `peer_device` empty on every row and `vrf` always `default` — `show ip bgp summary`
  covers the default VRF, and the template has no VRF column to read one from.
- `examples/bgp-edge/`: three routers in AS 65001 fully meshed over iBGP, with the edge
  router holding two eBGP sessions to peers no capture covers — one Established, one that
  never came up. Deliberately narrow, like `examples/hsrp-quad/`: the captures carry
  `show version`, `show ip interface brief` and `show ip bgp summary` and nothing else.
  The generated diagram is committed under `examples/bgp-edge/diagrams/bgp/`, the README
  embeds it, and `tests/test_examples_bgp_edge.py` pins it.

### Removed

- `nettopo hsrp` no longer accepts `--group-mode`; it takes `--vlan N` or `--all` and
  always writes one diagram per VLAN. Grouping asserts that several VLANs render
  identically, which HSRP never satisfies: each VLAN has its own virtual IP and its own
  SVI address on every router. A grouped run rendered the lowest VLAN of each group and
  silently dropped the rest, so `hsrp_vlans-10_20.drawio` showed VLAN 10's gateway address
  and none of VLAN 20's. Merging instead of dropping is no better — it stacks a gateway
  node and an address line per VLAN onto the same two routers. `hsrp.csv` was, and stays,
  the lossless per-VLAN view. `nettopo stp --group-mode` is unchanged: there the
  fingerprint really does guarantee identical diagrams.
- `model/grouping.py`'s `hsrp_fingerprint()`, which had no remaining caller.

### Fixed

- Multi-line node labels are now actually drawn on several lines. Every view since Phase 3
  has written them with `\n` — `core-sw1\n10.10.10.2`, `dist-sw1\n00a1.b2c3.d011 / 32778`,
  `VLAN 10 group 10\n10.10.10.1` — and every one of them rendered as a single long line:
  the label is an XML attribute, where a newline is normalized to a space, and draw.io
  then draws it as HTML, where a newline is ordinary whitespace. `render/drawio.py`
  translates it to an HTML `<br>`, the same break link tooltips already used. Node spacing
  is measured per line to match, since stacked lines are no wider than the longest of
  them. Every committed example diagram is regenerated; only their labels and layout
  change, not their content.

## [0.5.0] - 2026-08-11

Phase 5: HSRP. `nettopo hsrp` becomes the fourth real command, rendering per-VLAN
first-hop-redundancy diagrams, and `hsrp.csv` stops being header-only. Nothing existing
changes shape: the L2 and STP views, their filenames and every other CSV table are
untouched.

### Added

- Phase 5 HSRP parsing: `parsing/hsrp.py` reads `show standby brief` through
  ntc-templates and populates `NetworkModel.hsrp` — one `HsrpGroup` per `(vlan, group)`,
  carrying the virtual IP and each member's SVI, priority, role and preempt flag.
  IOS and IOS-XE print the command identically and share the one template;
  `tests/fixtures/hsrp/` carries a fixture for each.
- Phase 5 HSRP view: `views/hsrp.py` builds one render-ready `Diagram` per VLAN, or per
  matching group under `--group-mode`. Each HSRP group is drawn as a **virtual gateway**
  node — the address hosts actually point at — with one link to every router that offers
  it, labeled with that router's SVI, role and priority and colored green for active,
  amber for standby. The active router is highlighted. Each router node also carries its
  **own** SVI address in that VLAN beside the virtual one, so both halves of the first hop
  are on the page; that address comes from `show ip interface brief`/`show interfaces`,
  because `show standby brief` names only the active and standby routers by address and a
  merely listening member appears in it nowhere. A capture with neither command leaves the
  node labeled with its name alone. Several groups on one SVI (two gateways load-sharing a
  VLAN) share a diagram.
- `nettopo hsrp -i <dir> [--vlan N | --group-mode per-vlan|strict|topology] [--all]` is
  real, writing `output/hsrp/hsrp_vlan10.drawio` (or `hsrp_vlans-10_20.drawio` for a
  group) on the same naming rule as `stp`.
- `hsrp.csv` is populated: one row per member, with the group's virtual IP alongside the
  member's SVI, priority, role and preempt flag.
- `utils/interfaces.py` gains `svi_vlan()`, which reads the VLAN id back out of an SVI
  name. Reading a VLAN out of `Vl10` is a property of the naming rule, so it belongs to
  the module that owns it rather than to whichever parser needs it.
- `HsrpRole.LEARN`, the one HSRP state the enum was missing. A member reported in it
  would otherwise have been dropped.
- The `examples/campus/` core pair now runs HSRP on VLANs 10/20/30, aligned with the
  spanning trees (`core-sw1` active where it is root, `core-sw2` for VLAN 30), with
  VLAN 20's priorities offset so `--group-mode strict` and `topology` differ here too.
  The generated diagrams are committed under `examples/campus/diagrams/hsrp/` and the
  README embeds one.
- `examples/hsrp-quad/`: four layer-3 switches sharing one HSRP group on VLAN 50 — active,
  standby and **two listening** — for the shape the campus set's gateway pair cannot show.
  It is also what demonstrates where the node addresses come from: `show standby brief`
  names the active and standby routers by address and nobody else, so the two listeners'
  addresses appear in no HSRP output anywhere in that capture set. Documented in
  `examples/README.md`, embedded in the README, and pinned by
  `tests/test_examples_hsrp_quad.py`.

### Changed

- The two per-VLAN views now share their result type and filename rule
  (`views/diagram.py`'s `VlanDiagramGroup` and `vlan_diagram_filename()`), and `cli.py`
  drives both through one handler. `views/stp.py`'s `StpDiagramGroup` is now
  `VlanDiagramGroup`; `stp_output_filename()` is unchanged.

## [0.4.0] - 2026-08-11

Device roles inferred from the reported chassis, a modern Cisco icon set, and a legend
on every diagram. The visual output of `nettopo l2` and `nettopo stp` changes
substantially, and the `role` column in `devices.csv` changes with it.

### Added

- Device roles are now inferred from the reported chassis (`model/platforms.py`), not only
  from CDP/LLDP capabilities. A platform string that names a known product family wins,
  which fixes multilayer switches being drawn as routers (`Router Switch` capabilities
  forced a choice between the two) and gives a device we hold the capture for a role from
  its own `show version` instead of depending on a neighbor to describe it. This also
  reaches the `L3_SWITCH`, `FIREWALL`, `AP` and `SERVER` roles, which had icons mapped but
  were never assigned. The `role` column in `devices.csv` improves accordingly.
- Diagrams carry a legend (`render/legend.py`). The L2 view lists every device role it
  drew; the STP view explains the root bridge, uncaptured devices, and the
  forwarding/blocking link colors — each only when the diagram actually uses it.
- `examples/campus/`: a runnable six-switch capture set (ten devices once the
  neighbor-only ones are counted, four VLANs, two distinct spanning trees) covering
  every feature the L2 and STP views implement — port-channels, an uncaptured switch,
  Edge/PortFast host ports, and a per-VLAN root that moves. Documented in
  `examples/README.md`.
- README: an **Example diagrams** section showing what `nettopo l2` and `nettopo stp`
  actually produce from that capture set — the physical topology, the same topology
  under `--endpoints network-only --link-mode port-channel`, and the spanning tree for
  two VLANs with different roots. A table shows what each `--group-mode` writes for this
  capture set.
- `examples/campus/diagrams/`: the generated `.drawio` files themselves, plus white-
  background PNG exports of the four the README embeds, so a reader sees the real Cisco
  icons, colors and per-end labels without installing anything. The README documents
  both the `nettopo` commands that write the `.drawio` files and the draw.io CLI
  invocation that exports them.
- `tests/test_examples_campus.py`, which asserts the facts the README states about that
  capture set — node/link counts, the two roots and their blocked ports, the faded
  uncaptured switch, and each `--group-mode`'s output. Without it, editing an example
  capture or changing a view's output would leave the documented diagrams silently
  wrong.

### Changed

- Device icons are draw.io's modern flat Cisco set (`mxgraph.cisco19.*`) instead of the
  classic isometric stencils, with a color per role rather than one blue for everything.
  Note this makes fidelity under a Lucidchart import *less* likely, not more: these icons
  are drawn by draw.io's own code rather than looked up as named stencils. The tradeoff is
  deliberate — see the known limitation in `PROJECT_SPEC.md` §8.
- A device we hold no capture for is now drawn faded with an italic label, replacing the
  dashed border. On the previous stencils the dash rendered nothing at all, so an inferred
  device was indistinguishable from a measured one.
- The STP root bridge is marked with a gold card fill rather than a gold border. On the new
  icons the stroke color paints the glyph itself, so the old override made the icon
  unreadable.
- Link end labels are set smaller and gray on an opaque background, and links with no port
  state are drawn in soft gray rather than black, so the STP view's colors carry the
  emphasis.
- Diagrams are less sparse: node spacing needs less clearance now that end labels are
  smaller, and the icons themselves are drawn larger. The committed example diagrams are
  regenerated.

### Fixed

- Rendered diagrams no longer draw nodes on top of each other. N2G fits its igraph
  layout into one fixed-size canvas, so the spacing between nodes was the same however
  long their labels were — which the STP view's per-end labels
  (`Gi1/0/3 designated/forwarding`) overflowed badly: in the campus example `core-sw2`
  and `dist-sw2` landed ~157px apart and their icons, node labels and link end labels
  merged into an unreadable pile. `render/drawio.py` now scales the finished layout up
  until the closest two nodes have room for the labels the diagram actually carries, so
  the STP view spreads out while the short-labeled L2 view stays compact. Kamada-Kawai
  (`kk`) is kept as the layout algorithm — of N2G's alternatives, `fr` packs the closest
  pair tighter, `drl` collapses a graph this size almost to a point, and `rt` flattens
  it into rows of touching nodes. The committed example diagrams are regenerated.

## [0.3.0] - 2026-08-11

Neighbor identity resolution, port-channel awareness, and the STP view working against
real captures. Supersedes the `0.3.0rc1`..`0.3.0rc6` pre-releases; the entries below
cover everything that changed since `0.2.0`.

### Added

- `nettopo l2 --link-mode physical|port-channel`. `port-channel` draws one link per
  bundle instead of one per member port: the ends that have a port-channel are labeled
  with it (`Po150`), and the member interfaces are carried in the link's draw.io hover
  tooltip. `physical` (the default) keeps the previous behavior, one link per discovered
  adjacency. Links with no port-channel on either end render identically under both
  modes. The two modes write different filenames
  (`l2_full.drawio` vs `l2_full_port-channels.drawio`), so both can be generated into
  one output directory.
- `parsing/etherchannel.py`: parses `show etherchannel summary` (IOS/IOS-XE) and
  `show port-channel summary` (NX-OS) into `Interface.po_id` on every bundle member and
  `Interface.po_members` on the port-channel interface. These two model fields existed
  since Phase 1 but no parser populated them, which is why port-channel grouping in the
  L2 view had been a no-op against real captures; it now works from real data, and the
  `po_id`/`po_members` columns in `interfaces.csv` are no longer always empty.
- `DiagramLink.tooltip`, rendered by `render/drawio.py` as the link's draw.io `tooltip`
  attribute.
- `Device.platform` is now filled in for devices we hold no capture of, from the platform
  their neighbors report over CDP (`Platform: cisco ISR4331/K9`) or LLDP. That value was
  already parsed into `Link.remote_platform` and exported in `neighbors.csv`, but only
  `show version` ever wrote `Device.platform`, so the `platform` column of `devices.csv`
  was always empty for neighbor-only devices. A source device keeps whatever its own
  `show version` produced, and CDP outranks LLDP when both describe the same neighbor.
  `Device.model` is unchanged: it stays a `show version`-only field, since CDP platform
  strings are not always a hardware model (`VMware ESXi`).
- `Device.mgmt_ip` is now populated, from the management address a neighbor advertises
  over CDP or LLDP, and a `remote_mgmt_ip` column joins `neighbors.csv`. The field
  existed since Phase 1 but nothing wrote it, so the `mgmt_ip` column of `devices.csv`
  was always empty. Every device takes this value, source or not, since no parser reads
  a management address out of a device's own capture.
- `parsing/cdp.py` reads CDP's `Management address(es)` block itself instead of trusting
  ntc-templates' `mgmt_address`. The `cisco_ios` template fills that field from `Entry
  address(es)` — the neighbor's *connected interface* address, routinely a transit link
  rather than the management network — and never reads the management block; the
  `cisco_nxos` template does read it. Both spellings (`Management`/`Mgmt address(es)`,
  `IP address`/`IPv4 Address`) are now handled, with the template's value kept as a
  fallback for neighbors that advertise no management address.
- `nettopo --version` prints the installed version and exits. The string comes from the
  installed distribution's metadata (the same source as `nettopo.__version__`), so it
  cannot drift from `pyproject.toml` — but it does need `pip install -e .` re-run after a
  version bump for the metadata to catch up.
- The STP view now draws devices it holds no capture for. A switch seen only in a
  neighbor's CDP/LLDP output is drawn with a dashed border and labeled with its name
  alone (there is no bridge data to show for it), and is reached only through a port that
  is not Edge/PortFast — the filter that keeps phones, access points and servers out of a
  spanning-tree diagram. Requires the new `link_type` field, parsed from the Type column
  of `show spanning-tree` and exported as `link_type` in `stp.csv`.
- The STP view identifies a root bridge that sits outside the captures, by matching the
  root address `show spanning-tree` reports against the chassis address an LLDP neighbor
  advertises (`Link.remote_chassis_id` -> `Device.chassis_id`, both new; CDP advertises no
  chassis address, so a CDP-only neighbor cannot be identified this way). The match is
  exact, so it either names the root or reports nothing — it can never highlight the wrong
  switch. With no match, the run warns and highlights nothing. `StpBridge.root_mac` and
  `StpVlan.root_mac` are new, and `root_mac` is a new `stp.csv` column.
- `nettopo stp` now logs node and link counts per diagram (matching what `nettopo l2`
  already reported) and warns when a diagram has several nodes and no links at all. Each
  dropped link is logged at DEBUG with the reason and the resolved port names, which
  distinguishes a missing bundle table from a VLAN that is simply not allowed on a trunk.
- `Device.serial`, populated from a source device's own `show version` or recovered from
  the serial an NX-OS neighbor advertises inside its name, and exported as a new column
  in `devices.csv`.

### Fixed

- **STP diagrams drew arrowheads on their links.** Spanning-tree links are not directed,
  and the arrow pointed nowhere meaningful: the STP view orders an edge's ends by device
  name, so it ran from the alphabetically-earlier device to the later one. N2G substitutes
  its default link style for whatever style it is handed rather than merging the two, so
  the color the STP view sets for port state was displacing the default's `endArrow=none`.
  Only STP was affected, since it is the only view that colors links — and only its colored
  links, leaving transitioning (listening/learning) links arrowless while their neighbors
  had arrows. Links now spell out `endArrow=none` everywhere. Colors, stroke width, per-end
  labels, root highlighting and L2 output are all unchanged.
- **Link labels came loose from their links in `0.3.0rc4`.** That release placed each end
  label as a free-standing text cell on the canvas, which draw.io reads as a *node*: any
  Arrange layout (Circle, Tree, Organic) laid the labels out along with the devices,
  scattering every label away from the link it belonged to. Labels are back to being
  children of their edge, which is what makes draw.io treat them as edge labels — they move
  with the link and the Arrange layouts leave them alone. They are still one label per end;
  only the attachment changed.
- **The STP view drew no links at all on any network whose switches are joined by
  port-channels.** Nodes rendered, the root was highlighted, and not one line appeared
  between them. `show spanning-tree` reports a bundle as a single logical port (`Po1`)
  while CDP/LLDP report its physical members (`Gi1/0/1`), so the lookup that joins the two
  sources missed on both ends of every bundled link and dropped it silently. Member
  interfaces are now resolved through `Interface.po_id`/`po_members` and collapse into one
  link per bundle, labeled with the bundle name and carrying the members in its tooltip —
  spanning-tree sees one logical port, so drawing one line per member would misrepresent
  it. This needs `show etherchannel summary` (or `show port-channel summary`) in the
  captures; without it there is nothing to map `Gi1/0/1` onto `Po1`, and the new warning
  says so.
- `parsing/spanning_tree.py` dropped any port row whose state carries an inconsistency
  marker (`Desg BKN*ROOT_Inc`, `Desg BKN*PVID_Inc`), because the pattern required
  whitespace where IOS glues the reason directly onto the state. The bridge was still
  created, so the effect was the same silent one as above: a node with no link through
  that port. `StpState.BKN` is new, and a broken port now colors its link as blocking.
- A device reported under different names by CDP and LLDP is no longer split into
  several nodes. NX-OS advertises its name with the chassis serial appended
  (`nxos-core1(FDO21120U5D)`, by default as its CDP device id) where the other protocol
  reports it plain, and either protocol may use an FQDN where the other uses the short
  name; each spelling used to become its own `Device`, so one switch was drawn several
  times with parallel links. The new
  `utils/hostnames.py` correlates every reported spelling before the model is built, and
  `ingest/model_builder.py` (now a three-phase build, since correlation needs to see all
  spellings at once) rewrites each discovered link onto the canonical name. Two
  same-labeled devices in different domains (`sw1.site-a.com`, `sw1.site-b.com`) are
  deliberately *not* merged.
- `parsing/lldp.py` no longer reports a neighbor's configured interface description as
  its interface name. NX-OS puts free text in LLDP's "Port Description" field, which
  correlated with nothing and left a second, mislabeled edge for every link that CDP had
  already described. The field that actually looks like an interface name now wins, via
  the new `looks_like_interface()` in `utils/interfaces.py`.
- `ingest/model_builder.py` keeps a single link per local port and neighbor, preferring
  the CDP report over the LLDP one, so a link described by both protocols renders once.
- `nettopo.__version__` is read from the installed distribution metadata instead of being
  hardcoded. It had been left at the Phase 0 scaffolding's `0.0.1` through v0.1.0 and
  v0.2.0, disagreeing with `pyproject.toml` and therefore with what was on PyPI.

### Changed

- Link labels are no longer merged into one string at the middle of the link. Each end
  keeps its own label at its own end, so a link between two switches that both reported
  spanning-tree shows `Po110 designated/forwarding` at one end and `Po110 root/forwarding`
  at the other, instead of the two concatenated into
  `Po110 designated/forwarding — Po110 root/forwarding`. The L2 view gets the same
  treatment for its interface labels. `lucidify` now leaves N2G's label cells parented to
  their edge and only normalizes the `relative` flag N2G writes as `-1` on the target end,
  which draw.io tolerates but a stricter importer reads as "not relative at all".
  `--no-lucidify` still leaves N2G's raw output alone.
- A bundle is now identified by the *device pair* rather than by the direction its
  members happen to be stored in, so members reported from opposite ends still collapse
  into one link.
- `NetworkModel.port_channel_name()` replaces the L2 view's private bundle resolver, which
  the STP view needed as well; `Link.oriented()` likewise replaces the copy each view kept
  of the same re-pointing logic.

## [0.2.0] - 2026-08-07

Phase 4: STP.

### Added

- `parsing/spanning_tree.py`: parses `show spanning-tree` (per-VLAN Rapid-PVST) with
  its own regexes rather than ntc-templates -- the shipped template only captures the
  port role/state/cost table, not the "Root ID"/"Bridge ID" blocks the data model
  needs (bridge priority, MAC, root-election flag). One parser serves both IOS and
  IOS-XE, which emit this command identically; `tests/fixtures/spanning_tree/` carries
  a fixture for each. `ingest/model_builder.py` wires per-device captures into
  `model.stp`, keyed by VLAN.
- Phase 4 STP view: `views/stp.py` builds one render-ready `Diagram` per VLAN, or per
  topology group under `--group-mode strict|topology` (grouped VLANs are guaranteed by
  `model/grouping.py`'s fingerprints to render identically, so the view renders one
  representative VLAN per group and names the output file with every VLAN id it
  covers, e.g. `stp_vlans-10_20_30.drawio`). The root bridge is highlighted; links are
  colored by forwarding/blocking port state and labeled with role/state at each end,
  cross-referencing `model.links` for physical adjacency since `StpPort` alone doesn't
  record which device is on the other end of a port.
- `views/diagram.py`: `DiagramNode.highlight` and `DiagramLink.color` let a view drive
  root-bridge and port-state styling without `render/` knowing about STP concepts.
  `render/icons.py` and `render/drawio.py` apply them as draw.io style overrides.
- `nettopo stp -i <dir> [--vlan N | --group-mode per-vlan|strict|topology] [--all]` is
  now a real command, requiring either `--vlan` or `--all` (there is no single sensible
  default among a potentially many-VLAN model), writing to `output/stp/`.
- `export/csv_export.py` now writes real `stp.csv` rows (one per device/interface),
  including both base and effective bridge priority.
- Security: `tests/test_no_network.py` extended to cover `nettopo stp`.

### Known limitations (carried forward)

- Cisco icon fidelity under a real Lucidchart import still has not been manually
  validated (see the Phase 3 entry below) -- Phase 4 proceeded on the same rendering
  approach without it, since that validation needs interactive Lucid access this
  project's automation doesn't have. Checklist in `docs/architecture.md`.
- MLAG/port-channel grouping in the L2 view still has no data source (unchanged from
  Phase 3).

## [0.1.0] - 2026-08-07

First usable PyPI release.

### Added

- Phase 3 L2 view: `views/l2.py` builds a render-ready `Diagram`
  (`views/diagram.py`) from the model, honoring `--endpoints all|network-only`
  (network-only keeps a device if `is_source` is true or a neighbor's CDP/LLDP
  reported it with Router/Switch capabilities) and attaching interface labels to
  both ends of every link. Links whose local interface is a port-channel member are
  grouped into one rendered link per port-channel (MLAG), though no shipped parser
  populates `Interface.po_id` yet, so this is currently a no-op against real captures
  — see `docs/architecture.md`.
- `render/drawio.py` (the only module importing N2G), `render/icons.py`
  (`DeviceRole` -> real `mxgraph.cisco.*` draw.io stencil shapes, verified against
  jgraph/drawio's `Sidebar-Cisco.js`), and `render/lucidify.py` (collapses N2G's
  per-end `src_label`/`trgt_label` child cells, which Lucidchart's importer mangles,
  into a single label on the link itself; also cleans the doubled semicolons N2G's
  XML templates leave in style strings). Applied by default; `--no-lucidify` skips it.
- `nettopo l2 -i <dir> [--endpoints all|network-only]` is now a real command, writing
  `output/l2/l2_full.drawio` or `output/l2/l2_network-only.drawio`.
- `ingest/model_builder.py` now infers `Device.role` from CDP/LLDP capabilities
  reported by neighbors (a device's own capture never reports its own capabilities),
  applied before cross-discovery link deduplication so both ends of a
  source-to-source link get a role. This is what gives `render/icons.py` real data to
  key off of.
- `.github/workflows/publish.yml`: on tag `v*`, builds sdist+wheel and publishes to
  PyPI via trusted publishing (OIDC) — no long-lived token in secrets. PyPI name
  availability for `nettopo` verified (see `PROJECT_SPEC.md` section 1).
- Security: `tests/test_no_network.py` extended to cover `nettopo l2` now that it
  pulls in N2G/igraph.
- Phase 2 L2 parsing + CSV: `ingest/base.py` (`DataSource` interface) and
  `ingest/files.py` (`FileDataSource`, reads `utf-8-sig`, identifies source devices via
  their prompt line); parsers for `show version`, `show cdp neighbors detail`,
  `show lldp neighbors detail`, `show ip interface brief`/`show interfaces`, and
  `show vlan brief` via ntc-templates; `ingest/model_builder.py` wiring ingestion and
  parsing into a populated `NetworkModel`, including CDP/LLDP FQDN-vs-short-hostname
  neighbor correlation and cross-discovery link de-duplication; `export/csv_export.py`
  writing `devices.csv`, `interfaces.csv`, `neighbors.csv`, `vlans.csv` (plus
  header-only `stp.csv`/`hsrp.csv`/`bgp.csv` pending Phases 4-6). `nettopo parse -i
  <dir>` is now a real command. Security: `utils/paths.py` (filename sanitization and
  output-root resolution against path traversal) and CSV formula-injection escaping;
  `tests/test_no_network.py` proves the full `parse` run opens no sockets.
- Phase 1 foundations: the interface-name normalizer (`utils/interfaces.py`,
  PROJECT_SPEC.md section 5), the normalized data model dataclasses and enums
  (`model/entities.py`, section 6), and the STP/HSRP grouping fingerprint functions
  for `per-vlan`/`strict`/`topology` group modes (`model/grouping.py`, section 6).
  Nothing user-visible yet — the CLI still reports "not implemented yet".
- Phase 0 scaffolding: repository layout under `src/nettopo/` matching
  `PROJECT_SPEC.md` section 3, an empty CLI skeleton (`nettopo --help` and all
  subcommands parse but report "not implemented yet"), `pyproject.toml`, and a
  GitHub Actions pipeline running `ruff check`, `ruff format --check`, `mypy`,
  and `pytest --cov` on every push and pull request.
- `CLAUDE.md` engineering conventions, `docs/architecture.md`, and this changelog.

### Known limitations (tracked for Phase 4)

- Cisco icon fidelity under a real Lucidchart import has not yet been manually
  validated — checklist in `docs/architecture.md`.
- MLAG/port-channel grouping in the L2 view has no data source yet (see the Phase 3
  L2 view entry above) and is a no-op against real captures.

## [0.0.1] - 2026-08-06

- Initial repository scaffolding. No packaged release yet.
