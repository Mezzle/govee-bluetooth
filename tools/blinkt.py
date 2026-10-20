"""Pimoroni Blinkt on the bench Pi: eight APA102 LEDs as a status display.

https://pinout.xyz/pinout/blinkt: data on BCM 23, clock on BCM 24. The LEDs
are bit-banged through libgpiod 2 (``/dev/gpiochip0``), which needs the
``gpio`` group but not root, and works on Raspberry Pi OS trixie where the
old RPi.GPIO-based ``blinkt`` library does not. Without the hardware, the
library or the permission, :func:`open_blinkt` returns a :class:`NullBlinkt`
and every call is a no-op, so the rig never depends on it.

What the rig shows (``govee_rig.py --blinkt``):

    scanning         a cyan sweep, back and forth
    connected        LED 0 green
    frame out        LED 7 white flash; a colour command mirrors its colour on all eight
    reply in         LED 6 blue flash; a timeout flashes it red, a bad checksum magenta
    write ack        LED 5 green flash
    probe write      all eight show that dialect's probe colour, brightened to be visible
    verdict          verified: disco sparkle; probable / inferred: amber; silent / unknown: red;
                     encrypted: purple. Held for a few seconds, then off.
"""

from __future__ import annotations

import math
import random
import threading
import time
from collections.abc import Callable, Sequence

DATA_PIN = 23
CLOCK_PIN = 24
NUM_PIXELS = 8
CHIP = "/dev/gpiochip0"
DEFAULT_BRIGHTNESS = 0.2  # of the APA102's 5-bit global brightness; full is blinding

Pixel = tuple[int, int, int]


def frame_bits(pixels: Sequence[Pixel], brightness: float = DEFAULT_BRIGHTNESS) -> list[int]:
    """The APA102 bit stream for ``pixels``: 32 zero bits, one 32-bit word per LED, 36 latch bits.

    Each LED word is ``111bbbbb`` (5-bit global brightness) then blue, green,
    red, most significant bit first. Matches Pimoroni's blinkt library.
    """
    level = max(0, min(31, round(brightness * 31)))
    bits = [0] * 32
    for r, g, b in pixels:
        for byte in (0b11100000 | level, b & 0xFF, g & 0xFF, r & 0xFF):
            bits.extend((byte >> (7 - i)) & 1 for i in range(8))
    bits.extend([0] * 36)
    return bits


def visible(rgb: Pixel) -> Pixel:
    """Scale a colour so its brightest channel is 255 (probe colours are deliberately dark)."""
    peak = max(rgb)
    if peak == 0:
        return (0, 0, 0)
    return tuple(min(255, round(c * 255 / peak)) for c in rgb)  # type: ignore[return-value]


class NullBlinkt:
    """Does nothing: no Blinkt, no libgpiod, or no permission."""

    available = False
    reason = "not opened"

    def show(self, pixels: Sequence[Pixel]) -> None:
        pass

    def fill(self, rgb: Pixel) -> None:
        pass

    def flash(self, index: int, rgb: Pixel, seconds: float = 0.08) -> None:
        pass

    def set_base(self, index: int, rgb: Pixel) -> None:
        pass

    def sweep(self, rgb: Pixel) -> None:
        pass

    def stop_animation(self) -> None:
        pass

    def verdict(self, family: str, confidence: str, seconds: float = 6.0) -> None:
        pass

    def off(self) -> None:
        pass

    def close(self) -> None:
        pass


class Blinkt(NullBlinkt):
    """The real strip. Thread-safe: animations run on a background thread."""

    available = True
    reason = ""

    def __init__(self, write_bit: Callable[[int], None], close: Callable[[], None]) -> None:
        self._write_bit = write_bit  # clocks one data bit out
        self._close = close
        self._lock = threading.Lock()
        self._base: list[Pixel] = [(0, 0, 0)] * NUM_PIXELS
        self._anim: threading.Thread | None = None
        self._anim_stop = threading.Event()
        self.brightness = DEFAULT_BRIGHTNESS

    # -- low level -------------------------------------------------------

    def show(self, pixels: Sequence[Pixel]) -> None:
        with self._lock:
            for bit in frame_bits(pixels, self.brightness):
                self._write_bit(bit)

    def fill(self, rgb: Pixel) -> None:
        self.stop_animation()
        self._base = [rgb] * NUM_PIXELS
        self.show(self._base)

    def set_base(self, index: int, rgb: Pixel) -> None:
        self._base[index] = rgb
        if self._anim is None:
            self.show(self._base)

    def flash(self, index: int, rgb: Pixel, seconds: float = 0.08) -> None:
        """Light one LED briefly over the current base, without blocking the caller."""
        if self._anim is not None:
            return
        pixels = list(self._base)
        pixels[index] = rgb
        self.show(pixels)
        timer = threading.Timer(seconds, lambda: self._anim is None and self.show(self._base))
        timer.daemon = True
        timer.start()

    # -- animations ------------------------------------------------------

    def _run(self, step: Callable[[int], Sequence[Pixel]], period: float) -> None:
        self.stop_animation()
        self._anim_stop.clear()

        def loop() -> None:
            tick = 0
            while not self._anim_stop.is_set():
                self.show(step(tick))
                tick += 1
                self._anim_stop.wait(period)

        self._anim = threading.Thread(target=loop, name="blinkt", daemon=True)
        self._anim.start()

    def stop_animation(self) -> None:
        if self._anim is not None:
            self._anim_stop.set()
            self._anim.join(timeout=1.0)
            self._anim = None

    def sweep(self, rgb: Pixel) -> None:
        """A Larson-scanner sweep: what the strip does while the rig scans."""

        def step(tick: int) -> list[Pixel]:
            span = 2 * (NUM_PIXELS - 1)
            head = tick % span
            head = head if head < NUM_PIXELS else span - head
            return [
                tuple(int(c * max(0.0, 1 - abs(i - head) / 2.5)) for c in rgb)  # type: ignore[misc]
                for i in range(NUM_PIXELS)
            ]

        self._run(step, 0.07)

    def sparkle(self) -> None:
        """Disco ball: random white and coloured glints."""
        rng = random.Random()

        def step(_tick: int) -> list[Pixel]:
            out: list[Pixel] = []
            for _ in range(NUM_PIXELS):
                roll = rng.random()
                if roll < 0.25:
                    out.append((255, 255, 255))
                elif roll < 0.5:
                    hue = rng.random()
                    out.append(_hsv(hue))
                else:
                    out.append((8, 8, 12))
            return out

        self._run(step, 0.09)

    def pulse(self, rgb: Pixel) -> None:
        def step(tick: int) -> list[Pixel]:
            level = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(tick / 4))
            return [tuple(int(c * level) for c in rgb)] * NUM_PIXELS  # type: ignore[list-item]

        self._run(step, 0.05)

    def verdict(self, family: str, confidence: str, seconds: float = 6.0) -> None:
        """Show a census verdict, block for ``seconds``, then go dark."""
        if family.startswith("encrypted"):
            self.pulse((140, 0, 255))
        elif (
            family in ("silent-unknown", "unreachable", "secret-locked") or confidence == "unknown"
        ):
            self.pulse((255, 0, 0))
        elif confidence == "verified":
            self.sparkle()
        else:
            self.pulse((255, 140, 0))
        time.sleep(seconds)
        self.off()

    def off(self) -> None:
        self.stop_animation()
        self._base = [(0, 0, 0)] * NUM_PIXELS
        self.show(self._base)

    def close(self) -> None:
        try:
            self.off()
        finally:
            self._close()


def _hsv(h: float) -> Pixel:
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    q, t = int(255 * (1 - f)), int(255 * f)
    return [(255, t, 0), (q, 255, 0), (0, 255, t), (0, q, 255), (t, 0, 255), (255, 0, q)][i]


def open_blinkt(chip: str = CHIP) -> NullBlinkt:
    """The strip if it can be driven, else a :class:`NullBlinkt` whose ``reason`` says why."""
    null = NullBlinkt()
    try:
        import gpiod
        from gpiod.line import Direction, Value
    except ImportError:
        null.reason = "python gpiod (libgpiod 2) not importable: venv needs system site packages"
        return null
    try:
        request = gpiod.request_lines(
            chip,
            consumer="govee-rig-blinkt",
            config={
                (DATA_PIN, CLOCK_PIN): gpiod.LineSettings(
                    direction=Direction.OUTPUT, output_value=Value.INACTIVE
                )
            },
        )
    except (OSError, PermissionError, ValueError) as err:
        null.reason = f"cannot open {chip}: {err} (is this user in the gpio group?)"
        return null

    hi, lo = Value.ACTIVE, Value.INACTIVE

    def write_bit(bit: int) -> None:
        request.set_value(DATA_PIN, hi if bit else lo)
        request.set_value(CLOCK_PIN, hi)
        request.set_value(CLOCK_PIN, lo)

    return Blinkt(write_bit, request.release)


def main() -> None:
    """``python3 tools/blinkt.py``: walk a colour along the strip, then sparkle, then off."""
    strip = open_blinkt()
    if not strip.available:
        raise SystemExit(f"no Blinkt: {strip.reason}")
    try:
        for rgb in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
            for i in range(NUM_PIXELS):
                pixels = [(0, 0, 0)] * NUM_PIXELS
                pixels[i] = rgb
                strip.show(pixels)
                time.sleep(0.06)
        strip.sweep((0, 255, 255))
        time.sleep(2)
        strip.sparkle()
        time.sleep(3)
    finally:
        strip.close()
    print("blinkt ok")


if __name__ == "__main__":
    main()
