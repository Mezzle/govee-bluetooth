"""Detect which colour dialect a Govee light speaks by asking the light.

The static table in :mod:`models` is built from a decompiled app and community
captures, and the H6181 showed it can be wrong (the app says ``0x02`` /
0-255, firmware 2.03.11 speaks ``0x0D`` / 0-100). This module has two steps:

* :func:`async_safe_probe` only sends the four ``AA`` reads. If the light is
  currently showing a plain colour, the mode reply names the sub-mode and the
  question is answered without writing anything.
* :func:`async_active_probe` writes a test colour in each candidate sub-mode
  and accepts the first one the light echoes back. For ``0x02`` and ``0x0D``
  the echo is the RGB that was sent; for ``0x15`` (whose mode reply carries
  kelvin but no RGB) the test write is a white at a distinctive colour
  temperature and the echo is that kelvin. It is opt-in because it changes
  the light's colour and sends frames the firmware may not understand.

Neither step probes the brightness scale: that is only distinguishable by
writing values above 100, which the H6181 silently ignores. The scale is
derived from the detected sub-mode instead (``models.brightness_scale_for_submode``).

Everything here works over a :class:`ProbeLink`, a two-method interface that
:mod:`device` implements on top of a connected Bleak client and tests
implement with a scripted fake. No Bluetooth or Home Assistant imports.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from . import protocol
from .protocol import ModeReport, Reply

_LOGGER = logging.getLogger(__name__)

# Every colour sub-mode the parser understands, in default prior order.
COLOR_SUBMODES: tuple[int, ...] = (
    protocol.SUBMODE_COLOR_LEGACY,
    protocol.SUBMODE_COLOR_RGBWW,
    protocol.SUBMODE_COLOR_RGBIC,
)

# Sub-modes the active probe may write, in default prior order. 0x02 and 0x0D
# are confirmed by an RGB echo. The 0x15 mode reply (``AA 05 15 <op> <kHi>
# <kLo>``) carries no RGB, so 0x15 is tested with a white write instead and
# confirmed by the kelvin coming back. The 0x15 reply layout is from the
# decompiled app, not yet from hardware; see :func:`echoes_kelvin`.
ACTIVE_CANDIDATES: tuple[int, ...] = (
    protocol.SUBMODE_COLOR_LEGACY,
    protocol.SUBMODE_COLOR_RGBWW,
    protocol.SUBMODE_COLOR_RGBIC,
)

# A colour that is unlikely to be the one the light is already showing and
# that has three distinct, non-zero, non-full channels so a partial echo is
# easy to tell from a real one.
TEST_RGB: tuple[int, int, int] = (0x12, 0x34, 0x56)

# Colour temperatures for the 0x15 white write. Whole 100 K steps, because
# that is all the app ever sends and firmware may snap anything else. Two
# values so the probe can avoid the one the light is already reporting (a
# stale reply would otherwise look like an echo).
TEST_KELVINS: tuple[int, int] = (4300, 6700)

# Brightness writes during the probe never exceed this (see module docstring).
MAX_PROBE_BRIGHTNESS = 100

SUBMODE_NAMES: dict[int, str] = {
    protocol.SUBMODE_COLOR_LEGACY: "colour (0x02, H6159 dialect)",
    protocol.SUBMODE_COLOR_RGBWW: "colour (0x0D, RGBWW dialect)",
    protocol.SUBMODE_COLOR_RGBIC: "colour (0x15, RGBIC dialect)",
    protocol.SUBMODE_MUSIC: "music (0x03)",
    protocol.SUBMODE_SCENE: "scene (0x04)",
}


def submode_name(submode: int | None) -> str:
    if submode is None:
        return "no reply"
    return SUBMODE_NAMES.get(submode, f"unknown ({submode:#04x})")


def candidate_order(prior: int | None) -> tuple[int, ...]:
    """Active-probe candidates, the static table's guess first."""
    if prior in ACTIVE_CANDIDATES:
        return (prior, *(s for s in ACTIVE_CANDIDATES if s != prior))
    return ACTIVE_CANDIDATES


def pick_test_kelvin(recorded: SafeProbeResult) -> int:
    """The white temperature to write for the 0x15 test.

    Picks the first of :data:`TEST_KELVINS` the light is not already
    reporting, so a firmware that ignores the write cannot pass by accident.
    """
    mode = recorded.mode
    current = mode.color_temp_kelvin if mode is not None else None
    for kelvin in TEST_KELVINS:
        if kelvin != current:
            return kelvin
    return TEST_KELVINS[0]  # unreachable with two distinct values


def probe_frame(submode: int, rgb: tuple[int, int, int], kelvin: int) -> bytes:
    """The write the active probe sends for one candidate sub-mode."""
    if submode == protocol.SUBMODE_COLOR_RGBIC:
        return protocol.color_temp_frame(kelvin, submode)
    return protocol.rgb_frame(*rgb, submode=submode)


class ProbeConnectionLost(Exception):
    """The link dropped mid-probe. The probe stops and never retries a write."""


class ProbeLink(Protocol):
    """What the probe needs from a connected light."""

    async def write(self, frame: bytes) -> None:
        """Send one command frame. Raise :class:`ProbeConnectionLost` on failure."""

    async def query(self, frame: bytes, command: int) -> Reply | None:
        """Send a read frame and return the matching reply, or None on timeout.

        Raise :class:`ProbeConnectionLost` if the link dropped.
        """


@dataclass(slots=True)
class SafeProbeResult:
    """What the four read-only queries told us."""

    power: bool | None = None
    brightness_level: int | None = None
    mode: ModeReport | None = None
    firmware: str | None = None

    @property
    def answered(self) -> bool:
        return any(v is not None for v in (self.power, self.brightness_level, self.mode))

    @property
    def observed_submode(self) -> int | None:
        return self.mode.submode if self.mode is not None else None

    @property
    def detected_submode(self) -> int | None:
        """The colour sub-mode, if the light is currently in one."""
        submode = self.observed_submode
        return submode if submode in COLOR_SUBMODES else None

    @property
    def outcome(self) -> str:
        """``color``, ``scene``, ``music``, ``unknown`` or ``no_reply``."""
        if self.mode is None:
            return "no_reply"
        if self.detected_submode is not None:
            return "color"
        if self.mode.submode == protocol.SUBMODE_SCENE:
            return "scene"
        if self.mode.submode == protocol.SUBMODE_MUSIC:
            return "music"
        return "unknown"


@dataclass(slots=True)
class ActiveProbeResult:
    """Outcome of the write-and-echo sequence."""

    recorded: SafeProbeResult
    detected_submode: int | None = None
    attempted: list[int] = field(default_factory=list)
    test_kelvin: int | None = None  # the white written for a 0x15 attempt, if any
    restored: bool = False
    restore_note: str | None = None
    error: str | None = None

    @property
    def complete(self) -> bool:
        return self.error is None


async def async_safe_probe(link: ProbeLink) -> SafeProbeResult:
    """Step 1: version, power, brightness and mode reads. Writes nothing."""
    result = SafeProbeResult()
    version = await link.query(protocol.version_query_frame(), protocol.CMD_VERSION)
    if version is not None:
        result.firmware = version.version
    power = await link.query(protocol.power_query_frame(), protocol.CMD_POWER)
    if power is not None:
        result.power = power.power
    brightness = await link.query(protocol.brightness_query_frame(), protocol.CMD_BRIGHTNESS)
    if brightness is not None:
        result.brightness_level = brightness.brightness_level
    mode = await link.query(protocol.mode_query_frame(), protocol.CMD_MODE)
    if mode is not None:
        result.mode = mode.mode
    return result


def echoes(reply: Reply | None, submode: int, rgb: tuple[int, int, int]) -> bool:
    """True only if the mode reply carries both the sub-mode byte and the RGB.

    Firmware that does not understand a sub-mode typically ignores the write
    and keeps reporting the previous mode, so the sub-mode byte alone proves
    nothing; the previous mode may have been that very sub-mode.
    """
    return (
        reply is not None
        and reply.mode is not None
        and reply.mode.submode == submode
        and reply.mode.rgb == tuple(rgb)
    )


def echoes_kelvin(reply: Reply | None, submode: int, kelvin: int) -> bool:
    """True only if the mode reply carries the sub-mode byte and the kelvin sent.

    The 0x15 reply has no RGB, so the colour temperature is the only value
    that can prove the write landed. :func:`pick_test_kelvin` makes sure it is
    not the value the light was reporting before the write.
    """
    return (
        reply is not None
        and reply.mode is not None
        and reply.mode.submode == submode
        and reply.mode.color_temp_kelvin == kelvin
    )


def confirms(reply: Reply | None, submode: int, rgb: tuple[int, int, int], kelvin: int) -> bool:
    """Whichever echo rule applies to ``submode``."""
    if submode == protocol.SUBMODE_COLOR_RGBIC:
        return echoes_kelvin(reply, submode, kelvin)
    return echoes(reply, submode, rgb)


async def async_active_probe(
    link: ProbeLink,
    candidates: Sequence[int] = ACTIVE_CANDIDATES,
    test_rgb: tuple[int, int, int] = TEST_RGB,
) -> ActiveProbeResult:
    """Step 2: write a test colour per candidate and accept the first echo.

    Records power, brightness and mode first and restores what it can at the
    end. Stops without retrying at the first sign of a dropped link.
    """
    for candidate in candidates:
        if candidate not in ACTIVE_CANDIDATES:
            raise ValueError(f"{candidate:#04x} is not an active-probe candidate")

    try:
        recorded = await async_safe_probe(link)
    except ProbeConnectionLost as err:
        return ActiveProbeResult(recorded=SafeProbeResult(), error=f"disconnected: {err}")
    result = ActiveProbeResult(recorded=recorded)
    kelvin = pick_test_kelvin(recorded)

    try:
        if recorded.power is False:
            # Mode writes on a powered-off strip may not be reflected by the
            # mode read; switch on for the duration and switch off again after.
            await link.write(protocol.power_frame(True))
        for candidate in candidates:
            result.attempted.append(candidate)
            if candidate == protocol.SUBMODE_COLOR_RGBIC:
                result.test_kelvin = kelvin
            _LOGGER.debug("Active probe: trying sub-mode %#04x", candidate)
            await link.write(probe_frame(candidate, test_rgb, kelvin))
            reply = await link.query(protocol.mode_query_frame(), protocol.CMD_MODE)
            if confirms(reply, candidate, test_rgb, kelvin):
                result.detected_submode = candidate
                break
            _LOGGER.debug(
                "Active probe: sub-mode %#04x not echoed (reply %s)",
                candidate,
                reply.mode if reply is not None else None,
            )
        await _async_restore(link, result)
    except ProbeConnectionLost as err:
        result.error = f"disconnected: {err}"
    return result


async def _async_restore(link: ProbeLink, result: ActiveProbeResult) -> None:
    """Put back what step 1 recorded, within the no-scene / <=100 rules."""
    recorded = result.recorded
    notes: list[str] = []
    submode = result.detected_submode
    mode = recorded.mode

    if mode is None:
        notes.append("previous mode unknown, left on the probe's test colour")
    elif mode.submode == protocol.SUBMODE_SCENE:
        notes.append("previous mode was a scene, which the probe never writes")
    elif mode.submode not in COLOR_SUBMODES:
        notes.append(f"previous mode {submode_name(mode.submode)} cannot be restored")
    else:
        restore_submode = submode if submode is not None else mode.submode
        if restore_submode not in ACTIVE_CANDIDATES:
            notes.append(f"previous {submode_name(mode.submode)} state cannot be rewritten")
        elif mode.is_white and mode.color_temp_kelvin is not None:
            await link.write(protocol.color_temp_frame(mode.color_temp_kelvin, restore_submode))
        elif mode.is_white and mode.white_tint is not None:
            await link.write(
                protocol.color_temp_frame(protocol.rgb_to_kelvin(mode.white_tint), restore_submode)
            )
        elif mode.rgb is not None:
            await link.write(protocol.rgb_frame(*mode.rgb, submode=restore_submode))
        else:
            notes.append("previous colour unknown, left on the probe's test colour")

    level = recorded.brightness_level
    if level is not None and 1 <= level <= MAX_PROBE_BRIGHTNESS:
        await link.write(protocol.build_frame(protocol.CMD_BRIGHTNESS, [level]))
    elif level is not None and level > MAX_PROBE_BRIGHTNESS:
        # The probe never changed brightness, so there is nothing to undo;
        # it just will not write a value above 100 to confirm it.
        notes.append(f"brightness {level} left as read (probe never writes above 100)")

    if recorded.power is False:
        await link.write(protocol.power_frame(False))

    result.restored = not notes
    result.restore_note = "; ".join(notes) if notes else None
