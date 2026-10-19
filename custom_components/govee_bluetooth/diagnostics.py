"""Diagnostics support: what the light last told us and how reachable it is."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from . import GoveeConfigEntry
from .const import CONF_COLOR_SUBMODE, CONF_DETECTED_SUBMODE, CONF_MODEL, CONF_PROBE_HISTORY
from .models import table_profile_for


def _reachability(hass: HomeAssistant, address: str) -> str | None:
    """Home Assistant's own explanation of why an address is (un)reachable.

    The API arrived in 2026; older cores simply omit the field.
    """
    try:
        from homeassistant.components.bluetooth import BluetoothReachabilityIntent

        return bluetooth.async_address_reachability_diagnostics(
            hass, address, BluetoothReachabilityIntent.CONNECTION
        )
    except (ImportError, AttributeError):
        return None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GoveeConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    device = coordinator.device
    # The probe history is large (it carries raw frames); surface it once at
    # the top level rather than buried in the entry data.
    entry_data = {k: v for k, v in entry.data.items() if k != CONF_PROBE_HISTORY}
    return {
        "entry": {"data": entry_data, "options": dict(entry.options)},
        "profile": asdict(device.profile),
        "color_submode": {
            "static_table": table_profile_for(entry.data.get(CONF_MODEL)).color_submode,
            "detected": entry.data.get(CONF_DETECTED_SUBMODE),
            "manual_override": entry.options.get(CONF_COLOR_SUBMODE),
            "last_observed": device.state.extra.get("submode"),
            "in_use": device.profile.color_submode,
        },
        "state": asdict(device.state),
        "bluetooth": {
            "seen_by_connectable_adapter": coordinator.available,
            "connectable_scanners": bluetooth.async_scanner_count(hass, connectable=True),
            "reachability": _reachability(hass, device.address),
        },
        "last_update_success": coordinator.last_update_success,
        # Raw frames, oldest first: "tx" is what this integration sent, "rx"
        # what the light answered. Hex, with the 0x33/0xAA type byte first.
        "frames": device.frames,
        # Dialect probe runs from setup or Configure, newest last, with the
        # frames each one exchanged over its own short-lived connection.
        "probe_history": list(entry.data.get(CONF_PROBE_HISTORY, [])),
    }
