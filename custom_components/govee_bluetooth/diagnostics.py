"""Diagnostics support: what the light last told us and how reachable it is."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothReachabilityIntent
from homeassistant.core import HomeAssistant

from . import GoveeConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GoveeConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    device = coordinator.device
    return {
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        "profile": asdict(device.profile),
        "state": asdict(device.state),
        "bluetooth": {
            "seen_by_connectable_adapter": coordinator.available,
            "connectable_scanners": bluetooth.async_scanner_count(hass, connectable=True),
            "reachability": bluetooth.async_address_reachability_diagnostics(
                hass, device.address, BluetoothReachabilityIntent.CONNECTION
            ),
        },
        "last_update_success": coordinator.last_update_success,
    }
