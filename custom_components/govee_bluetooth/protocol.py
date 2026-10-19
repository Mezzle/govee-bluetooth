"""Pure functions for building and parsing Govee BLE frames.

Every frame is 20 bytes: a type byte (``0x33`` write, ``0xAA`` read/heartbeat,
``0xEE`` unsolicited push), a command byte, up to 17 payload bytes padded with
zeros, and a trailing XOR checksum over the first 19 bytes.

Nothing in this module touches Bluetooth, so it can be unit tested without
Home Assistant installed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

FRAME_LENGTH = 20

TYPE_WRITE = 0x33
TYPE_READ = 0xAA
TYPE_NOTIFY = 0xEE

CMD_POWER = 0x01
CMD_BRIGHTNESS = 0x04
CMD_MODE = 0x05
CMD_VERSION = 0x06
# Read-only capability and identity commands (docs/govee-ble-protocol.md 6.6).
CMD_DEVICE_INFO = 0x07  # ``AA 07 <sel>``; 03 = hardware version ASCII
CMD_LIGHT_COUNT = 0x0F  # ``AA 0F`` -> bulb / light count
CMD_IC_COUNT = 0x40  # ``AA 40`` -> ic u16 BE, segment count u8
CMD_SEGMENT_COLORS = 0xA5  # ``AA A5 <group>`` -> N x [bri R G B]
CMD_SECRET_READ = 0xB1  # ``AA B1`` -> 01 + 8-byte pairing secret (unbound lights only)
CMD_SECRET_WRITE = 0xB2  # ``33 B2 <8 bytes>`` authorises a session on secret families
INFO_HARDWARE = 0x03

SUBMODE_MUSIC = 0x03
SUBMODE_SCENE = 0x04
SUBMODE_COLOR_LEGACY = 0x02
SUBMODE_COLOR_RGBIC = 0x15
# RGBWW scheme used by bulbs, lamps and newer firmware on some strips:
# ``33 05 0D R G B <kHi> <kLo> R2 G2 B2`` (kelvin big-endian, zero for RGB).
SUBMODE_COLOR_RGBWW = 0x0D
# "Dreamcolor" RGBIC scheme the decompiled app lists as the primary colour
# sub-mode for the H6168 / H617A / H619A / H61A0 family (``dreamcolorlightv1``),
# with 0x15 as the alternate: ``33 05 0B R G B <mask0> <mask1>``. No colour
# temperature path exists on this scheme. Not yet seen on hardware.
SUBMODE_COLOR_DREAMCOLOR = 0x0B

# Legacy colour sub-mode ``33 05 02 R G B <whiteFlag> R2 G2 B2``.
WHITE_FLAG_COLOR = 0x00
WHITE_FLAG_WHITE = 0x01

# RGBIC colour sub-mode ``33 05 15 <op> ...``. The byte after the sub-mode is
# an operation selector; ``01`` sets colour / white on the segments in the
# trailing mask. Segment masks for strips of up to 16 segments are two bytes,
# little-endian, one bit per segment. 0x7FFF selects all 15 segments of the
# common strip layout.
RGBIC_OP_COLOR = 0x01
RGBIC_ALL_SEGMENTS = (0xFF, 0x7F)


def control_write_response(properties: Iterable[str] | None) -> bool:
    """Whether to write the control characteristic with response.

    Most lights expose ``2b11`` as ``write`` + ``write-without-response`` and
    the app writes with response, which is what the H6159 and H6181 were
    verified with. The bulblight family (H6004, H6005, H6006) exposes
    ``write-without-response`` only: a write request is protocol-illegal
    there, the H6006 answers it with Write Not Permitted and drops the link
    (bench, 2026-10-19), and the H6004 does the same per egold555's notes.
    Unknown properties keep the old behaviour.
    """
    props = {str(p).lower() for p in (properties or ())}
    if not props:
        return True
    return "write" in props


def checksum(data: Iterable[int]) -> int:
    """XOR every byte together (Govee's "BCC")."""
    result = 0
    for value in data:
        result ^= value
    return result & 0xFF


def build_frame(command: int, payload: Iterable[int] = (), frame_type: int = TYPE_WRITE) -> bytes:
    """Build a checksummed 20-byte frame."""
    body = bytes([frame_type & 0xFF, command & 0xFF, *payload])
    if len(body) > FRAME_LENGTH - 1:
        raise ValueError(f"Payload too long for a Govee frame: {len(body) - 2} bytes")
    body = body.ljust(FRAME_LENGTH - 1, b"\x00")
    return body + bytes([checksum(body)])


def verify_frame(frame: bytes) -> bool:
    """Return True if ``frame`` is 20 bytes with a valid trailing checksum."""
    return len(frame) == FRAME_LENGTH and checksum(frame[:-1]) == frame[-1]


# ---------------------------------------------------------------------------
# Write commands
# ---------------------------------------------------------------------------


def power_frame(on: bool) -> bytes:
    return build_frame(CMD_POWER, [0x01 if on else 0x00])


def brightness_frame(ha_brightness: int, scale: int) -> bytes:
    """Build a brightness command from a Home Assistant 1-255 value.

    ``scale`` is the device's native maximum (255 for the H6159 family, 100
    for newer firmware). Zero is never sent: on these strips brightness 0
    doubles as "power off", which would desynchronise state.
    """
    if not 1 <= ha_brightness <= 255:
        raise ValueError("Brightness must be between 1 and 255")
    if scale == 255:
        level = ha_brightness
    elif scale == 100:
        level = max(1, round(ha_brightness * 100 / 255))
    else:
        raise ValueError(f"Unsupported brightness scale {scale}")
    return build_frame(CMD_BRIGHTNESS, [level])


def device_to_ha_brightness(level: int, scale: int) -> int:
    """Convert a device brightness byte back to Home Assistant's 1-255 range."""
    if scale == 255:
        return max(1, min(255, level))
    if scale == 100:
        return max(1, min(255, round(level * 255 / 100)))
    raise ValueError(f"Unsupported brightness scale {scale}")


def rgb_frame(red: int, green: int, blue: int, submode: int = SUBMODE_COLOR_LEGACY) -> bytes:
    """Build a solid-colour command."""
    rgb = (red, green, blue)
    if any(not 0 <= value <= 0xFF for value in rgb):
        raise ValueError("RGB values must be between 0 and 255")
    if submode == SUBMODE_COLOR_LEGACY:
        return build_frame(CMD_MODE, [submode, *rgb, WHITE_FLAG_COLOR])
    if submode == SUBMODE_COLOR_RGBIC:
        # ``33 05 15 01 R G B 00 00 00 00 00 <mask>``: whole-strip mask for
        # 15-segment RGBIC strips. Layout per the decompiled Govee Home app
        # (Brady-Woods/govee-ble-local issue #15, ``Config.Yt`` -> ``Bytes.h``).
        return build_frame(
            CMD_MODE, [submode, RGBIC_OP_COLOR, *rgb, 0, 0, 0, 0, 0, *RGBIC_ALL_SEGMENTS]
        )
    if submode == SUBMODE_COLOR_RGBWW:
        # ``33 05 0D R G B 00 00 00 00 00``: kelvin and tint zeroed for a plain
        # colour (bulblightv3/ble/SubModeColor.getWriteBytes in the decompiled
        # app). Confirmed on an H6181, firmware 2.03.11.
        return build_frame(CMD_MODE, [submode, *rgb, 0, 0, 0, 0, 0])
    if submode == SUBMODE_COLOR_DREAMCOLOR:
        # ``33 05 0B R G B <mask0> <mask1>``: whole-strip segment mask inline
        # after the colour (dreamcolorlightv1/ble/SubModeColor.getWriteBytes,
        # GOVEE_BLE_GATT_PROTOCOL.md section 16.1). Unverified on hardware.
        return build_frame(CMD_MODE, [submode, *rgb, *RGBIC_ALL_SEGMENTS])
    raise ValueError(f"Unsupported colour sub-mode {submode:#x}")


def color_temp_frame(kelvin: int, submode: int = SUBMODE_COLOR_LEGACY) -> bytes:
    """Build a white / colour-temperature command.

    The H6159 family has no dedicated white LEDs. The official app sends the
    legacy colour frame with the white flag set and a kelvin-derived RGB tint:
    ``33 05 02 FF FF FF 01 R G B``.
    """
    tint = kelvin_to_rgb(kelvin)
    if submode == SUBMODE_COLOR_LEGACY:
        return build_frame(CMD_MODE, [submode, 0xFF, 0xFF, 0xFF, WHITE_FLAG_WHITE, *tint])
    if submode == SUBMODE_COLOR_RGBIC:
        # ``33 05 15 01 FF FF FF <kHi> <kLo> R G B <mask>``: white point in the
        # colour slot, kelvin as a big-endian 16-bit value, then the app's
        # kelvin-derived RGB tint and the segment mask. Layout per the
        # decompiled Govee Home 7.7.02 app as documented in
        # Brady-Woods/govee-ble-local (spec/docs/GOVEE_BLE_GATT_PROTOCOL.md
        # section 4.2 and issue #15, ``Config.Yt`` -> ``Bytes.r`` mode 1). The
        # app only ever sends whole 100 K steps within 2000-9000 K.
        if not 0 <= kelvin <= 0xFFFF:
            raise ValueError("Kelvin must fit in 16 bits")
        return build_frame(
            CMD_MODE,
            [
                submode,
                RGBIC_OP_COLOR,
                0xFF,
                0xFF,
                0xFF,
                (kelvin >> 8) & 0xFF,
                kelvin & 0xFF,
                *tint,
                *RGBIC_ALL_SEGMENTS,
            ],
        )
    if submode == SUBMODE_COLOR_RGBWW:
        # ``33 05 0D FF FF FF <kHi> <kLo> R G B``: white point, big-endian
        # kelvin, then the kelvin-derived tint. The app snaps kelvin to 100 K
        # before its tint lookup (Constant.getTemColorByKelvin), so do the same.
        # Confirmed on an H6181, firmware 2.03.11: the reply echoes the kelvin.
        snapped = max(0, min(0xFFFF, round(kelvin / 100) * 100))
        return build_frame(
            CMD_MODE,
            [submode, 0xFF, 0xFF, 0xFF, snapped >> 8, snapped & 0xFF, *kelvin_to_rgb(snapped)],
        )
    raise ValueError(f"Colour temperature is not supported on sub-mode {submode:#x}")


def scene_frame(scene_id: int) -> bytes:
    if not 0 <= scene_id <= 0xFF:
        raise ValueError("Scene id must fit in one byte")
    return build_frame(CMD_MODE, [SUBMODE_SCENE, scene_id])


# ---------------------------------------------------------------------------
# Read / heartbeat commands
# ---------------------------------------------------------------------------


def heartbeat_frame() -> bytes:
    """``AA 01 00``: keep-alive. The device answers with its power state."""
    return build_frame(CMD_POWER, frame_type=TYPE_READ)


power_query_frame = heartbeat_frame


def brightness_query_frame() -> bytes:
    return build_frame(CMD_BRIGHTNESS, frame_type=TYPE_READ)


def mode_query_frame() -> bytes:
    """``AA 05 01``: the ``01`` selector is required for mode reads."""
    return build_frame(CMD_MODE, [0x01], frame_type=TYPE_READ)


def version_query_frame() -> bytes:
    return build_frame(CMD_VERSION, frame_type=TYPE_READ)


def hardware_query_frame() -> bytes:
    """``AA 07 03``: hardware version string."""
    return build_frame(CMD_DEVICE_INFO, [INFO_HARDWARE], frame_type=TYPE_READ)


def ic_count_query_frame() -> bytes:
    """``AA 40``: IC count and segment count, the one live capability read the app does."""
    return build_frame(CMD_IC_COUNT, frame_type=TYPE_READ)


def light_count_query_frame() -> bytes:
    """``AA 0F``: light / bulb count (string lights, some dreamcolor strips)."""
    return build_frame(CMD_LIGHT_COUNT, frame_type=TYPE_READ)


def secret_query_frame() -> bytes:
    """``AA B1``: the pairing secret; only an unbound light answers."""
    return build_frame(CMD_SECRET_READ, frame_type=TYPE_READ)


def segment_colors_query_frame(group: int = 1) -> bytes:
    """``AA A5 <group>``: one page of per-segment colours."""
    if not 0 <= group <= 0xFF:
        raise ValueError("Segment group must fit in one byte")
    return build_frame(CMD_SEGMENT_COLORS, [group], frame_type=TYPE_READ)


# Commands whose reply echoes the selector byte, so a reply must match on it.
_SELECTOR_ECHO_COMMANDS = frozenset({CMD_DEVICE_INFO, CMD_SEGMENT_COLORS, 0xA2})


def reply_matches(request: bytes, reply: bytes) -> bool:
    """True if ``reply`` is the ``AA`` answer to ``request`` (same command, same selector where echoed)."""
    if len(reply) < 3 or reply[0] != TYPE_READ or reply[1] != request[1]:
        return False
    if request[1] in _SELECTOR_ECHO_COMMANDS:
        return reply[2] == request[2]
    return True


# ---------------------------------------------------------------------------
# Evidence hygiene
# ---------------------------------------------------------------------------


def frame_has_secret(frame: bytes) -> bool:
    """True for an ``AA B1 01 <secret>`` reply or a ``33 B2 <secret>`` write."""
    if len(frame) < 3:
        return False
    if frame[0] == TYPE_READ and frame[1] == CMD_SECRET_READ and frame[2] == 0x01:
        return True
    return frame[0] == TYPE_WRITE and frame[1] == CMD_SECRET_WRITE


def redact_frame(frame: bytes) -> tuple[bytes, bool]:
    """``(frame, redacted)``: the secret bytes zeroed and the checksum recomputed.

    Everything else is returned unchanged, so logs stay parseable and a
    consumer can still tell *that* a secret was exchanged.
    """
    frame = bytes(frame)
    if not frame_has_secret(frame) or len(frame) != FRAME_LENGTH:
        return frame, False
    start = 3 if frame[0] == TYPE_READ else 2
    body = frame[:start] + bytes(FRAME_LENGTH - 1 - start)
    return body + bytes([checksum(body)]), True


# ---------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ModeReport:
    """Decoded ``AA 05`` reply."""

    submode: int
    rgb: tuple[int, int, int] | None = None
    white_tint: tuple[int, int, int] | None = None
    color_temp_kelvin: int | None = None
    scene_id: int | None = None

    @property
    def is_white(self) -> bool:
        return self.white_tint is not None


@dataclass(slots=True)
class SegmentRecord:
    """One ``[bri R G B]`` record of an ``AA A5`` page."""

    brightness: int
    rgb: tuple[int, int, int]


@dataclass(slots=True)
class Reply:
    """One decoded frame from the status characteristic."""

    command: int
    power: bool | None = None
    brightness_level: int | None = None
    mode: ModeReport | None = None
    version: str | None = None
    hardware: str | None = None  # ``AA 07 03``
    ic_count: int | None = None  # ``AA 40``
    segments: int | None = None  # ``AA 40``
    light_count: int | None = None  # ``AA 0F``
    secret_present: bool | None = None  # ``AA B1``: True when 01 + 8 bytes followed
    secret: bytes | None = None  # the 8 bytes; keep it out of anything shared
    segment_group: int | None = None  # ``AA A5``
    segment_records: list[SegmentRecord] | None = None  # ``AA A5``
    raw_selector: int | None = None  # byte 2, for commands that echo one


def parse_reply(frame: bytes) -> Reply | None:
    """Decode an ``AA``-type read reply. Returns None for anything else."""
    frame = bytes(frame)
    if not verify_frame(frame) or frame[0] != TYPE_READ:
        return None
    command = frame[1]
    payload = frame[2:19]
    if command == CMD_POWER:
        if payload[0] not in (0, 1):
            return None
        return Reply(command, power=bool(payload[0]))
    if command == CMD_BRIGHTNESS:
        return Reply(command, brightness_level=payload[0])
    if command == CMD_MODE:
        return Reply(command, mode=_parse_mode(payload))
    if command == CMD_VERSION:
        text = payload.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
        return Reply(command, version=text or None)
    if command == CMD_DEVICE_INFO:
        reply = Reply(command, raw_selector=payload[0])
        if payload[0] == INFO_HARDWARE:
            reply.hardware = _ascii(payload[1:]) or None
        return reply
    if command == CMD_IC_COUNT:
        return Reply(command, ic_count=(payload[0] << 8) | payload[1], segments=payload[2])
    if command == CMD_LIGHT_COUNT:
        return Reply(command, light_count=payload[0])
    if command == CMD_SECRET_READ:
        present = payload[0] == 0x01
        return Reply(
            command, secret_present=present, secret=bytes(payload[1:9]) if present else None
        )
    if command == CMD_SEGMENT_COLORS:
        return Reply(
            command,
            segment_group=payload[0],
            raw_selector=payload[0],
            segment_records=_segment_records(payload[1:]),
        )
    return Reply(command)


def _ascii(data: bytes) -> str:
    """Printable ASCII up to the first NUL or non-printable byte, trailing spaces dropped."""
    out = []
    for byte in data:
        if byte == 0 or not 0x20 <= byte <= 0x7E:
            break
        out.append(chr(byte))
    return "".join(out).rstrip()


def _segment_records(data: bytes) -> list[SegmentRecord]:
    """``[bri R G B]`` x N; a page of all zeros means "no records"."""
    records = []
    for i in range(0, len(data) - 3, 4):
        bri, r, g, b = data[i : i + 4]
        if (bri, r, g, b) == (0, 0, 0, 0):
            break
        records.append(SegmentRecord(bri, (r, g, b)))
    return records


def segment_page_has(reply: Reply | None, rgb: tuple[int, int, int]) -> bool:
    """True if any record of an ``AA A5`` page carries ``rgb``."""
    if reply is None or not reply.segment_records:
        return False
    return any(record.rgb == tuple(rgb) for record in reply.segment_records)


def _parse_mode(payload: bytes) -> ModeReport:
    submode = payload[0]
    report = ModeReport(submode=submode)
    if submode == SUBMODE_COLOR_LEGACY:
        rgb = (payload[1], payload[2], payload[3])
        if payload[4] == WHITE_FLAG_WHITE:
            report.white_tint = (payload[5], payload[6], payload[7])
            report.rgb = rgb
        else:
            report.rgb = rgb
    elif submode == SUBMODE_COLOR_RGBIC:
        # ``AA 05 15 <op> <kHi> <kLo>``: unlike the write there is no white
        # point before the kelvin field, and the reply carries no RGB (the app
        # reads segment colours through a separate ``AA A5`` walk). A non-zero
        # kelvin means white mode; zero means a colour the reply cannot tell
        # us. Source: Brady-Woods/govee-ble-local GOVEE_BLE_GATT_PROTOCOL.md
        # section 4.2 (``SubModeColorV2.parse`` in the decompiled app).
        kelvin = (payload[2] << 8) | payload[3]
        if kelvin:
            report.color_temp_kelvin = kelvin
            report.white_tint = kelvin_to_rgb(kelvin)
            report.rgb = report.white_tint
    elif submode == SUBMODE_COLOR_RGBWW:
        # ``AA 05 0D R G B <kHi> <kLo> R2 G2 B2``: mirrors the write body.
        # Confirmed on an H6181 (fw 2.03.11): kelvin and tint are echoed back.
        report.rgb = (payload[1], payload[2], payload[3])
        kelvin = (payload[4] << 8) | payload[5]
        if kelvin:
            report.color_temp_kelvin = kelvin
            tint = (payload[6], payload[7], payload[8])
            report.white_tint = tint if any(tint) else kelvin_to_rgb(kelvin)
    elif submode == SUBMODE_COLOR_DREAMCOLOR:
        # The app's reader for a 0x0B reply is ``[gradual, kHi, kLo]`` with no
        # RGB (docs/govee-ble-protocol.md section 6.2); RGB comes from the
        # ``AA A2`` / ``AA A5`` segment reads instead. Only the sub-mode byte
        # is trusted here until a dreamcolor light is read back on the bench.
        pass
    elif submode == SUBMODE_SCENE:
        report.scene_id = payload[1]
    return report


# ---------------------------------------------------------------------------
# Colour temperature helpers
# ---------------------------------------------------------------------------


def kelvin_to_rgb(kelvin: int) -> tuple[int, int, int]:
    """Approximate the RGB tint of a black body at ``kelvin``.

    Tanner Helland's fit, which is what most open-source Govee clients use. The
    official app uses a lookup table in 100 K steps; the curves agree closely
    enough that the strip looks the same.
    """
    temp = max(1000, min(40000, kelvin)) / 100.0
    if temp <= 66:
        red = 255.0
        green = 99.4708025861 * _ln(temp) - 161.1195681661
        blue = 0.0 if temp <= 19 else 138.5177312231 * _ln(temp - 10) - 305.0447927307
    else:
        red = 329.698727446 * (temp - 60) ** -0.1332047592
        green = 288.1221695283 * (temp - 60) ** -0.0755148492
        blue = 255.0
    return (_clamp(red), _clamp(green), _clamp(blue))


def rgb_to_kelvin(rgb: tuple[int, int, int], lo: int = 2000, hi: int = 9000) -> int:
    """Invert :func:`kelvin_to_rgb` by nearest match on a 100 K grid."""
    best_k, best_dist = lo, None
    for kelvin in range(lo, hi + 1, 100):
        candidate = kelvin_to_rgb(kelvin)
        dist = sum((a - b) ** 2 for a, b in zip(candidate, rgb, strict=True))
        if best_dist is None or dist < best_dist:
            best_k, best_dist = kelvin, dist
    return best_k


def _ln(value: float) -> float:
    import math

    return math.log(value)


def _clamp(value: float) -> int:
    return int(max(0, min(255, round(value))))
