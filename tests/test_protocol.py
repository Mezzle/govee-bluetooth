"""Protocol tests. Fixture bytes come from captures published in the
BeauJBurroughs H6127, jurassic-marc H6159 and rnodern H617A reverse-engineering
repositories, plus the decompiled-app spec in Brady-Woods/govee-ble-local."""

from __future__ import annotations

import pytest

from custom_components.govee_bluetooth import protocol


def test_power_frames_match_captures() -> None:
    assert protocol.power_frame(True).hex() == "3301010000000000000000000000000000000033"
    assert protocol.power_frame(False).hex() == "3301000000000000000000000000000000000032"


def test_heartbeat_matches_capture() -> None:
    assert protocol.heartbeat_frame().hex() == "aa010000000000000000000000000000000000ab"


def test_mode_query_requires_selector_byte() -> None:
    frame = protocol.mode_query_frame()
    assert frame[:3] == bytes([0xAA, 0x05, 0x01])
    assert protocol.verify_frame(frame)


def test_h6159_colour_frames_match_captures() -> None:
    # From jurassic-marc/govee-h6159-light-strip-reverse-engineer bin/*.sh
    assert protocol.rgb_frame(255, 0, 0).hex() == "330502ff000000000000000000000000000000cb"
    assert protocol.rgb_frame(0, 255, 0).hex() == "33050200ff0000000000000000000000000000cb"
    assert protocol.rgb_frame(0, 0, 255).hex() == "3305020000ff00000000000000000000000000cb"


def test_rgbic_colour_frame_matches_h617a_capture() -> None:
    frame = protocol.rgb_frame(255, 0, 0, submode=protocol.SUBMODE_COLOR_RGBIC)
    assert frame.hex() == "33051501ff00000000000000ff7f00000000005d"


def test_rgbic_white_frame_layout() -> None:
    # ``33 05 15 01 FF FF FF <kHi> <kLo> R G B <mask>`` per the decompiled app
    # (Brady-Woods/govee-ble-local issue #15). 2700 K is 0x0A8C.
    frame = protocol.color_temp_frame(2700, submode=protocol.SUBMODE_COLOR_RGBIC)
    assert frame[:7] == bytes([0x33, 0x05, 0x15, 0x01, 0xFF, 0xFF, 0xFF])
    assert frame[7:9] == bytes([0x0A, 0x8C])
    assert tuple(frame[9:12]) == protocol.kelvin_to_rgb(2700)
    assert frame[12:14] == bytes([0xFF, 0x7F])  # same whole-strip mask as the colour frame
    assert frame[14:19] == bytes(5)
    assert protocol.verify_frame(frame)

    cool = protocol.color_temp_frame(6500, submode=protocol.SUBMODE_COLOR_RGBIC)
    assert cool[7:9] == bytes([0x19, 0x64])


def test_colour_temperature_rejects_unknown_submode() -> None:
    with pytest.raises(ValueError):
        protocol.color_temp_frame(2700, submode=0x0C)
    with pytest.raises(ValueError):
        protocol.rgb_frame(1, 2, 3, submode=0x0C)


def test_white_frame_sets_flag_and_tint() -> None:
    frame = protocol.color_temp_frame(2700)
    assert frame[:7] == bytes([0x33, 0x05, 0x02, 0xFF, 0xFF, 0xFF, 0x01])
    tint = tuple(frame[7:10])
    assert tint == protocol.kelvin_to_rgb(2700)
    assert tint[0] == 255 and tint[2] < 200  # warm white is red-heavy
    assert protocol.verify_frame(frame)


def test_brightness_scaling() -> None:
    assert protocol.brightness_frame(255, 255)[2] == 0xFF
    assert protocol.brightness_frame(1, 255)[2] == 0x01
    assert (
        protocol.brightness_frame(50 * 255 // 100 + 1, 100).hex()
        == "3304320000000000000000000000000000000005"
    )
    assert protocol.brightness_frame(255, 100).hex() == "3304640000000000000000000000000000000053"
    # Never emit zero: it turns the strip off.
    assert protocol.brightness_frame(1, 100)[2] == 1
    with pytest.raises(ValueError):
        protocol.brightness_frame(0, 255)


def test_device_to_ha_brightness_roundtrip() -> None:
    for value in (1, 2, 50, 128, 254, 255):
        assert protocol.device_to_ha_brightness(value, 255) == value
    assert protocol.device_to_ha_brightness(100, 100) == 255
    assert protocol.device_to_ha_brightness(50, 100) == 128


def test_scene_frame_matches_capture() -> None:
    assert protocol.scene_frame(0x0F).hex() == "3305040f0000000000000000000000000000003d"


def test_parse_power_reply() -> None:
    reply = protocol.parse_reply(bytes.fromhex("aa010100000000000000000000000000000000aa"))
    assert reply is not None and reply.power is True
    reply = protocol.parse_reply(bytes.fromhex("aa010000000000000000000000000000000000ab"))
    assert reply is not None and reply.power is False


def test_parse_brightness_reply() -> None:
    frame = protocol.build_frame(0x04, [0x80], frame_type=0xAA)
    reply = protocol.parse_reply(frame)
    assert reply is not None and reply.brightness_level == 0x80


def test_parse_mode_reply_colour_and_white() -> None:
    colour = protocol.build_frame(0x05, [0x02, 10, 20, 30, 0x00], frame_type=0xAA)
    reply = protocol.parse_reply(colour)
    assert reply and reply.mode and reply.mode.rgb == (10, 20, 30) and not reply.mode.is_white

    white = protocol.build_frame(0x05, [0x02, 255, 255, 255, 0x01, 255, 180, 107], frame_type=0xAA)
    reply = protocol.parse_reply(white)
    assert reply and reply.mode and reply.mode.is_white
    assert reply.mode.white_tint == (255, 180, 107)

    scene = protocol.build_frame(0x05, [0x04, 0x09], frame_type=0xAA)
    reply = protocol.parse_reply(scene)
    assert reply and reply.mode and reply.mode.scene_id == 0x09


def test_parse_rgbic_mode_reply() -> None:
    # ``AA 05 15 01 <kHi> <kLo>``: kelvin directly after the op byte, no white
    # point (govee-ble-local GOVEE_BLE_GATT_PROTOCOL.md section 4.2).
    white = protocol.build_frame(0x05, [0x15, 0x01, 0x0A, 0x8C], frame_type=0xAA)
    reply = protocol.parse_reply(white)
    assert reply and reply.mode and reply.mode.submode == 0x15
    assert reply.mode.is_white
    assert reply.mode.color_temp_kelvin == 2700
    assert reply.mode.white_tint == protocol.kelvin_to_rgb(2700)

    # Kelvin 0: the strip is in colour mode but the reply carries no RGB.
    colour = protocol.build_frame(0x05, [0x15, 0x01, 0x00, 0x00], frame_type=0xAA)
    reply = protocol.parse_reply(colour)
    assert reply and reply.mode and reply.mode.submode == 0x15
    assert not reply.mode.is_white
    assert reply.mode.color_temp_kelvin is None
    assert reply.mode.rgb is None


def test_parse_rejects_bad_checksum_and_other_types() -> None:
    bad = bytearray(protocol.power_frame(True))
    bad[-1] ^= 0xFF
    assert protocol.parse_reply(bytes(bad)) is None
    assert protocol.parse_reply(protocol.power_frame(True)) is None  # 0x33 write, not a reply
    assert protocol.parse_reply(b"\xaa\x01") is None


def test_kelvin_roundtrip() -> None:
    for kelvin in (2000, 2700, 4000, 6500, 9000):
        assert protocol.rgb_to_kelvin(protocol.kelvin_to_rgb(kelvin)) == kelvin


def test_rgbww_colour_frames() -> None:
    frame = protocol.rgb_frame(255, 0, 0, submode=protocol.SUBMODE_COLOR_RGBWW)
    assert frame[:6] == bytes([0x33, 0x05, 0x0D, 0xFF, 0x00, 0x00])
    assert protocol.verify_frame(frame)
    white = protocol.color_temp_frame(2749, submode=protocol.SUBMODE_COLOR_RGBWW)
    # Snapped to 2700 K = 0x0A8C, white point FF FF FF, then the tint.
    assert white[2:8] == bytes([0x0D, 0xFF, 0xFF, 0xFF, 0x0A, 0x8C])
    assert white[8:11] == bytes(protocol.kelvin_to_rgb(2700))


def test_parse_rgbww_mode_reply() -> None:
    body = bytes([0xAA, 0x05, 0x0D, 0x00, 0x00, 0xFF, 0x00, 0x00, 0, 0, 0]).ljust(19, b"\x00")
    reply = protocol.parse_reply(body + bytes([protocol.checksum(body)]))
    assert reply is not None and reply.mode is not None
    assert reply.mode.rgb == (0, 0, 255) and not reply.mode.is_white
    body = bytes([0xAA, 0x05, 0x0D, 0xFF, 0xFF, 0xFF, 0x0A, 0x8C, 0xFF, 0xA9, 0x5D]).ljust(
        19, b"\x00"
    )
    reply = protocol.parse_reply(body + bytes([protocol.checksum(body)]))
    assert reply.mode.color_temp_kelvin == 2700 and reply.mode.white_tint == (0xFF, 0xA9, 0x5D)


def test_dreamcolor_colour_frame_layout() -> None:
    frame = protocol.rgb_frame(0x12, 0x34, 0x56, submode=protocol.SUBMODE_COLOR_DREAMCOLOR)
    assert frame[:8] == bytes([0x33, 0x05, 0x0B, 0x12, 0x34, 0x56, 0xFF, 0x7F])
    assert frame[8:19] == bytes(11)
    assert protocol.verify_frame(frame)
    with pytest.raises(ValueError):
        protocol.color_temp_frame(4000, submode=protocol.SUBMODE_COLOR_DREAMCOLOR)


def test_dreamcolor_mode_reply_names_submode_only() -> None:
    # The app reads a 0x0B reply as [gradual, kHi, kLo]; there is no RGB in it.
    body = bytes([0xAA, 0x05, 0x0B, 0x00, 0x00, 0x00]).ljust(19, b"\x00")
    reply = protocol.parse_reply(body + bytes([protocol.checksum(body)]))
    assert reply is not None and reply.mode is not None
    assert reply.mode.submode == 0x0B
    assert reply.mode.rgb is None and not reply.mode.is_white


def test_read_frames_for_the_census() -> None:
    assert protocol.hardware_query_frame()[:3] == bytes([0xAA, 0x07, 0x03])
    assert protocol.ic_count_query_frame()[:3] == bytes([0xAA, 0x40, 0x00])
    assert protocol.light_count_query_frame()[:2] == bytes([0xAA, 0x0F])
    assert protocol.secret_query_frame().hex() == "aab100000000000000000000000000000000001b"
    assert protocol.segment_colors_query_frame(1)[:3] == bytes([0xAA, 0xA5, 0x01])
    with pytest.raises(ValueError):
        protocol.segment_colors_query_frame(256)


def test_reply_matching_uses_the_selector_where_echoed() -> None:
    hw = protocol.hardware_query_frame()
    assert protocol.reply_matches(hw, protocol.build_frame(0x07, [0x03, *b"1.0"], frame_type=0xAA))
    assert not protocol.reply_matches(hw, protocol.build_frame(0x07, [0x02], frame_type=0xAA))
    mode = protocol.mode_query_frame()
    assert protocol.reply_matches(mode, protocol.build_frame(0x05, [0x15, 0x01], frame_type=0xAA))
    assert not protocol.reply_matches(mode, protocol.build_frame(0x04, [1], frame_type=0xAA))
    assert not protocol.reply_matches(
        mode, protocol.build_frame(0x05, [0x02])
    )  # a 33 echo is an ack


def test_parse_capability_replies() -> None:
    hw = protocol.parse_reply(protocol.build_frame(0x07, [0x03, *b"3.01.01"], frame_type=0xAA))
    assert hw is not None and hw.hardware == "3.01.01" and hw.raw_selector == 3
    ic = protocol.parse_reply(protocol.build_frame(0x40, [0x01, 0x2C, 0x0F], frame_type=0xAA))
    assert ic is not None and (ic.ic_count, ic.segments) == (300, 15)
    lc = protocol.parse_reply(protocol.build_frame(0x0F, [20], frame_type=0xAA))
    assert lc is not None and lc.light_count == 20
    secret = protocol.parse_reply(
        protocol.build_frame(0xB1, [1, 8, 7, 6, 5, 4, 3, 2, 1], frame_type=0xAA)
    )
    assert (
        secret is not None
        and secret.secret_present
        and secret.secret == bytes([8, 7, 6, 5, 4, 3, 2, 1])
    )
    absent = protocol.parse_reply(protocol.build_frame(0xB1, [0], frame_type=0xAA))
    assert absent is not None and absent.secret_present is False and absent.secret is None
    # GENZERO's H617A page: AA A5 01, three [bri R G B] records.
    page = protocol.parse_reply(
        protocol.build_frame(
            0xA5,
            [1, 0x64, 0xFF, 0x8D, 0x0B, 0x64, 0xAA, 0x55, 0x11, 0x64, 0xFF, 0x8D, 0x0B],
            frame_type=0xAA,
        )
    )
    assert page is not None and page.segment_group == 1 and len(page.segment_records or []) == 3
    assert (
        page.segment_records[1].rgb == (0xAA, 0x55, 0x11)
        and page.segment_records[1].brightness == 100
    )
    assert protocol.segment_page_has(page, (0xAA, 0x55, 0x11))
    assert not protocol.segment_page_has(page, (1, 2, 3))
    assert not protocol.segment_page_has(None, (1, 2, 3))


def test_secret_frames_are_redacted_but_stay_valid() -> None:
    reply = protocol.build_frame(0xB1, [1, 1, 2, 3, 4, 5, 6, 7, 8], frame_type=0xAA)
    assert protocol.frame_has_secret(reply)
    redacted, changed = protocol.redact_frame(reply)
    assert changed and redacted[:3] == bytes([0xAA, 0xB1, 0x01]) and redacted[3:19] == bytes(16)
    assert protocol.verify_frame(redacted)
    write = protocol.build_frame(0xB2, [9] * 8)
    redacted, changed = protocol.redact_frame(write)
    assert changed and redacted[2:19] == bytes(17) and protocol.verify_frame(redacted)
    absent = protocol.build_frame(0xB1, [0], frame_type=0xAA)
    assert not protocol.frame_has_secret(absent) and protocol.redact_frame(absent) == (
        absent,
        False,
    )
    assert protocol.redact_frame(protocol.power_frame(True)) == (protocol.power_frame(True), False)
