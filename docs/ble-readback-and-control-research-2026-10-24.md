# BLE readback, diagnostics and independent control

Research date: 2026-10-24. Documentation only; no hardware contacted, no BLE
connections, and no app/account access. This report correlates three delegated
source investigations with the integration at commit
`e69b84693dbaddfbf4abd3b38d27238fc1b887d7`. Recommendations below are proposed
work, not implemented features or newly qualified hardware support.

## Findings in brief

**Yes, there is substantially more local information to collect on supported
families.** The biggest opportunities are separate segment-colour reads,
mode-specific settings, device identity/topology, and event notifications.
There is no universal query that returns every capability or all rendered state.

- **Colour missing from a Kelvin-only reply:** use the family's separate
  `AA A5`/`AA A2` colour pages, or its `AC` bulk status. These can give segment
  RGB, and sometimes per-segment brightness. They are not interchangeable
  across all models and do not establish continuous live animation telemetry.
- **Modes:** scene/DIY identifiers and music/video parameters can make state
  more informative and enable matching controls. A current identifier does not
  download the effect definition, palette, full scene catalogue or camera feed.
- **Diagnostics:** device versions, topology, protocol identity and host
  communication health are useful. No general electrical/thermal sensor API
  for these lights was established by the reviewed evidence.
- **ICs:** read qualified counts as topology diagnostics; use them to inform
  addressing and segment masks. IC detection/calibration is a separate active
  operation, not a routine health read. `33 46` is destructive on some families.
- **Independence:** local reads can improve control and reduce model guessing.
  Full app independence additionally requires local authentication, effect
  assets/authoring, and persistent device profiles; readback alone cannot
  recover all vendor assets or guarantee factory-fresh onboarding on every SKU.

Supporting citations and family qualifications are in the three source reports
below; this section summarizes their findings rather than extending their scope.

## Reading guide and evidence

- [Readback and mode state](ble-readback-source-research-2026-10-24.md).
- [Device diagnostics and IC/topology](ble-diagnostics-source-research-2026-10-24.md).
- [Implications for independent control](ble-control-correlation-source-research-2026-10-24.md).

The source reports distinguish captured device behaviour, firmware analysis,
app-derived layouts, existing implementation, and inference. A command appearing
in the app is not proof that all lights implement it. A timed-out read is not a
zero value, an absent component, or definitive proof of lack of support.

The reports retain their exact inspected revisions. The diagnostics report uses
Brady-Woods `e052b17`; the control investigation additionally checked newer
`276b14b6`. Read their pinned links rather than assuming all reports describe the
same upstream HEAD. The newer investigation also identifies `AA EF` pact
readback on named firmware: it reports a pact, not a negotiated protocol or a
universal feature catalogue.

## What the integration does today

The premise needs a small correction: normal polling already reads state without
first changing the light. [`async_update`](../custom_components/govee_bluetooth/device.py)
queries power (`AA 01`), brightness (`AA 04`), mode (`AA 05 01`), and firmware
(`AA 06`, once per connection). The default polling interval is 120 seconds;
the two-second heartbeat runs only while connected. A protocol read is sent via
a GATT write, then answered through notifications: that transport write is not a
request to change the displayed light state.

The larger gap is between protocol helpers and normal integration use:

- [`protocol.py`](../custom_components/govee_bluetooth/protocol.py) already builds
  and parses hardware-version, IC/segment-count, light-count and segment-page
  queries. Normal device polling does not collect these fields.
- [`FrameExchange`](../custom_components/govee_bluetooth/exchange.py) validates
  frames and distinguishes application ACKs, but routes parsed state only from
  `AA` frames. It does not reassemble bulk status or decode `EE` pushes.
- [`_on_notify`](../custom_components/govee_bluetooth/device.py) applies only
  power from unsolicited parsed replies. Other unsolicited state is logged but
  not merged into the live light state.
- [`coordinator.py`](../custom_components/govee_bluetooth/coordinator.py) uses
  advertisements for reachability, not light-state decoding.
- [`diagnostics.py`](../custom_components/govee_bluetooth/diagnostics.py) offers
  a diagnostics download. [`const.py`](../custom_components/govee_bluetooth/const.py)
  registers only the light platform: there are no diagnostic sensor entities.
- Colour state has no per-field observation timestamp or source. Writes update
  it optimistically, while partial mode reads can leave older RGB/CCT values
  present. More reads need explicit freshness and provenance to avoid presenting
  retained or computed colour as an observation.

## What our existing hardware evidence actually establishes

The saved 2026-10-23 census frames establish hardware-version replies for H6159
(`1.00.02`) and H6181 (`2.01.01`), as well as firmware and mode replies. In these
particular sessions, `AA 40`, `AA 0F` and `AA A5 01` had no recorded responses.
That is a reason to qualify richer features on appropriate RGBIC hardware later,
not to advertise these counts or pages on those two lights now. The H6159 also
missed the power query in that session despite answering other queries.

Sources: [H6159 recorded frames](bench-evidence/2026-10-23-firmware/h6159.jsonl),
[H6181 recorded frames](bench-evidence/2026-10-23-firmware/h6181.jsonl).
The `secret_present: false` summary in those files accompanies an unanswered
secret query; it must not be reinterpreted as proof that no secret exists.

## Home Assistant presentation

Proposal: put firmware/hardware versions in device registry metadata; expose
supported counts and useful health indicators as diagnostic entities. Keep raw
pages, protocol bytes, per-family decode details and recent frames in the
diagnostics download. Put actual user controls in the light or suitable
configuration entities. Home Assistant defines diagnostic entities for information
about a device, and recommends disabling noisy or rarely useful entities by
default ([entity documentation](https://contributors.home-assistant.io/core/entities/entity-class/),
[entity categories](https://contributors.home-assistant.io/core/quality-scale/entity-category/)).

Useful initial diagnostic entities, subject to supported data being available:

- IC count, logical segment count, bulb/section count, with distinct names.
- Last successful state observation timestamp and last command confirmation.
- RSSI with scanner/source provenance, and device reachability.
- Query failures, reconnects, command rejection count and readback completeness.
- Protocol/profile identity and whether state is observed, retained or optimistic.

The latter counters and freshness fields are host diagnostics we would add, not
physical sensors inside the light. Do not invent power, energy, voltage, chip
temperature, microphone level or per-LED health telemetry without a decoded,
model-qualified source. Counts are not health measurements. IC checks may be
active operations and must not become automatic sensor polling.

Entity state should come from a shared cached snapshot, not a BLE query per
entity. Keep unsupported, unanswered, stale and zero distinct. A missed optional
query should not make an otherwise working light unavailable.

## Proposed collection and control architecture

This is a design recommendation derived from the source investigations, not a
claim that every light supports the following operations.

1. **Describe the protocol per device profile.** Store advertisement pact,
   firmware, read layout, write layout, brightness scale, topology interpretation,
   page record size/count, status transport and notification decoders separately.
   A reported mode byte is evidence about the read layout, not a universal write
   dialect negotiation. The current automatic submode switch should acquire
   family-specific exceptions before extending it to more RGBIC lights.
2. **Collect identity and topology infrequently.** On a suitable connection, read
   supported identity/count fields once, cache them with provenance, and refresh
   after firmware/profile changes or an explicit diagnostics refresh. Do not
   repeat unanswered optional queries on every poll. No exhaustive opcode scan.
3. **Collect state according to mode and family.** Retain the basic power,
   brightness and mode poll. Add either direct segment pages or a supported bulk
   status transaction, plus only the current mode's useful settings. Bound page
   walks and total time; do not connect independently for every sensor.
4. **Decode pushes into the same state model.** Apply qualified `EE` events and
   unsolicited `AA` state as partial updates. Scope bulk assembly and pending
   requests to the connection; reject corrupt/incomplete bursts. Push support
   complements polling, since a short-lived BLE connection cannot receive
   events while disconnected.
5. **Keep observation separate from intent.** Store desired state, device-reported
   state, timestamp and confirmation separately. An ATT write success confirms
   transport; an application ACK confirms acceptance; subsequent state readback
   confirms stored parameters. None alone measures the emitted light. Mark an
   RGB value calculated from Kelvin as derived. In multi-colour modes preserve
   segment data rather than asserting that the entire strip has one RGB value.
6. **Use topology to control deliberately.** Validated segment counts and page
   mapping can drive mask construction, bounds checks, per-segment controls and
   better restoration. Distinguish physical ICs, addressable points, logical
   groups, zones and bulbs: a raw IC count need not equal the writable segment
   count. Account for hardware-specific mask widths and controller operations.
7. **Make offline control reproducible.** Preserve the actual scene/DIY payload,
   code, checksum, transfer format and compatible profile locally; preserve
   user-authored effects as local data. Store required secrets privately with
   a recovery/backup path. A scene ID or current-mode reply cannot reconstruct
   a missing effect library. Existing cipher implementations do not by
   themselves establish that the HA device lifecycle performs authentication.

The practical sequence is richer state collection first, then topology-aware
segment control, then mode-specific music/video/DIY/settings controls with local
assets. This produces useful offline behaviour incrementally and gives later
control work better evidence.

## Parser issues to resolve before using the extra data

The existing helpers are a useful starting point, but should not be promoted
unchanged into universal collectors:

- `_segment_records` assumes four-byte brightness/RGB records and stops at the
  first all-zero record. Upstream profiles also describe three-byte RGB records.
  A black/off segment can legitimately contain zeros; use a qualified record
  count and padding rule so a dark segment cannot hide subsequent records.
- The current IC parser extracts one unsigned count/segment pair. Upstream
  describes signed counts and a second strip pair on applicable devices. Preserve
  the raw reply, validate values and apply the correct profile before summing or
  sizing masks. A second pair must not be guessed from unrelated padding.
- Bulk status needs reassembly before state parsing. The final `FF` packet can
  contain actual data; treating it as an empty terminator truncates state.
- Direct `AA A5` pages and bulk `AC` colour records are different transports.
  Adding only bulk read will miss devices whose app reads direct groups.
- The current `0x0D` mode parser follows the locally observed layout; other
  profiles need their own read layout. Likewise, zero Kelvin in a partial `0x15`
  reply is not universal proof that the light switched out of white mode.
- Older notes describe the BGC encryption version as byte zero. The qualified
  format schema places a format discriminator first and encryption version at
  byte one. Preserve raw bytes and select a validated format; absence of this
  characteristic does not prove plaintext. See the diagnostics report's BGC
  evidence and the control report's H1270 implementation citation.

Two qualifications from the newer readback investigation are especially relevant:
upstream narrative and executable readback code disagree on brightness-bearing
versus RGB-only pages for H6047/H6641; their page grammar needs qualification
before implementation. Also, the older generic bit-1 description of `EE 30`
zone state conflicts with newer profile-specific notification branches, including
bit-0 forms. Do not reuse a write-side state mask to decode notifications.
See [readback research §§2 and 4](ble-readback-source-research-2026-10-24.md).

Primary source: pinned upstream
[state grammar](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/govee_state.ksy),
[status stream grammar](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/govee_status_stream.ksy),
and [bulk/status reference](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md#45-single-request--multi-reply-status-read-0xac).
These are reverse-engineering artifacts with app and firmware attributions, not
a manufacturer promise about every SKU.
