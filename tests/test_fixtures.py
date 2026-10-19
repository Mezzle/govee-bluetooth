"""fixtures/govee-models.json is the shared classification; models.py must agree with it."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from custom_components.govee_bluetooth import census, models

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "govee-models.json"


def _fixture() -> dict:  # type: ignore[type-arg]
    return json.loads(FIXTURE.read_text())


def test_every_known_model_is_in_the_fixture_with_the_same_dialect() -> None:
    fixture = _fixture()
    submodes = {name: int(d["submode"], 16) for name, d in fixture["dialects"].items()}
    scales = {name: d["brightness_scale"] for name, d in fixture["dialects"].items()}
    missing = sorted(set(models.KNOWN_MODELS) - set(fixture["models"]))
    assert not missing, f"models.py knows SKUs the fixture does not: {missing}"
    for sku, profile in models.KNOWN_MODELS.items():
        row = fixture["models"][sku]
        assert submodes[row["dialect"]] == profile.color_submode, sku
        assert scales[row["dialect"]] == profile.brightness_scale, sku


def test_fixture_models_not_in_the_table_are_marked_dance_party_only() -> None:
    fixture = _fixture()
    extra = sorted(sku for sku in fixture["models"] if sku not in models.KNOWN_MODELS)
    assert all(fixture["models"][sku].get("dance_party_only") for sku in extra), extra


def test_secret_family_list_matches_census_module() -> None:
    fixture = _fixture()
    assert set(fixture["secret_family_models"]) == census.SECRET_FAMILY_MODELS
    for sku, row in fixture["models"].items():
        assert bool(row.get("secret_family")) == (sku in census.SECRET_FAMILY_MODELS), sku


def test_evidence_values_are_known_words() -> None:
    fixture = _fixture()
    for sku, row in fixture["models"].items():
        assert row["evidence"] in ("hardware", "community", "app", "sibling"), sku
        assert row["dialect"] in fixture["dialects"], sku


def test_export_script_generates_both_headers(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "export_fixtures", ROOT / "tools" / "export_fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["export_fixtures"] = module
    spec.loader.exec_module(module)
    assert module.main(["--out-dir", str(tmp_path)]) == 0
    models_h = (tmp_path / "govee_models_gen.h").read_text()
    vectors_h = (tmp_path / "govee_frame_vectors_gen.h").read_text()
    assert '"H6181"' in models_h and "GOVEE_GEN_RGBWW_MODELS" in models_h
    assert '"H617A"' in models_h and "GOVEE_GEN_SECRET_MODELS" in models_h
    assert '"H6159"' in models_h and "GOVEE_GEN_LEGACY_MODELS" in models_h
    assert (
        "H6125" not in models_h.split("GOVEE_GEN_LEGACY_MODELS")[1].split("};")[0]
    )  # pact decides for it
    assert '{"rgb_rgbic_123456_15seg", "330515011234560000000000ff7f0000000000d2"}' in vectors_h
    assert '{"read_mode", "aa050100000000000000000000000000000000ae"}' in vectors_h
