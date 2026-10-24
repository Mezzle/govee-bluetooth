"""Cached protocol identity and host diagnostics; no sensor makes BLE requests."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities(
        [
            GoveeDiagnostic(entry.runtime_data, key, name)
            for key, name in (
                ("profile", "Readback profile"),
                ("last_observation", "Last state observation"),
                ("last_acceptance", "Last command acceptance"),
                ("completeness", "Colour page completeness"),
                ("optional_failures", "Host optional read failures"),
                ("command_rejections", "Host command rejections"),
                ("ic_count", "Reported IC count"),
                ("section_count", "Reported section count"),
                ("segment_count", "Reported segment count"),
                ("bulb_count", "Reported bulb count"),
            )
        ]
    )


class GoveeDiagnostic(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self.key = key
        if key in ("last_observation", "last_acceptance"):
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        device = coordinator.device
        self._attr_name = name
        self._attr_unique_id = f"{device.address.replace(':', '').lower()}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device.address)})

    @property
    def native_value(self):
        device = self.coordinator.device
        if self.key in ("last_observation", "last_acceptance"):
            value = (
                device.last_observation
                if self.key == "last_observation"
                else device.last_command_acceptance
            )
            return datetime.fromisoformat(value) if value else None
        if self.key == "completeness":
            complete = device.state.extra.get("pages_complete")
            return None if complete is None else "complete" if complete else "partial"
        if self.key == "profile":
            return device.read_profile.key
        if self.key == "optional_failures":
            return device.optional_read_failures
        if self.key == "command_rejections":
            return device.command_rejections
        decoded = device.optional_reads.get("topology", {}).get("decoded") or {}
        if self.key in decoded:
            return decoded[self.key]
        pairs = decoded.get("pairs", [])
        # Multiple strip pairs are retained separately; aggregation would imply
        # topology semantics that have not been established.
        return pairs[0].get(self.key) if len(pairs) == 1 else None

    @property
    def extra_state_attributes(self):
        device = self.coordinator.device
        return {
            "last_state_observation": device.last_observation,
            "field_provenance": device.state.provenance,
            "topology_observation": device.optional_reads.get("topology"),
        }
