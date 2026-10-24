"""Constants for the Govee Bluetooth integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "govee_bluetooth"
PLATFORMS: Final = ["light", "sensor"]

CONF_ADDRESS: Final = "address"
CONF_NAME: Final = "name"
CONF_MODEL: Final = "model"
CONF_BRIGHTNESS_SCALE: Final = "brightness_scale"
CONF_COLOR_SUBMODE: Final = "color_submode"  # manual override (options)
CONF_DETECTED_SUBMODE: Final = "detected_color_submode"  # learned from the light (data)
CONF_FIRMWARE: Final = "firmware"
CONF_PROBE_HISTORY: Final = "probe_history"  # last few probe runs with their raw frames (data)
PROBE_HISTORY_LIMIT: Final = 3
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_IDLE_DISCONNECT: Final = "idle_disconnect"

DEFAULT_POLL_INTERVAL: Final = 120  # seconds
MIN_POLL_INTERVAL: Final = 15
DEFAULT_IDLE_DISCONNECT: Final = 8  # seconds to keep a connection open after the last command
MAX_IDLE_DISCONNECT: Final = 300

# GATT identifiers shared by nearly every Govee light.
SERVICE_UUID: Final = "00010203-0405-0607-0809-0a0b0c0d1910"
CONTROL_CHARACTERISTIC_UUID: Final = "00010203-0405-0607-0809-0a0b0c0d2b11"
STATUS_CHARACTERISTIC_UUID: Final = "00010203-0405-0607-0809-0a0b0c0d2b10"

MIN_KELVIN: Final = 2000
MAX_KELVIN: Final = 9000
