"""Keep the manifest's discovery matchers and KNOWN_MODELS from drifting apart."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.govee_bluetooth import models, protocol
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


def test_rgbic_profile_uses_segmented_colour_and_percent_brightness() -> None:
    for model in ("H619A", "H6143", "H6199", "H6061", "H6076"):
        profile = models.profile_for(model)
        assert profile.model == model
        assert profile.color_submode == protocol.SUBMODE_COLOR_RGBIC
        assert profile.brightness_scale == 100
        assert not profile.supports_scenes


def test_unknown_model_falls_back_to_legacy_dialect() -> None:
    profile = models.profile_for("H9999")
    assert profile.model == "H9999"
    assert profile.color_submode == protocol.SUBMODE_COLOR_LEGACY
    assert "unverified" in profile.description


def test_brightness_scale_override_keeps_colour_submode() -> None:
    profile = models.profile_for("H619A", brightness_scale=255)
    assert profile.color_submode == protocol.SUBMODE_COLOR_RGBIC
    assert profile.brightness_scale == 255


def test_every_model_is_discoverable_by_name() -> None:
    # H619Z has a non-hex suffix, which the original name pattern rejected.
    for model in KNOWN_MODELS:
        assert model_from_name(f"ihoment_{model}_1A2B") == model
        assert model_from_name(f"GVH{model}_1A2B") == model
