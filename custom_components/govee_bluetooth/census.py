"""The census: what a light speaks, with the evidence for each claim.

The same battery the Dance Party Flipper app and ESP32 image run
(``govee_census.c`` there), over the :class:`probe.ProbeLink` interface so the
bench rig and the tests can drive it. Read-only by default; the opt-in probe
writes one colour per dialect and judges each dialect only by its own
colour appearing where it was not showing before.

Nothing here decides what the light *is*; :func:`classify` turns a
:class:`CensusResult` into a protocol-family verdict with a confidence and
the observations that support it, and the report generator renders that.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from . import protocol
from .advertisement import GoveeAdvertisement
from .probe import ProbeConnectionLost, ProbeLink

SCHEMA = "govee-evidence/1"

READ_TIMEOUT_S = 1.5
SETTLE_S = 0.15
# After this many unanswered core reads (power, brightness, firmware) the
# light is silent and the remaining reads are skipped.
SILENT_AFTER = 3

# One probe colour per dialect; none is a kelvin-tint colour, no channel is
# zero or full. Same values as govee_probe_color() in Dance Party.
PROBE_COLORS: dict[int, tuple[int, int, int]] = {
    protocol.SUBMODE_COLOR_LEGACY: (0xC8, 0x1E, 0x28),  # red
    protocol.SUBMODE_COLOR_DREAMCOLOR: (0x1E, 0xC8, 0x3C),  # green
    protocol.SUBMODE_COLOR_RGBWW: (0x28, 0x3C, 0xC8),  # blue
    protocol.SUBMODE_COLOR_RGBIC: (0xB4, 0x1E, 0xC8),  # magenta
}
# The first H6006 session used four near-identical dark blues: once one
# dialect landed, a later one could not be seen to work. These are
# saturated, distinct to the eye and named in the operator's question.
PROBE_COLOR_NAMES: dict[int, str] = {
    protocol.SUBMODE_COLOR_LEGACY: "red",
    protocol.SUBMODE_COLOR_DREAMCOLOR: "green",
    protocol.SUBMODE_COLOR_RGBWW: "blue",
    protocol.SUBMODE_COLOR_RGBIC: "magenta",
}
# After going back to the pre-probe state between dialects, wait this long
# so the reset's own write ack arrives before the next probe write is
# attributed. With a person answering, wait long enough for them to see the
# light settle at the baseline: in the H6006 session a 0.6 s gap made the
# blue -> white reset look like the 0x15 write's effect.
RESET_SETTLE_S = 0.6
RESET_SETTLE_ASKING_S = 2.5
PROBE_ORDER: tuple[int, ...] = (
    protocol.SUBMODE_COLOR_LEGACY,
    protocol.SUBMODE_COLOR_DREAMCOLOR,
    protocol.SUBMODE_COLOR_RGBWW,
    protocol.SUBMODE_COLOR_RGBIC,
)

# Families tagged secret_key_pairing in the decompiled app (docs 10.1), as
# tabled by Dance Party's govee_protocol.c. A table fact, not a measurement.
SECRET_FAMILY_MODELS: frozenset[str] = frozenset(
    {
        # dreamcolorlightv1
        "H611B", "H611Z", "H6123", "H612A", "H612B", "H612C", "H612D", "H612E", "H612F",
        "H6168", "H617A", "H619A", "H61A0", "H61A8", "H61A9", "H61E0", "H61E1", "H70A1",
        "H70A2", "H70A3",
        # h61d3
        "H61B5", "H61E5", "H61D3", "H6640", "H6641",
        # stringlightv2 (SKUs not also in a plain legacy family)
        "H6141", "H6142", "H6154", "H6170", "H6178", "H6192", "H6195", "H6196", "H6197",
        # hollowlamp
        "H6050", "H6051", "H6055", "H6058", "H6059", "H6073",
        # h60ax
        "H60A0", "H60A1", "H60A4", "H60A6", "H60C1",
        # h6047
        "H6047", "H6043", "H6042", "H6039", "H6038", "H6048",
    }
)  # fmt: skip


def needs_secret(model: str | None) -> bool:
    return (model or "").upper() in SECRET_FAMILY_MODELS


@dataclass(slots=True)
class CensusResult:
    """Everything one visit learned, each field a verified reply or absent."""

    connected: bool = False
    subscribed: bool = False
    aborted: bool = False
    reads: int = 0
    replies: int = 0
    bad_frames: int = 0
    acks: int = 0
    power: bool | None = None
    brightness: int | None = None
    mode: protocol.ModeReport | None = None
    mode_raw: bytes | None = None  # bytes 2..6 of the AA 05 reply
    firmware: str | None = None
    hardware: str | None = None
    ic_count: int | None = None
    segments: int | None = None
    light_count: int | None = None
    secret_present: bool = False
    secret: bytes | None = None  # kept only for the operator's own use
    segment_page: protocol.Reply | None = None
    probed: bool = False
    echoed: list[int] = field(default_factory=list)
    # Per probe dialect, what the firmware's write ack said: "accepted"
    # (33 05 00), "rejected" (33 05 <non-zero>) or "none". The H6006 rejects
    # 02 / 0B / 15 with 33 05 01 and accepts 0D with 33 05 00 (bench).
    probe_acks: dict[int, str] = field(default_factory=dict)
    # Per probe dialect, what the operator saw when asked: "yes", "no",
    # "wrong" (changed, but not to the probe colour) or "partial".
    observed: dict[int, str] = field(default_factory=dict)
    restored: str | None = None  # "full", "power", "none"
    restore_note: str | None = None
    error: str | None = None

    @property
    def silent(self) -> bool:
        return self.connected and self.subscribed and self.replies == 0 and not self.aborted


async def async_census(
    link: ProbeLink,
    *,
    probe_writes: bool = False,
    abort: Callable[[], bool] | None = None,
    ask: Callable[[int, tuple[int, int, int]], Awaitable[str | None]] | None = None,
    notify: Callable[[str], None] | None = None,
) -> CensusResult:
    """Run the read battery (and the probe if asked) over a connected, subscribed link.

    ``ask(submode, rgb)`` is called right after each probe write, before the
    read-back, so a person can say whether the light changed; it returns
    "yes", "no", "wrong", "partial", or None to stop (the light is still
    restored). Links that expose an ``ack_log`` list of ``(cmd, status)``
    pairs get each probe write's application ack recorded too.
    """
    result = CensusResult(connected=True, subscribed=True)
    try:
        await _reads(link, result, abort)
        if probe_writes and not result.silent and not result.aborted:
            await _probe(link, result, abort, ask, notify)
    except ProbeConnectionLost as err:
        result.error = f"disconnected: {err}"
    return result


async def _query(
    link: ProbeLink, result: CensusResult, frame: bytes, abort: Callable[[], bool] | None
):
    if abort is not None and abort():
        result.aborted = True
        return None
    result.reads += 1
    reply = await link.query(frame, frame[1])
    if reply is not None:
        result.replies += 1
    return reply


async def _reads(link: ProbeLink, result: CensusResult, abort: Callable[[], bool] | None) -> None:
    core_timeouts = 0
    reply = await _query(link, result, protocol.power_query_frame(), abort)
    if reply is not None:
        result.power = reply.power
    else:
        core_timeouts += 1
    reply = await _query(link, result, protocol.brightness_query_frame(), abort)
    if reply is not None:
        result.brightness = reply.brightness_level
    else:
        core_timeouts += 1
    reply = await _query(link, result, protocol.version_query_frame(), abort)
    if reply is not None:
        result.firmware = reply.version
    else:
        core_timeouts += 1
    if core_timeouts >= SILENT_AFTER and result.replies == 0:
        return  # locked, encrypted or a foreign dialect: more reads buy nothing
    reply = await _query(link, result, protocol.mode_query_frame(), abort)
    if reply is not None:
        result.mode = reply.mode
    reply = await _query(link, result, protocol.hardware_query_frame(), abort)
    if reply is not None:
        result.hardware = reply.hardware
    reply = await _query(link, result, protocol.ic_count_query_frame(), abort)
    if reply is not None:
        result.ic_count, result.segments = reply.ic_count, reply.segments
    reply = await _query(link, result, protocol.light_count_query_frame(), abort)
    if reply is not None:
        result.light_count = reply.light_count
    reply = await _query(link, result, protocol.secret_query_frame(), abort)
    if reply is not None and reply.secret_present:
        result.secret_present = True
        result.secret = reply.secret
    reply = await _query(link, result, protocol.segment_colors_query_frame(1), abort)
    if reply is not None:
        result.segment_page = reply


def _mode_rgb(mode: protocol.ModeReport | None) -> tuple[int, int, int] | None:
    """The RGB a mode reply carries, on the dialects whose reply carries one."""
    if mode is None or mode.submode not in (
        protocol.SUBMODE_COLOR_LEGACY,
        protocol.SUBMODE_COLOR_RGBWW,
    ):
        return None
    return mode.rgb


def _restore_frame(result: CensusResult) -> bytes | None:
    """The pre-probe 0x02 / 0x0D state as a write, or None when it cannot be rewritten.

    The reported body is written back verbatim: an RGB write would turn a
    white (kelvin) 0x0D state into RGB 00 00 00, i.e. dark. The H6006
    reported 0D 000000 0A8C 000000 (2700 K white) on the bench.
    """
    if result.mode is None or _mode_rgb(result.mode) is None:
        return None
    return protocol.build_frame(protocol.CMD_MODE, [result.mode.submode, *result.mode.body])


def _ack_since(link: ProbeLink, start: int, command: int) -> str:
    ack_log = getattr(link, "ack_log", None)
    if ack_log is None:
        return "none"
    for cmd, status in ack_log[start:]:
        if cmd == command:
            return "accepted" if status == 0 else "rejected"
    return "none"


async def _probe(
    link: ProbeLink,
    result: CensusResult,
    abort: Callable[[], bool] | None,
    ask: Callable[[int, tuple[int, int, int]], Awaitable[str | None]] | None = None,
    notify: Callable[[str], None] | None = None,
) -> None:
    result.probed = True
    powered_on = result.power is False
    if powered_on:
        await link.write(protocol.power_frame(True))
    page = result.segment_page
    last_mode_rgb = _mode_rgb(result.mode)
    for submode in PROBE_ORDER:
        if abort is not None and abort():
            result.aborted = True
            break
        rgb = PROBE_COLORS[submode]
        stale_page = protocol.segment_page_has(page, rgb)
        stale_mode = last_mode_rgb == rgb
        acks_before = len(getattr(link, "ack_log", None) or [])
        await link.write(protocol.rgb_frame(*rgb, submode=submode))
        if ask is not None:
            answer = await ask(submode, rgb)
            if answer is None:
                result.aborted = True
                result.probe_acks[submode] = _ack_since(link, acks_before, protocol.CMD_MODE)
                break
            result.observed[submode] = answer
        echoed = False
        reply = await _query(link, result, protocol.mode_query_frame(), abort)
        if reply is not None and reply.mode is not None and reply.mode.submode == submode:
            if submode in (protocol.SUBMODE_COLOR_LEGACY, protocol.SUBMODE_COLOR_RGBWW):
                echoed = reply.mode.rgb == rgb and not stale_mode
            last_mode_rgb = _mode_rgb(reply.mode)
        reply = await _query(link, result, protocol.segment_colors_query_frame(1), abort)
        if reply is not None:
            if protocol.segment_page_has(reply, rgb) and not stale_page:
                echoed = True
            page = reply
        if echoed:
            result.echoed.append(submode)
        result.probe_acks[submode] = _ack_since(link, acks_before, protocol.CMD_MODE)
        if submode != PROBE_ORDER[-1] and _restore_frame(result) is not None:
            # Back to the starting state, so the next dialect's change is
            # judged against the same baseline (by eye and by read-back).
            if notify is not None:
                notify("back to the starting state; watch it settle before the next colour")
            await link.write(_restore_frame(result))
            await asyncio.sleep(RESET_SETTLE_ASKING_S if ask is not None else RESET_SETTLE_S)
            last_mode_rgb = _mode_rgb(result.mode)

    restore = _restore_frame(result)
    if restore is not None and result.mode is not None:
        await link.write(restore)
        result.restored = "full"
        if result.mode.submode not in result.echoed:
            result.restore_note = "colour rewritten in the reported dialect, which did not echo"
    elif result.mode is not None and result.mode.submode in PROBE_COLORS:
        result.restored = "power"
        result.restore_note = "pre-probe colour not readable from the mode reply (0B/15); left on the last probe colour"
    elif result.mode is not None:
        result.restored = "power"
        result.restore_note = "pre-probe mode was not a static colour; not rewritten"
    else:
        result.restored = "power"
        result.restore_note = "pre-probe mode unknown; left on the last probe colour"
    if result.power is not None:
        await link.write(protocol.power_frame(result.power))
    else:
        result.restored = "none"
        result.restore_note = "pre-probe power unknown; nothing restored"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Verdict:
    """What the evidence supports, and how strongly."""

    family: str  # plaintext-legacy, plaintext-rgbww, plaintext-rgbic, plaintext-dreamcolor,
    # secret-locked, encrypted-v1, encrypted-v2, encrypted-unknown, silent-unknown,
    # unreachable, unknown
    security: str  # none, secret-key, aes-ecb-rc4, aes-gcm, unknown
    confidence: str  # verified, probable, inferred, unknown
    reasons: list[str] = field(default_factory=list)
    control_dialects: list[int] = field(default_factory=list)  # sub-modes with an echo


_DIALECT_FAMILY = {
    protocol.SUBMODE_COLOR_LEGACY: "plaintext-legacy",
    protocol.SUBMODE_COLOR_RGBWW: "plaintext-rgbww",
    protocol.SUBMODE_COLOR_RGBIC: "plaintext-rgbic",
    protocol.SUBMODE_COLOR_DREAMCOLOR: "plaintext-dreamcolor",
}


def classify(
    result: CensusResult,
    *,
    model: str | None,
    adv: GoveeAdvertisement | None,
    bgc: bytes | None = None,
) -> Verdict:
    """Turn one visit into a family / security / confidence verdict with its reasons."""
    reasons: list[str] = []
    encrypted_adv = adv.encrypted if adv is not None else None
    bgc_version: int | None = None
    if bgc:
        from .cipher import bgc_encrypt_version

        bgc_version, rule = bgc_encrypt_version(bgc)
        reasons.append(f"2b12 bytes {bgc.hex()}: encryptVersion {bgc_version} ({rule})")

    if not result.connected:
        return Verdict("unreachable", "unknown", "unknown", ["no connection was made"])

    if encrypted_adv or (bgc_version or 0) > 0:
        security = {1: "aes-ecb-rc4", 2: "aes-gcm"}.get(bgc_version or 0, "unknown")
        family = {1: "encrypted-v1", 2: "encrypted-v2"}.get(bgc_version or 0, "encrypted-unknown")
        if encrypted_adv:
            reasons.append("advertisement flags bit 6 set: AES-wrapped command channel")
        confidence = "probable" if bgc_version else "inferred"
        if result.replies:
            reasons.append(
                f"{result.replies} plaintext replies arrived anyway; the flag may be stale"
            )
            confidence = "inferred"
        return Verdict(family, security, confidence, reasons)

    rejected = [s for s, a in result.probe_acks.items() if a == "rejected"]
    accepted = [s for s, a in result.probe_acks.items() if a == "accepted"]
    seen = [s for s, a in result.observed.items() if a in ("yes", "partial")]
    if accepted or rejected:
        reasons.append(
            "write acks: accepted "
            + (", ".join(f"{s:#04x}" for s in accepted) or "none")
            + "; rejected "
            + (", ".join(f"{s:#04x}" for s in rejected) or "none")
        )
    if result.observed:
        reasons.append(
            "operator saw a change on " + (", ".join(f"{s:#04x}" for s in seen) or "no dialect")
        )
    if result.echoed:
        best = result.echoed[0]
        reasons.append("probe colour echoed on " + ", ".join(f"{s:#04x}" for s in result.echoed))
        return Verdict(
            _DIALECT_FAMILY[best], "none", "verified", reasons, control_dialects=list(result.echoed)
        )

    if result.silent:
        reasons.append(f"{result.reads} reads, no verified reply")
        if needs_secret(model):
            reasons.append("model family is tagged secret_key_pairing; AA B1 did not answer")
            return Verdict("secret-locked", "secret-key", "probable", reasons)
        if adv is None:
            reasons.append("no manufacturer data seen; encryption flag unknown")
        return Verdict("silent-unknown", "unknown", "unknown", reasons)

    if result.replies and result.mode is not None and result.mode.submode in _DIALECT_FAMILY:
        reasons.append(f"mode reply names sub-mode {result.mode.submode:#04x} (no write was tried)")
        if result.secret_present:
            reasons.append("AA B1 answered: unbound light handing out its secret")
        return Verdict(
            _DIALECT_FAMILY[result.mode.submode],
            "secret-key" if needs_secret(model) else "none",
            "inferred",
            reasons,
        )
    if result.replies:
        reasons.append(f"{result.replies} replies but the mode reply names no colour dialect")
        return Verdict(
            "unknown", "secret-key" if needs_secret(model) else "none", "inferred", reasons
        )
    reasons.append("no evidence beyond the connection")
    return Verdict("unknown", "unknown", "unknown", reasons)


# ---------------------------------------------------------------------------
# Summary record (govee-evidence/1, same keys as govee_census_summary_json)
# ---------------------------------------------------------------------------


def summary_record(
    result: CensusResult,
    *,
    address: str,
    name: str | None,
    model: str | None,
    adv: GoveeAdvertisement | None,
    rssi: int | None,
    source: str,
    at: str,
    software: str,
    dialect: int | None = None,
    dialect_source: str | None = None,
    known_model: bool | None = None,
    include_secret: bool = False,
) -> dict[str, object]:
    """One ``{"summary":"light", ...}`` object in the shared shape."""
    rec: dict[str, object] = {
        "summary": "light",
        "schema": SCHEMA,
        "source": source,
        "at": at,
        "software": software,
        "address": address,
        "name": name or "",
        "model": model or "",
    }
    if known_model is not None:
        rec["known_model"] = known_model
    rec["rssi"] = rssi
    if adv is not None:
        rec["pact"] = adv.pact
        rec["flags"] = adv.flags
        rec["encrypted"] = adv.encrypted
    else:
        rec["pact"] = rec["flags"] = rec["encrypted"] = None
    if dialect is not None:
        rec["dialect"] = {0x02: "RGB", 0x0D: "RGBWW", 0x15: "RGBIC", 0x0B: "Dream"}.get(
            dialect, f"{dialect:#04x}"
        )
        rec["dialect_source"] = dialect_source or "default"
    rec["secret_family"] = needs_secret(model)
    rec["connected"] = result.connected
    rec["subscribed"] = result.subscribed
    rec["silent"] = result.silent
    if result.aborted:
        rec["aborted"] = True
    rec["reads"] = result.reads
    rec["replies"] = result.replies
    if result.bad_frames:
        rec["bad_frames"] = result.bad_frames
    if result.acks:
        rec["acks"] = result.acks
    if result.power is not None:
        rec["power"] = result.power
    if result.brightness is not None:
        rec["brightness"] = result.brightness
    if result.mode is not None:
        rec["submode"] = f"{result.mode.submode:02X}"
        if result.mode_raw is not None:
            rec["submode"] = f"{result.mode.submode:02X} {result.mode_raw[1:5].hex().upper()}"
        rec["submode_name"] = submode_name(result.mode.submode)
    if result.firmware:
        rec["firmware"] = result.firmware
    if result.hardware:
        rec["hardware"] = result.hardware
    if result.ic_count is not None:
        rec["ic_count"] = result.ic_count
        rec["segments"] = result.segments
    if result.light_count is not None:
        rec["light_count"] = result.light_count
    rec["secret_present"] = result.secret_present
    if result.secret_present and include_secret and result.secret is not None:
        rec["secret"] = result.secret.hex()
    rec["segment_page"] = result.segment_page is not None
    rec["probed"] = result.probed
    if result.probed:
        rec["echoed"] = ",".join(f"{s:02X}" for s in result.echoed)
        if result.probe_acks:
            rec["probe_acks"] = {f"{s:02X}": a for s, a in result.probe_acks.items()}
        if result.observed:
            rec["observed"] = {f"{s:02X}": a for s, a in result.observed.items()}
        rec["restored"] = result.restored
        if result.restore_note:
            rec["restore_note"] = result.restore_note
    if result.error:
        rec["error"] = result.error
    return rec


_SUBMODE_NAMES = {
    0x00: "video", 0x01: "music01", 0x02: "colour02", 0x03: "music03", 0x04: "scene",
    0x05: "mic", 0x07: "diy07", 0x09: "scene09", 0x0A: "diy", 0x0B: "colour0B", 0x0C: "music0C",
    0x0D: "colour0D", 0x0E: "music0E", 0x0F: "music0F", 0x11: "music11", 0x12: "music12",
    0x13: "music13", 0x14: "music14", 0x15: "colour15", 0x16: "music", 0x17: "music",
    0x19: "music", 0x22: "music", 0xFF: "mic",
}  # fmt: skip


def submode_name(submode: int) -> str:
    """The short names Dance Party's govee_submode_name() uses."""
    return _SUBMODE_NAMES.get(submode, "?")
