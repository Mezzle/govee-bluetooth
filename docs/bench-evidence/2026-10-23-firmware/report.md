# Govee BLE compatibility report

Generated 2026-10-23T21:09:25+00:00 by `govee_report.py 64b2f0e` from 2 input file(s): `h6159.jsonl`, `h6181.jsonl`

Secrets: redacted on ingest (AA B1 / 33 B2 payloads zeroed).

## Sessions

| source | command | board / transport | runs | software (latest) | latest |
| --- | --- | --- | --- | --- | --- |
| Pi bench rig | census | default | 2 | govee_rig.py unknown | 2026-10-23T21:08:51.003+00:00 |

## Devices

### H6181 at 60:74:F4:6A:FB:1E

| | |
| --- | --- |
| advertised name | - |
| model | H6181 |
| firmware / hardware | 2.03.11 / 2.01.01 |
| OTA services (update unverified) | {'status': 'services_detected', 'transports': [{'transport': 'ti_oad_v2', 'service': 'f000ffc0-0451-4000-b000-000000000000', 'characteristics': [{'uuid': 'f000ffc2-0451-4000-b000-000000000000', 'properties': ['notify', 'write', 'write-without-response']}, {'uuid': 'f000ffc1-0451-4000-b000-000000000000', 'properties': ['notify', 'write', 'write-without-response']}], 'complete': True}], 'update_verified': False} |
| pact / encryption flag | ? / ? |
| table profile | Govee RGB TV backlight (0x0D colour scheme): colour 0x0d, brightness 0-100 |
| evidence sources | Pi bench rig |
| seen | 2026-10-23T21:08:54.958+00:00 to 2026-10-23T21:09:05.555+00:00 |

**Verdict: plaintext-rgbww**, session security `none`, confidence **inferred** (read from the light's own report or from the tables, no write tried).

- mode reply names sub-mode 0x0d (no write was tried)

| capability | evidence | basis |
| --- | --- | --- |
| power | inferred | AA 01 reply read back the power state |
| brightness | inferred | AA 04 reply read back the brightness |
| rgb | inferred | mode reply names colour sub-mode 0x0d; no write tried |
| rgbww | untested | no evidence |
| segments | untested | no evidence |
| scene | untested | no evidence |
| music | untested | no evidence |

Reads: 5 answered of 9 sent, 0 timed out, 0 bad checksums, 0 write acks. State seen: {"power": true, "brightness": 100, "submode": "0x0d"}. Secret handed out: no.

Not established by this evidence: rgbww, segments, scene, music. A `verified` colour dialect means the firmware stored the probe colour; whether every segment lit is an operator observation.

### H6159 at A4:C1:38:9C:8E:17

| | |
| --- | --- |
| advertised name | - |
| model | H6159 |
| firmware / hardware | 1.07.02 / 1.00.02 |
| OTA services (update unverified) | {'status': 'services_detected', 'transports': [{'transport': 'govee_v1', 'service': '00010203-0405-0607-0809-0a0b0c0d1912', 'characteristics': [{'uuid': '00010203-0405-0607-0809-0a0b0c0d2b12', 'properties': ['read', 'write-without-response']}], 'complete': True}], 'update_verified': False} |
| pact / encryption flag | ? / ? |
| GATT / 2b12 | ? / 00 |
| table profile | Govee RGB strip (H6159 family): colour 0x02, brightness 0-255 |
| evidence sources | Pi bench rig |
| seen | 2026-10-23T21:08:27.397+00:00 to 2026-10-23T21:08:40.393+00:00 |

**Verdict: plaintext-legacy**, session security `none`, confidence **inferred** (read from the light's own report or from the tables, no write tried).

- 2b12 bytes 00: encryptVersion 0 (both candidate bytes zero (plaintext))
- mode reply names sub-mode 0x02 (no write was tried)

| capability | evidence | basis |
| --- | --- | --- |
| power | untested | no evidence |
| brightness | inferred | AA 04 reply read back the brightness |
| rgb | inferred | mode reply names colour sub-mode 0x02; no write tried |
| rgbww | untested | no evidence |
| segments | untested | no evidence |
| scene | untested | no evidence |
| music | untested | no evidence |

Reads: 4 answered of 9 sent, 0 timed out, 0 bad checksums, 0 write acks. State seen: {"brightness": 26, "submode": "0x02"}. Secret handed out: no.

Not established by this evidence: power, rgbww, segments, scene, music. A `verified` colour dialect means the firmware stored the probe colour; whether every segment lit is an operator observation.

## How to read this

- *verified*: a write followed by the light reporting that exact value back.
- *probable*: every reading agrees, but no controlled write proved it.
- *inferred*: from the light's own status reply or the model tables, nothing written.
- *observed*: an operator saw it on the strip.
- *disputed*: the light reported the value back, but the operator saw no effect.
- *untested*: no evidence either way; an opcode existing in another family is not evidence.

🪩 Dance Party Certified marks evidence gathered while a Flipper was running Dance Party, Live DJ or Census. It does not mean every capability was tested or that any suite passed.
