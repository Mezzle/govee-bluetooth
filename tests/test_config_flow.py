"""Config flow tests."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee_bluetooth.const import (
    CONF_ADDRESS,
    CONF_BRIGHTNESS_SCALE,
    CONF_IDLE_DISCONNECT,
    CONF_MODEL,
    CONF_NAME,
    CONF_POLL_INTERVAL,
    DOMAIN,
)

from .conftest import ADDRESS, LOCAL_NAME, inject_advertisement, make_service_info


async def test_bluetooth_discovery_creates_entry(
    hass: HomeAssistant, bluetooth_ready: None
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=make_service_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"]["model"] == "H6159"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_NAME: "Kitchen strip"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kitchen strip"
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_NAME: "Kitchen strip",
        CONF_MODEL: "H6159",
    }
    assert result["result"].unique_id == ADDRESS


async def test_bluetooth_discovery_aborts_when_already_configured(
    hass: HomeAssistant, bluetooth_ready: None
) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS, data={CONF_ADDRESS: ADDRESS}).add_to_hass(
        hass
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=make_service_info()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_bluetooth_discovery_aborts_when_not_connectable(
    hass: HomeAssistant, bluetooth_ready: None
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=make_service_info(connectable=False),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_connectable"


async def test_manifest_matcher_starts_discovery_flow(
    hass: HomeAssistant, bluetooth_ready: None
) -> None:
    """An ``ihoment_H6159_*`` advertisement should trigger our flow by itself."""
    inject_advertisement(hass, make_service_info())
    await hass.async_block_till_done(wait_background_tasks=True)
    flows = [f for f in hass.config_entries.flow.async_progress() if f["handler"] == DOMAIN]
    assert len(flows) == 1
    assert flows[0]["context"]["title_placeholders"] == {"name": LOCAL_NAME}


async def test_user_flow_lists_discovered_devices(
    hass: HomeAssistant, bluetooth_ready: None
) -> None:
    inject_advertisement(hass, make_service_info())
    inject_advertisement(hass, make_service_info("AA:BB:CC:DD:EE:FF", "SomeOtherGadget"))
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    options = result["data_schema"].schema[CONF_ADDRESS].config["options"]
    assert [o["value"] for o in options] == [ADDRESS]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_ADDRESS: ADDRESS, CONF_MODEL: "h6159"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == LOCAL_NAME
    assert result["data"][CONF_MODEL] == "H6159"


async def test_user_flow_aborts_without_devices(hass: HomeAssistant, bluetooth_ready: None) -> None:
    with patch(
        "custom_components.govee_bluetooth.config_flow.bluetooth.async_scanner_count",
        return_value=1,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_flow_aborts_without_scanners(
    hass: HomeAssistant, bluetooth_ready: None
) -> None:
    """The mocked environment has no connectable scanner registered."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_scanners"


async def test_options_flow(hass: HomeAssistant, bluetooth_ready: None, fake_client) -> None:
    inject_advertisement(hass, make_service_info())
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title=LOCAL_NAME,
        data={CONF_ADDRESS: ADDRESS, CONF_NAME: LOCAL_NAME, CONF_MODEL: "H6159"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_POLL_INTERVAL: 30, CONF_IDLE_DISCONNECT: 0, CONF_BRIGHTNESS_SCALE: "100"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {
        CONF_POLL_INTERVAL: 30,
        CONF_IDLE_DISCONNECT: 0,
        CONF_BRIGHTNESS_SCALE: 100,
    }
    # Reload picked the new scale up.
    assert entry.runtime_data.device.profile.brightness_scale == 100
