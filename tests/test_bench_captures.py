"""Independent reply captures retain their observed semantics and provenance."""

import json
from pathlib import Path

import pytest

from custom_components.govee_bluetooth import protocol

EVIDENCE = Path(__file__).resolve().parents[1] / "docs/bench-evidence/2026-10-23-additional"
CASES = json.loads((EVIDENCE / "fixtures.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[f"{c['model']}-{c['observed']}" for c in CASES])
def test_captured_reply(case) -> None:
    frame = bytes.fromhex(case["frame"])
    assert protocol.verify_frame(frame)
    records = [json.loads(line) for line in (EVIDENCE / case["source"]).read_text().splitlines()]
    assert any(
        record.get("frame") == case["frame"]
        and record.get("at") == case["at"]
        and record.get("direction") == "rx"
        for record in records
    )
    reply = protocol.parse_reply(frame)
    expected = case["expected"]
    if expected.get("ack_only"):
        # A successful write ACK cannot masquerade as independently read state.
        assert reply is None
        return
    assert reply is not None
    for key, value in expected.items():
        target = (
            reply.mode
            if key in {"submode", "rgb", "white_tint", "color_temp_kelvin", "is_white"}
            else reply
        )
        assert target is not None
        actual = getattr(target, key)
        assert actual == (tuple(value) if isinstance(value, list) else value)
