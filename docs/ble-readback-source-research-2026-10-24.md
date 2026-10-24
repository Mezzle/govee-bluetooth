# Govee BLE status and readback: source research

Research date: 2026-10-24 UTC. Repository baseline: `e69b84693dbaddfbf4abd3b38d27238fc1b887d7` plus the working tree inspected during this task. This is document-only research. No hardware, BLE scanning/connections, SSH, light-network access, app, or cloud account was used. Public upstream source was browsed through GitHub; existing local captures were read as files. This delegated investigation did not delegate further.

## Findings and evidence rules

App-independent local status is feasible on documented families, but there is no universal complete-state request. Power, brightness, mode, segment colors, and uploaded effect bodies have different evidence and completeness guarantees. `AA 05` can identify the active mode without revealing RGB or a resident animation. `AA A2`/`AA A5` can supply color pages; selected devices use an `AC` burst instead. Pushes and advertisements add partial observations. None of these alone establishes the instantaneous rendered output of an animation.

Evidence labels used below:

- **Hardware evidence:** an author's direct-device observations, including this repo's already saved bench records. These are historical observations on named devices, not new validation by this researcher.
- **App-derived:** upstream reconstruction of decompiled Govee Home controllers/feature splits. It establishes what an app reader expects, not what every firmware returns. The original decompiled Java was not independently obtained here.
- **Firmware-derived:** upstream static analysis of identified firmware images. This is stronger than a guessed layout but is not a live observation. Address/class references are upstream attributions.
- **Implementation evidence:** open-source parsers, schemas, or tests. A test vector or successful decoder alone is not hardware qualification.
- **Inference/recommendation:** conclusions for a local client, explicitly separated from measurements.

Primary artifacts are pinned below. “Primary” means the project owns its implementation or capture report; these are not official Govee BLE specifications. Recent upstream revisions were checked rather than assuming the snapshots cited by older repo notes remained current.

| Source | Pinned revision and scope |
| --- | --- |
| Brady-Woods/govee-ble-local | [`276b14b6b383b35ce931bbc2e7e6e81ca765d5f3`](https://github.com/Brady-Woods/govee-ble-local/tree/276b14b6b383b35ce931bbc2e7e6e81ca765d5f3), app/firmware-derived schemas and client, plus explicitly identified bench observations |
| teh-hippo/ha-govee-led-ble | [`50ab40c9e25b1710b9dded9b94eea9991e4b364d`](https://github.com/teh-hippo/ha-govee-led-ble/tree/50ab40c9e25b1710b9dded9b94eea9991e4b364d), current parsers; historical hardware ledgers pinned separately to [`b2cd788226682c7405ffa0816d3a8d9e71d97ab1`](https://github.com/teh-hippo/ha-govee-led-ble/tree/b2cd788226682c7405ffa0816d3a8d9e71d97ab1), where their paths exist |
| rnodern/govee-h617a-ble | [`4279e64a54a028856902ea12fd9b67a066d6e8e4`](https://github.com/rnodern/govee-h617a-ble/blob/4279e64a54a028856902ea12fd9b67a066d6e8e4/README.md), independent captured/replayed local control; explicitly describes displayed state as write-confirmed rather than readback |

## 1. Frame envelope and AA05 variants

**App-derived / implementation:** the plaintext single-frame envelope is 20 bytes: `type@0, command@1, payload@2..18, XOR@19`; the checksum covers bytes 0..18, and unused request bytes are zero-filled. Mode request prefix `AA 05 01` uses `01` as a read selector, not a requested music mode. Replies start `AA 05 <active-submode> ...`; write acknowledgements beginning `33` have different semantics. These are application frames, not raw ATT packet layouts. Encryption wrappers must be removed before applying these offsets. [Envelope and request semantics][envelope]

**Firmware-derived:** the current upstream note narrows its older claim that the `01` selector is required. The examined H6006/H6008/H60A1/H60A6/H6047 handlers ignore that request byte; the app still sends `01`. **Hardware:** an H6199 report found `AA 05 00` and `AA 05 01` produced the same video/static/DIY data. Neither result proves equivalence on unexamined firmware. Use the app's `01` as a default, without diagnosing a zero selector as the cause of every failed read. [Current selector analysis][selector], [H6199 narrowed claims][h6199-limits]

The following prefixes omit zero fill and XOR. Offsets count from the start of the 20-byte plaintext frame. Each row is conditional on the named family/reader.

| Variant | Supported reply interpretation | Evidence and limit |
| --- | --- | --- |
| Legacy `02` | `AA 05 02 R G B flag tintR tintG tintB ...` | Existing H6159 bench evidence supports RGB and white-flag/tint behavior. A Kelvin estimate derived from tint is not a received Kelvin scalar. [Local ledger][local-bench], [saved frames][local-frames] |
| RGBWW `0D`, H6181 | `AA 05 0D R G B kHi kLo tintR tintG tintB ...` | Existing H6181 fw 2.03.11 records include 4300 K (`10 CC`) and tint. This does not establish the same tail on bulbs. [Saved frames][local-frames] |
| RGBWW `0D`, H6006/H6008 | `AA 05 0D R G B kHi kLo 00 ...` | Upstream firmware analysis and bench report explicitly find no trailing tint. Their observed CCT RGB slot itself carries tint; nonzero trailing tint is not required. [Firmware/bench report][fw-mode] |
| RGBIC `15`, common compose reader | `AA 05 15 flag kHi kLo ...` | App reader has Kelvin at bytes 4..5, rather than the write layout's bytes 7..8. No RGB triple is established here. A field called gradual or CCT flag depends on the selected reader. [Reader variants][mode-schema], [compose discussion][selector] |
| `15`, H6047 bars | `AA 05 15 gradual leftKHi leftKLo rightKHi rightKLo ...` | App-derived two-bar state; aggregate CCT applies only if both agree. A one-Kelvin decoder drops information. [H6047 reader][selector] |
| `0B` and other family color forms | Reader may decode gradual only, gradual + Kelvin, RGB, or nothing | The current schema selects a color decode using the profile, separately for direct replies and status TLVs. Do not copy `33 05 0B` RGB write offsets into read decoding. [Profile-dependent mode schema][mode-schema] |

**Hardware limit:** H6199 returned zero static detail even after a successful 3000 K write, while segment RGB changed. Thus “upstream has a Kelvin parser” does not imply live Kelvin readback on that unit. H617A's report similarly distinguishes verified RGB companions from Kelvin readback. [H6199 limits][h6199-limits], [H617A qualification][h617a-hardware]

**Inference:** select the decoder using family + advertised pact + known firmware qualifications, rather than submode alone. The source maps `0B` to game on H6057 and `0D` to background color on some strips; music has many numeric variants. Preserve unknown modes without promoting them into a color dialect. [App mode mappings][mode-map]

## 2. Alternate RGB readback: AA A2 and AA A5

**App-derived / implementation:** these are command bytes under the `AA` envelope. They must not be confused with top-level `A2` chunked transfers or other top-level `A5` protocols. Current schemas distinguish the following forms explicitly. [Color-page schemas][pages]

| Profile form | Request prefix | Reply prefix / records |
| --- | --- | --- |
| `bulb_batch_v1`, e.g. legacy dreamcolor reader | `AA A2 batch` | `AA A2 batch` followed by four `[R G B]` records; batch is 1-based |
| `bulb_batch_v2`, H61A8 reader | `AA A5 batch` | `AA A5 batch` followed by three `[brightness R G B]` records |
| General `color_group`, brightness-capable | `AA A5 group` | group byte followed by profile-sized `[brightness R G B]` records |
| General RGB-only group | `AA A2 group` or the profile's selected command/form | group byte followed by profile-sized `[R G B]` records; opcode alone does not define record width |

Record count, last-page remainder, placement, and physical mapping require external geometry. Padding is not another segment. The current readback client separates H61A8's three-record brightness batches from the H6047/H6641 direct walk, which its implementation decodes as color-only groups of four. H6641 derives pieces from a bounded live IC count, then derives the number of four-piece pages; `ceil(IC/5)` is not itself the page count. [Current readback implementation][walks]

**Unresolved source disagreement:** the current upstream narrative still describes H6047/H6641 with four-byte brightness records, while its `_readback.py` describes/uses color-only decoding. Treat this as a qualification question, not permission to pick a universal A5 width. The historical H6199 grammar independently models brightness-bearing pages in groups `4+4+4+3` for 15 segments. The current H6125 RGB-only A2 grammar explicitly labels itself a speculative compatibility hypothesis. [Narrative][bulk-spec], [client][walks], [H6199 grammar][h6199-parser], [H6125 hypothesis][rgb-hypothesis]

**Hardware evidence:** the H617A ledger reports complete 15-segment RGB/relative-brightness verification, including valid zero relative brightness, and explains an earlier stale-query attribution failure. This supports that device's qualified page mechanism, not every dreamcolor SKU. [H617A hardware and freshness limits][h617a-hardware]

**Inference:** build a complete, timestamped page set using explicit geometry and expected record counts. A `[0,0,0,0]` record can represent a dark segment; its validity cannot be decided by treating zero as an end sentinel. A single page containing a desired RGB is weaker evidence than a complete layout matching the request and untouched siblings. Do not infer the current RGB from `AA05` zeroes or from the last command sent.

## 3. AC bulk status, including the final data packet

**App-derived, with upstream H60A6 capture cross-check:** request prefix is `AC cmd N sub1 ... subN`, zero-filled to byte 18 plus XOR. `AC 03 02 41 30` and `AC 03 03 41 30 A5` are documented H60A6 forms. The list requests additional records; it is not an exhaustive promise that only those TLVs will be returned. [Bulk controller and captures][bulk-spec]

Supported response layout from that controller:

```text
first:  AC 00 Plo Phi L cmd sub data[12] XOR
middle: AC i  data[17] XOR
last:   AC FF data[L] zero-fill XOR
```

Here `P` is total packet count, including first and last; `L` is valid final-data length (0..17). The first frame's data starts at offset 7; later data starts at offset 2. The final `FF` packet contains data. For `P >= 2`, reconstructed length is `12 + 17*(P-2) + L`; for `P == 1`, use `L <= 12` bytes from the first frame. The app controller also accepts the final positional tag `P-1`. The cited real H60A6 example has `P=10,L=12`, producing 160 bytes. [Exact upstream reassembly analysis][bulk-spec]

The reconstructed stream contains `[tag, length, value]` records: power `01`, brightness `04`, mode `05`, info `07`, timers and family-specific records such as zone `30`, power memory `41`, and color groups `A5`. The current schema retains unknown values/tail, and stops its modeled walk when fewer than three bytes remain; it does not define a universal “stop at tag zero” rule. It also records an H60A6 empty `05 00` mode TLV in some internal states. Empty mode is missing information, not mode zero/video. [TLV schema][tlv], [empty-mode handling][empty-mode]

**Scope limit:** the app's bulk reader is selected for H60A6 and particular goodsTypes (including 277/288/298 and 263); H6047 goodsType 119 and H6641 goodsType 247 use direct group reads. The source reports no response to H60A6's burst request on those curated units. The ability to write RGBIC colors does not establish AC support. [Dispatch distinctions][bulk-spec]

**Inference:** validate checksum, first-frame command/subselector, count, ordering, final length, and TLV bounds before installing a complete snapshot. Keep partial observations marked partial; never concatenate chunks across reconnects or requests. No claim here establishes an atomic simultaneous physical snapshot across modes/zones/pages.

## 4. EE unsolicited pushes

**App-/firmware-derived:** `EE subtype payload... XOR` uses the same 20-byte plaintext envelope, but its subtype is not an `AA` request command to match. Current schema supports Wi-Fi result `EE 11 status reason` (status zero means connected), brightness `EE 20 level`, IC-related notifications, and family-specific switch/zone `EE 30`. H6047 `EE 24` earphone state is separately firmware-attributed. [Push schema][pushes]

For `EE 30`, the schema exposes `[detail, flags_a, flags_b, flags_c]` and selects a profile-specific form. Its H60A6-associated `main_bit0_a` form derives main from bit 0 of `flags_a` and zones from bit 0 of `flags_b/c`; other forms use different bytes or masks. This is evidence against the older repo note's blanket zone bit-1 interpretation. Do not generalize a write-side zone value or a generic notification form to every light. [Exact family branches][pushes]

**Limit/inference:** these sources do not establish a universally emitted full mode/color push, emission cadence, or guaranteed delivery after every external change. A brightness push updates brightness only; a Wi-Fi result is not light power. Use supported pushes as partial fresh observations and reconcile with family reads. A `33` acknowledgement proves protocol acceptance at most; it does not replace those reads.

## 5. Passive advertisements

**App-derived; selected firmware attribution:** after AD type `FF`, the light manufacturer payload is `[flags][88 EC][pactType u16be][pactCode][device_state...]`. Exact zero-based payload offsets are flags 0, marker 1..2, pact type 3..4, pact code 5, state beginning 6. Flags bit `0x40` signals an encrypted command channel; low nibble is broadcast protocol version. Bleak splits the first two bytes as a little-endian company ID, so rejoin that ID and its value before interpreting this custom structure. [Manufacturer schema][advert]

The current schema provides a conditional switch byte at payload offset 6: Govee marker, sufficient length, and signed-flags-compatible range 2..127. Its value remains SKU-specific; plugs may use relay bits. Firmware-attributed H6047 example is `03 88 EC 00 01 01 <power>`. This supersedes the confusing offset prose in the older combined advertisement schema; the latter's `data[3]` must not be copied as offset 3 of this manufacturer payload. [Explicit offsets and gates][advert]

**Limit/inference:** no inspected source establishes passive RGB, complete scene/music/DIY/video configuration, or guaranteed refresh timing across the lighting range. Preserve raw payload and observation time; expose only qualified state overlays. Advertisement presence is reachability evidence, not proof that the emitter is powered on. Identity/pact discovery can be local using a bundled model table; the app's use of cloud-provided metadata does not force a local client to contact that cloud.

## 6. Scene, music, DIY and video state

| Mode | What a supported reader can report | What that does not recover |
| --- | --- | --- |
| Scene, commonly `04` | Current profile schema reads a little-endian 16-bit scene code and, where present, another little-endian 16-bit music code | Scene name/catalogue, uploaded effect bytes, visual equivalence, or availability of arbitrary scene activation |
| DIY, commonly `0A` | Selector/code; current schema includes the same optional second word and signed DIY interpretation | Resident authored body, palette, layers, speed, or an arbitrary pre-existing DIY needed for exact restoration |
| Music | Family-specific mode and sensitivity, sometimes style/dynamic/fixed-color fields | Uploaded music table/palette/companions unless separately readable and qualified |
| Video `00`, H6199 | `AA 05 00 region source saturation sound_effects softness ...` | Camera image/output, universal calibration layout, or identical field meanings on other camera/HDMI products |

**App-derived / implementation:** scene/DIY words come from the current `scene_report` schema. Music `13` has multiple strategies: a flagged form reads code, value, dynamic, color flag and conditional RGB; another reads effect/sensitivity, optional sub-effect for code 4, then color flag/RGB, but stops early for designated new music codes. H6199's pinned parser establishes video **region then source**, correcting section 8.3 of this repo's older protocol reference. [Mode/scene schema][mode-schema], [music branches][music], [H6199 video grammar][h6199-parser]

**Hardware evidence:** H617A new-music workflows read back mode/sensitivity, while native DIY reads back the selector; its report explicitly rejects interpreting these or positive upload results as resident-body equality. The independent H617A capture/replay project likewise labels displayed state as write-confirmed, not readback. [H617A ledger][h617a-hardware], [independent implementation statement][rnodern]

**Inference:** use separate fields for observed selector, locally known uploaded body, and unknown resident parameters. Keep locally authored assets and model-specific catalogues on disk for repeatable app-independent operation. Matching an activation selector is useful but cannot certify replay/restoration of an unreadable previous effect. Do not advertise full backup/export from mode readback. There is no demonstrated universal BLE export of arbitrary resident scene/music/DIY assets in the sources inspected.

## 7. Comparison with the current repository

These are source observations, not changes made by this task. Relative links identify local implementation; baseline commit is given at the top.

| Area | Current repository | Consequence / recommended direction (inference) |
| --- | --- | --- |
| Routine refresh | `device.py:219` queries AA01/04/05 and optionally firmware; it does not walk color pages | RGBIC mode identity can refresh while actual RGB remains unavailable/stale. Add profile-qualified readback plans rather than assuming AA05 is complete. |
| Mode decode | `protocol.py:437` selects by submode only; 15 reads one Kelvin, 0D assumes RGB/K/tint, 0B stays opaque | Preserve working H6159/H6181 behavior; introduce family-specific read shapes, including per-zone Kelvin and opaque unsupported forms. |
| Raw mode body | `ModeReport.body` retains only eight bytes after submode | Insufficient as a general full 17-byte mode payload. Preserve complete raw data separately from parsed fields. |
| Segment parser | `protocol.py:419` assumes four-byte A5 records and stops at the first all-zero record; A2 has selector matching but no typed RGB decode | Qualify record width/count/geometry. A zero record is not universally padding. |
| Segment usage | `census.py:247` requests A5 group 1; normal device refresh does not consume a complete page set | One-page probe evidence does not establish a full static layout or whole-device color. |
| AC / EE | `parse_reply` accepts only checksummed AA; neither AC reassembly nor EE state decoding is present | Existing `TYPE_NOTIFY` constant is not push support. Add distinct transport paths if a qualified family needs them. |
| Unsolicited state | `device.py:469` installs unsolicited parsed power only | Brightness/mode AA observations and EE pushes currently cannot refresh those fields through this callback. |
| Mode presentation | `device.py:360` handles scene or a report containing RGB | Music, DIY, video, or color without RGB can leave a previous effect/color visible. Track active mode and field freshness explicitly. |
| Advertisements | `advertisement.py` parses flags/pact/encryption, not switch state | Passive power is a separate qualified overlay; existing discovery does not provide it. |
| Correlation | `exchange.py` serializes queries and checks echoed selectors | Useful foundation, but no transaction ID exists to distinguish delayed identical replies. Preserve that limit when adding page/burst collectors. |
| Restoration | `census.py:276` deliberately restricts raw mode restoration to readable 02/0D state | Do not extend raw read-body-to-write replay to 0B/15 or effect bodies: layouts differ and state can be incomplete. |

Local source: [protocol.py](../custom_components/govee_bluetooth/protocol.py), [device.py](../custom_components/govee_bluetooth/device.py), [exchange.py](../custom_components/govee_bluetooth/exchange.py), [advertisement.py](../custom_components/govee_bluetooth/advertisement.py), [census.py](../custom_components/govee_bluetooth/census.py).

The older [protocol reference](govee-ble-protocol.md) remains useful background, but its absolute `/tmp` citations are not portable evidence links. Specific corrections supported above: AC packet count/final data semantics, the current TLV walk, H6199 video field order, family-specific EE30 masks, selector tolerance on examined firmware, and non-universal A5 record widths. Its historical backlog is not a current-code defect list.

## 8. App-independent local-control priorities

Recommendations, not hardware-validated implementation:

1. Retain the known basic AA plan for qualified legacy devices. Represent each field as observed/estimated/commanded/unknown with observation time and raw provenance; a Kelvin approximation from RGB must remain distinguishable from received Kelvin.
2. Bundle a reviewed family/pact/firmware table selecting mode decode, brightness scale, readback mechanism, geometry, and push/advertisement overlays. Do not use capability uncertainty as justification for a write probe during status collection.
3. Add pure offline parsing/replay fixtures for each qualified page and AC form before wiring new reads into refresh. Preserve partial sets, missing chunks, zero segments, unknown TLVs and opaque tails faithfully. Synthetic tests establish parser behavior only.
4. Keep authored scene/DIY/music assets locally with their dialect and activation codes. Report selector-only verification accurately; make no exact-restoration promise for unknown resident bodies.
5. Keep transport authorization separate from read layout. App independence is not established for every previously bound/encrypted unit merely by knowing these packets; pairing secret/cipher requirements remain per-device limitations. This task neither obtained secrets nor tested pairing. See the existing [pairing-source research](pairing-secret-research-sources.md).

No runtime code, tests, fixtures, live device state, or account state was changed. Open qualifications are A5 form selection on disputed families, complete mode/push coverage by firmware, passive-state cadence, and exact recovery of unreadable effect parameters.

[envelope]: https://github.com/Brady-Woods/govee-ble-local/blob/276b14b6b383b35ce931bbc2e7e6e81ca765d5f3/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md#L204-L257
[selector]: https://github.com/Brady-Woods/govee-ble-local/blob/276b14b6b383b35ce931bbc2e7e6e81ca765d5f3/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md#L1475-L1504
[mode-map]: https://github.com/Brady-Woods/govee-ble-local/blob/276b14b6b383b35ce931bbc2e7e6e81ca765d5f3/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md#L868-L910
[mode-schema]: https://github.com/Brady-Woods/govee-ble-local/blob/276b14b6b383b35ce931bbc2e7e6e81ca765d5f3/spec/govee_state.ksy#L238-L426
[fw-mode]: https://github.com/Brady-Woods/govee-ble-local/blob/276b14b6b383b35ce931bbc2e7e6e81ca765d5f3/spec/docs/FIRMWARE_VALIDATION.md#L268-L302
[pages]: https://github.com/Brady-Woods/govee-ble-local/blob/276b14b6b383b35ce931bbc2e7e6e81ca765d5f3/spec/govee_state.ksy#L1173-L1343
[walks]: https://github.com/Brady-Woods/govee-ble-local/blob/276b14b6b383b35ce931bbc2e7e6e81ca765d5f3/src/govee_ble_local/devices/_readback.py#L86-L192
[bulk-spec]: https://github.com/Brady-Woods/govee-ble-local/blob/276b14b6b383b35ce931bbc2e7e6e81ca765d5f3/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md#L576-L666
[tlv]: https://github.com/Brady-Woods/govee-ble-local/blob/276b14b6b383b35ce931bbc2e7e6e81ca765d5f3/spec/govee_status_stream.ksy
[empty-mode]: https://github.com/Brady-Woods/govee-ble-local/blob/276b14b6b383b35ce931bbc2e7e6e81ca765d5f3/spec/govee_state.ksy#L90-L100
[pushes]: https://github.com/Brady-Woods/govee-ble-local/blob/276b14b6b383b35ce931bbc2e7e6e81ca765d5f3/spec/govee_ble_frame.ksy#L1230-L1334
[advert]: https://github.com/Brady-Woods/govee-ble-local/blob/276b14b6b383b35ce931bbc2e7e6e81ca765d5f3/spec/govee_manufacturer_data.ksy
[music]: https://github.com/Brady-Woods/govee-ble-local/blob/276b14b6b383b35ce931bbc2e7e6e81ca765d5f3/spec/govee_state.ksy#L760-L864
[h6199-parser]: https://github.com/teh-hippo/ha-govee-led-ble/blob/50ab40c9e25b1710b9dded9b94eea9991e4b364d/tools/ble/kaitai/h6199_status_reply.ksy
[rgb-hypothesis]: https://github.com/teh-hippo/ha-govee-led-ble/blob/50ab40c9e25b1710b9dded9b94eea9991e4b364d/tools/ble/kaitai/speculative/govee_rgb_segment_page.ksy
[h6199-limits]: https://github.com/teh-hippo/ha-govee-led-ble/blob/b2cd788226682c7405ffa0816d3a8d9e71d97ab1/docs/h6199-support-findings.md#L306-L318
[h617a-hardware]: https://github.com/teh-hippo/ha-govee-led-ble/blob/b2cd788226682c7405ffa0816d3a8d9e71d97ab1/docs/h617a-support-findings.md#L49-L109
[rnodern]: https://github.com/rnodern/govee-h617a-ble/blob/4279e64a54a028856902ea12fd9b67a066d6e8e4/README.md#L10-L15
[local-bench]: bench-evidence/2026-10-23-additional/README.md
[local-frames]: ../tests/fixtures/bench_status_2026_10_23.json
