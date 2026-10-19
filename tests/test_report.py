"""The report generator over the fixture logs: ingest, redaction, verdicts, badge, rendering."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
TOOL = Path(__file__).resolve().parents[1] / "tools" / "govee_report.py"


@pytest.fixture(scope="module")
def report():
    spec = importlib.util.spec_from_file_location("govee_report", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["govee_report"] = module  # dataclasses resolve string annotations via sys.modules
    spec.loader.exec_module(module)
    return module


def _doc(report, *names: str, keep_secrets: bool = False, observe: list[str] | None = None):
    evidence = report.Evidence(keep_secrets=keep_secrets)
    for name in names:
        report.load_input(evidence, FIXTURES / name, source=None)
    for text in observe or []:
        report.ingest_records(
            evidence, [report.parse_observation(text)], source="manual", label="--observe"
        )
    return report.analyse(evidence)


def test_dance_party_log_yields_verified_rgbic_and_the_badge(report) -> None:
    doc = _doc(report, "dance_party_census.jsonl")
    assert doc["schema"] == "govee-evidence-report/1"
    assert doc["dance_party_certified"] is True
    assert len(doc["sessions"]) == 1 and doc["sessions"][0]["source"] == "dance_party"
    by_addr = {d["address"]: d for d in doc["devices"]}
    h617a = by_addr["A4:C1:38:00:00:01"]
    assert h617a["model"] == "H617A" and h617a["dance_party_certified"]
    v = h617a["verdict"]
    assert (v["family"], v["security"], v["confidence"]) == ("plaintext-rgbic", "none", "verified")
    assert v["control_dialects"] == ["0x15"]
    assert h617a["capabilities"]["rgb"]["level"] == "verified"
    assert h617a["capabilities"]["music"]["level"] == "untested"
    assert h617a["secret_present"] is True
    assert h617a["probe"]["writes"] == ["0x15"] and h617a["probe"]["echoed"] == ["0x15"]
    assert h617a["restoration"]["restored"] == "power"
    assert h617a["reads"]["acks"] == 4  # the summary's count wins over the single logged ack
    h61f6 = by_addr["A4:C1:38:00:00:02"]
    assert h61f6["known_model"] is False
    assert h61f6["verdict"]["family"] == "encrypted-unknown"
    assert h61f6["verdict"]["confidence"] == "inferred"
    assert h61f6["reads"]["timeouts"] == 3


def test_pi_log_without_summaries_is_reconstructed_from_frames_and_redacted(report) -> None:
    doc = _doc(report, "pi_rig_h6181.jsonl")
    assert doc["dance_party_certified"] is False
    (dev,) = doc["devices"]
    assert dev["sources"] == ["pi"] and dev["model"] is None
    assert dev["firmware"] == "2.03.11"
    assert dev["state_seen"]["submode"] == "0x0d"
    # No summary line: the verdict is inferred from the mode reply, never verified.
    assert dev["verdict"]["confidence"] == "inferred"
    assert dev["verdict"]["family"] == "plaintext-rgbww"
    # The raw AA B1 reply in the bench log was redacted on ingest.
    secret_frames = [f for f in dev["frames"] if f["frame"].startswith("aab101")]
    assert secret_frames and all(f.get("redacted") for f in secret_frames)
    assert all("deadbeef" not in f["frame"] for f in secret_frames)
    assert dev["frame_counts"]["redacted"] == 1
    assert dev["secret_present"] is True


def test_keep_secrets_leaves_private_captures_alone(report) -> None:
    doc = _doc(report, "pi_rig_h6181.jsonl", keep_secrets=True)
    (dev,) = doc["devices"]
    assert any("deadbeef" in f["frame"] for f in dev["frames"])
    assert doc["secrets_redacted"] is False
    assert "do not share" in report.render_markdown(doc)


def test_esp32_serial_log_skips_chatter_and_reads_census_lines(report) -> None:
    doc = _doc(report, "esp32_usb.log")
    (dev,) = doc["devices"]
    assert dev["sources"] == ["esp32"] and dev["model"] == "H6159"
    assert dev["gatt"] == "1910/2b11/2b10"
    assert dev["capabilities"]["scene"]["level"] == "inferred"
    assert dev["verdict"]["family"] == "unknown"  # a scene reply names no colour dialect
    assert doc["sessions"][0]["board"] == "M5STACK_ATOMS3"


def test_sources_merge_per_device_and_observations_count(report) -> None:
    doc = _doc(
        report,
        "dance_party_census.jsonl",
        "esp32_usb.log",
        observe=[
            "a4:c1:38:00:00:01=whole strip went dark blue on the 0x15 write;capability=rgb;outcome=worked"
        ],
    )
    by_addr = {d["address"]: d for d in doc["devices"]}
    h617a = by_addr["A4:C1:38:00:00:01"]
    assert set(h617a["sources"]) == {"dance_party", "manual"}
    assert h617a["capabilities"]["rgb"]["level"] == "observed"
    assert h617a["observations"][0]["outcome"] == "worked"
    assert len(doc["devices"]) == 3


def test_markdown_rendering_mentions_badge_verdict_and_limits(report) -> None:
    doc = _doc(report, "dance_party_census.jsonl")
    md = report.render_markdown(doc)
    assert md.startswith("# Govee BLE compatibility report")
    assert "🪩 Dance Party Certified" in md
    assert "### H617A at A4:C1:38:00:00:01 🪩" in md
    assert "**Verdict: plaintext-rgbic**" in md
    assert "**Verdict: encrypted-unknown**" in md
    assert "Not established by this evidence" in md
    assert "redacted on ingest" in md


def test_cli_writes_json_and_markdown(report, tmp_path: Path) -> None:
    out_json = tmp_path / "evidence.json"
    out_md = tmp_path / "report.md"
    rc = report.main(
        [
            str(FIXTURES / "dance_party_census.jsonl"),
            "--json",
            str(out_json),
            "--markdown",
            str(out_md),
        ]
    )
    assert rc == 0
    doc = json.loads(out_json.read_text())
    assert doc["dance_party_certified"] and len(doc["devices"]) == 2
    assert "Dance Party Certified" in out_md.read_text()
    assert report.main([str(tmp_path / "missing.jsonl")]) == 2


def test_observation_parsing(report) -> None:
    obs = report.parse_observation(
        "aa:bb:cc:dd:ee:ff=segments 1-5 lit;capability=segments;outcome=partial"
    )
    assert (
        obs["address"] == "AA:BB:CC:DD:EE:FF"
        and obs["capability"] == "segments"
        and obs["outcome"] == "partial"
    )
    with pytest.raises(argparse.ArgumentTypeError):
        report.parse_observation("no-equals-sign")
