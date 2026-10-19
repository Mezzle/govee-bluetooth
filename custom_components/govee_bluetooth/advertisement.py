"""Govee manufacturer data: pact and encryption flags, read without connecting.

On the air the AD structure is ``[flags][88 EC][pactType u16 BE][pactCode]...``
(docs/govee-ble-protocol.md section 11.2). Because the flags byte precedes
the ``88 EC`` company id, BlueZ and Home Assistant report a "company id" of
``0x8803``, ``0x8843`` and so on (``0x88 << 8 | flags``) with the payload
starting at ``EC``. NimBLE and the Flipper hand over the raw bytes. Both
shapes are accepted here.

Bit 6 of the flags byte says the command channel is AES-wrapped; the low
nibble is the broadcast protocol version. The pact (type, code) is what the
Govee app keys its dialect choice on: dreamcolor SKUs speak 0x0B on pact 1
and 0x15 on pact 2 / 10. Nothing here is a measurement of the light's
behaviour; it is what the light says about itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

GOVEE_COMPANY_BYTES = b"\x88\xec"
FLAG_ENCRYPTED = 0x40


@dataclass(frozen=True, slots=True)
class GoveeAdvertisement:
    """Pact and flags from one advertisement."""

    pact_type: int
    pact_code: int
    flags: int | None  # None when the stack ate the flags byte with the company id
    raw: bytes  # the manufacturer payload as reconstructed on the air

    @property
    def encrypted(self) -> bool | None:
        """True / False from the flags byte, None when the flags were not seen."""
        if self.flags is None:
            return None
        return bool(self.flags & FLAG_ENCRYPTED)

    @property
    def broadcast_version(self) -> int | None:
        return None if self.flags is None else self.flags & 0x0F

    @property
    def pact(self) -> str:
        return f"{self.pact_type}/{self.pact_code}"


def parse_raw(data: bytes) -> GoveeAdvertisement | None:
    """Parse the manufacturer-specific payload as it appears on the air."""
    data = bytes(data)
    if len(data) >= 6 and data[1:3] == GOVEE_COMPANY_BYTES:
        flags: int | None = data[0]
        off = 3
    elif len(data) >= 5 and data[0:2] == GOVEE_COMPANY_BYTES:
        flags = None
        off = 2
    elif len(data) >= 4 and data[0] == 0xEC:
        flags = None
        off = 1
    else:
        return None
    pact_type = (data[off] << 8) | data[off + 1]
    pact_code = data[off + 2]
    return GoveeAdvertisement(pact_type=pact_type, pact_code=pact_code, flags=flags, raw=data)


def parse_manufacturer_data(manufacturer_data: Mapping[int, bytes]) -> GoveeAdvertisement | None:
    """Parse Bleak / Home Assistant style ``{company_id: payload}`` data.

    The flags byte is recovered from the low byte of the fake company id
    (``0x8803`` -> flags ``0x03``).
    """
    for company_id, payload in manufacturer_data.items():
        if not 0 <= company_id <= 0xFFFF:
            continue
        on_air = company_id.to_bytes(2, "little") + bytes(payload)
        parsed = parse_raw(on_air)
        if parsed is not None:
            return parsed
    return None


def is_govee_manufacturer_data(manufacturer_data: Mapping[int, bytes]) -> bool:
    return parse_manufacturer_data(manufacturer_data) is not None
