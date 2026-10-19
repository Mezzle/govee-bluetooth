# Encrypted and locked Govee BLE lights: what is known, from where

Companion to `docs/govee-ble-protocol.md` sections 10 and 11. The point of
this page is the provenance column: a mechanism is only "verified" when a
named light did it in a capture we can replay, "source-derived" when it
comes out of the decompiled Govee Home app or a port of it that nobody has
run against that mechanism on hardware, and "unknown" otherwise.

## The four mechanisms

| mechanism | what the light does | how it is announced | implemented here | status |
| --- | --- | --- | --- | --- |
| **Plaintext** | takes 20-byte `33`/`AA` frames from anyone | advertisement flags bit 6 clear; `2b12` reads `00 ..` | everything in `protocol.py` | **verified** on H6159 fw 1.07.02 and H6181 fw 2.03.11 (bench); community captures for H617A, H6199 pact 2, H60A1, H60A6, H6001, H6127 |
| **Pairing secret ("account lock")** | accepts the connection, ignores every frame until `33 B2 <8 bytes>`; an unbound light answers `AA B1` with `01 + 8 bytes` | not announced; a family tag in the app (`secret_key_pairing`) | `protocol.secret_query_frame`, `census.needs_secret`, redaction; **no** `33 B2` writer in the HA path (the rig can send one as `raw`) | **source-derived**. hass-govee-ble-local reads `AA B1` on unbound lights in its config flow and reports bound lights "decline"; HGL drives an H617A without a secret; GENZERO got no data from `AA B1` on an H617A. No capture in our hands shows a light that ignored frames *and then* obeyed after `33 B2` |
| **V1: AES-128-ECB + RC4 session** | `E7 01` / `E7 02` handshake under a fixed PSK, then every frame AES-ECB on bytes 0-15 and RC4 on 16-19 under the device-chosen session key | flags bit 6 set, or `2b12` byte 0 = `01` | `cipher.py` (`v1_*`, `V1Session`), rig `--cipher v1` | **verified by replay** of a phone capture: egold555 H600B (`GVH600B4F5A`, 2026-08-09) decrypts under the PSK to `E7 01 <key>` and the later frames to `33 01 00` and `33 05 0D FF 00 00` with valid checksums (`tests/test_cipher.py`). Live use by govee-ble-local on an H60A6. **Not run by us on hardware** |
| **V2: AES-GCM session** | `E7 11 01 <iv> 10` + GCM(handshake key) request; reply `E7 11 00 <iv>` + GCM(rx IV key, device info); session key = AES-ECB(device key, device info); frames `counter(4) + GCM(...)`, 16-byte tag | flags bit 6 set; `2b12` byte 1 = `02` (byte 0 read `01` on the H1270) | `cipher.py` (`v2_*`, `V2Session`); no transport integration | **verified by known answer**: lasswellt's H1270 device-info / device-key pair reproduces (`tests/test_cipher.py`). lasswellt verified power, brightness and colour on an H1270 with it. **Not run by us on hardware** |

Two facts the sources disagree on, carried forward as open items:

- **Where `encryptVersion` lives in `2b12`.** The decompiled app reads byte 0
  (`BgcInfoReader`); lasswellt found byte 1 on an H1270 and byte 0 reading
  `01` there. `cipher.bgc_encrypt_version` reports which rule it used and
  the census records the raw bytes so a capture settles it per light.
- **Whether a V1 light needs the `E7 02` confirm.** The app sends it; the
  H600B script waits only 0.8 s for the ack and carries on; govee-ble-local
  found a race if the first read goes out before the `E7 02` ack arrives.
  The rig sends it and reports whether it was acked.

## Per-model status

Only models with a claim from a named source. "us" means this project's
bench. Firmware and hardware versions are listed where the source gave them.

| model | announced | mechanism | source | status |
| --- | --- | --- | --- | --- |
| H6159 | plaintext | plaintext 0x02 | us, fw 1.07.02 | verified |
| H6181 | plaintext | plaintext 0x0D | us, fw 2.03.11 | verified |
| H617A | pact 10/1, plaintext | plaintext 0x15; `secret_key_pairing` family | GENZERO fw 3.02.24 / hw 3.01.01; HGL; rnodern | community-verified plaintext control without a secret |
| H6199 | pact 2/1 (hw 3.02.01, fw 1.10.04) and pact 1/1 (hw 1.00.01) | plaintext 0x15 / 0x0B by pact | HGL | community-verified (pact 2); 0x0B app-derived |
| H60A6 | `secret_key_pairing`, V1 cipher on the wire | AES-ECB+RC4 | govee-ble-local live reference; its `decrypt_btsnoop.py` was written for this light | community-verified V1 |
| H61A8, H6641 | `secret_key_pairing` | observed plaintext despite the tag | govee-ble-local devices.yaml | community observation; contradicts the tag |
| H600B | flags unknown | AES-ECB+RC4 (`e701`/`e702`), then 0x0D colour, `AA 05` read-back | egold555 (mpalczew capture) | community-verified V1; **our replay of the capture passes** |
| H1270 | `2b12` = `01 02 ..` | AES-GCM | lasswellt | community-verified V2; **our key derivation matches** |
| H61F6 | encrypted | unknown cipher; plaintext ignored, replayed ciphertext causes a disconnect in 2.5 s, no per-message counter but per-connection variation | egold555 (SimonDedman) | negative result; consistent with V1 (per-connection session key) |
| H70B3/B4/B5, H6810/11, H6800, H6840 | `ctlchannel` | AES-GCM, UUIDs supplied per message | spec | source-derived only |
| H6168, H619A, H61A0, H61E1, H61B5, H61E5, H6059 | `secret_key_pairing` family | expected: silent until `33 B2` | spec, devices.yaml | **unknown on hardware**; the census's `silent` + `secret_family` reading |

## What the census can and cannot tell

| observation | means | does not mean |
| --- | --- | --- |
| `encrypted: true` in the advertisement | the light says its command channel is wrapped | which cipher; a stale flag on a light that answers plaintext has not been seen but is not excluded |
| `bgc` with a non-zero version byte | the app would run the V1 or V2 handshake | which byte is authoritative (see above) |
| `silent: true` on a `secret_family` model | consistent with the pairing lock | proven; a foreign dialect or a different characteristic looks the same |
| `silent: true` on an unknown model with no encryption signs | nothing | anything; this is where a GATT dump (`SVC`) and a `2b12` read go next |
| `AA B1` answered `01 + 8 bytes` | unbound light, secret in hand | that `33 B2` is required; plain lights may answer too (untested) |
| `AA B1` unanswered | nothing by itself | bound; plain lights also ignore it |

## Plan for hardware (in order; each step produces a replayable capture)

1. **Plaintext lights (H618A, H6168 on hand).** `govee_rig.py census ADDR` then `--probe`; the expected verdict is `plaintext-rgbic` with `echoed: 15` (or `0B` on an old pact). Record `bgc`.
2. **A `secret_family` light that was paired with the app.** Expect `silent`. Then `send ADDR raw 33 b2 <secret> on rgb 255 0 0` with the secret from a `btmon` capture of the app's first frames (`tools/characterise.sh` stage 6 records it). The first light that ignores frames and obeys after `33 B2` upgrades that row to verified.
3. **A V1 light (H600B class, or anything whose `2b12` byte 0 is `01`).** `govee_rig.py census ADDR --cipher v1`. Success is a decrypted `AA 01` reply with a valid checksum; the rig logs the handshake frames (never the key). Failure modes to record: no `E7 01` reply (not V1, or wrong characteristic), reply that does not decrypt (different PSK), reply that decrypts but later frames do not (key offset).
4. **A V2 light (H1270 class, `2b12` byte 1 = `02`).** Not wired into the rig yet; `cipher.v2_*` plus the lasswellt transport sequence (`start_notify`, request with `response=False`, 5 s wait, counters from 1) is the implementation plan. Add `--cipher v2` only after step 3 has produced one V1 capture, so the transport scaffolding is proven on the simpler cipher first.
5. **Flipper / ESP32.** Neither should do a handshake until the Pi has. Both already record `gatt` and `bgc` (ESP32) and `silent` + `secret_family`, which is all the field needs to pick the lights worth bringing to the bench. mbedtls on the ESP32 has AES-ECB and GCM; the Flipper SDK exposes mbedtls too; RC4 is ten lines. Budget: V1 adds roughly 2 KB of code to the ATOM Lite image.

What this plan refuses to do: brute-force a secret, replay another light's
session, or treat "the GATT write succeeded" as "the light accepted it". A
V1 or V2 session is only reported as working when a decrypted reply carries
a valid checksum and answers the frame that was sent.
