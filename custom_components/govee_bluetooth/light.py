"""Light entity for Govee BLE strips."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GoveeConfigEntry
from .const import DOMAIN, MAX_KELVIN, MIN_KELVIN
from .coordinator import GoveeCoordinator
from .device import GoveeConnectionError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: GoveeConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([GoveeBleLight(entry.runtime_data)])


class GoveeBleLight(CoordinatorEntity[GoveeCoordinator], LightEntity):
    """A Govee strip controlled locally over Bluetooth."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "strip"
    _attr_supported_color_modes = {ColorMode.RGB, ColorMode.COLOR_TEMP}
    _attr_min_color_temp_kelvin = MIN_KELVIN
    _attr_max_color_temp_kelvin = MAX_KELVIN

    def __init__(self, coordinator: GoveeCoordinator) -> None:
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_unique_id = device.address.replace(":", "").lower()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.address)},
            connections={(CONNECTION_BLUETOOTH, device.address)},
            manufacturer="Govee",
            model=device.profile.model,
            name=device.name,
            sw_version=device.state.firmware,
        )
        if device.profile.supports_scenes:
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = list(device.profile.scenes)

    @property
    def _state(self):
        return self.coordinator.device.state

    @property
    def available(self) -> bool:
        return self.coordinator.available

    @property
    def is_on(self) -> bool | None:
        return self._state.is_on

    @property
    def brightness(self) -> int | None:
        return self._state.brightness

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self._state.rgb_color

    @property
    def color_temp_kelvin(self) -> int | None:
        return self._state.color_temp_kelvin

    @property
    def color_mode(self) -> ColorMode:
        return ColorMode.COLOR_TEMP if self._state.white_mode else ColorMode.RGB

    @property
    def effect(self) -> str | None:
        return self._state.effect

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"bluetooth_address": self.coordinator.device.address}
        if self._state.firmware:
            attrs["firmware"] = self._state.firmware
        return attrs

    def _handle_coordinator_update(self) -> None:
        firmware = self._state.firmware
        if firmware and self.device_entry and self.device_entry.sw_version != firmware:
            dr.async_get(self.hass).async_update_device(self.device_entry.id, sw_version=firmware)
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness == 0:
            await self.async_turn_off()
            return
        try:
            await self.coordinator.device.async_turn_on(
                brightness=brightness,
                rgb_color=kwargs.get(ATTR_RGB_COLOR),
                color_temp_kelvin=kwargs.get(ATTR_COLOR_TEMP_KELVIN),
                effect=kwargs.get(ATTR_EFFECT),
            )
        except GoveeConnectionError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.device.async_turn_off()
        except GoveeConnectionError as err:
            raise HomeAssistantError(str(err)) from err
