"""Unit tests for the dialect probe over a scripted fake link. No Bluetooth, no HA."""

from __future__ import annotations

import pytest

from custom_components.govee_bluetooth import probe, protocol
from custom_components.govee_bluetooth.probe import (
    ActiveProbeResult,
    ProbeConnectionLost,
    SafeProbeResult,
    async_active_probe,
    async_safe_probe,
    candidate_order,
    echoes,
    echoes_kelvin,
    pick_test_kelvin,
)
from custom_components.govee_bluetooth.protocol import ModeReport, Reply


def _mode_frame(submode: int, *payload: int) -> bytes:
    return protocol.build_frame(
        protocol.CMD_MODE, [submode, *payload], frame_type=protocol.TYPE_READ
    )


class FakeFirmware:
    """A light that understands exactly one colour sub-mode (or none).

    ``mode`` is the raw mode reply payload. A colour write in the understood
    sub-mode updates it the way real firmware echoes the write; every other
    write is ignored. ``echo_submode_only`` models firmware that flips the
    sub-mode byte but reports stale RGB, which the probe must reject. A 0x15
    firmware echoes the kelvin of a white write and nothing else, as the
    decompiled app's ``SubModeColorV2.parse`` describes.
    """

    def __init__(
        self,
        *,
        speaks: int | None,
        mode: bytes,
        power: bool = True,
        brightness: int = 50,
        firmware: str = "2.03.11",
        drop_after_writes: int | None = None,
        echo_submode_only: bool = False,
    ) -> None:
        self.speaks = speaks
        self.mode = mode
        self.power = power
        self.brightness = brightness
        self.firmware = firmware
        self.drop_after_writes = drop_after_writes
        self.echo_submode_only = echo_submode_only
        self.writes: list[bytes] = []
        self.connected = True

    async def write(self, frame: bytes) -> None:
        if not self.connected:
            raise ProbeConnectionLost("link closed")
        self.writes.append(frame)
        if self.drop_after_writes is not None and len(self.writes) > self.drop_after_writes:
            self.connected = False
            raise ProbeConnectionLost("link dropped")
        command, payload = frame[1], frame[2:19]
        if command == protocol.CMD_POWER:
            self.power = bool(payload[0])
        elif command == protocol.CMD_BRIGHTNESS:
            self.brightness = payload[0]
        elif command == protocol.CMD_MODE and payload[0] == self.speaks:
            if self.echo_submode_only:
                self.mode = _mode_frame(payload[0], 0xAA, 0xBB, 0xCC)
            elif payload[0] == protocol.SUBMODE_COLOR_RGBIC:
                # ``33 05 15 01 FF FF FF kHi kLo ...`` -> ``AA 05 15 01 kHi kLo``
                self.mode = _mode_frame(payload[0], payload[1], payload[5], payload[6])
            else:
                self.mode = _mode_frame(payload[0], *payload[1:4])

    async def query(self, frame: bytes, command: int) -> Reply | None:
        if not self.connected:
            raise ProbeConnectionLost("link closed")
        if command == protocol.CMD_POWER:
            reply = protocol.build_frame(command, [int(self.power)], frame_type=protocol.TYPE_READ)
        elif command == protocol.CMD_BRIGHTNESS:
            reply = protocol.build_frame(command, [self.brightness], frame_type=protocol.TYPE_READ)
        elif command == protocol.CMD_MODE:
            reply = self.mode
        elif command == protocol.CMD_VERSION:
            reply = protocol.build_frame(
                command, list(self.firmware.encode()), frame_type=protocol.TYPE_READ
            )
        else:
            return None
        return protocol.parse_reply(reply)

    @property
    def mode_writes(self) -> list[bytes]:
        return [w for w in self.writes if w[0] == protocol.TYPE_WRITE and w[1] == protocol.CMD_MODE]


# ---------------------------------------------------------------------------
# Step 1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (_mode_frame(0x02, 10, 20, 30, 0), 0x02),
        (_mode_frame(0x0D, 10, 20, 30, 0, 0), 0x0D),
        (_mode_frame(0x15, 0x01, 0, 0), 0x15),
    ],
)
async def test_safe_probe_detects_each_colour_submode(mode: bytes, expected: int) -> None:
    link = FakeFirmware(speaks=expected, mode=mode)
    result = await async_safe_probe(link)
    assert result.detected_submode == expected
    assert result.outcome == "color"
    assert result.firmware == "2.03.11"
    assert result.power is True
    assert result.brightness_level == 50
    assert link.writes == []  # read-only: nothing of type 0x33 was sent


@pytest.mark.parametrize(
    ("mode", "outcome"),
    [
        (_mode_frame(protocol.SUBMODE_SCENE, 0x04), "scene"),
        (_mode_frame(protocol.SUBMODE_MUSIC, 0x00), "music"),
        (_mode_frame(0x7E), "unknown"),
    ],
)
async def test_safe_probe_falls_through_on_non_colour_modes(mode: bytes, outcome: str) -> None:
    result = await async_safe_probe(FakeFirmware(speaks=None, mode=mode))
    assert result.detected_submode is None
    assert result.outcome == outcome


async def test_safe_probe_reports_no_reply() -> None:
    class Silent(FakeFirmware):
        async def query(self, frame: bytes, command: int) -> Reply | None:
            return None

    result = await async_safe_probe(Silent(speaks=None, mode=b""))
    assert result.outcome == "no_reply"
    assert not result.answered


# ---------------------------------------------------------------------------
# Step 2
# ---------------------------------------------------------------------------


async def test_active_probe_stops_at_first_echo() -> None:
    light = FakeFirmware(speaks=0x02, mode=_mode_frame(protocol.SUBMODE_SCENE, 0x04))
    result = await async_active_probe(light, candidates=(0x02, 0x0D))
    assert result.detected_submode == 0x02
    assert result.attempted == [0x02]
    assert result.complete
    # Exactly one colour write went out, in the accepted sub-mode.
    assert [w[2] for w in light.mode_writes] == [0x02]


async def test_active_probe_moves_on_when_first_candidate_is_ignored() -> None:
    light = FakeFirmware(speaks=0x0D, mode=_mode_frame(protocol.SUBMODE_SCENE, 0x04))
    result = await async_active_probe(light, candidates=(0x02, 0x0D))
    assert result.detected_submode == 0x0D
    assert result.attempted == [0x02, 0x0D]


async def test_active_probe_rejects_submode_only_echo_with_mismatched_rgb() -> None:
    light = FakeFirmware(
        speaks=0x02, mode=_mode_frame(protocol.SUBMODE_SCENE, 0x04), echo_submode_only=True
    )
    result = await async_active_probe(light, candidates=(0x02, 0x0D))
    assert result.detected_submode is None
    assert result.attempted == [0x02, 0x0D]
    assert result.complete


async def test_active_probe_rejects_stale_submode_from_previous_mode() -> None:
    """The light was already in 0x02 showing another colour and ignores every write."""
    light = FakeFirmware(speaks=None, mode=_mode_frame(0x02, 1, 2, 3, 0))
    result = await async_active_probe(light, candidates=(0x02, 0x0D))
    assert result.detected_submode is None


async def test_active_probe_never_sends_scene_ids_or_brightness_above_100() -> None:
    light = FakeFirmware(
        speaks=0x0D, mode=_mode_frame(protocol.SUBMODE_SCENE, 0x04), brightness=200
    )
    result = await async_active_probe(light)
    assert result.detected_submode == 0x0D
    for frame in light.writes:
        assert frame[0] == protocol.TYPE_WRITE
        if frame[1] == protocol.CMD_MODE:
            assert frame[2] != protocol.SUBMODE_SCENE
        if frame[1] == protocol.CMD_BRIGHTNESS:
            assert frame[2] <= probe.MAX_PROBE_BRIGHTNESS
    assert not any(w[1] == protocol.CMD_BRIGHTNESS for w in light.writes)
    assert result.restore_note is not None and "brightness 200" in result.restore_note


async def test_active_probe_restores_colour_brightness_and_power() -> None:
    """A light in 0x0D colour mode, off, at brightness 40, ends up back that way."""
    light = FakeFirmware(
        speaks=0x0D, mode=_mode_frame(0x0D, 9, 8, 7, 0, 0), power=False, brightness=40
    )
    result = await async_active_probe(light, candidates=(0x02, 0x0D))
    assert result.detected_submode == 0x0D
    assert result.restored
    assert result.restore_note is None
    assert light.power is False
    assert light.brightness == 40
    assert protocol.parse_reply(light.mode).mode.rgb == (9, 8, 7)
    # Power on first so writes take effect, power off last.
    assert light.writes[0] == protocol.power_frame(True)
    assert light.writes[-1] == protocol.power_frame(False)
    assert light.writes[-2] == protocol.build_frame(protocol.CMD_BRIGHTNESS, [40])


async def test_active_probe_restores_white_via_kelvin() -> None:
    light = FakeFirmware(
        speaks=0x0D, mode=_mode_frame(0x0D, 255, 255, 255, 0x0F, 0xA0, 255, 200, 150)
    )
    result = await async_active_probe(light, candidates=(0x0D,))
    assert result.detected_submode == 0x0D
    assert light.mode_writes[-1] == protocol.color_temp_frame(4000, 0x0D)


async def test_active_probe_cannot_restore_a_scene_and_says_so() -> None:
    light = FakeFirmware(speaks=0x02, mode=_mode_frame(protocol.SUBMODE_SCENE, 0x09))
    result = await async_active_probe(light, candidates=(0x02,))
    assert result.detected_submode == 0x02
    assert not result.restored
    assert "scene" in (result.restore_note or "")
    assert not any(w[1] == protocol.CMD_MODE and w[2] == 0x04 for w in light.writes)


async def test_active_probe_stops_on_disconnect_without_retry() -> None:
    light = FakeFirmware(
        speaks=0x0D, mode=_mode_frame(protocol.SUBMODE_SCENE, 0x04), drop_after_writes=1
    )
    result = await async_active_probe(light, candidates=(0x02, 0x0D))
    assert not result.complete
    assert result.error is not None and "dropped" in result.error
    assert result.detected_submode is None
    assert result.attempted == [0x02, 0x0D]  # second write was the one that dropped the link
    assert len(light.writes) == 2  # nothing was retried after the drop
    assert not result.restored


async def test_active_probe_disconnect_during_record_step() -> None:
    class Dead(FakeFirmware):
        async def query(self, frame: bytes, command: int) -> Reply | None:
            raise ProbeConnectionLost("gone")

    result = await async_active_probe(Dead(speaks=0x02, mode=b""))
    assert isinstance(result, ActiveProbeResult)
    assert result.error == "disconnected: gone"
    assert result.attempted == []


def test_active_probe_refuses_non_candidates() -> None:
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(
            async_active_probe(
                FakeFirmware(speaks=None, mode=b""), candidates=(protocol.SUBMODE_SCENE,)
            )
        )


# ---------------------------------------------------------------------------
# Step 2, the 0x15 (RGBIC) path: a white write confirmed by its kelvin
# ---------------------------------------------------------------------------


async def test_active_probe_detects_rgbic_firmware_via_kelvin_echo() -> None:
    """An RGBIC strip in a scene ignores 0x02 and 0x0D, then echoes the 0x15 white."""
    light = FakeFirmware(speaks=0x15, mode=_mode_frame(protocol.SUBMODE_SCENE, 0x04))
    result = await async_active_probe(light)
    assert result.detected_submode == 0x15
    assert result.attempted == [0x02, 0x0D, 0x15]
    assert result.test_kelvin == probe.TEST_KELVINS[0]
    assert result.complete
    # The 0x15 write was a white at the test temperature, whole-strip mask.
    assert light.mode_writes[-1] == protocol.color_temp_frame(probe.TEST_KELVINS[0], 0x15)
    # No RGB test colour went out in 0x15 (the probe writes whites there).
    for frame in light.mode_writes:
        if frame[2] == 0x15:
            assert frame[3:7] == bytes([0x01, 0xFF, 0xFF, 0xFF])


async def test_active_probe_rgbic_first_when_table_says_so() -> None:
    light = FakeFirmware(speaks=0x15, mode=_mode_frame(protocol.SUBMODE_SCENE, 0x04))
    result = await async_active_probe(light, candidates=candidate_order(0x15))
    assert result.detected_submode == 0x15
    assert result.attempted == [0x15]
    assert len(light.mode_writes) == 1


async def test_active_probe_rejects_stale_rgbic_kelvin() -> None:
    """The light already reports 0x15 at the first test kelvin and ignores every write.

    The probe must pick the other test kelvin, and then the stale reply is not
    an echo.
    """
    first = probe.TEST_KELVINS[0]
    light = FakeFirmware(speaks=None, mode=_mode_frame(0x15, 0x01, first >> 8, first & 0xFF))
    result = await async_active_probe(light, candidates=(0x15,))
    assert result.test_kelvin == probe.TEST_KELVINS[1]
    assert result.detected_submode is None
    assert result.complete


async def test_active_probe_rgbic_kelvin_only_echo_is_not_enough_for_other_dialects() -> None:
    """A 0x0D light that happens to report the test kelvin does not pass as 0x15."""
    first = probe.TEST_KELVINS[0]
    light = FakeFirmware(
        speaks=None, mode=_mode_frame(0x0D, 255, 255, 255, first >> 8, first & 0xFF, 1, 2, 3)
    )
    result = await async_active_probe(light, candidates=(0x15,))
    assert result.detected_submode is None


async def test_active_probe_restores_rgbic_white() -> None:
    """A 0x15 strip showing 3000 K white is put back to 3000 K afterwards."""
    light = FakeFirmware(speaks=0x15, mode=_mode_frame(0x15, 0x01, 3000 >> 8, 3000 & 0xFF))
    result = await async_active_probe(light, candidates=(0x15,))
    assert result.detected_submode == 0x15
    assert result.restored
    assert light.mode_writes[-1] == protocol.color_temp_frame(3000, 0x15)
    assert protocol.parse_reply(light.mode).mode.color_temp_kelvin == 3000


async def test_active_probe_cannot_restore_rgbic_colour_and_says_so() -> None:
    """A 0x15 reply with kelvin 0 is a colour the reply cannot name."""
    light = FakeFirmware(speaks=0x15, mode=_mode_frame(0x15, 0x01, 0, 0))
    result = await async_active_probe(light, candidates=(0x15,))
    assert result.detected_submode == 0x15
    assert not result.restored
    assert "previous colour unknown" in (result.restore_note or "")


def test_test_kelvin_avoids_what_the_light_reports() -> None:
    first, second = probe.TEST_KELVINS
    assert pick_test_kelvin(SafeProbeResult()) == first
    assert (
        pick_test_kelvin(SafeProbeResult(mode=ModeReport(0x15, color_temp_kelvin=first))) == second
    )
    assert (
        pick_test_kelvin(SafeProbeResult(mode=ModeReport(0x15, color_temp_kelvin=second))) == first
    )
    assert (
        pick_test_kelvin(SafeProbeResult(mode=ModeReport(0x0D, color_temp_kelvin=first))) == second
    )
    # The app only ever sends whole 100 K steps; so does the probe.
    assert all(k % 100 == 0 and 2000 <= k <= 9000 for k in probe.TEST_KELVINS)
    assert first != second


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_echo_requires_submode_and_rgb() -> None:
    rgb = (1, 2, 3)
    assert echoes(Reply(protocol.CMD_MODE, mode=ModeReport(0x02, rgb=rgb)), 0x02, rgb)
    assert not echoes(Reply(protocol.CMD_MODE, mode=ModeReport(0x02, rgb=(1, 2, 4))), 0x02, rgb)
    assert not echoes(Reply(protocol.CMD_MODE, mode=ModeReport(0x0D, rgb=rgb)), 0x02, rgb)
    assert not echoes(Reply(protocol.CMD_MODE, mode=ModeReport(0x15)), 0x15, rgb)
    assert not echoes(None, 0x02, rgb)


def test_echo_kelvin_requires_submode_and_kelvin() -> None:
    assert echoes_kelvin(
        Reply(protocol.CMD_MODE, mode=ModeReport(0x15, color_temp_kelvin=4300)), 0x15, 4300
    )
    assert not echoes_kelvin(
        Reply(protocol.CMD_MODE, mode=ModeReport(0x15, color_temp_kelvin=4200)), 0x15, 4300
    )
    assert not echoes_kelvin(
        Reply(protocol.CMD_MODE, mode=ModeReport(0x0D, color_temp_kelvin=4300)), 0x15, 4300
    )
    assert not echoes_kelvin(Reply(protocol.CMD_MODE, mode=ModeReport(0x15)), 0x15, 4300)
    assert not echoes_kelvin(None, 0x15, 4300)


def test_candidate_order_puts_table_prior_first() -> None:
    assert candidate_order(0x0D) == (0x0D, 0x02, 0x15)
    assert candidate_order(0x02) == (0x02, 0x0D, 0x15)
    assert candidate_order(0x15) == (0x15, 0x02, 0x0D)
    assert candidate_order(None) == probe.ACTIVE_CANDIDATES
    assert probe.ACTIVE_CANDIDATES == (0x02, 0x0D, 0x15)
