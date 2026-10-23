# govee-evidence/1: the shared evidence format

One JSON object per line. The same shape is written by the Pi bench rig
(`tools/govee_rig.py --log`), the Dance Party Flipper app
(`apps_data/govee_party/census.jsonl`) and the Dance Party ESP32 image (USB
log, LittleFS), and read by `tools/govee_report.py`, which also takes a Home
Assistant diagnostics download. The machine-readable schema is
`schema/govee-evidence-v1.schema.json`.

## Compatibility rule

The frame record is the Pi rig's original log line and is unchanged:

```
{"at","direction","frame","note","decoded","address"}
```

Everything else is additive. Within version 1 a producer may add keys and a
consumer must ignore keys it does not know. A change that alters the
meaning of an existing key, or removes one, is version 2 and gets a new
`schema` string. The HA diagnostics `frames` list uses the first four keys
of the frame record and stays compatible.

## Record kinds

| kind | written by | when |
| --- | --- | --- |
| frame (no `summary` key) | all | every frame in either direction, after redaction |
| `"summary":"session"` | all | once per run: `schema`, `source`, `software`, `at`, and `board` / `transport` / `adapter`, `lights`, `probe` |
| `"summary":"light"` | all | once per visited light; the keys are listed in the schema and in Dance Party's `docs/evidence.md` |
| `"summary":"observation"` | `govee_report.py --observe`, or by hand | what a person saw the strip do; the only way a capability reaches *observed* |

## Sources and provenance

`source` is one of `pi`, `dance_party`, `esp32`, `home_assistant`, `manual`.
A frame record without a `source` inherits the most recent session header
in the same file; a file with no header is `pi` (or `esp32` when `at` is an
uptime). The report keeps every observation's source; a device section
lists all of them, and the 🪩 Dance Party Certified badge appears when at
least one is `dance_party`. The badge is provenance, not a pass mark.

## Confidence vocabulary (the report)

| word | meaning |
| --- | --- |
| verified | a write followed by the light reporting that value back (probe echo), or a capture replayed byte for byte |
| probable | every reading agrees, no controlled write proved it (e.g. `silent` on a `secret_family` model) |
| inferred | from the light's status reply or the model tables; nothing was written |
| observed | an operator saw it on the strip (`observation` record) |
| untested | no evidence either way. An opcode existing in another family is not evidence |

## Secrets

The 8-byte pairing secret appears on the wire in `AA B1 01 ...` replies and
`33 B2 ...` writes. Every producer zeroes those bytes before writing a
frame record and sets `"redacted": true`; the checksum is recomputed so the
record still parses. `secret_present` in the light summary says the secret
was handed out; the bytes are only written with an explicit opt-in
(`--keep-secrets` on the rig, `CENSUS_PRINT_SECRET 1` in the sketch), never
by the Flipper app, and the report redacts again on ingest unless told not
to. Keep such files private.

## Example

```
{"summary":"session","schema":"govee-evidence/1","source":"pi","at":"2026-10-19T18:00:00+00:00","software":"govee_rig.py 350e150","host":"pi-zero","adapter":"hci0","command":"census","probe":true,"lights":1}
{"at":"2026-10-19T18:00:04.120+00:00","direction":"tx","frame":"aa010000000000000000000000000000000000ab","note":"query","decoded":"read power","address":"A4:C1:38:00:00:01"}
{"at":"2026-10-19T18:00:04.210+00:00","direction":"rx","frame":"aa010100000000000000000000000000000000aa","note":"reply","decoded":"power on","address":"A4:C1:38:00:00:01"}
{"at":"2026-10-19T18:00:06.010+00:00","direction":"rx","frame":"aab101000000000000000000000000000000001a","note":"reply","decoded":"secret present","address":"A4:C1:38:00:00:01","redacted":true}
{"summary":"light","schema":"govee-evidence/1","source":"pi","at":"2026-10-19T18:00:09+00:00","software":"govee_rig.py 350e150","address":"A4:C1:38:00:00:01","name":"","model":"H617A","known_model":true,"rssi":null,"pact":"10/1","flags":3,"encrypted":false,"dialect":"RGBIC","dialect_source":"table","secret_family":true,"connected":true,"subscribed":true,"silent":false,"reads":17,"replies":14,"acks":4,"power":true,"brightness":100,"submode":"15","submode_name":"colour15","firmware":"3.02.24","hardware":"3.01.01","ic_count":0,"segments":15,"secret_present":true,"segment_page":true,"probed":true,"echoed":"15","restored":"power","restore_note":"pre-probe colour not readable from the mode reply (0B/15); left on the last probe colour","bgc":"0100"}
```

`python3 tools/govee_report.py that.jsonl --markdown report.md --json evidence.json`
turns it into the report; the JSON document (`govee-evidence-report/1`)
keeps the per-device verdict, capability table, sources and the redacted
frames.


### Optional OTA metadata (Pi census)

Light summaries may include `ota`: `status` (`services_detected` or
`not_observed`), `transports` (transport name, service UUID, matching
characteristic UUIDs/properties and a `complete` flag), and
`update_verified: false`. This is read-only GATT discovery evidence.
Missing `ota` means not inspected, while `not_observed` does not rule out
an OTA bootloader. Model, hardware and firmware remain in the existing fields.
