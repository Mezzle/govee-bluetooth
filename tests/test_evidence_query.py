"""tools/evidence_query.py: what discover.sh reads back out of the rig's logs."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "evidence_query.py"


@pytest.fixture(scope="module")
def eq():
    spec = importlib.util.spec_from_file_location("evidence_query", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["evidence_query"] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, *recs: dict) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in recs) + "serial chatter\n")
    return path


def _run(eq, capsys, *argv: str) -> str:
    assert eq.main(list(argv)) == 0
    return capsys.readouterr().out.strip()


def test_light_fields_come_from_the_latest_run(eq, tmp_path, capsys) -> None:
    log = _write(
        tmp_path / "census.jsonl",
        {"summary": "session", "source": "pi"},
        {"summary": "light", "firmware": "0.9", "address": "OLD"},
        {"summary": "session", "source": "pi"},
        {
            "at": "t",
            "direction": "tx",
            "frame": "aa",
            "note": "query",
            "decoded": "",
            "address": "A",
        },
        {
            "summary": "light",
            "firmware": "1.00.38",
            "encrypted": False,
            "pact": None,
            "probe_acks": {"02": "rejected", "0D": "accepted"},
        },
    )
    assert _run(eq, capsys, str(log), "light", "firmware") == "1.00.38"
    assert _run(eq, capsys, str(log), "light", "encrypted") == "no"
    assert _run(eq, capsys, str(log), "light", "pact") == ""
    assert _run(eq, capsys, str(log), "light", "probe_acks") == "02=rejected,0D=accepted"
    assert _run(eq, capsys, str(log), "light", "missing") == ""
    assert _run(eq, capsys, str(tmp_path / "nope.jsonl"), "light", "firmware") == ""


def test_ads_strongest_first_deduplicated(eq, tmp_path, capsys) -> None:
    log = _write(
        tmp_path / "scan.jsonl",
        {"summary": "session"},
        {"summary": "advertisement", "address": "AA", "name": "ihoment_H6159_8E17", "model": "H6159",
         "pact": "1/1", "encrypted": False, "rssi": -81},
        {"summary": "advertisement", "address": "BB", "name": "ihoment_H6006_B075", "model": "H6006",
         "pact": "1/1", "encrypted": False, "rssi": -40},
        {"summary": "advertisement", "address": "CC", "name": "GVH61F6_X", "model": None,
         "pact": None, "encrypted": None, "rssi": -60},
        {"summary": "advertisement", "address": "AA", "name": "ihoment_H6159_8E17", "model": "H6159",
         "pact": "1/1", "encrypted": False, "rssi": -79},
    )  # fmt: skip
    assert _run(eq, capsys, str(log), "ads").splitlines() == [
        "BB|ihoment_H6006_B075|H6006|1/1|no|-40",
        "CC|GVH61F6_X|-|-|?|-60",
        "AA|ihoment_H6159_8E17|H6159|1/1|no|-79",
    ]


def test_verify_and_echoed(eq, tmp_path, capsys) -> None:
    log = _write(
        tmp_path / "bri.jsonl",
        {"summary": "session"},
        {
            "summary": "verify",
            "action": "brightness",
            "args": [100],
            "echoed": True,
            "verdict": "ok",
        },
        {
            "summary": "verify",
            "action": "brightness",
            "args": [200],
            "echoed": False,
            "verdict": "differs",
        },
        {"summary": "verify", "action": "scene", "args": [0], "echoed": True, "verdict": "ok"},
        {"summary": "verify", "action": "scene", "args": [1], "echoed": False, "verdict": "no"},
        {"summary": "verify", "action": "scene", "args": [4], "echoed": True, "verdict": "ok"},
        {"summary": "verify", "action": "ct", "args": [13, 4300], "echoed": True, "verdict": "ok"},
        {"summary": "verify", "action": "ct", "args": [13, 6700], "echoed": False, "verdict": "no"},
    )
    assert _run(eq, capsys, str(log), "verify", "brightness", "200") == "no"
    assert _run(eq, capsys, str(log), "verify", "brightness", "100") == "yes"
    assert _run(eq, capsys, str(log), "verify", "ct", "4300") == "yes"
    assert _run(eq, capsys, str(log), "verify", "ct", "6700") == "no"
    assert _run(eq, capsys, str(log), "verify", "power") == ""
    assert _run(eq, capsys, str(log), "echoed", "scene") == "0,4"


def test_usage_errors(eq, capsys) -> None:
    assert eq.main([]) == 2
    assert eq.main(["x", "bogus"]) == 2
