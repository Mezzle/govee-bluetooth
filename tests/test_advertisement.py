"""Manufacturer-data parsing: the same bytes as seen by NimBLE, BlueZ and the Flipper."""

from __future__ import annotations

from custom_components.govee_bluetooth import advertisement

# GENZERO's H617A: flags 03, company 88 EC, pact type 0x000A, code 01.
H617A_ON_AIR = bytes.fromhex("0388ec000a0101")


def test_parse_raw_on_air_bytes() -> None:
    adv = advertisement.parse_raw(H617A_ON_AIR)
    assert adv is not None
    assert (adv.pact_type, adv.pact_code) == (10, 1)
    assert adv.flags == 0x03
    assert adv.encrypted is False
    assert adv.broadcast_version == 3
    assert adv.pact == "10/1"


def test_parse_bluez_style_company_id_quirk() -> None:
    # BlueZ reports company id 0x8803 (88 << 8 | flags 03) and the payload from EC.
    adv = advertisement.parse_manufacturer_data({0x8803: bytes.fromhex("ec000a0101")})
    assert adv is not None
    assert (adv.pact_type, adv.pact_code, adv.flags) == (10, 1, 0x03)
    assert adv.raw == H617A_ON_AIR


def test_encrypted_flag_bit_six() -> None:
    adv = advertisement.parse_manufacturer_data({0x8843: bytes.fromhex("ec00010100")})
    assert adv is not None
    assert adv.encrypted is True
    assert adv.broadcast_version == 3
    assert (adv.pact_type, adv.pact_code) == (1, 1)


def test_flags_unknown_when_stack_dropped_them() -> None:
    adv = advertisement.parse_raw(bytes.fromhex("88ec00020100"))
    assert adv is not None and adv.flags is None and adv.encrypted is None
    assert (adv.pact_type, adv.pact_code) == (2, 1)
    adv = advertisement.parse_raw(bytes.fromhex("ec000201"))
    assert adv is not None and adv.pact_type == 2


def test_other_vendors_are_not_govee() -> None:
    assert advertisement.parse_manufacturer_data({0x004C: bytes.fromhex("0215")}) is None
    assert advertisement.parse_raw(b"\x03\x88") is None
    assert not advertisement.is_govee_manufacturer_data({0x0059: b"\x00" * 6})
    assert advertisement.is_govee_manufacturer_data({0x8803: bytes.fromhex("ec000a0101")})
