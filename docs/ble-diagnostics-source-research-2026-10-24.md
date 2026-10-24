# Govee BLE diagnostics and topology source research — 2026-10-24

## Scope and evidence

Source inspection only: no hardware, Bluetooth operations, SSH, device-network access, or cloud/app account access. No commands described below were transmitted. This delegated investigation wrote only this document and did not delegate further.

Upstream evidence is pinned to **Brady-Woods/govee-ble-local `e052b1732d42019675437f91efca0bf870f5d190`**. Its schemas are primary artifacts of that project's reverse engineering, with `doc-ref` pointers into decompiled app classes and firmware handlers. They are **not an official Govee BLE specification**, and app code is not proof that a particular device accepts a request. Firmware-derived claims apply to the named image/model; this research independently verified source contents, not their hardware behavior. The existing `/tmp/govee_ref` checkout was older (`374a3a3…`); the cited revision was fetched separately over public HTTPS. Prior local bench notes are historical evidence, not new observations here.

Pinned sources:

- [Protocol reference](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md), especially §§2, 5, 15, 18, 21.
- [Common types and command identities](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/govee_common.ksy).
- [State/read-reply schema](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/govee_state.ksy).
- [Frame/query/write/push schema](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/govee_ble_frame.ksy).
- [BGC characteristic schema](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/govee_bgc_info.ksy).
- [Per-device capability and decode catalog](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/devices.yaml).

## Identity and topology readback

The following summarizes source layouts, not a universal query allowlist. Hex prefixes omit padding/checksum. `AA` queries still require a GATT write; “read-only” describes intended application behavior, not absence of radio writes.

| Area | Source-supported interpretation | Evidence |
|---|---|---|
| Firmware/hardware | `AA 06` reports version text. `AA 07` selectors include `02` device ID, `03` hardware ASCII, `04` software ASCII, `07` DSP, `0A/0B` MCU versions. Selector availability varies; some MCU replies contain no data. | Common schema `device_info_selector`; frame schema `device_info_query` |
| IC count `40` | `AA 40`/status TLV `40`: signed big-endian 16-bit IC count plus segment byte; a second count/segment pair can follow. App pair readers reject suspicious counts ≥`0x1000`; they do not automatically swap bytes. H6047 can report segment zero. | State schema `ic_count` |
| Section count `41` | H6062 `AA 41` and `EE 21` carry signed IC count and signed section byte. Other families use `41` for power memory `[02][mode]` or device info. | Common schema `section_count`, `form_41`; state schema `cmd_41_state`; frame schema `notify` |
| Check/refresh `42/43/46` | Catalog names refer to IC refresh, IC amount check and IC detection, but do not establish a universal health response or safe payload. H60A6 `42` is calibration; `46` resets H60A1/H60A6 and resets network/Matter on H6008. | Common schema command enum `42`, `43`, `46` |

Those layouts describe **device-reported topology**, not electrical measurements or proof that every LED works. Suggested representation: retain each strip's count, group/section count, raw bytes, decode family and acquisition time separately. Only calculate a total where the selected family defines the second pair. Do not label ICs “LEDs” without a documented model mapping; do not infer strip length, LED density, current draw, failed-pixel count, or temperature.

`EE 40` is a separate push/status namespace, not automatically the `AA 40` topology record. H1161 and splicing families reuse these command numbers for unrelated operations. The protocol reference's family matrix records those collisions. A reply must be interpreted using envelope, family/pact, selector and firmware evidence, not opcode alone. [Pinned protocol reference](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md)

Unresolved: the inspected sources do not establish a cross-family `42/43/46` status bitfield, pass/fail vocabulary, completed-check notification, safe universal check request, or supported polling interval. Do not invent an “IC fault” binary sensor from an ACK, timeout, count difference, or command name.

## Timers, settings, calibration and limits

The frame schema defines `AA 23 [slot|FF]` for four timer slots; records contain flags, hour, minute and repeat. Plug families instead use `AA 12 [group]` counts and `AA 13 [group][page]` pages. Time-sync `33 09` bodies vary by timer-config version. Sleep/wake, night mode and gradual behavior also depend on selected forms. The frame schema's H6038 direction-calibration writer and H6062 count push further demonstrate why settings and notifications need family context. [Pinned frame schema](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/govee_ble_frame.ksy)

Classify schedules, delayed-off, power memory, gradual transitions, direction, child/zone switches and white balance as **configuration or reported control state**, not physical telemetry. A timer read does not prove the clock is correct or the event will execute. Store timezone/repeat semantics explicitly before offering editing.

Calibration is an active state-changing workflow. Besides H60A6 `42`, families have camera/install/direction and panel calibration operations; source names alone cannot establish a safe calibration-state read. `0E` is a current-limit setting, and upstream marks any H6047 `33 0E` as saving a flag and rebooting. [Pinned common schema](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/govee_common.ksy)

A current-limit flag is not measured amperage or watts. Brightness scale, Kelvin bounds, group mechanism, mask width and static segment counts should have provenance. The upstream device catalog explicitly distinguishes compiled per-goodsType capability data from live IC/segment readback; it contains multiple mode parsers and group mechanisms. A shared color submode is insufficient to select all these properties. [Pinned device catalog](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/devices.yaml)

Recommended diagnostic policy: allow only qualified queries per profile; retain unsupported, timed-out and malformed as separate outcomes. Exclude IC checks, refresh, cut/length calibration, current-limit writes, reset, OTA, secret authorization and calibration start/save from routine diagnostics. This is a design recommendation, not a claim that every `AA` opcode is harmless. Do not scan numeric command ranges.

## GATT metadata and host diagnostics

Control service is `…1910`, writer `…2b11`, notifications `…2b10`. Control-service `…2b12` is BGC info; the same characteristic UUID under OTA service `…1912` is a different endpoint. Select by service and characteristic together. Discover actual properties instead of assuming both write procedures exist. Alternate `ffe0/ffe1` and sensor services are separate profiles. [Pinned protocol reference](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md)

BGC format byte precedes encryption version: format 1 has `[format, encrypt_version]`; format 2 adds broadcast flag, big-endian pact type and pact code. Encryption version is **byte 1**, not byte 0. Missing/short values need explicit handling. Several examined firmware images lack this characteristic, so absence cannot prove plaintext. BGC is an unframed characteristic value, not a 20-byte `AA` response. [Pinned BGC schema](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/govee_bgc_info.ksy)

RSSI measures the receiver's radio observation, not a lamp health metric. Connection failures, last successful query, adapter/proxy reachability, retry/timeout totals, selected ATT write procedure, discovered UUIDs/properties and negotiated MTU are **host/transport diagnostics**. Mark stale observations and identify the observing adapter when available. Firmware, hardware, ID and pact are **identity/protocol metadata**. Power/brightness/color readback is reported state, not a photometric measurement.

## Local implementation gaps

Inspection refers to the current working tree; these are findings, not code changes.

1. [protocol.py](../custom_components/govee_bluetooth/protocol.py) already builds firmware, hardware, IC/light-count and segment-color queries. `parse_reply` decodes `40` unconditionally as unsigned first count plus segment; it ignores the second pair and has no family context or suspect-count validation. Add an explicit topology decode profile before broadening interpretation. Preserve raw bytes when unsupported. Unknown commands currently return an otherwise empty `Reply`, so “received reply” does not imply a decoded capability.
2. The same parser supports only hardware selector `07/03`; it lacks typed ID, alternate software, DSP/MCU and aggregate identity records. Extend with qualified selector-specific parsers rather than treating opaque data as ASCII. There is no typed `41` section/power-memory decoder or IC-check completion decoder.
3. [census.py](../custom_components/govee_bluetooth/census.py) `_reads` collects hardware, IC/segment count and light count, but sends a generic set of queries across profiles. It also requests `B1` pairing secrets: identity diagnostics should not inherit that sensitive request. No census was run for this research.
4. [device.py](../custom_components/govee_bluetooth/device.py) normal status polling reads power, brightness, mode and firmware; `GoveeState` does not retain hardware or topology. [light.py](../custom_components/govee_bluetooth/light.py) populates `sw_version`, not hardware version. Connect-time qualified identity/topology acquisition could close that gap without increasing every poll's traffic.
5. [diagnostics.py](../custom_components/govee_bluetooth/diagnostics.py) already reports profile, state, firmware history, HA reachability, control properties, write procedure and frame history. It lacks structured topology provenance/freshness and a full service-qualified GATT inventory. Its raw frames, entry data and probe history require a separate redaction audit before adding identifiers or secret-bearing replies.
6. [cipher.py](../custom_components/govee_bluetooth/cipher.py) has a BGC parser; [device.py](../custom_components/govee_bluetooth/device.py) exposes control properties. Do not confuse helper availability with runtime integration or complete BGC metadata retention. Verify service-qualified lookup and strict format/length handling against the pinned schema in any implementation follow-up.
7. [const.py](../custom_components/govee_bluetooth/const.py) forwards only the light platform. No dedicated diagnostic sensor/binary-sensor platform exists. Timers/settings, calibration state, limits and `42/43/46` health semantics are not implemented as typed readback.

## Proposed Home Assistant surface

These are design suggestions, not verified HA API prescriptions:

- Device registry: model, firmware `sw_version`, qualified hardware `hw_version`; keep raw IDs out of default shared diagnostics.
- Disabled-by-default diagnostic sensors: reported IC count per strip, section/group count with an exact family-specific label, last successful read time, and RSSI where the observer is known. No measurement/state class for topology counts.
- Host diagnostics: query failures/timeouts and connection status clearly named as integration observations. A timeout should make an observation unknown/stale, never “zero ICs” or “hardware fault.”
- Configuration entities only after qualified read/write support: power-on behavior select, gradual switch, schedule controls. Readback-only settings can initially remain attributes/diagnostics. Do not create configuration switches that falsely suggest writes are supported.
- Keep calibration, reset, current-limit writes and IC-refresh/check actions out of automatic diagnostic polling. No generic “check IC” button: a model-specific implementation needs exact request, completion semantics and documented side effects first.

Priority: fix family-aware topology interpretation and diagnostic provenance first; retain hardware identity in runtime state next; add settings only after model-specific evidence. Nothing here establishes electrical/thermal telemetry for these lighting devices.
