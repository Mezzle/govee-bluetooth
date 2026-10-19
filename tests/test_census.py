"""The census over a scripted fake light: evidence rules, probe echoes, restoration, classification."""

from __future__ import annotations

import pytest

from custom_components.govee_bluetooth import census, protocol
from custom_components.govee_bluetooth.advertisement import parse_raw
from custom_components.govee_bluetooth.probe import ProbeConnectionLost


def _reply(cmd: int, *payload: int) -> bytes:
    return protocol.build_frame(cmd, list(payload), frame_type=protocol.TYPE_READ)


class FakeLight:
    """Answers the census the way one firmware family does.

    ``speaks`` is the colour sub-mode the firmware understands (None: ignores
    every write and answers no read, like a locked or encrypted light).
    """

    def __init__(
        self,
        *,
        speaks: int | None,
        power: bool = True,
        brightness: int = 50,
        rgb: tuple[int, int, int] = (0xAA, 0xBB, 0xCC),
        submode: int | None = None,
        has_page: bool | None = None,
        has_secret: bool = False,
        answers_light_count: bool = False,
        drop_after_writes: int | None = None,
    ) -> None:
        self.speaks = speaks
        self.power = power
        self.brightness = brightness
        self.rgb = rgb
        self.submode = submode if submode is not None else (speaks or 0x02)
        self.has_page = has_page if has_page is not None else speaks in (0x15, 0x0B)
        self.has_secret = has_secret
        self.answers_light_count = answers_light_count
        self.drop_after_writes = drop_after_writes
        self.writes: list[bytes] = []
        self.queries: list[bytes] = []
        # A verbatim mode body (H6006: 0D 000000 0A8C 000000), reported while
        # the light is still in that sub-mode and has not taken an RGB write.
        self.raw_mode_body: list[int] | None = None
        self.raw_mode_submode: int | None = None

    async def write(self, frame: bytes) -> None:
        self.writes.append(frame)
        if self.drop_after_writes is not None and len(self.writes) > self.drop_after_writes:
            raise ProbeConnectionLost("link dropped")
        if self.speaks is None:
            return
        cmd, payload = frame[1], frame[2:19]
        if cmd == protocol.CMD_POWER:
            self.power = bool(payload[0])
        elif cmd == protocol.CMD_BRIGHTNESS:
            self.brightness = payload[0]
        elif cmd == protocol.CMD_MODE and payload[0] == self.speaks:
            self.raw_mode_body = list(payload[1:9])
            self.raw_mode_submode = payload[0]
            self.submode = payload[0]
            offset = 2 if payload[0] == 0x15 else 1
            self.rgb = tuple(payload[offset : offset + 3])

    async def query(self, frame: bytes, command: int) -> protocol.Reply | None:
        self.queries.append(frame)
        if self.speaks is None:
            return None
        if command == protocol.CMD_POWER:
            return protocol.parse_reply(_reply(command, int(self.power)))
        if command == protocol.CMD_BRIGHTNESS:
            return protocol.parse_reply(_reply(command, self.brightness))
        if command == protocol.CMD_MODE:
            body = [self.submode]
            if self.raw_mode_body is not None and self.submode == self.raw_mode_submode:
                return protocol.parse_reply(_reply(command, self.submode, *self.raw_mode_body))
            if self.submode in (0x02, 0x0D):
                body += list(self.rgb)
            elif self.submode == 0x15:
                body += [0x01, 0, 0]
            elif self.submode == 0x04:
                body += [0x07, 0x00]
            return protocol.parse_reply(_reply(command, *body))
        if command == protocol.CMD_VERSION:
            return protocol.parse_reply(_reply(command, *b"3.02.24"))
        if command == protocol.CMD_DEVICE_INFO:
            return protocol.parse_reply(_reply(command, 0x03, *b"1.00.04"))
        if command == protocol.CMD_IC_COUNT:
            return protocol.parse_reply(_reply(command, 0x00, 0x0F, 0x0F))
        if command == protocol.CMD_LIGHT_COUNT:
            return protocol.parse_reply(_reply(command, 20)) if self.answers_light_count else None
        if command == protocol.CMD_SECRET_READ:
            if not self.has_secret:
                return None
            return protocol.parse_reply(_reply(command, 0x01, 1, 2, 3, 4, 5, 6, 7, 8))
        if command == protocol.CMD_SEGMENT_COLORS:
            if not self.has_page:
                return None
            records = [0x64, *self.rgb] * 3
            return protocol.parse_reply(_reply(command, frame[2], *records))
        return None


@pytest.fixture(autouse=True)
def _no_reset_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """The between-dialect baseline pause is for human eyes, not tests."""
    monkeypatch.setattr(census, "RESET_SETTLE_S", 0)
    monkeypatch.setattr(census, "RESET_SETTLE_ASKING_S", 0)


H617A_ADV = parse_raw(bytes.fromhex("0388ec000a0101"))
ENCRYPTED_ADV = parse_raw(bytes.fromhex("4388ec00010100"))


async def test_read_only_census_writes_nothing_and_records_replies() -> None:
    light = FakeLight(speaks=0x02, has_secret=True)
    result = await census.async_census(light)
    assert light.writes == []
    assert result.reads == 9 and result.replies == 7
    assert result.power is True and result.brightness == 50
    assert (
        result.mode is not None
        and result.mode.submode == 0x02
        and result.mode.rgb == (0xAA, 0xBB, 0xCC)
    )
    assert result.firmware == "3.02.24" and result.hardware == "1.00.04"
    assert (result.ic_count, result.segments) == (15, 15)
    assert result.light_count is None
    assert result.secret_present and result.secret == bytes([1, 2, 3, 4, 5, 6, 7, 8])
    assert result.segment_page is None
    assert not result.silent and not result.probed


async def test_silent_light_is_bounded_to_three_reads() -> None:
    light = FakeLight(speaks=None)
    result = await census.async_census(light, probe_writes=True)
    assert result.silent
    assert result.reads == census.SILENT_AFTER and result.replies == 0
    assert light.writes == [] and not result.probed


@pytest.mark.parametrize("speaks", [0x02, 0x0D, 0x15, 0x0B])
async def test_probe_echo_is_per_dialect(speaks: int) -> None:
    light = FakeLight(speaks=speaks, has_page=True)
    result = await census.async_census(light, probe_writes=True)
    assert result.probed
    assert result.echoed == [speaks]


async def test_probe_ignores_a_colour_that_was_already_showing() -> None:
    light = FakeLight(speaks=0x15, rgb=census.PROBE_COLORS[0x02])
    result = await census.async_census(light, probe_writes=True)
    assert result.echoed == [0x15]


async def test_probe_restores_colour_and_power() -> None:
    light = FakeLight(speaks=0x0D, power=False)
    result = await census.async_census(light, probe_writes=True)
    assert result.echoed == [0x0D]
    assert light.writes[0] == protocol.power_frame(True)
    assert light.power is False
    assert light.rgb == (0xAA, 0xBB, 0xCC)
    assert result.restored == "full" and result.restore_note is None


async def test_probe_says_when_colour_cannot_be_restored() -> None:
    light = FakeLight(speaks=0x15)
    result = await census.async_census(light, probe_writes=True)
    assert result.restored == "power" and "0B/15" in (result.restore_note or "")
    scene = FakeLight(speaks=0x02, submode=0x04)
    result = await census.async_census(scene, probe_writes=True)
    assert result.restored == "power" and "not a static colour" in (result.restore_note or "")


async def test_abort_stops_between_reads() -> None:
    light = FakeLight(speaks=0x02)
    calls = iter([False, False, True, True, True, True, True, True, True, True, True, True])
    result = await census.async_census(light, probe_writes=True, abort=lambda: next(calls))
    assert result.aborted and result.reads == 2 and not result.probed and not result.silent


async def test_disconnect_mid_probe_is_reported_not_retried() -> None:
    light = FakeLight(speaks=0x02, drop_after_writes=1)
    result = await census.async_census(light, probe_writes=True)
    assert result.error is not None and "disconnected" in result.error
    assert len(light.writes) == 2


def _classified(result: census.CensusResult, model: str, adv=H617A_ADV, bgc=None) -> census.Verdict:
    return census.classify(result, model=model, adv=adv, bgc=bgc)


async def test_classify_verified_from_echo() -> None:
    result = await census.async_census(FakeLight(speaks=0x15), probe_writes=True)
    verdict = _classified(result, "H617A")
    assert (verdict.family, verdict.security, verdict.confidence) == (
        "plaintext-rgbic",
        "none",
        "verified",
    )
    assert verdict.control_dialects == [0x15]


async def test_classify_inferred_from_mode_reply_only() -> None:
    result = await census.async_census(FakeLight(speaks=0x0D))
    verdict = _classified(result, "H6181")
    assert (verdict.family, verdict.confidence) == ("plaintext-rgbww", "inferred")
    assert verdict.security == "none"


async def test_classify_secret_locked_versus_silent_unknown() -> None:
    result = await census.async_census(FakeLight(speaks=None))
    locked = _classified(result, "H617A")
    assert (locked.family, locked.security, locked.confidence) == (
        "secret-locked",
        "secret-key",
        "probable",
    )
    unknown = _classified(result, "H9999")
    assert (unknown.family, unknown.confidence) == ("silent-unknown", "unknown")
    assert any(
        "encryption flag unknown" in r for r in _classified(result, "H9999", adv=None).reasons
    )


async def test_classify_encrypted_from_flag_and_bgc() -> None:
    result = census.CensusResult(connected=True)
    flagged = _classified(result, "H61F6", adv=ENCRYPTED_ADV)
    assert (flagged.family, flagged.security, flagged.confidence) == (
        "encrypted-unknown",
        "unknown",
        "inferred",
    )
    v2 = _classified(result, "H1270", adv=ENCRYPTED_ADV, bgc=b"\x01\x02")
    assert (v2.family, v2.security, v2.confidence) == ("encrypted-v2", "aes-gcm", "probable")
    v1 = _classified(result, "H600B", adv=None, bgc=b"\x01\x00")
    assert (v1.family, v1.security) == ("encrypted-v1", "aes-ecb-rc4")
    assert _classified(census.CensusResult(), "H6159").family == "unreachable"


async def test_summary_record_shape_and_secret_handling() -> None:
    light = FakeLight(speaks=0x02, has_secret=True)
    result = await census.async_census(light, probe_writes=True)
    rec = census.summary_record(
        result,
        address="A4:C1:38:00:00:01",
        name="ihoment_H6159_0001",
        model="H6159",
        adv=H617A_ADV,
        rssi=-60,
        source="pi",
        at="2026-10-19T12:00:00+00:00",
        software="govee_rig.py test",
        dialect=0x02,
        dialect_source="table",
        known_model=True,
    )
    assert rec["summary"] == "light" and rec["schema"] == "govee-evidence/1"
    assert rec["secret_present"] is True and "secret" not in rec
    assert rec["echoed"] == "02" and rec["restored"] == "full"
    assert rec["pact"] == "10/1" and rec["encrypted"] is False
    assert rec["dialect"] == "RGB" and rec["dialect_source"] == "table"
    assert rec["submode_name"] == "colour02"
    private = census.summary_record(
        result, address="x", name=None, model="H6159", adv=None, rssi=None,
        source="pi", at="t", software="s", include_secret=True,
    )  # fmt: skip
    assert private["secret"] == "0102030405060708" and private["pact"] is None


def test_probe_colours_are_distinct_and_safe() -> None:
    colours = list(census.PROBE_COLORS.values())
    assert len(set(colours)) == len(colours)
    for rgb in colours:
        assert all(0 < c < 0xFF for c in rgb)


async def test_white_0d_state_is_restored_verbatim_not_as_black() -> None:
    """The H6006 on the bench: 0x0D white at 2700 K, RGB and tint zero."""
    light = FakeLight(speaks=0x0D)
    light.raw_mode_body = [0, 0, 0, 0x0A, 0x8C, 0, 0, 0]
    light.raw_mode_submode = 0x0D
    result = await census.async_census(light, probe_writes=True)
    assert result.mode is not None and result.mode.color_temp_kelvin == 2700
    restore = [w for w in light.writes if w[1] == protocol.CMD_MODE][-1]
    assert restore == protocol.build_frame(protocol.CMD_MODE, [0x0D, 0, 0, 0, 0x0A, 0x8C, 0, 0, 0])
    assert result.restored == "full"


class AckingLight(FakeLight):
    """Acks every mode write the way the H6006 did: 33 05 00 if understood, 33 05 01 if not."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.ack_log: list[tuple[int, int]] = []

    async def write(self, frame: bytes) -> None:
        await super().write(frame)
        if frame[1] == protocol.CMD_MODE:
            self.ack_log.append((protocol.CMD_MODE, 0 if frame[2] == self.speaks else 1))


async def test_probe_records_write_acks_per_dialect() -> None:
    light = AckingLight(speaks=0x0D)
    result = await census.async_census(light, probe_writes=True)
    assert result.probe_acks == {
        0x02: "rejected",
        0x0B: "rejected",
        0x0D: "accepted",
        0x15: "rejected",
    }
    verdict = census.classify(result, model="H6006", adv=None)
    assert any("accepted 0x0d; rejected 0x02, 0x0b, 0x15" in r for r in verdict.reasons)
    rec = census.summary_record(
        result,
        address="x",
        name=None,
        model="H6006",
        adv=None,
        rssi=None,
        source="pi",
        at="t",
        software="s",
    )
    assert rec["probe_acks"] == {
        "02": "rejected",
        "0B": "rejected",
        "0D": "accepted",
        "15": "rejected",
    }


async def test_probe_asks_after_each_write_before_reading_back() -> None:
    light = FakeLight(speaks=0x0D)
    asked: list[tuple[int, int]] = []

    async def ask(submode: int, rgb: tuple[int, int, int]) -> str | None:
        # The write for this dialect is the last thing sent; no read-back yet.
        asked.append((submode, len(light.queries)))
        assert light.writes[-1][2] == submode
        return "yes" if submode == 0x0D else "no"

    result = await census.async_census(light, probe_writes=True, ask=ask)
    assert [s for s, _ in asked] == list(census.PROBE_ORDER)
    assert result.observed == {0x02: "no", 0x0B: "no", 0x0D: "yes", 0x15: "no"}
    assert any(
        "operator saw a change on 0x0d" in r
        for r in census.classify(result, model="H6006", adv=None).reasons
    )


async def test_quitting_the_questions_still_restores() -> None:
    light = FakeLight(speaks=0x0D, power=False)

    async def ask(submode: int, rgb: tuple[int, int, int]) -> str | None:
        return None if submode == 0x0B else "no"

    result = await census.async_census(light, probe_writes=True, ask=ask)
    assert result.aborted and result.observed == {0x02: "no"}
    assert result.restored == "full" and light.power is False


def test_probe_colours_are_far_apart_to_the_eye() -> None:
    colours = list(census.PROBE_COLORS.values())
    for i, a in enumerate(colours):
        for b in colours[i + 1 :]:
            assert sum(abs(x - y) for x, y in zip(a, b, strict=True)) > 150
    assert set(census.PROBE_COLOR_NAMES) == set(census.PROBE_COLORS)


async def test_probe_goes_back_to_the_baseline_between_dialects(monkeypatch) -> None:
    monkeypatch.setattr(census, "RESET_SETTLE_S", 0)
    light = FakeLight(speaks=0x0D)
    light.raw_mode_body = [0, 0, 0, 0x0A, 0x8C, 0, 0, 0]
    light.raw_mode_submode = 0x0D
    seen_before_write: list[tuple[int, ...]] = []

    async def ask(submode: int, rgb: tuple[int, int, int]) -> str | None:
        # The write just before this probe write was the reset to 2700 K white.
        if submode != census.PROBE_ORDER[0]:
            seen_before_write.append(tuple(light.writes[-2][2:8]))
        return "yes" if submode == 0x0D else "no"

    result = await census.async_census(light, probe_writes=True, ask=ask)
    assert seen_before_write == [(0x0D, 0, 0, 0, 0x0A, 0x8C)] * 3
    assert result.echoed == [0x0D] and result.restored == "full"


async def test_baseline_reset_is_announced_to_the_watcher() -> None:
    light = FakeLight(speaks=0x0D)
    events: list[str] = []

    async def ask(submode: int, rgb: tuple[int, int, int]) -> str | None:
        events.append(f"ask {submode:02X}")
        return "no"

    await census.async_census(
        light, probe_writes=True, ask=ask, notify=lambda t: events.append("reset")
    )
    # Every question after the first is preceded by an announced reset.
    assert events == ["ask 02", "reset", "ask 0B", "reset", "ask 0D", "reset", "ask 15"]
