# Govee Bluetooth encryption: fixed keys and evidence

Checked 2026-10-23. Scope is the application protocol for lights, not Bluetooth
link-layer pairing or all Govee sensors. No evidence found here for numeric key
`4`; there is direct evidence for hard-coded shared keys instead.

## V1: a slogan is the shared handshake key

The 16 ASCII bytes `MakingLifeSmarte` are the V1 pre-shared key. The upstream
[constant and extraction attribution](https://github.com/Brady-Woods/govee-ble-local/blob/master/src/govee_ble_local/const.py)
say this resolves from an app resource and has worked on real handshakes.
The library's README identifies H60A6 as its live-verified device.

The [handshake implementation](https://github.com/Brady-Woods/govee-ble-local/blob/master/src/govee_ble_local/transport/handshake.py)
gets a device-chosen 16-byte session key in an `E7 01` response encrypted under
that shared key; `E7 02` confirms. The fresh session key does not solve the
shared-key problem: somebody with a complete captured handshake and this public
constant can recover it. That is a deduction from the implemented exchange.

The [frame cipher](https://github.com/Brady-Woods/govee-ble-local/blob/master/src/govee_ble_local/crypto.py)
uses AES-ECB on the first 16 bytes of each 20-byte frame and RC4 on the last four.
RC4 restarts from the beginning for each frame, reusing the same four-byte
keystream within a session. Consequently identical plaintext frames produce
identical ciphertext within that session, and XORing two encrypted tails cancels
the stream. These are deductions from the code, not a claim of a demonstrated
remote exploit. The inner XOR checksum is not cryptographic authentication.

This repo already contains an H600B phone-capture known-answer fixture in
`tests/test_cipher.py`. On this investigation the parent agent directly checked
that the handshake decrypts under the slogan key and the following power-off
and red frames decode with valid checksums. This validates a captured exchange;
it is not a new live hardware test. Full pytest was unavailable in the local
environment.

## V2: stronger packet protection, still static root keys

The H1270 [implementation](https://github.com/lasswellt/govee-homeassistant/blob/main/custom_components/govee/api/ble_crypto.py)
contains these app-extracted constants:

- Handshake key: `fc03783c7c42cb83e202a1643648aff6`.
- Device derivation key: `ae028b630bae6ecc4bff1b249e22f955`.

It reports hardware-verified power, brightness and colour on H1270. Its
[known-answer tests](https://github.com/lasswellt/govee-homeassistant/blob/main/tests/test_ble_crypto.py)
use real H1270 device identity and the resulting data key. Identity consists of
the SKU and reversed MAC, padded to 16 bytes and encrypted with the static
derivation key. Thus the per-device data key is derived from identity rather
than an independent secret. A handshake protected with the static handshake
key exchanges the eight-byte IV prefixes. Data uses AES-GCM, a four-byte counter
and a 16-byte authentication tag. The parent agent independently reproduced the
H1270 known-answer derivation using this repo's implementation.

Inference: V2 gives meaningful packet integrity and varying nonces, but knowledge
of those root constants and the handshake permits reconstruction of its data
protection. This is a key-management weakness; it is not evidence AES-GCM itself
is broken. The upstream source's claim that every device ships those constants
should not be generalized to all Govee models or future firmware.

## Limits and practical meaning

A separate eight-byte account/pairing secret can gate commands on some families;
knowing a wire-encryption key does not by itself prove that lock can be bypassed.
Our [protocol matrix](encrypted-protocol-matrix.md) tracks that distinction and
marks hardware gaps. H6159 and H6181 on this project's tested firmware accepted
plaintext control, so there is no application cipher to crack on those units.

The useful answer is therefore model-specific: some lights have no application
encryption; V1 has a public slogan key and weak packet construction; V2 has
modern packet crypto anchored in app-wide constants. None of this establishes
that every nearby Govee device is controllable without an account secret, nor
does it establish any Internet-reachable attack.
