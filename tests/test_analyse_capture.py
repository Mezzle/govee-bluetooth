"""Offline capture hints must not leak secrets or claim demonstrated control."""

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "analyse_capture.py"
spec = importlib.util.spec_from_file_location("analyse_capture", TOOL)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


def test_no_att_explains_insufficient_capture():
    assert "No ATT traffic" in capture.summarise("")


def test_payloads_withheld_even_for_truncated_or_unknown_frames():
    secret = "deadbeefcafefeed"
    rows = "\n".join(
        f"0x0040\t0x52\t0x0015\t{value}"
        for value in ("33b2" + secret, "aab101" + secret, secret, "33b2de")
    )
    summary = capture.summarise(rows)
    assert secret not in summary
    assert "33b2de" not in summary
    assert "3 x pairing-secret exchange candidate" in summary
    assert "encryption not established" in summary


def test_xor_checked_and_channels_kept_separate():
    # Synthetic power-on command with valid XOR, and one corrupted byte.
    frame = "330101" + "00" * 16 + "33"
    rows = f"0x0040\t0x52\t0x0015\t{frame}\n0x0041\t0x1b\t0x0018\t{frame[:-2]}32"
    summary = capture.summarise(rows)
    assert "ATT packets: 2" in summary
    assert "link 0x0040" in summary and "link 0x0041" in summary
    assert "XOR-valid plaintext candidate" in summary
    assert "invalid XOR" in summary
    assert "not verified Govee support" in summary


@pytest.mark.parametrize("failure", [FileNotFoundError(), subprocess.TimeoutExpired("tshark", 60)])
def test_tool_failure_is_actionable(tmp_path, monkeypatch, capsys, failure):
    path = tmp_path / "capture.btsnoop"
    path.touch()

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(capture.subprocess, "run", fail)
    assert capture.main([str(path)]) == 1
    assert capsys.readouterr().err


def test_dissector_errors_never_echo_private_capture_content(tmp_path, monkeypatch, capsys):
    path = tmp_path / "capture.btsnoop"
    path.touch()
    monkeypatch.setattr(
        capture.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=2, stdout="secret payload", stderr="secret payload"
        ),
    )
    assert capture.main([str(path)]) == 1
    output = capsys.readouterr()
    assert "secret payload" not in output.err + output.out


def test_offline_invocation_handles_spaces_and_empty_capture(tmp_path, monkeypatch, capsys):
    path = tmp_path / "phone capture.btsnoop"
    path.touch()

    def run(command, **kwargs):
        assert command[command.index("-r") + 1] == str(path)
        assert kwargs["timeout"] == 60
        assert "-Y" in command and "btatt" in command
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(capture.subprocess, "run", run)
    assert capture.main([str(path)]) == 0
    assert "No ATT traffic" in capsys.readouterr().out
