"""The two wire ciphers newer Govee lights wrap the 20-byte frames in.

Nothing in the Home Assistant device path uses this module yet. It exists so
the handshakes are implemented once, against known-answer vectors from
hardware captures, before anything is pointed at a light:

* **V1, AES-ECB + RC4** (``com.govee.encryp.ble.Safe`` and ``Controller4Aes``
  in the decompiled app). Each 20-byte frame is AES-128-ECB on bytes 0-15 and
  RC4 on bytes 16-19 under one 16-byte key. The handshake runs under a fixed
  pre-shared key: ``E7 01`` request, ``E7 01 <16-byte session key>`` reply,
  ``E7 02`` confirm, ``E7 02`` ack. Everything after that uses the session
  key. Sources: Brady-Woods/govee-ble-local ``crypto.py`` / ``handshake.py``
  (live on an H60A6), egold555/Govee-Reverse-Engineering ``Products/H600B``
  (a phone capture that decrypts with this exact routine; those bytes are
  the test vector in tests/test_cipher.py).
* **V2, AES-GCM** (``Controller4AesGcm``). ``E7 11 01 <12-byte IV> 10`` +
  GCM(handshake key, IV, our 8-byte tx IV key, AAD = header); reply
  ``E7 11 00 <IV>`` + GCM(rx IV key || 11-byte device info). The session key
  is AES-ECB(device key, device info padded to 16). Frames are
  ``counter(4, BE) || GCM(session key, IV key || counter, frame, AAD = counter)``
  with a 16-byte tag. Source: lasswellt/govee-homeassistant ``ble_crypto.py``,
  verified on an H1270; its device-info / key pair is the vector here.

The keys below are constants compiled into the vendor app and shipped in
every device; they are not user secrets. The 8-byte pairing secret (``AA B1``
/ ``33 B2``) is a different mechanism and never passes through here.

``cryptography`` is a Home Assistant core dependency, so it is imported
lazily and only the AES primitives come from it; RC4 is ten lines.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import protocol

FRAME_LEN = protocol.FRAME_LENGTH
HANDSHAKE = 0xE7

# V1 (AES-ECB + RC4): the handshake PSK, decoded from the app's resources by
# govee-ble-local and confirmed as a literal in the H5083 firmware image.
V1_PSK = b"MakingLifeSmarte"
V1_OP_SESSION_KEY = 0x01
V1_OP_CONFIRM = 0x02

# V2 (AES-GCM): static keys from the app (lasswellt/govee-homeassistant).
V2_HANDSHAKE_KEY = bytes.fromhex("fc03783c7c42cb83e202a1643648aff6")
V2_DEVICE_KEY = bytes.fromhex("ae028b630bae6ecc4bff1b249e22f955")
V2_REQUEST_HEADER = bytes((HANDSHAKE, 0x11, 0x01))
V2_RESPONSE_HEADER = bytes((HANDSHAKE, 0x11, 0x00))
V2_IV_LEN = 12
V2_IV_KEY_LEN = 8
V2_COUNTER_LEN = 4
V2_TAG_LEN = 16

# The BGC-info characteristic (2b12). The decompiled app reads encryptVersion
# from byte 0; lasswellt found the version at byte 1 on an H1270 (byte 0 read
# 0x01 there). Record the raw bytes and let the classifier look at both.
BGC_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b12"


class CipherError(ValueError):
    """A handshake frame did not decode as expected."""


# ---------------------------------------------------------------------------
# V1: AES-ECB on the first block, RC4 on the 4-byte remainder
# ---------------------------------------------------------------------------


def _aes_ecb(key: bytes, block: bytes, *, encrypt: bool) -> bytes:
    if len(key) != 16 or len(block) != 16:
        raise CipherError("AES-ECB needs a 16-byte key and a 16-byte block")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(algorithms.AES(key), modes.ECB())
    ctx = cipher.encryptor() if encrypt else cipher.decryptor()
    return ctx.update(block) + ctx.finalize()


def rc4(key: bytes, data: bytes) -> bytes:
    """Plain RC4 (``Safe.f`` key schedule + ``Safe.g`` keystream), restarted per call."""
    if not key:
        raise CipherError("RC4 key must not be empty")
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray(len(data))
    i = j = 0
    for k, byte in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[k] = byte ^ s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


def v1_encrypt(frame: bytes, key: bytes) -> bytes:
    """``Safe.d``: AES-ECB on every whole 16-byte block, RC4 on the tail."""
    blocks, rem = divmod(len(frame), 16)
    out = b"".join(_aes_ecb(key, frame[i * 16 : i * 16 + 16], encrypt=True) for i in range(blocks))
    if rem:
        out += rc4(key, frame[blocks * 16 :])
    return out


def v1_decrypt(data: bytes, key: bytes) -> bytes:
    """``Safe.b``: the inverse of :func:`v1_encrypt`."""
    blocks, rem = divmod(len(data), 16)
    out = b"".join(_aes_ecb(key, data[i * 16 : i * 16 + 16], encrypt=False) for i in range(blocks))
    if rem:
        out += rc4(key, data[blocks * 16 :])
    return out


def v1_handshake_frame(op: int, fill: bytes | None = None) -> bytes:
    """Plaintext ``E7 <op> <17 fill bytes> <xor>``; ``Controller4Aes.a`` fills with random bytes."""
    body = fill if fill is not None else os.urandom(FRAME_LEN - 3)
    if len(body) != FRAME_LEN - 3:
        raise CipherError("handshake fill must be 17 bytes")
    return protocol.build_frame(op, body, frame_type=HANDSHAKE)


def v1_build_step1(psk: bytes = V1_PSK, fill: bytes | None = None) -> bytes:
    """PSK-encrypted ``E7 01`` request (``Controller4Aes.e``)."""
    return v1_encrypt(v1_handshake_frame(V1_OP_SESSION_KEY, fill), psk)


def v1_build_step2(psk: bytes = V1_PSK, fill: bytes | None = None) -> bytes:
    """PSK-encrypted ``E7 02`` confirm (``Controller4Aes.f``)."""
    return v1_encrypt(v1_handshake_frame(V1_OP_CONFIRM, fill), psk)


def v1_parse_session_key(reply: bytes, psk: bytes = V1_PSK) -> bytes | None:
    """The 16-byte session key from a PSK-encrypted ``E7 01`` reply, else None.

    ``Controller4Aes.g`` checks only bytes 0 and 1 and takes bytes 2..17; the
    reply's checksum is not validated by the app and is not here either.
    """
    if len(reply) < 18:
        return None
    plain = v1_decrypt(reply[:FRAME_LEN], psk)
    if plain[0] != HANDSHAKE or plain[1] != V1_OP_SESSION_KEY:
        return None
    return plain[2:18]


def v1_is_confirm_ack(reply: bytes, psk: bytes = V1_PSK) -> bool:
    if len(reply) < 2:
        return False
    plain = v1_decrypt(reply[:FRAME_LEN], psk)
    return plain[0] == HANDSHAKE and plain[1] == V1_OP_CONFIRM


@dataclass(slots=True)
class V1Session:
    """A negotiated V1 session: wrap writes, unwrap notifications."""

    key: bytes

    def wrap(self, frame: bytes) -> bytes:
        if len(frame) != FRAME_LEN:
            raise CipherError("V1 wraps 20-byte frames")
        return v1_encrypt(frame, self.key)

    def unwrap(self, data: bytes) -> bytes:
        return v1_decrypt(bytes(data)[:FRAME_LEN], self.key)


# ---------------------------------------------------------------------------
# V2: AES-GCM with a per-connection IV key pair
# ---------------------------------------------------------------------------


def _aesgcm(key: bytes):  # type: ignore[no-untyped-def]
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(key)


def v2_derive_device_key(device_info: bytes) -> bytes:
    """AES-ECB(device key, device_info zero-padded to 16): the session data key."""
    if not 1 <= len(device_info) <= 16:
        raise CipherError("device info must be 1-16 bytes")
    return _aes_ecb(V2_DEVICE_KEY, device_info.ljust(16, b"\x00"), encrypt=True)


def v2_build_request(iv: bytes, tx_iv_key: bytes) -> bytes:
    """``E7 11 01 <iv> 10`` + GCM(handshake key, iv, tx_iv_key, AAD = that header)."""
    if len(iv) != V2_IV_LEN or len(tx_iv_key) != V2_IV_KEY_LEN:
        raise CipherError("V2 request needs a 12-byte IV and an 8-byte IV key")
    header = V2_REQUEST_HEADER + iv + bytes((16,))
    return header + _aesgcm(V2_HANDSHAKE_KEY).encrypt(iv, tx_iv_key, header)


def v2_parse_response(response: bytes) -> tuple[bytes, bytes]:
    """``(rx_iv_key, device_info)`` from an ``E7 11 00`` reply; raises on a bad tag."""
    response = bytes(response)
    if not response.startswith(V2_RESPONSE_HEADER):
        raise CipherError("not a V2 handshake reply")
    aad = response[: 3 + V2_IV_LEN]
    iv = response[3 : 3 + V2_IV_LEN]
    from cryptography.exceptions import InvalidTag

    try:
        plain = _aesgcm(V2_HANDSHAKE_KEY).decrypt(iv, response[3 + V2_IV_LEN :], aad)
    except InvalidTag as err:
        raise CipherError("V2 handshake reply failed authentication") from err
    return plain[:V2_IV_KEY_LEN], plain[V2_IV_KEY_LEN:]


def v2_identity_matches(device_info: bytes, address: str) -> bool | None:
    """The reply carries the SKU and the MAC reversed; None when the address is not a MAC."""
    if len(device_info) < 11:
        return False
    try:
        mac = bytes.fromhex(address.replace(":", ""))
    except ValueError:
        return None
    return device_info[5:11] == bytes(reversed(mac))


@dataclass(slots=True)
class V2Session:
    """A negotiated V2 session. Counters restart at 1 per connection."""

    device_key: bytes
    tx_iv_key: bytes
    rx_iv_key: bytes
    _tx_counter: int = field(default=0, repr=False)

    def wrap(self, frame: bytes) -> bytes:
        self._tx_counter += 1
        counter = self._tx_counter.to_bytes(V2_COUNTER_LEN, "big")
        return counter + _aesgcm(self.device_key).encrypt(self.tx_iv_key + counter, frame, counter)

    def unwrap(self, packet: bytes) -> bytes:
        packet = bytes(packet)
        counter = packet[:V2_COUNTER_LEN]
        return _aesgcm(self.device_key).decrypt(
            self.rx_iv_key + counter, packet[V2_COUNTER_LEN:], counter
        )


def bgc_encrypt_version(bgc: bytes) -> tuple[int | None, str]:
    """Read a qualified BGC format discriminator followed by encryption version.

    Format 2 also carries broadcast flags and pact. Unknown/short formats
    remain unknown; missing BGC is not evidence of plaintext or authentication.
    """
    bgc = bytes(bgc)
    if not bgc:
        return None, "no bgc bytes"
    if bgc[0] not in (1, 2):
        return None, "unknown BGC format"
    minimum = 2 if bgc[0] == 1 else 6
    if len(bgc) < minimum:
        return None, "incomplete BGC format"
    return bgc[1], f"BGC format {bgc[0]}, encryption version at byte 1"
