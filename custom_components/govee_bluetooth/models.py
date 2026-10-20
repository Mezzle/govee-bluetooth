"""Model profiles and advertisement-name parsing for Govee BLE lights.

Govee reuses a handful of wire dialects across many SKUs. The only per-model
facts this integration needs are which colour sub-mode byte the firmware
expects and how it scales brightness. Everything else (framing, checksum,
status reads) is shared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

# Advertised local names look like ``ihoment_H6159_1A2B``, ``Govee_H6159_1A2B``,
# ``GBK_H6159_1A2B`` or ``GVH6159_1A2B``. The app's ``BaseBleProcessor`` splits
# the first three forms on ``_``; for ``GV`` names it takes ``substring(2)``,
# so the SKU letter is the third character and newer SKUs may omit the
# separator altogether (``GVH600B4F5A``). ``GVR...`` are sensors, not lights.
# Light SKUs are ``H`` + digit + three alphanumerics (H6159, H600B, H61F6).
_NAME_RE = re.compile(
    r"^(?:(?:ihoment_|Govee_|Minger_|GBK_)(H[0-9][0-9A-Z]{3})(?:_|$)|GV(H[0-9][0-9A-Z]{3}))",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """Wire-level quirks for one family of Govee lights."""

    model: str
    description: str
    color_submode: int
    brightness_scale: int  # 255 or 100
    scenes: dict[str, int]
    # Colour-temperature range the firmware renders. The default is the
    # widest any Govee family documents (strips, SPEC:1255-1256); a model
    # whose firmware clamps tighter gets its measured range.
    min_kelvin: int = 2000
    max_kelvin: int = 9000

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
    "H7020": "Govee outdoor string lights (H6159 dialect)",
    "H7021": "Govee outdoor string lights (H6159 dialect)",
}

# RGBIC strips, TV backlights, light bars and panels. Colour is the segmented
# ``33 05 15 01 R G B ... <mask>`` command sent with a whole-strip mask, white is
# the same frame with a kelvin field, and brightness is a percentage. Scene IDs
# on these firmwares differ from the legacy table and are left empty until
# they can be confirmed on hardware. None of these has been verified on
# hardware yet.
_RGBIC = ModelProfile(
    model="H619A",
    description="Govee RGBIC strip (0x15 colour dialect)",
    color_submode=0x15,
    brightness_scale=100,
    scenes={},
)

RGBIC_MODELS: dict[str, str] = {
    **dict.fromkeys(
        (
            "H6143",
            "H6144",
            "H619A",
            "H619B",
            "H619C",
            "H619E",
            "H619Z",
            "H61A0",
            "H61A1",
            "H61A2",
            "H618A",
            "H61B5",
            "H61E1",
            "H61E5",
        ),
        "Govee RGBIC strip (0x15 colour dialect)",
    ),
    "H6168": "Govee RGBIC TV backlight (0x15 colour dialect)",
    "H6199": "Govee RGBIC TV backlight (0x15 colour dialect)",
    # The most-captured RGBIC strip in the community sources (GENZERO fw 3.02.24,
    # rnodern, ha-govee-led-ble): 0x15 writes on pact 10/1. Not bench verified.
    "H617A": "Govee RGBIC strip (0x15 colour dialect)",
    **dict.fromkeys(
        ("H6052", "H6056", "H6059", "H6061", "H6062", "H6065", "H6072", "H6076"),
        "Govee RGBIC light bar / lamp / panel (0x15 colour dialect)",
    ),
}


def _family(template: ModelProfile, models: dict[str, str]) -> dict[str, ModelProfile]:
    return {
        model: replace(template, model=model, description=description)
        for model, description in models.items()
    }


# Verified on hardware 2026-10-18: an H6181 on firmware 2.03.11 speaks the
# RGBWW colour scheme (sub-mode 0x0D, kelvin carried in the frame) and takes
# brightness 0-100, not the legacy 0x02 / 0-255 the decompiled app lists for
# it. RGB, colour temperature, scenes and read-back all confirmed.
_RGBWW_TV = ModelProfile(
    model="H6181",
    description="Govee RGB TV backlight (0x0D colour scheme)",
    color_submode=0x0D,
    brightness_scale=100,
    scenes=LEGACY_SCENES,
)

# Verified on hardware 2026-10-19/20 (Pi bench rig): an H6006 bulb on
# firmware 1.00.38, hardware 1.02.01, pact 1/1, plaintext. Colour only in
# 0x0D (0x02 / 0x0B / 0x15 are rejected with a 33 05 01 ack); white in 0x0D
# with kelvin echoed and clamped to 2700-6500 K; brightness 0-100, where
# a byte above 100 is acked but renders dimmer, so it must never be sent;
# none of the legacy scene IDs visibly ran. Its 2b11 is write-without-
# response only, which device.py now handles per connection.
_RGBWW_BULB = ModelProfile(
    model="H6006",
    description="Govee RGBWW bulb (0x0D colour scheme)",
    color_submode=0x0D,
    brightness_scale=100,
    scenes={},
    # Read back on the bench: 2000 K -> 2700 K, 9000 K -> 6500 K; warm and
    # cool both confirmed by eye (2026-10-20).
    min_kelvin=2700,
    max_kelvin=6500,
)

KNOWN_MODELS: dict[str, ModelProfile] = {
    **_family(_LEGACY_RGB, LEGACY_RGB_MODELS),
    **_family(_RGBIC, RGBIC_MODELS),
    "H6181": _RGBWW_TV,
    "H6006": _RGBWW_BULB,
}

DEFAULT_MODEL = "H6159"


def model_from_name(name: str | None) -> str | None:
    """Extract the SKU (e.g. ``H6159``) from an advertised Bluetooth name."""
    if not name:
        return None
    match = _NAME_RE.match(name)
    if not match:
        return None
    return (match.group(1) or match.group(2)).upper()


def is_govee_name(name: str | None) -> bool:
    """Return True if the advertised name looks like a Govee light."""
    return model_from_name(name) is not None


def table_profile_for(model: str | None) -> ModelProfile:
    """The static-table profile for a model, before any detected or manual override."""
    base = KNOWN_MODELS.get((model or "").upper(), _LEGACY_RGB)
    if model and model.upper() not in KNOWN_MODELS:
        base = replace(
            base,
            model=model.upper(),
            description=f"Govee {model.upper()} (unverified, using H6159 dialect)",
        )
    return base


def brightness_scale_for_submode(submode: int, fallback: int) -> int:
    """Prior for the brightness scale given a colour sub-mode (0x02 -> 255, else 100)."""
    if submode == 0x02:
        return 255
    if submode in (0x0D, 0x15):
        return 100
    return fallback


def profile_for(
    model: str | None,
    brightness_scale: int | None = None,
    detected_submode: int | None = None,
    color_submode: int | None = None,
) -> ModelProfile:
    """Return the effective profile for a model.

    Precedence for the colour sub-mode: the manual ``color_submode`` override,
    then ``detected_submode`` (what the light itself reported), then the
    static table. When the sub-mode comes from somewhere other than the table
    the brightness scale is re-derived from it (0x02 -> 255, 0x0D/0x15 ->
    100), since the two are only ever distinguished together on hardware. The
    manual ``brightness_scale`` override wins over all of that.
    """
    base = table_profile_for(model)
    submode = base.color_submode
    description = base.description
    if color_submode in (0x02, 0x0D, 0x15):
        submode = color_submode
        description = f"Govee {base.model} (colour sub-mode {submode:#04x} set manually)"
    elif detected_submode in (0x02, 0x0D, 0x15):
        submode = detected_submode
        description = f"Govee {base.model} (colour sub-mode {submode:#04x} detected on the light)"
    scale = base.brightness_scale
    if submode != base.color_submode:
        scale = brightness_scale_for_submode(submode, scale)
    if brightness_scale in (100, 255):
        scale = brightness_scale
    if submode == base.color_submode and scale == base.brightness_scale:
        return base
    return replace(base, description=description, color_submode=submode, brightness_scale=scale)
