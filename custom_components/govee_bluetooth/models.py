"""Model profiles and advertisement-name parsing for Govee BLE lights.

Govee reuses a handful of wire dialects across many SKUs. The only per-model
facts this integration needs are which colour sub-mode byte the firmware
expects and how it scales brightness. Everything else (framing, checksum,
status reads) is shared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Advertised local names look like ``ihoment_H6159_1A2B``, ``Govee_H6159_1A2B``,
# ``GBK_H6159_1A2B`` or ``GVH6159_1A2B``.
_NAME_RE = re.compile(
    r"^(?:ihoment_|Govee_|Minger_|GBK_|GVH|GVR)(H[0-9A-F]{4})(?:_|$)", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """Wire-level quirks for one family of Govee lights."""

    model: str
    description: str
    color_submode: int
    brightness_scale: int  # 255 or 100
    scenes: dict[str, int]

    @property
    def supports_scenes(self) -> bool:
        return bool(self.scenes)


# Built-in scene IDs for the H6159 family (``33 05 04 <id>``). Taken from the
# softgrass/govee-api-ble captures, which were confirmed on an H6159.
LEGACY_SCENES: dict[str, int] = {
    "Sunrise": 0x00,
    "Sunset": 0x01,
    "Movie": 0x04,
    "Dating": 0x05,
    "Romantic": 0x07,
    "Blinking": 0x08,
    "Candlelight": 0x09,
    "Snowflake": 0x0F,
}

# The classic Telink-based RGB strips. Colour is ``33 05 02 R G B``, brightness
# is a raw byte 0x00-0xFF. The H6159 app module also drives H6110/H6109/H614x.
_LEGACY_RGB = ModelProfile(
    model="H6159",
    description="Govee RGB strip (H6159 family)",
    color_submode=0x02,
    brightness_scale=255,
    scenes=LEGACY_SCENES,
)

# Every model that speaks the legacy dialect, grouped by form factor. Only the
# H6159 has been exercised on hardware; the rest are listed on the strength of
# the decompiled Govee app and community packet captures. The manifest's
# ``bluetooth`` matchers must list exactly these models (see tests/test_models.py).
LEGACY_RGB_MODELS: dict[str, str] = {
    **dict.fromkeys(
        (
            "H6159",
            "H6110",
            "H6109",
            "H614A",
            "H614B",
            "H614E",
            "H6127",
            "H6107",
            "H6116",
            "H6161",
            "H6129",
            "H6139",
            "H6141",
            "H6142",
            "H6104",
            "H6117",
            "H6125",
            "H6126",
            "H6135",
            "H6137",
            "H6138",
            "H6160",
            "H6163",
            "H6170",
            "H6171",
            "H6172",
            "H6182",
            "H6188",
            "H6195",
        ),
        "Govee RGB strip (H6159 family)",
    ),
    "H6181": "Govee RGB TV backlight (H6159 dialect)",
    "H7020": "Govee outdoor string lights (H6159 dialect)",
    "H7021": "Govee outdoor string lights (H6159 dialect)",
}

KNOWN_MODELS: dict[str, ModelProfile] = {
    model: ModelProfile(
        model=model,
        description=description,
        color_submode=_LEGACY_RGB.color_submode,
        brightness_scale=_LEGACY_RGB.brightness_scale,
        scenes=_LEGACY_RGB.scenes,
    )
    for model, description in LEGACY_RGB_MODELS.items()
}

DEFAULT_MODEL = "H6159"


def model_from_name(name: str | None) -> str | None:
    """Extract the SKU (e.g. ``H6159``) from an advertised Bluetooth name."""
    if not name:
        return None
    match = _NAME_RE.match(name)
    if not match:
        return None
    return match.group(1).upper()


def is_govee_name(name: str | None) -> bool:
    """Return True if the advertised name looks like a Govee light."""
    return model_from_name(name) is not None


def profile_for(model: str | None, brightness_scale: int | None = None) -> ModelProfile:
    """Return the profile for a model, falling back to the legacy RGB dialect."""
    base = KNOWN_MODELS.get((model or "").upper(), _LEGACY_RGB)
    if model and model.upper() not in KNOWN_MODELS:
        base = ModelProfile(
            model=model.upper(),
            description=f"Govee {model.upper()} (unverified, using H6159 dialect)",
            color_submode=base.color_submode,
            brightness_scale=base.brightness_scale,
            scenes=base.scenes,
        )
    if brightness_scale in (100, 255) and brightness_scale != base.brightness_scale:
        base = ModelProfile(
            model=base.model,
            description=base.description,
            color_submode=base.color_submode,
            brightness_scale=brightness_scale,
            scenes=base.scenes,
        )
    return base
