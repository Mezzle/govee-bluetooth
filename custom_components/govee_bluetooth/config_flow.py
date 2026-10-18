"""Config and options flow for Govee Bluetooth.

Known models go straight from the confirm screen to a config entry. Unknown
models get the two-step dialect probe from :mod:`probe`: a read-only step that
runs after one more confirmation, and an opt-in active step behind a consent
screen that spells out the risk. The options flow can re-run either step and
carries manual overrides for the colour sub-mode and brightness scale.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from . import probe
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
    MAX_IDLE_DISCONNECT,
    MIN_POLL_INTERVAL,
)
from .device import GoveeBleDevice, GoveeConnectionError
from .models import (
    DEFAULT_MODEL,
    KNOWN_MODELS,
    is_govee_name,
    model_from_name,
    profile_for,
    table_profile_for,
)

_LOGGER = logging.getLogger(__name__)

CHOICE_RETRY = "retry"
CHOICE_ACTIVE = "active"
CHOICE_SKIP = "skip"
AUTO = "auto"


def _default_title(name: str | None, model: str, address: str) -> str:
    if name and is_govee_name(name):
        return name
    return f"Govee {model} {address.replace(':', '')[-4:]}"


def _submode_options() -> list[SelectOptionDict]:
    return [
        SelectOptionDict(value=AUTO, label="Automatic (detected on the light, else model table)"),
        SelectOptionDict(value="2", label="0x02 (H6159 and other older strips)"),
        SelectOptionDict(value="13", label="0x0D (H6181 and RGBWW firmware)"),
        SelectOptionDict(value="21", label="0x15 (RGBIC strips, segmented)"),
    ]


def _scale_options() -> list[SelectOptionDict]:
    return [
        SelectOptionDict(value=AUTO, label="Automatic (from the colour dialect)"),
        SelectOptionDict(value="255", label="0-255 (H6159 and other older strips)"),
        SelectOptionDict(value="100", label="0-100 (newer firmware)"),
    ]


class _ProbeRunner:
    """Shared probe plumbing for the config and options flows.

    Each probe opens its own short-lived connection through a throwaway
    :class:`GoveeBleDevice`, so it never competes with a running entry's lock.

    A progress task that finishes eagerly (a connect that fails before its
    first await) makes Home Assistant call the step after the progress step
    with the *previous* step's user input, so the form steps that follow a
    probe only act on input that carries their own field.
    """

    hass: HomeAssistant

    def __init__(self) -> None:
        self._task: asyncio.Task[Any] | None = None
        self.safe_result: probe.SafeProbeResult | None = None
        self.active_result: probe.ActiveProbeResult | None = None
        self.probe_error: str | None = None

    def _device(self, address: str, name: str, model: str) -> GoveeBleDevice:
        return GoveeBleDevice(self.hass, address, name, profile_for(model), idle_disconnect=0)

    async def _run_safe(self, address: str, name: str, model: str) -> None:
        self.safe_result = None
        self.probe_error = None
        device = self._device(address, name, model)
        try:
            self.safe_result = await device.async_safe_probe()
        except GoveeConnectionError as err:
            self.probe_error = str(err)
        finally:
            await device.async_disconnect()

    async def _run_active(self, address: str, name: str, model: str) -> None:
        self.active_result = None
        self.probe_error = None
        device = self._device(address, name, model)
        prior = table_profile_for(model).color_submode
        try:
            self.active_result = await device.async_active_probe(probe.candidate_order(prior))
        except GoveeConnectionError as err:
            self.probe_error = str(err)
        finally:
            await device.async_disconnect()

    def _start(self, coro: Any) -> asyncio.Task[Any]:
        if self._task is None:
            self._task = self.hass.async_create_task(coro)
        else:
            coro.close()
        return self._task

    def _finish(self) -> None:
        self._task = None

    def _safe_placeholders(self, model: str) -> dict[str, str]:
        result = self.safe_result
        if result is None:
            return {
                "model": model,
                "firmware": "unknown",
                "mode": "no reply",
                "detail": self.probe_error or "the light did not answer",
            }
        detail = {
            "scene": "The light is running a built-in scene, so its colour dialect is not visible.",
            "music": "The light is in music mode, so its colour dialect is not visible.",
            "unknown": "The light answered with a mode this integration does not recognise.",
            "no_reply": "The light answered other queries but not the mode query.",
            "color": "",
        }[result.outcome]
        return {
            "model": model,
            "firmware": result.firmware or "unknown",
            "mode": probe.submode_name(result.observed_submode),
            "detail": detail,
        }

    def _active_placeholders(self, model: str) -> dict[str, str]:
        result = self.active_result
        if result is None:
            return {
                "model": model,
                "attempted": "none",
                "detail": self.probe_error or "the light did not answer",
            }
        attempted = ", ".join(f"{s:#04x}" for s in result.attempted) or "none"
        if result.error:
            detail = f"Stopped early: {result.error}. No write was retried."
        else:
            detail = "None of the candidate dialects echoed the test colour back."
        if result.restore_note:
            detail += f" Restore: {result.restore_note}."
        return {"model": model, "attempted": attempted, "detail": detail}


class GoveeBluetoothConfigFlow(ConfigFlow, _ProbeRunner, domain=DOMAIN):
    """Discover Govee lights via Home Assistant's Bluetooth integration."""

    VERSION = 1

    def __init__(self) -> None:
        _ProbeRunner.__init__(self)
        self._discovery: bluetooth.BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, bluetooth.BluetoothServiceInfoBleak] = {}
        self._address: str | None = None
        self._name: str | None = None
        self._model: str = DEFAULT_MODEL
        self._detected: int | None = None
        self._firmware: str | None = None

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
            return await self._async_after_identify(
                self._discovery.address, user_input[CONF_NAME], model
            )
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
            return await self._async_after_identify(
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

    # -- unknown-model probe -------------------------------------------------

    async def _async_after_identify(self, address: str, name: str, model: str) -> ConfigFlowResult:
        self._address, self._name, self._model = address, name, model
        if model in KNOWN_MODELS:
            return self._create_entry()
        return await self.async_step_unknown_model()

    async def async_step_unknown_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Tell the user the model is not in the table and that a read-only check follows."""
        if user_input is not None:
            return await self.async_step_probe()
        return self.async_show_form(
            step_id="unknown_model",
            description_placeholders={"model": self._model, "name": self._name or ""},
            data_schema=vol.Schema({}),
        )

    async def async_step_probe(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: the read-only probe, shown as a progress screen."""
        assert self._address is not None and self._name is not None
        task = self._start(self._run_safe(self._address, self._name, self._model))
        if not task.done():
            return self.async_show_progress(
                step_id="probe",
                progress_action="probe",
                progress_task=task,
                description_placeholders={"name": self._name},
            )
        self._finish()
        result = self.safe_result
        if result is not None and result.detected_submode is not None:
            self._detected = result.detected_submode
            self._firmware = result.firmware
            return self.async_show_progress_done(next_step_id="finish")
        return self.async_show_progress_done(next_step_id="probe_fallback")

    async def async_step_probe_fallback(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1 could not see a colour sub-mode: retry, go active, or accept the default."""
        if user_input is not None and "choice" in user_input:
            choice = user_input["choice"]
            if choice == CHOICE_RETRY:
                return await self.async_step_probe()
            if choice == CHOICE_ACTIVE:
                return await self.async_step_active_consent()
            return self._create_entry()
        if self.safe_result is not None:
            self._firmware = self.safe_result.firmware or self._firmware
        return self.async_show_form(
            step_id="probe_fallback",
            description_placeholders=self._safe_placeholders(self._model),
            data_schema=vol.Schema(
                {
                    vol.Required("choice", default=CHOICE_RETRY): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=CHOICE_RETRY,
                                    label="Set the light to a plain colour in the Govee app, then retry",
                                ),
                                SelectOptionDict(
                                    value=CHOICE_ACTIVE,
                                    label="Run the active probe (writes to the light)",
                                ),
                                SelectOptionDict(
                                    value=CHOICE_SKIP,
                                    label="Skip and use the H6159 dialect (can be changed in options)",
                                ),
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_active_consent(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2 consent screen. The description carries the risk wording."""
        errors: dict[str, str] = {}
        if user_input is not None and "consent" in user_input:
            if user_input["consent"]:
                return await self.async_step_active_probe()
            errors["consent"] = "consent_required"
        return self.async_show_form(
            step_id="active_consent",
            description_placeholders={"name": self._name or "", "model": self._model},
            data_schema=vol.Schema({vol.Required("consent", default=False): bool}),
            errors=errors,
        )

    async def async_step_active_probe(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: the write-and-echo probe, shown as a progress screen."""
        assert self._address is not None and self._name is not None
        task = self._start(self._run_active(self._address, self._name, self._model))
        if not task.done():
            return self.async_show_progress(
                step_id="active_probe",
                progress_action="active_probe",
                progress_task=task,
                description_placeholders={"name": self._name},
            )
        self._finish()
        result = self.active_result
        if result is not None:
            self._firmware = result.recorded.firmware or self._firmware
            if result.detected_submode is not None:
                self._detected = result.detected_submode
                return self.async_show_progress_done(next_step_id="finish")
        return self.async_show_progress_done(next_step_id="active_failed")

    async def async_step_active_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None and "choice" in user_input:
            if user_input["choice"] == CHOICE_RETRY:
                return await self.async_step_active_consent()
            return self._create_entry()
        return self.async_show_form(
            step_id="active_failed",
            description_placeholders=self._active_placeholders(self._model),
            data_schema=vol.Schema(
                {
                    vol.Required("choice", default=CHOICE_SKIP): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=CHOICE_SKIP,
                                    label="Finish with the H6159 dialect (can be changed in options)",
                                ),
                                SelectOptionDict(
                                    value=CHOICE_RETRY, label="Try the active probe again"
                                ),
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self._create_entry()

    def _create_entry(self) -> ConfigFlowResult:
        assert self._address is not None and self._name is not None
        data: dict[str, Any] = {
            CONF_ADDRESS: self._address,
            CONF_NAME: self._name,
            CONF_MODEL: self._model,
        }
        if self._detected is not None:
            data[CONF_DETECTED_SUBMODE] = self._detected
        if self._firmware:
            data[CONF_FIRMWARE] = self._firmware
        return self.async_create_entry(title=self._name, data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GoveeOptionsFlow:
        return GoveeOptionsFlow()


class GoveeOptionsFlow(OptionsFlow, _ProbeRunner):
    """Tune polling and connection lingering, override the dialect, or re-run a probe."""

    def __init__(self) -> None:
        _ProbeRunner.__init__(self)

    @property
    def _model(self) -> str:
        return (self.config_entry.data.get(CONF_MODEL) or DEFAULT_MODEL).upper()

    @property
    def _address(self) -> str:
        return self.config_entry.data[CONF_ADDRESS]

    @property
    def _name(self) -> str:
        return self.config_entry.data.get(CONF_NAME) or self.config_entry.title

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "probe", "active_consent"]
        )

    # -- settings ------------------------------------------------------------

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            new_options: dict[str, Any] = {
                CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL]),
                CONF_IDLE_DISCONNECT: int(user_input[CONF_IDLE_DISCONNECT]),
            }
            if user_input[CONF_BRIGHTNESS_SCALE] != AUTO:
                new_options[CONF_BRIGHTNESS_SCALE] = int(user_input[CONF_BRIGHTNESS_SCALE])
            if user_input[CONF_COLOR_SUBMODE] != AUTO:
                new_options[CONF_COLOR_SUBMODE] = int(user_input[CONF_COLOR_SUBMODE])
            return self.async_create_entry(data=new_options)

        options = self.config_entry.options
        data = self.config_entry.data
        effective = profile_for(self._model, detected_submode=data.get(CONF_DETECTED_SUBMODE))
        detected = data.get(CONF_DETECTED_SUBMODE)
        return self.async_show_form(
            step_id="settings",
            description_placeholders={
                "table_submode": f"{table_profile_for(self._model).color_submode:#04x}",
                "detected_submode": f"{detected:#04x}" if detected else "not detected yet",
                "effective_scale": str(effective.brightness_scale),
            },
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
                        CONF_COLOR_SUBMODE,
                        default=str(options[CONF_COLOR_SUBMODE])
                        if options.get(CONF_COLOR_SUBMODE)
                        else AUTO,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=_submode_options(), mode=SelectSelectorMode.DROPDOWN
                        )
                    ),
                    vol.Required(
                        CONF_BRIGHTNESS_SCALE,
                        default=str(options[CONF_BRIGHTNESS_SCALE])
                        if options.get(CONF_BRIGHTNESS_SCALE)
                        else AUTO,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=_scale_options(), mode=SelectSelectorMode.DROPDOWN
                        )
                    ),
                }
            ),
        )

    # -- re-run the probes ---------------------------------------------------

    async def async_step_probe(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        task = self._start(self._run_safe(self._address, self._name, self._model))
        if not task.done():
            return self.async_show_progress(
                step_id="probe",
                progress_action="probe",
                progress_task=task,
                description_placeholders={"name": self._name},
            )
        self._finish()
        result = self.safe_result
        if result is not None and result.detected_submode is not None:
            self._store(result.detected_submode, result.firmware)
            return self.async_show_progress_done(next_step_id="finish")
        return self.async_show_progress_done(next_step_id="probe_fallback")

    async def async_step_probe_fallback(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None and "choice" in user_input:
            choice = user_input["choice"]
            if choice == CHOICE_RETRY:
                return await self.async_step_probe()
            if choice == CHOICE_ACTIVE:
                return await self.async_step_active_consent()
            return self.async_create_entry(data=dict(self.config_entry.options))
        return self.async_show_form(
            step_id="probe_fallback",
            description_placeholders=self._safe_placeholders(self._model),
            data_schema=vol.Schema(
                {
                    vol.Required("choice", default=CHOICE_RETRY): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=CHOICE_RETRY,
                                    label="Set the light to a plain colour in the Govee app, then retry",
                                ),
                                SelectOptionDict(
                                    value=CHOICE_ACTIVE,
                                    label="Run the active probe (writes to the light)",
                                ),
                                SelectOptionDict(
                                    value=CHOICE_SKIP, label="Leave things as they are"
                                ),
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_active_consent(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and "consent" in user_input:
            if user_input["consent"]:
                return await self.async_step_active_probe()
            errors["consent"] = "consent_required"
        return self.async_show_form(
            step_id="active_consent",
            description_placeholders={"name": self._name, "model": self._model},
            data_schema=vol.Schema({vol.Required("consent", default=False): bool}),
            errors=errors,
        )

    async def async_step_active_probe(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        task = self._start(self._run_active(self._address, self._name, self._model))
        if not task.done():
            return self.async_show_progress(
                step_id="active_probe",
                progress_action="active_probe",
                progress_task=task,
                description_placeholders={"name": self._name},
            )
        self._finish()
        result = self.active_result
        if result is not None and result.detected_submode is not None:
            self._store(result.detected_submode, result.recorded.firmware)
            return self.async_show_progress_done(next_step_id="finish")
        return self.async_show_progress_done(next_step_id="active_failed")

    async def async_step_active_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None and "choice" in user_input:
            if user_input["choice"] == CHOICE_RETRY:
                return await self.async_step_active_consent()
            return self.async_create_entry(data=dict(self.config_entry.options))
        return self.async_show_form(
            step_id="active_failed",
            description_placeholders=self._active_placeholders(self._model),
            data_schema=vol.Schema(
                {
                    vol.Required("choice", default=CHOICE_SKIP): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=CHOICE_SKIP, label="Leave things as they are"
                                ),
                                SelectOptionDict(
                                    value=CHOICE_RETRY, label="Try the active probe again"
                                ),
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # Options unchanged: the data update above already triggered a reload.
        return self.async_create_entry(data=dict(self.config_entry.options))

    def _store(self, submode: int, firmware: str | None) -> None:
        data = {**self.config_entry.data, CONF_DETECTED_SUBMODE: submode}
        if firmware:
            data[CONF_FIRMWARE] = firmware
        _LOGGER.info("%s: probe detected colour sub-mode %#04x", self._name, submode)
        self.hass.config_entries.async_update_entry(self.config_entry, data=data)
