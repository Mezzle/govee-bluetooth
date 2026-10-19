"""The Blinkt driver's bit stream and fallbacks, without hardware."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "blinkt.py"


@pytest.fixture(scope="module")
def blinkt():
    spec = importlib.util.spec_from_file_location("blinkt", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["blinkt"] = module
    spec.loader.exec_module(module)
    return module


def _bytes(bits: list[int]) -> bytes:
    return bytes(int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8))


def test_apa102_frame_layout(blinkt) -> None:
    pixels = [(0x12, 0x34, 0x56)] + [(0, 0, 0)] * 7
    bits = blinkt.frame_bits(pixels, brightness=1.0)
    assert len(bits) == 32 + 8 * 32 + 36
    assert bits[:32] == [0] * 32 and bits[-36:] == [0] * 36
    first = _bytes(bits[32:64])
    assert first == bytes([0b11111111, 0x56, 0x34, 0x12])  # brightness 31, then B G R
    dark = _bytes(bits[64:96])
    assert dark == bytes([0b11111111, 0, 0, 0])


def test_brightness_is_clamped_to_five_bits(blinkt) -> None:
    assert _bytes(blinkt.frame_bits([(1, 2, 3)], brightness=0.2)[32:40])[0] == 0b11100000 | 6
    assert _bytes(blinkt.frame_bits([(1, 2, 3)], brightness=5)[32:40])[0] == 0xFF
    assert _bytes(blinkt.frame_bits([(1, 2, 3)], brightness=-1)[32:40])[0] == 0b11100000


def test_probe_colours_are_brightened_to_be_seen(blinkt) -> None:
    assert blinkt.visible((0x12, 0x34, 0x56)) == (53, 154, 255)
    assert blinkt.visible((0, 0, 0)) == (0, 0, 0)


def test_strip_writes_one_bit_per_clock_and_animates_off_thread(blinkt) -> None:
    bits: list[int] = []
    closed = []
    strip = blinkt.Blinkt(bits.append, lambda: closed.append(True))
    strip.fill((255, 0, 0))
    assert len(bits) == 32 + 8 * 32 + 36
    strip.sweep((0, 255, 255))
    strip.stop_animation()
    strip.close()
    assert closed == [True]


def test_missing_hardware_is_a_silent_no_op(blinkt, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "gpiod", None)  # import fails
    strip = blinkt.open_blinkt()
    assert not strip.available and "gpiod" in strip.reason
    strip.fill((1, 2, 3))
    strip.verdict("plaintext-rgbww", "verified", seconds=0)
    strip.close()
