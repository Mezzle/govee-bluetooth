"""The brightness-scale decision in govee_rig.py (the stepped test's logic, no Bluetooth)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "govee_rig.py"


@pytest.fixture(scope="module")
def rig():
    spec = importlib.util.spec_from_file_location("govee_rig_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["govee_rig_under_test"] = module
    spec.loader.exec_module(module)
    return module


def test_eyes_and_read_back_agree_on_255(rig) -> None:
    scale, reason = rig.scale_from(True, "no", "yes")
    assert scale == "255" and "stored 255" in reason


def test_eyes_say_100_even_if_the_light_stores_255(rig) -> None:
    scale, reason = rig.scale_from(True, "yes", "no")
    assert scale == "100" and "clamps" in reason


def test_contradictory_answers_are_undecided(rig) -> None:
    assert rig.scale_from(True, "yes", "yes")[0] is None
    assert rig.scale_from(None, "no", "no")[0] is None


def test_read_back_only_when_nobody_answered(rig) -> None:
    assert rig.scale_from(True, None, None) == ("255", "read-back only: the light stored 255")
    assert rig.scale_from(False, None, None)[0] == "100"
