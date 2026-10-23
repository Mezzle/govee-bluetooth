# BLE protocol recheck — 2026-10-23

Reviewed the working tree against freshly fetched upstream heads:
[govee-ble-local e052b17](https://github.com/Brady-Woods/govee-ble-local/tree/e052b1732d42019675437f91efca0bf870f5d190)
and [ha-govee-led-ble c7587b0](https://github.com/teh-hippo/ha-govee-led-ble/tree/c7587b005918e9c5ca0b64572b7ecaf740aa7781).
These are community reverse-engineering sources, not a manufacturer BLE specification.
App-derived layouts establish what the app builds; they do not establish that every
model or firmware accepts the frame. The source review below is separate from the Discoball bench session recorded at
the end of this note.

## Actionable findings

1. **Scene IDs are little-endian 16-bit values (fixed).** Before this review,
   `scene_frame` rejected IDs above 255 and `_parse_mode` discarded the high
   byte. Both now handle the two-byte code, with regression coverage. Existing IDs below 256 produce the same padded frame.
   This does not enable library scenes that require an upload before activation.
   Evidence: upstream [scene-select wire schema](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/govee_ble_frame.ksy#L578)
   attributes the layout to `SubModeScenes.java:59-77`; its `effect` field is `u2le`.

2. **Keep timing claims narrower than the evidence.** README says firmware
   disconnects after about ten silent seconds. The reference establishes a
   heartbeat operation, but does not establish a universal ten-second device
   timeout. Document two seconds as this client's heartbeat interval and leave
   device-specific idle limits as unverified.
   Evidence: upstream [connection and heartbeat reference](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md).

3. **Zero Kelvin in a `0x15` reply is not universal proof of RGB mode.** The
   existing detailed reference already records H6199 pact 2/1 returning zero
   after a successful CCT write. Its README's unconditional "kelvin 0 meaning
   colour mode" is stronger than that evidence. Keep this distinction when
   clearing retained white state or probing a dialect: an incomplete reply
   cannot establish the rendered colour. RGB is absent from the shared
   `0x15` mode reply; segment reads are the separate source of RGB evidence.
   Evidence: repository `docs/govee-ble-protocol.md` §6.1, and upstream
   [shared mode-read layout](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md#164-status-reads--mode-replies).

4. **Do not promote H6199 pact-1 hypotheses into hardware facts.** The
   historical HGL note explicitly says its `0x0B` writer with Kelvin slots and
   brightness range 20–254 remain app-derived candidates awaiting independent
   captures and owner qualification. The current speculative schema retains
   that warning. The project's detailed reference correctly marks the frame
   candidate in §6, but its brightness table omits the same qualification.
   Keep the mask-direct `0x0B` builder rather than replacing it globally with
   the H6199 candidate. Evidence:
   [historical qualification note](https://github.com/teh-hippo/ha-govee-led-ble/blob/b2cd788/docs/h6199-pact1.md)
   and [current speculative schema](https://github.com/teh-hippo/ha-govee-led-ble/blob/c7587b005918e9c5ca0b64572b7ecaf740aa7781/tools/ble/kaitai/speculative/h6199_pact1_wire_write.ksy).

5. **Keep colour parsing tied to a device profile.** `0x0D` is a dialect byte,
   not a guarantee that every read mirrors the H6181 write body. Likewise,
   `0x15` has compact and extended write forms, different operation selectors,
   and family-specific mode-read fields. The fixed `FF 7F` mask selects fifteen
   segments, not every possible layout. Before widening supported models,
   record advertised pact, firmware, read layout, native brightness scale and
   mask width alongside the submode. Evidence: upstream
   [colour layouts and masks](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md#16-readwrite-payload-layouts--confirmed-against-decompiled-classes).

6. **`AA 05 01` is the app-compatible query, not universally a required selector.**
   Current upstream §16.4 records several firmware handlers ignoring that
   selector. Keep sending `01`; soften the comment that it is universally
   required. This is documentation precision, not a reason to change bytes.
   Evidence: [current reference](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md).

## Making the existing reference easier to maintain

The 2026-10-19 reference remains a useful historical snapshot, but its `/tmp`
paths and line numbers require the author's clones. Preserve its pinned commit
IDs and add durable source links when touching a section. Upstream has since
renamed `govee_ble.ksy` to several focused schemas, including
`govee_ble_frame.ksy`, and removed HGL's historical prose ledgers from its
current tree. Link old ledgers at their historical commit instead of assuming
those paths exist on the default branch. Its §13 list is a historical backlog:
item 4's guessed `0x0B` RGB parser has already been removed in this project's
reviewed code. Reconcile that list before treating it as a current defect list.

The checksum, 20-byte single-frame format, service/write/notification UUIDs,
power opcode and the app-compatible power/brightness/mode queries remain
consistent with the fetched reference. Those checks do not qualify untested
hardware, encryption, scene uploads or segment control.

## Changes and validation

- Preserve the manual brightness-scale option when a status read changes the
  detected colour dialect. Previously the live profile silently switched scales
  until reload. A regression test covers read-back and subsequent brightness writes.
- Encode/decode 16-bit scene IDs and show both bytes in the rig's outgoing log.
  Existing scene tables are unchanged; arbitrary scene IDs are not hardware qualified.
- Remove the universal ten-second idle-timeout claim and soften the mode-selector
  requirement. Qualify H6199 pact-1 brightness as app-derived.
- Correct the characterisation wizard and rig instructions: Pi `btmon` records
  the Pi adapter's HCI traffic, not a phone's separate BLE connection. Pairing
  authorisation capture must happen on the phone running the app.
- Initial local verification: 100 tests passed; Ruff lint, format check, shell syntax and
  whitespace checks passed using the existing `/tmp/govee_venv` (Python 3.14.4).

## Discoball bench session — 2026-10-23 UTC

Connected over Tailscale SSH as `homelab-operator`. A twelve-second scan found one
light, `ihoment_H6159_8E17`, address `A4:C1:38:9C:8E:17`, RSSI −54 dBm. The installed
rig reported pact 1/1 and plaintext transport. No other Govee strip was observed.

Read-only status returned:

- Power off: `aa010000000000000000000000000000000000ab`.
- Raw brightness 26: `aa041a00000000000000000000000000000000b4`.
- Legacy RGB `(214, 226, 255)`: `aa0502d6e2ff0000000000000000000000000066`.
- Firmware-version query timed out; no firmware version is asserted here.

Re-sent the same brightness and RGB values without powering the strip on. Both
read-backs matched, and RGB produced the mode-write ACK
`3305000000000000000000000000000000000036`. Since the values were unchanged,
matching reads alone do not prove a state transition. The verify connection
needed retries before succeeding. No active dialect probe or scene experiment ran.

**Transport observation (before refreshing the branch from main):** this light advertises control properties
`read` and `write-without-response`; the installed rig selected writes without
response and worked. The starting checkout forced writes with response for commands and queries.
Newer main already selects the method for those writes; the follow-up change
retains that selection, applies it to heartbeats, rejects known unwritable
characteristics and reports the selection in diagnostics. The bench therefore verifies the wire frames through the
installed rig, not the current Home Assistant connection path. Live integration verification through BlueZ and an HA Bluetooth proxy remains
separate from these rig observations. The remote rig differs from the local checkout, and `git`
is absent on Discoball, so its revision could not be identified. No remote files
were replaced.

### TV bias light follow-up

After the owner made the bias light available, another scan found
`GBK_H6181_FB1E` at `60:74:F4:6A:FB:1E`, RSSI −68 dBm, advertised pact 10/1,
plaintext. Status confirmed firmware `2.03.11`, power on, raw brightness 100,
and `0x0D` RGB white `(255, 255, 255)` with zero Kelvin.

On one connection, the installed rig wrote and read back each of:

1. Raw brightness 30 (`aa041e00000000000000000000000000000000b0`).
2. RGB `(18, 52, 86)` (`aa050d12345600000000000000000000000000d2`).
3. White at 4300 K (`aa050dffffff10ccffd5b3000000000000000018`).
4. Original RGB white (`aa050dffffff000000000000000000000000005d`).
5. Original brightness 100 (`aa046400000000000000000000000000000000ca`).

All five writes received an accepted ACK and matched subsequent read-back.
The light remained powered on; final mode and brightness matched its initial
state. These changed-value echoes provide stronger evidence than the earlier
H6159 same-value check. Brightness 30 and 100 are consistent with the known
percentage profile; no value above 100 was sent to discriminate scale limits.

This H6181 also advertises only `read` and `write-without-response` on `2b11`.
The rig selected writes without response. Connection setup retried several
times before succeeding; there were no dropped writes during the sequence.

## Follow-up implementation

The implementation branch was rebased onto `origin/main` at `58093df`, which
already contained write-method selection for commands, queries and probes, and
phone-side capture tooling. Those features were preserved. The follow-up:

- Uses the selected procedure for heartbeats in HA and the rig (the rig retains
  its existing cipher-session write policy).
- Exposes the last connection's control properties and `write_response` in HA
  diagnostics. These are observed transport facts, not inferred model defaults.
- Rejects a known control characteristic with neither write capability, closing
  the connection before sending commands.
- Tests response-only, command-only and dual-mode characteristics across status,
  normal commands, active probes, heartbeats and reconnection, using a fake that
  rejects unsupported write procedures.
- Adds sanitised real status frames under
  `tests/fixtures/bench_status_2026_10_23.json`, with decoding regression coverage.

Broader device-profile work remains capture-led: do not infer all write formats,
brightness limits or segment masks solely from the reported colour submode.
The independent additional probing session owns the physical lights and records
further results in its own worktree.

Final local validation on refreshed main: **192 tests passed**, Ruff lint and
format checks passed, shell syntax and whitespace checks passed. Live HA proxy
validation is pending the independent probing session's deployment discovery.
