"""Govee Bluetooth: local control of Govee lights over BLE, proxies included."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_ADDRESS,
    CONF_BRIGHTNESS_SCALE,
    CONF_COLOR_SUBMODE,
    CONF_DETECTED_SUBMODE,
    CONF_FIRMWARE,
    CONF_IDLE_DISCONNECT,
    CONF_MODEL,
    CONF_NAME,
    CONF_POLL_INTERVAL,
    DEFAULT_IDLE_DISCONNECT,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import GoveeCoordinator
from .device import GoveeBleDevice
from .models import profile_for

_LOGGER = logging.getLogger(__name__)

type GoveeConfigEntry = ConfigEntry[GoveeCoordinator]


def profile_for_entry(entry: ConfigEntry):
    """Effective profile: manual override > detected on the light > static table."""
    return profile_for(
        entry.data.get(CONF_MODEL),
        brightness_scale=entry.options.get(CONF_BRIGHTNESS_SCALE) or None,
        detected_submode=entry.data.get(CONF_DETECTED_SUBMODE),
        color_submode=entry.options.get(CONF_COLOR_SUBMODE) or None,
    )


async def async_setup_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    """Set up one Govee light from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    profile = profile_for_entry(entry)

    @callback
    def _remember_submode(submode: int, firmware: str | None) -> None:
        """Persist what the light reported so the next setup starts from it."""
        data = {**entry.data, CONF_DETECTED_SUBMODE: submode}
        if firmware:
            data[CONF_FIRMWARE] = firmware
        if data != dict(entry.data):
            _LOGGER.info(
                "%s: storing detected colour sub-mode %#04x (firmware %s)",
                entry.title,
                submode,
                firmware or "unknown",
            )
            hass.config_entries.async_update_entry(entry, data=data)

    device = GoveeBleDevice(
        hass,
        address,
        entry.data.get(CONF_NAME) or entry.title,
        profile,
        idle_disconnect=entry.options.get(CONF_IDLE_DISCONNECT, DEFAULT_IDLE_DISCONNECT),
        on_submode_observed=_remember_submode,
        submode_locked=bool(entry.options.get(CONF_COLOR_SUBMODE)),
    )
    coordinator = GoveeCoordinator(
        hass, entry, device, entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
    )
    entry.runtime_data = coordinator

    # Read the real state once in the background. A BLE connect through a
    # proxy can take tens of seconds or fail outright (strip busy with the
    # Govee app, out of range), and that must never block or cancel setup.
    # The entity starts with unknown state and fills in when the read lands.
    coordinator.initial_refresh = entry.async_create_background_task(
        hass, coordinator.async_refresh(), f"{DOMAIN} first refresh {address}"
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: GoveeConfigEntry) -> None:
    """Reload on any change: options, or data learned by a probe / status read."""
    await hass.config_entries.async_reload(entry.entry_id)
