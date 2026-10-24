"""Synthetic integration contracts with an in-memory transport only."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.govee_bluetooth import protocol
from custom_components.govee_bluetooth.device import GoveeBleDevice
from custom_components.govee_bluetooth.models import profile_for


async def scripted_device(model, *, firmware="3.02.24", missing=None, uniform=False):
    device = GoveeBleDevice(Mock(), "AA:BB:CC:DD:EE:FF", "Synthetic", profile_for(model), 60)
    device._async_connect = AsyncMock()
    device._touch = Mock()
    device._exchange.connected(["write"])
    device.pact = (10, 1)
    sent = []

    async def send(frame, response, note):
        sent.append(frame)
        command = frame[1]
        payload = {1: [1], 4: [50], 5: [0x15, 1, 0, 0], 6: list(firmware.encode())}.get(command)
        if command == 0xA5 and frame[2] != missing:
            # Black/off first record must not truncate the two following records.
            payload = [
                frame[2],
                0,
                0,
                0,
                0,
                *([0, 0, 0, 0] if uniform else [50, 12, 34, 56]),
                *([0, 0, 0, 0] if uniform else [100, 90, 80, 70]),
            ]
        if payload is not None:
            device._on_notify(0, bytearray(protocol.build_frame(command, payload, frame_type=0xAA)))

    device._exchange._send = send
    return device, sent


@pytest.mark.parametrize("uniform", [False, True])
async def test_refresh_uses_complete_qualified_page_set(uniform):
    device, sent = await scripted_device("H617A", uniform=uniform)
    with patch(
        "custom_components.govee_bluetooth.device.bluetooth.async_last_service_info",
        return_value=None,
    ):
        state = await device.async_update()
    assert state.extra["pages_complete"] is True
    assert len(state.extra["segment_pages"]) == 5
    assert state.extra["segment_pages"][1][0]["rgb"] == (0, 0, 0)
    assert state.rgb_color == ((0, 0, 0) if uniform else None)
    assert [f[2] for f in sent if f[1] == 0xA5] == [1, 2, 3, 4, 5]
    assert all(f[0] == 0xAA for f in sent)
    assert state.provenance["rgb_color"]["source"] == ("observed" if uniform else "unknown")


async def test_missing_page_is_partial_and_silence_cached_without_failing_light():
    device, sent = await scripted_device("H617A", missing=2)
    device._exchange._timeout = 0.001
    with patch(
        "custom_components.govee_bluetooth.device.bluetooth.async_last_service_info",
        return_value=None,
    ):
        state = await device.async_update()
        assert state.is_on
        assert state.extra["pages_complete"] is False
        assert len(state.extra["segment_pages"]) == 1
        await device.async_update()
    assert [f[2] for f in sent if f[1] == 0xA5] == [1, 2]
    assert device.optional_reads["colors"]["outcome"] == "unanswered"


async def test_unknown_firmware_does_not_enable_pages_or_change_writer():
    device, sent = await scripted_device("H617A", firmware="future")
    with patch(
        "custom_components.govee_bluetooth.device.bluetooth.async_last_service_info",
        return_value=None,
    ):
        await device.async_update()
    assert not any(f[1] == 0xA5 for f in sent)
    device._apply_observed_submode(0x0D)
    assert device.profile.color_submode == 0x15
    assert device.profile.brightness_scale == 100


async def test_observed_optimistic_derived_unknown_and_partial_updates():
    device, _ = await scripted_device("H6159")
    device._async_write_frames = AsyncMock()
    assert device.state.provenance["rgb_color"]["source"] == "unknown"
    await device.async_turn_on(color_temp_kelvin=4300, brightness=123)
    assert device.state.provenance["brightness"]["source"] == "optimistic"
    assert device.state.provenance["rgb_color"]["source"] == "derived"
    desired = dict(device.desired_state)
    device._on_notify(0, bytearray(protocol.build_frame(4, [42], frame_type=0xAA)))
    assert device.state.brightness == 42
    assert device.state.provenance["brightness"]["source"] == "observed"
    assert device.desired_state == desired
    old_rgb = device.state.rgb_color
    old_provenance = dict(device.state.provenance["rgb_color"])
    device._on_notify(0, bytearray(protocol.build_frame(1, [0], frame_type=0xAA)))
    assert device.state.rgb_color == old_rgb
    assert device.state.provenance["rgb_color"] == old_provenance
    device._on_notify(0, bytearray(protocol.build_frame(5, [4, 7, 0], frame_type=0xAA)))
    assert device.state.rgb_color is None
    assert device.state.provenance["rgb_color"]["source"] == "unknown"


async def test_optional_identity_silence_cached_across_connection_and_rearmed_by_revision():
    device, sent = await scripted_device("H6159")
    device._exchange._timeout = 0.001
    with patch(
        "custom_components.govee_bluetooth.device.bluetooth.async_last_service_info",
        return_value=None,
    ):
        await device.async_update()
        assert device.state.is_on
        assert device.state.hardware is None
        assert device.optional_reads["hardware"]["outcome"] == "unanswered"
        device._firmware_checked = False  # next connection, unchanged identity
        await device.async_update()
        assert sum(f[1] == 7 for f in sent) == 1
        await device._collect_optional("new-revision", None)
        assert sum(f[1] == 7 for f in sent) == 2


async def test_section_count_is_cached_and_does_not_change_write_mask():
    device, sent = await scripted_device("H6062")
    original_send = device._exchange._send

    async def send(frame, response, note):
        await original_send(frame, response, note)
        if frame[1] == 0x41:
            device._on_notify(0, bytearray(protocol.build_frame(0x41, [0, 72, 6], frame_type=0xAA)))

    device._exchange._send = send
    writer = protocol.rgb_frame(1, 2, 3, device.profile.color_submode)
    with patch(
        "custom_components.govee_bluetooth.device.bluetooth.async_last_service_info",
        return_value=None,
    ):
        await device.async_update()
        await device.async_update()
    assert sum(f[1] == 0x41 for f in sent) == 1
    assert device.optional_reads["topology"]["decoded"] == {
        "pairs": [{"ic_count": 72, "section_count": 6}]
    }
    assert protocol.rgb_frame(1, 2, 3, device.profile.color_submode) == writer


async def test_bulk_refresh_and_push_are_partial_and_profile_scoped():
    from tests.test_readback import burst

    device, sent = await scripted_device("H60A6")
    original_send = device._exchange._send
    data = bytes([1, 1, 0, 4, 1, 25, 5, 0, 254, 10, *range(10)])

    # The basic fake dispatcher keys commands only; AC uses a distinct envelope.
    async def scoped_send(frame, response, note):
        if frame[0] == 0xAC:
            sent.append(frame)
            for packet in burst(data):
                device._on_notify(0, bytearray(packet))
        else:
            await original_send(frame, response, note)

    device._exchange._send = scoped_send
    with patch(
        "custom_components.govee_bluetooth.device.bluetooth.async_last_service_info",
        return_value=None,
    ):
        state = await device.async_update()
    assert state.extra["bulk_complete"] is True
    assert state.is_on is False
    assert state.extra["brightness_raw"] == 25
    assert state.brightness == round(25 * 255 / device.profile.brightness_scale)
    assert state.extra["submode"] == 0x15  # Empty mode did not become mode zero.
    device._on_notify(0, bytearray(protocol.build_frame(0x30, [0, 1, 0, 0], frame_type=0xEE)))
    assert device.state.is_on is True
    device._on_notify(0, bytearray(protocol.build_frame(0x20, [42], frame_type=0xEE)))
    assert device.state.brightness == protocol.device_to_ha_brightness(
        42, device.profile.brightness_scale
    )
    assert device.state.provenance["brightness"]["source"] == "observed"


async def test_read_scale_is_independent_and_manual_override_wins():
    from dataclasses import replace

    device, _ = await scripted_device("H617A")
    device.read_profile = replace(device.read_profile, brightness_scale=100)
    device.profile = replace(device.profile, brightness_scale=255)
    device._apply_partial_reply(protocol.Reply(4, brightness_level=50))
    assert device.state.brightness == round(50 * 255 / 100)
    device._brightness_scale_locked = True
    device._apply_partial_reply(protocol.Reply(4, brightness_level=50))
    assert device.state.brightness == 50


async def test_missing_rgbww_tint_is_marked_derived_but_bulb_slot_is_observed():
    for model, expected in (("H6181", "derived"), ("H6006", "observed")):
        device, _ = await scripted_device(model)
        frame = protocol.build_frame(5, [0x0D, 255, 200, 100, 0x10, 0xCC, 0, 0, 0], frame_type=0xAA)
        device._apply_partial_reply(protocol.parse_reply(frame))
        assert device.state.provenance["rgb_color"]["source"] == expected
        assert device.state.color_temp_kelvin == 4300
        assert device.state.provenance["color_temp_kelvin"]["source"] == "observed"
        if model == "H6006":
            assert device.state.rgb_color == (255, 200, 100)


async def test_saved_bench_frames_merge_as_observations_without_new_qualification():
    import json
    from pathlib import Path

    cases = json.loads(
        (
            Path(__file__).parents[1] / "docs/bench-evidence/2026-10-23-additional/fixtures.json"
        ).read_text()
    )["cases"]
    devices = {}
    observations = 0
    for case in cases:
        if case["model"] not in devices:
            devices[case["model"]], _ = await scripted_device(case["model"])
        device = devices[case["model"]]
        device._on_notify(0, bytearray.fromhex(case["frame"]))
        expected = case["expected"]
        if "power" in expected:
            assert device.state.is_on == expected["power"]
            assert device.state.provenance["is_on"]["source"] == "observed"
            observations += 1
        if "brightness_level" in expected:
            assert device.state.brightness == protocol.device_to_ha_brightness(
                expected["brightness_level"], device.profile.brightness_scale
            )
            assert device.state.provenance["brightness"]["source"] == "observed"
            observations += 1
        if "rgb" in expected and not expected.get("is_white"):
            assert device.state.rgb_color == tuple(expected["rgb"])
            assert device.state.provenance["rgb_color"]["source"] == "observed"
            observations += 1
        if expected.get("kelvin"):
            assert device.state.color_temp_kelvin == expected["kelvin"]
            assert device.state.provenance["color_temp_kelvin"]["source"] == "observed"
            observations += 1
    assert observations >= 6
