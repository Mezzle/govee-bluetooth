"""Config and options flow for Govee Bluetooth."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_ADDRESS,
    CONF_BRIGHTNESS_SCALE,
    CONF_IDLE_DISCONNECT,
    CONF_MODEL,
    CONF_NAME,
    CONF_POLL_INTERVAL,
    DEFAULT_IDLE_DISCONNECT,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_IDLE_DISCONNECT,
    MIN_POLL_INTERVAL,
)
from .models import DEFAULT_MODEL, is_govee_name, model_from_name, profile_for


def _default_title(name: str | None, model: str, address: str) -> str:
    if name and is_govee_name(name):
        return name
    return f"Govee {model} {address.replace(':', '')[-4:]}"


class GoveeBluetoothConfigFlow(ConfigFlow, domain=DOMAIN):
    """Discover Govee lights via Home Assistant's Bluetooth integration."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: bluetooth.BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, bluetooth.BluetoothServiceInfoBleak] = {}

    # -- automatic discovery ------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if not discovery_info.connectable:
            return self.async_abort(reason="not_connectable")
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovery is not None
        model = model_from_name(self._discovery.name) or DEFAULT_MODEL
        if user_input is not None:
            return self._create_entry(self._discovery.address, user_input[CONF_NAME], model)
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovery.name,
                "address": self._discovery.address,
                "model": model,
            },
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME,
                        default=_default_title(
                            self._discovery.name, model, self._discovery.address
                        ),
                    ): str
                }
            ),
        )

    # -- manual add from the integrations page ------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            info = self._discovered[address]
            model = (
                user_input.get(CONF_MODEL) or model_from_name(info.name) or DEFAULT_MODEL
            ).upper()
            return self._create_entry(
                address,
                user_input.get(CONF_NAME) or _default_title(info.name, model, address),
                model,
            )

        current = self._async_current_ids(include_ignore=False)
        for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
            if info.address in current or not is_govee_name(info.name):
                continue
            self._discovered[info.address] = info

        if not self._discovered:
            if bluetooth.async_scanner_count(self.hass, connectable=True) == 0:
                return self.async_abort(reason="no_scanners")
            return self.async_abort(reason="no_devices_found")

        options = [
            SelectOptionDict(value=address, label=f"{info.name} ({address})")
            for address, info in sorted(self._discovered.items(), key=lambda kv: kv[1].name)
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                    ),
                    vol.Optional(CONF_NAME): str,
                    vol.Optional(CONF_MODEL): str,
                }
            ),
        )

    def _create_entry(self, address: str, name: str, model: str) -> ConfigFlowResult:
        return self.async_create_entry(
            title=name,
            data={CONF_ADDRESS: address, CONF_NAME: name, CONF_MODEL: model},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GoveeOptionsFlow:
        return GoveeOptionsFlow()


class GoveeOptionsFlow(OptionsFlow):
    """Tune polling, connection lingering and the brightness scale."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL]),
                    CONF_IDLE_DISCONNECT: int(user_input[CONF_IDLE_DISCONNECT]),
                    CONF_BRIGHTNESS_SCALE: int(user_input[CONF_BRIGHTNESS_SCALE]),
                }
            )

        options = self.config_entry.options
        default_scale = profile_for(self.config_entry.data.get(CONF_MODEL)).brightness_scale
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_POLL_INTERVAL,
                            max=3600,
                            step=1,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_IDLE_DISCONNECT,
                        default=options.get(CONF_IDLE_DISCONNECT, DEFAULT_IDLE_DISCONNECT),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=MAX_IDLE_DISCONNECT,
                            step=1,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_BRIGHTNESS_SCALE,
                        default=str(options.get(CONF_BRIGHTNESS_SCALE, default_scale)),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value="255", label="0-255 (H6159 and other older strips)"
                                ),
                                SelectOptionDict(value="100", label="0-100 (newer firmware)"),
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )
