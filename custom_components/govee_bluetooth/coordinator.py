"""Polling coordinator and availability tracking for a Govee BLE light."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .device import GoveeBleDevice, GoveeConnectionError, GoveeState

_LOGGER = logging.getLogger(__name__)


class GoveeCoordinator(DataUpdateCoordinator[GoveeState]):
    """Poll the light periodically and relay pushed state changes."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device: GoveeBleDevice,
        poll_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {device.name}",
            update_interval=timedelta(seconds=poll_interval),
        )
        self.device = device
        self.initial_refresh: asyncio.Task[None] | None = None
        self.ble_seen = bluetooth.async_address_present(hass, device.address, connectable=True)
        self._unsubscribers = [
            device.async_add_listener(self._handle_device_state),
            bluetooth.async_register_callback(
                hass,
                self._handle_advertisement,
                {"address": device.address, "connectable": True},
                bluetooth.BluetoothScanningMode.PASSIVE,
            ),
            bluetooth.async_track_unavailable(
                hass, self._handle_unavailable, device.address, connectable=True
            ),
        ]

    @property
    def available(self) -> bool:
        """True when some adapter or proxy can currently reach the light."""
        return self.ble_seen

    @callback
    def _handle_device_state(self, state: GoveeState) -> None:
        self.async_set_updated_data(state)

    @callback
    def _handle_advertisement(
        self, service_info: bluetooth.BluetoothServiceInfoBleak, change: bluetooth.BluetoothChange
    ) -> None:
        if not self.ble_seen:
            _LOGGER.debug("%s is back in Bluetooth range", self.device.name)
            self.ble_seen = True
            self.async_update_listeners()

    @callback
    def _handle_unavailable(self, service_info: bluetooth.BluetoothServiceInfoBleak) -> None:
        _LOGGER.debug("%s is no longer seen by any connectable adapter", self.device.name)
        self.ble_seen = False
        self.async_update_listeners()

    async def _async_update_data(self) -> GoveeState:
        try:
            return await self.device.async_update()
        except GoveeConnectionError as err:
            raise UpdateFailed(str(err)) from err

    async def async_shutdown(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        await self.device.async_disconnect()
        await super().async_shutdown()
