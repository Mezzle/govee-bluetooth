"""Known-answer tests for the two wire ciphers.

The V1 vector is a phone capture published by mpalczew in
egold555/Govee-Reverse-Engineering (Products/H600B/utils.py, captured
2026-08-09 against GVH600B4F5A): the device's ``E7 01`` reply decrypts under
the PSK to the session key, and two later frames decrypt under that key to a
power-off and a red colour write. The V2 vector is the H1270 device-info /
device-key pair from lasswellt/govee-homeassistant tests/test_ble_crypto.py.
Both are hardware-derived; neither was produced by this implementation.
"""

from __future__ import annotations

import pytest

from custom_components.govee_bluetooth import cipher, protocol

# --- V1 (AES-ECB + RC4), H600B capture ---------------------------------------
H600B_E701_REPLY = bytes.fromhex("9e6122db157166686c5d658a2de1cdfbc9202c8e")
H600B_POWER_OFF = bytes.fromhex("3ced005c862462cb58c0fa5753826089c5128c78")
H600B_RED = bytes.fromhex("9eea057b3a50524ada34503afca43690c5128c8e")

# --- V2 (AES-GCM), H1270 ------------------------------------------------------
H1270_DEVICE_INFO = bytes.fromhex("4831323730fc19c132ebc0")
H1270_DEVICE_KEY = bytes.fromhex("6180f19566dedc80a3adef2f8b936184")


def test_v1_session_key_from_captured_reply() -> None:
    key = cipher.v1_parse_session_key(H600B_E701_REPLY)
    assert key is not None and len(key) == 16
    # The same reply is not a confirm ack.
    assert not cipher.v1_is_confirm_ack(H600B_E701_REPLY)


def test_v1_captured_frames_decrypt_under_session_key() -> None:
    key = cipher.v1_parse_session_key(H600B_E701_REPLY)
    assert key is not None
    session = cipher.V1Session(key)
    power_off = session.unwrap(H600B_POWER_OFF)
    assert power_off[:3] == bytes([0x33, 0x01, 0x00])
    assert protocol.verify_frame(power_off)
    red = session.unwrap(H600B_RED)
    assert red[:6] == bytes([0x33, 0x05, 0x0D, 0xFF, 0x00, 0x00])
    assert protocol.verify_frame(red)
    # And our builder, wrapped, reproduces the captured ciphertext exactly.
    assert session.wrap(protocol.power_frame(False)) == H600B_POWER_OFF
    assert session.wrap(protocol.rgb_frame(255, 0, 0, submode=0x0D)) == H600B_RED


def test_v1_handshake_frames_are_psk_encrypted_e7_frames() -> None:
    fill = bytes(range(1, 18))
    step1 = cipher.v1_build_step1(fill=fill)
    plain = cipher.v1_decrypt(step1, cipher.V1_PSK)
    assert plain[:2] == b"\xe7\x01" and plain[2:19] == fill and protocol.verify_frame(plain)
    step2 = cipher.v1_build_step2(fill=fill)
    assert cipher.v1_decrypt(step2, cipher.V1_PSK)[:2] == b"\xe7\x02"
    # Random fill by default: two requests differ on the wire.
    assert cipher.v1_build_step1() != cipher.v1_build_step1()
    with pytest.raises(cipher.CipherError):
        cipher.v1_handshake_frame(1, b"short")


def test_v1_round_trip_and_wrong_key() -> None:
    key = bytes(range(16))
    session = cipher.V1Session(key)
    frame = protocol.mode_query_frame()
    assert session.unwrap(session.wrap(frame)) == frame
    assert cipher.V1Session(bytes(16)).unwrap(session.wrap(frame)) != frame
    assert cipher.v1_parse_session_key(bytes(20)) is None or True  # garbage never raises
    assert cipher.v1_parse_session_key(b"\x00" * 5) is None


def test_rc4_is_standard() -> None:
    # RFC 6229 vector: key 0102030405, first keystream bytes b2 39 63 05 f0 3d c0 27.
    assert cipher.rc4(bytes.fromhex("0102030405"), bytes(8)) == bytes.fromhex("b2396305f03dc027")


def test_v2_device_key_matches_real_h1270() -> None:
    assert cipher.v2_derive_device_key(H1270_DEVICE_INFO) == H1270_DEVICE_KEY
    assert H1270_DEVICE_INFO[:5] == b"H1270"
    assert cipher.v2_identity_matches(H1270_DEVICE_INFO, "C0:EB:32:C1:19:FC") is True
    assert cipher.v2_identity_matches(H1270_DEVICE_INFO, "C0:EB:32:C1:19:FD") is False
    assert cipher.v2_identity_matches(H1270_DEVICE_INFO, "not-a-mac") is None


def test_v2_handshake_request_layout_and_response_parse() -> None:
    iv = bytes(range(12))
    tx = bytes.fromhex("0011223344556677")
    request = cipher.v2_build_request(iv, tx)
    assert request[:3] == b"\xe7\x11\x01" and request[3:15] == iv and request[15] == 16
    assert len(request) == 16 + 8 + 16
    # A reply the device would send: header + GCM(rx_iv_key || device_info).
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    rx = bytes.fromhex("8a10ed17775ef1f4")
    header = b"\xe7\x11\x00" + iv
    body = AESGCM(cipher.V2_HANDSHAKE_KEY).encrypt(iv, rx + H1270_DEVICE_INFO, header)
    parsed_rx, info = cipher.v2_parse_response(header + body)
    assert parsed_rx == rx and info == H1270_DEVICE_INFO
    with pytest.raises(cipher.CipherError):
        cipher.v2_parse_response(header + body[:-1] + bytes([body[-1] ^ 1]))
    with pytest.raises(cipher.CipherError):
        cipher.v2_parse_response(b"\xe7\x01" + bytes(18))


def test_v2_session_counters_and_tag() -> None:
    sender = cipher.V2Session(H1270_DEVICE_KEY, tx_iv_key=bytes(8), rx_iv_key=bytes(range(8)))
    receiver = cipher.V2Session(
        H1270_DEVICE_KEY, tx_iv_key=sender.rx_iv_key, rx_iv_key=sender.tx_iv_key
    )
    frame = protocol.power_frame(True)
    first = sender.wrap(frame)
    assert first[:4] == (1).to_bytes(4, "big")
    assert len(first) == 4 + 20 + 16
    assert receiver.unwrap(first) == frame
    assert sender.wrap(frame)[:4] == (2).to_bytes(4, "big")
    assert sender.wrap(frame) != sender.wrap(frame)


def test_bgc_version_reading_reports_its_rule() -> None:
    assert cipher.bgc_encrypt_version(b"\x00\x00")[0] is None
    assert cipher.bgc_encrypt_version(b"\x01\x02\x00")[0] == 2
    assert "byte 1" in cipher.bgc_encrypt_version(b"\x01\x02")[1]
    assert cipher.bgc_encrypt_version(b"\x01\x00")[0] == 0
    assert cipher.bgc_encrypt_version(b"\x02\x01\x03\x00\x0a\x01")[0] == 1
    assert cipher.bgc_encrypt_version(b"\x02\x01")[0] is None
    assert cipher.bgc_encrypt_version(b"\x01")[0] is None
    assert cipher.bgc_encrypt_version(b"")[0] is None
    assert cipher.bgc_encrypt_version(b"\x05\x07")[0] is None
