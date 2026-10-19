# Govee BLE census: research, gap analysis and the shared-evidence architecture

Written 2026-10-19 against Govee-Bluetooth `main` (350e150) and Dance Party
PR #1 (`feat/census`, 2f712d0). It records what the two repositories and
the four external sources establish, what PR #1 got wrong, and how the two
projects share protocol knowledge from here on. The protocol itself is in
`govee-ble-protocol.md`; the encrypted families in
`encrypted-protocol-matrix.md`; the record format in `evidence-schema.md`.

## 1. Where the two repositories stood

**Govee-Bluetooth** owns the documented protocol (`govee-ble-protocol.md`,
1200 lines with a citation per claim), the reference frame builders
(`protocol.py`), the model table (`models.py`), the dialect probe
(`probe.py`), a Pi bench rig (`tools/govee_rig.py`) with a JSONL frame log,
a twelve-stage characterisation wizard (`tools/characterise.sh`) and 98
Home Assistant tests. Hardware verified: H6159 (0x02) and H6181 (0x0D).

**Dance Party PR #1** added a census mode to the Flipper app and the ESP32
image: advertisement parsing (pact type/code, flags, encryption bit), a
read path through the bridge (`N`, `R`, `NOTIFY`), nine capability reads,
four colour-dialect probe writes, a JSON summary per light, LittleFS
buffering on the ESP32, and the pact-aware 0x0B/0x15 choice. Its own
testing section said nothing had been built or run.

The external sources add, in order of weight:

| source | contributes | hardware behind it |
| --- | --- | --- |
| Brady-Woods/govee-ble-local | the decompiled-app spec and devices.yaml the protocol doc already cites; a Python port of the V1 cipher and handshake; a btsnoop decryptor | H60A6 (live), H6047, H6641 |
| Brady-Woods/hass-govee-ble-local | an HA adapter over that library; reads `AA B1` on unbound lights in its config flow, reports that bound lights decline | H60A6, H6047 |
| lasswellt/govee-homeassistant | the V2 AES-GCM transport with the static keys and an H1270 known-answer vector; LAN and cloud paths | H1270 |
| egold555/Govee-Reverse-Engineering | per-model notes; a replayable H600B V1 capture (PSK, session key, two frames); the H61F6 negative result; H6061/H6088 hexa notes | H600B, H61F6, H6061, H6088, H6199, H6127, H6102 |

## 2. Confirmed defects in PR #1 (all fixed, all with host tests)

| defect | evidence | fix |
| --- | --- | --- |
| `GVH`-style names rejected by both C parsers (and by `models.py` and the HA manifest matchers, which required `GVHH6159`) | `govee_name_parse("GVH6159_1A2B")` returned false on the host; the app's `BaseBleProcessor` takes `substring(2)`; egold555's H600B advertises `GVH600B4F5A` | app rule in C, C++ and Python; manifest matchers regenerated; tests |
| Cross-dialect false echoes: one probe colour for all four dialects, and the `AA A5` page keeps the last accepted colour, so a colour that landed through 0x02 was credited to 0x0B, 0x0D and 0x15 | reading `govee_census_run`; reproduced with a fake light on the host | one colour per dialect; a dialect echoes only when its colour appears where it was not showing before |
| Replies used without checksum verification | `census_read` copied bytes out of any 20-byte frame; the ESP32 ring accepted any 20-byte notification | reject and count `bad_checksum` frames on both |
| Raw pairing secret in the frame log regardless of `CENSUS_PRINT_SECRET` | the `AA B1 01 ...` reply was logged as hex | redact on both producers, `"redacted":true`; the Pi rig and the report generator redact too |
| Summary JSON cut mid-string on overflow | `n >= out_size` clamp in `govee_census_summary_json` | field-granular writer, closes with `"truncated":true`; buffer sized and moved to the heap |
| Probe on a powered-off light; only power restored even when the pre-probe colour was readable | reading `census_probe` | power on first, colour restored when the 02/0D mode reply carried it, power off after; `restored` / `restore_note` recorded |
| Nine 1.5 s timeouts per silent light; Back key honoured only between lights | reading the loops | silent after three unanswered core reads; abort flag checked before every read; `aborted` recorded |
| Write acks (`33 xx` echoes) dropped by the Flipper, logged as `unsolicited` by the ESP32 | reading `uart_read` and `readFrame` | transport notify callback on the Flipper; `ack` / `ack_error` notes and an `acks` count on both |
| Worker stack 3 KB for a path that nests `snprintf` logging inside transport calls | precautionary; not reproduced without hardware | 4 KB |
| README claimed the branch "compiled"; the PR said no build was run | the PR body | built here: ESP32 for `m5stack_atoms3` and `m5stack_atom`, Flipper with the official 1.5.1 SDK |

Not defects, but gaps the census could not answer and now records: which
Govee characteristics the light exposes (`gatt`), the raw `2b12` bytes
(`bgc`), whether the SKU is in a table at all (`known_model`), where the
dialect guess came from (`dialect_source`), and the session header that
says which software gathered the evidence.

## 3. Gaps in Govee-Bluetooth closed on this branch

- `models.py` and the manifest did not discover `GVH...` lights; the H617A,
  the best-documented RGBIC strip in every source, was not in the table.
- No manufacturer-data parser: the pact that decides 0x0B vs 0x15 and the
  encryption flag were invisible. `advertisement.py` now parses both the
  on-air and the BlueZ-mangled form.
- `protocol.py` had no read frames beyond the four status reads and no
  parser for `AA 07 03`, `AA 40`, `AA 0F`, `AA B1`, `AA A5`, nor a
  selector-aware reply matcher, nor redaction.
- No shared census: `census.py` is the Python twin of `govee_census.c`
  (same battery, same probe rules, same summary keys) plus `classify()`.
- The rig had no census command, no pact column in `scan`, logged secrets
  raw, matched replies by command only, and could not see write acks.
- No report generator; no evidence schema; no model fixture shared with
  Dance Party; no cipher implementation or vectors.

## 4. Architecture: how discoveries flow between the repositories

```
                 fixtures/govee-models.json          protocol.py
                 (classification, provenance)        (frame builders)
                          |                               |
        tools/export_fixtures.py  ---------------->  generated headers
                          |                               |
   Govee-Bluetooth        v                               v        Dance Party
   models.py <-- test_fixtures.py        govee_models_gen.h  govee_frame_vectors_gen.h
   census.py                                       |                    |
   govee_rig.py census  --> govee-evidence/1 <--  govee_census.c / sketch mirror
   govee_report.py      <-- census.jsonl, USB log, HA diagnostics
```

- **Govee-Bluetooth owns**: the protocol reference, the model fixture, the
  frame builders, the evidence schema, the classifier and the report. A
  new model or dialect lands here first, with its provenance.
- **Dance Party consumes**: the generated model tables (one header,
  vendored in two places, CI checks they are identical) and the frame
  vectors (its host tests compare every builder byte for byte). It owns
  the Flipper and ESP32 transports, the census runner in C, the playful
  modes, and the field-evidence format it writes.
- **Both produce** `govee-evidence/1` records; the report keeps the source
  of each. A finding from a Flipper session is upgraded to "verified" the
  same way as one from the Pi: an echo in the log.
- **What is deliberately not shared**: the C census runner and the Python
  census are two implementations of one documented procedure (the
  battery, the probe rules, the summary keys). They are kept in step by
  the fixture tests and by the sections of `evidence.md` (Dance Party) and
  `evidence-schema.md` (here) that describe the procedure. A single C
  library compiled into both an HA custom component and a Flipper app was
  judged not worth the packaging cost.

## 5. Research summary beyond plaintext

See `encrypted-protocol-matrix.md` for the per-mechanism and per-model
tables. The short form:

- Plaintext 0x02 / 0x0D / 0x15 is verified on our bench and in captures.
  0x0B has two app-derived layouts and no capture for either.
- The pairing secret is a separate mechanism from the cipher. Nothing in
  our hands shows a light that ignored frames and then obeyed after
  `33 B2`; the census records `silent` + `secret_family` and the hardware
  plan's step 2 is the first such capture.
- V1 (AES-ECB+RC4) is now implemented and proven against a phone capture
  (H600B); the rig can run it (`--cipher v1`). V2 (AES-GCM) is implemented
  against lasswellt's H1270 vector and not wired to a transport.
- `2b12`'s version byte is contested between the sources; the census
  records the raw bytes.
- Lights with the encryption flag set are no longer skipped entirely: the
  ESP32 connects to read the GATT table and `2b12` and sends no frame.

## 6. What still needs a light

Everything in `hardware-testing-checklist.md` (Dance Party). In particular:
one plaintext RGBIC light through the full census on all three producers;
one `secret_family` light before and after app pairing; one V1 light
through `--cipher v1`; the `2b12` byte question; and a human in front of
the strip for every `observed` claim.
