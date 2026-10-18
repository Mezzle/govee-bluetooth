"""Keep the manifest's discovery matchers and KNOWN_MODELS from drifting apart."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.govee_bluetooth.models import KNOWN_MODELS, model_from_name

MANIFEST = Path(__file__).resolve().parents[1] / "custom_components/govee_bluetooth/manifest.json"
MATCHER_PREFIXES = ("ihoment_", "Govee_", "GBK_", "GVH")


def _manifest_matchers() -> set[str]:
    manifest = json.loads(MANIFEST.read_text())
    return {entry["local_name"] for entry in manifest["bluetooth"]}


def test_every_known_model_has_all_four_matchers() -> None:
    matchers = _manifest_matchers()
    missing = {
        f"{prefix}{model}*"
        for model in KNOWN_MODELS
        for prefix in MATCHER_PREFIXES
        if f"{prefix}{model}*" not in matchers
    }
    assert not missing, f"manifest.json is missing matchers: {sorted(missing)}"


def test_every_matcher_refers_to_a_known_model() -> None:
    unknown = set()
    for matcher in _manifest_matchers():
        assert matcher.endswith("*"), matcher
        model = model_from_name(matcher.removesuffix("*"))
        if model not in KNOWN_MODELS:
            unknown.add(matcher)
    assert not unknown, f"manifest.json matches models not in KNOWN_MODELS: {sorted(unknown)}"


def test_tier1_models_use_legacy_dialect() -> None:
    for model in ("H6181", "H7020", "H7021", "H6104", "H6195"):
        profile = KNOWN_MODELS[model]
        assert profile.color_submode == 0x02
        assert profile.brightness_scale == 255
        assert profile.supports_scenes
