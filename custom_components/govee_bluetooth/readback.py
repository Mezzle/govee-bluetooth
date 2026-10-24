"""Evidence-qualified read contracts, independent of control dialects.

See docs/ble-readback-source-research-2026-10-24.md. Contract decoders are
usable offline without granting a model runtime support. No inferred count is
used for a write mask.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import protocol
from .models import RGBIC_MODELS


@dataclass(frozen=True)
class PageLayout:
    command: int
    counts: tuple[int, ...]
    record_width: int

    def __post_init__(self) -> None:
        if (
            self.command not in (0xA2, 0xA5)
            or self.record_width not in (3, 4)
            or not 1 <= len(self.counts) <= 16
            or any(n < 1 or n * self.record_width > 16 for n in self.counts)
        ):
            raise ValueError("Invalid bounded colour page geometry")

    def query(self, group: int) -> bytes:
        if not 1 <= group <= len(self.counts):
            raise ValueError("Group outside qualified geometry")
        return protocol.build_frame(self.command, [group], frame_type=0xAA)

    def decode(self, frame: bytes) -> list[protocol.SegmentRecord] | None:
        if (
            not protocol.verify_frame(frame)
            or frame[:2] != bytes((0xAA, self.command))
            or not 1 <= frame[2] <= len(self.counts)
        ):
            return None
        count = self.counts[frame[2] - 1]
        records = []
        for offset in range(3, 3 + count * self.record_width, self.record_width):
            body = frame[offset : offset + self.record_width]
            brightness = body[0] if self.record_width == 4 else None
            records.append(protocol.SegmentRecord(brightness, tuple(body[-3:])))
        return records


@dataclass(frozen=True)
class ReadbackProfile:
    key: str
    mode_layout: str = "opaque"
    hardware: bool = False
    topology: str | None = None
    pages: PageLayout | None = None
    bulk: bool = False
    ee_brightness: bool = False
    ee30: str | None = None
    brightness_scale: int | None = None


# No disputed H6047/H6641 geometry. No speculative H6125 A2 runtime profile.
H617A_PAGES = PageLayout(0xA5, (3, 3, 3, 3, 3), 4)
H6199_PAGES = PageLayout(0xA5, (4, 4, 4, 3), 4)  # offline schema contract only


def resolve(model: str, pact: tuple[int, int] | None, firmware: str | None) -> ReadbackProfile:
    """Conservative allowlist; missing qualification never means unsupported."""
    if model == "H6159":
        return ReadbackProfile("h6159-captured", "legacy", hardware=True)
    if model == "H6181":
        return ReadbackProfile("h6181-captured", "rgbww", hardware=True)
    if model == "H6006":
        return ReadbackProfile("h6006-captured", "bulb_rgbww", hardware=True)
    if model == "H60A6":
        return ReadbackProfile(
            "h60a6-source", "segmented", bulk=True, ee_brightness=True, ee30="main_bit0_a"
        )
    if model == "H6062":
        return ReadbackProfile("h6062-section-source", "segmented", topology="section")
    if model == "H617A" and pact == (10, 1) and firmware == "3.02.24":
        return ReadbackProfile(
            "h617a-10-1-3.02.24-source",
            "segmented",
            topology="ic_pair",
            pages=H617A_PAGES,
            brightness_scale=100,
        )
    if model in RGBIC_MODELS:
        return ReadbackProfile("rgbic-table-mode-only", "segmented")
    return ReadbackProfile("unqualified")


def mode_changes(mode: protocol.ModeReport, layout: str) -> dict:
    """Partial observations only. Zero Kelvin never clears colour by itself."""
    if mode.submode == protocol.SUBMODE_SCENE:
        return {"white_mode": False, "rgb_color": None, "color_temp_kelvin": None}
    expected = {"legacy": 0x02, "rgbww": 0x0D, "bulb_rgbww": 0x0D, "segmented": 0x15}
    if expected.get(layout) != mode.submode:
        return {}
    if layout == "segmented":
        if mode.color_temp_kelvin:
            return {
                "white_mode": True,
                "color_temp_kelvin": mode.color_temp_kelvin,
                "rgb_color": protocol.kelvin_to_rgb(mode.color_temp_kelvin),
                "effect": None,
            }
        return {}
    if mode.is_white:
        tint = mode.rgb if layout == "bulb_rgbww" else mode.white_tint
        return {
            "white_mode": True,
            "rgb_color": tint,
            "color_temp_kelvin": mode.color_temp_kelvin or protocol.rgb_to_kelvin(mode.white_tint),
            "effect": None,
        }
    return {"white_mode": False, "rgb_color": mode.rgb, "color_temp_kelvin": None, "effect": None}


def topology(frame: bytes, form: str) -> dict | None:
    """Explicit signed pair semantics; never reinterpret padding as another pair."""
    if not protocol.verify_frame(frame) or frame[0] != 0xAA:
        return None
    if form not in ("ic_pair", "ic_two_pairs", "section", "bulb"):
        return None
    command = {"ic_pair": 0x40, "ic_two_pairs": 0x40, "section": 0x41, "bulb": 0x0F}[form]
    if frame[1] != command:
        return None
    if form == "bulb":
        return {"bulb_count": frame[2]}
    pairs = []
    for offset in (2, 5) if form == "ic_two_pairs" else (2,):
        count = int.from_bytes(frame[offset : offset + 2], "big", signed=True)
        sections = int.from_bytes(frame[offset + 2 : offset + 3], "big", signed=form == "section")
        if not 0 <= count < 0x1000 or sections < 0:
            return None
        pairs.append(
            {"ic_count": count, "section_count" if form == "section" else "segment_count": sections}
        )
    return {"pairs": pairs}


class BulkAssembler:
    """Bounded, ordered AC burst contract. Caller owns timeout/connection scope."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.parts: list[bytes] = []
        self.packets = 0
        self.final_length = 0
        self.command: int | None = None
        self.selector: int | None = None

    def feed(self, frame: bytes) -> bytes | None:
        if not protocol.verify_frame(frame) or frame[0] != 0xAC:
            self.reset()
            return None
        tag = frame[1]
        if tag == 0:
            self.reset()
            count = int.from_bytes(frame[2:4], "little")
            length = frame[4]
            if not 1 <= count <= 32 or length > (12 if count == 1 else 17):
                return None
            self.packets, self.final_length = count, length
            self.command, self.selector = frame[5:7]
            if count == 1:
                return bytes(frame[7 : 7 + length])
            self.parts = [bytes(frame[7:19])]
            return None
        if not self.parts:
            return None
        final = len(self.parts) == self.packets - 1
        if tag != len(self.parts) and not (final and tag == 0xFF):
            self.reset()
            return None
        self.parts.append(bytes(frame[2 : 2 + (self.final_length if final else 17)]))
        if final:
            result = b"".join(self.parts)
            self.parts = []
            return result
        return None


def tlvs(data: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """Keep unknown TLVs and incomplete tails rather than guessing padding."""
    result = []
    offset = 0
    while len(data) - offset >= 2:
        tag, length = data[offset : offset + 2]
        if len(data) - offset - 2 < length:
            break
        result.append((tag, data[offset + 2 : offset + 2 + length]))
        offset += 2 + length
    return result, data[offset:]


def push(frame: bytes, profile: ReadbackProfile) -> protocol.Reply | None:
    if not protocol.verify_frame(frame) or frame[0] != 0xEE:
        return None
    if frame[1] == 0x20 and profile.ee_brightness:
        return protocol.Reply(protocol.CMD_BRIGHTNESS, brightness_level=frame[2])
    if frame[1] == 0x30 and profile.ee30 == "main_bit0_a":
        return protocol.Reply(protocol.CMD_POWER, power=bool(frame[3] & 1))
    return None
