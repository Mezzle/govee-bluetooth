"""End-to-end light entity tests against a fake H6159."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from bleak.exc import BleakError
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.govee_bluetooth import protocol
from custom_components.govee_bluetooth.const import (
    CONF_ADDRESS,
    CONF_IDLE_DISCONNECT,
    CONF_MODEL,
    CONF_NAME,
    CONF_POLL_INTERVAL,
    DOMAIN,
)

from .conftest import ADDRESS, LOCAL_NAME, FakeGoveeClient, inject_advertisement, make_service_info

ENTITY_ID = "light.ihoment_h6159_02db"


async def setup_entry(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    inject_advertisement(hass, make_service_info())
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title=LOCAL_NAME,
        data={CONF_ADDRESS: ADDRESS, CONF_NAME: LOCAL_NAME, CONF_MODEL: "H6159"},
        options=options or {CONF_IDLE_DISCONNECT: 60},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await entry.runtime_data.initial_refresh
    await hass.async_block_till_done()
    return entry


async def test_initial_poll_populates_state(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    await setup_entry(hass)
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 0x80
    assert state.attributes[ATTR_RGB_COLOR] == (16, 32, 48)
    assert state.attributes[ATTR_COLOR_MODE] == "rgb"
    assert state.attributes["firmware"] == "1.00.17"
    assert state.attributes["effect_list"] == [
        "Sunrise",
        "Sunset",
        "Movie",
        "Dating",
        "Romantic",
        "Blinking",
        "Candlelight",
        "Snowflake",
    ]
    # Poll was: power, brightness, mode, version — all reads, no writes.
    assert [w[:2] for w in fake_client.writes] == [
        b"\xaa\x01",
        b"\xaa\x04",
        b"\xaa\x05",
        b"\xaa\x06",
    ]
    assert fake_client.writes[2][:3] == b"\xaa\x05\x01"


async def test_turn_on_with_colour_and_brightness(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    await setup_entry(hass)
    fake_client.writes.clear()
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_RGB_COLOR: [255, 0, 0], ATTR_BRIGHTNESS: 200},
        blocking=True,
    )
    # Power, colour then brightness in one connection.
    assert fake_client.commands == [
        protocol.power_frame(True),
        bytes.fromhex("330502ff000000000000000000000000000000cb"),
        protocol.brightness_frame(200, 255),
    ]
    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_RGB_COLOR] == (255, 0, 0)
    assert state.attributes[ATTR_BRIGHTNESS] == 200


async def test_turn_off_then_on_sends_power_frames(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    await setup_entry(hass)
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    assert hass.states.get(ENTITY_ID).state == STATE_OFF
    assert fake_client.commands[-1] == protocol.power_frame(False)

    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    assert hass.states.get(ENTITY_ID).state == STATE_ON
    assert fake_client.commands[-1] == protocol.power_frame(True)


async def test_colour_temperature_uses_white_flag(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    await setup_entry(hass)
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_COLOR_TEMP_KELVIN: 2700},
        blocking=True,
    )
    frame = fake_client.commands[-1]
    assert frame[:7] == bytes([0x33, 0x05, 0x02, 0xFF, 0xFF, 0xFF, 0x01])
    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_COLOR_MODE] == "color_temp"
    assert state.attributes[ATTR_COLOR_TEMP_KELVIN] == 2700


async def test_effect_sends_scene(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    await setup_entry(hass)
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_EFFECT: "Snowflake"},
        blocking=True,
    )
    assert fake_client.commands[-1].hex() == "3305040f0000000000000000000000000000003d"
    assert hass.states.get(ENTITY_ID).attributes[ATTR_EFFECT] == "Snowflake"


async def test_heartbeat_reply_updates_power_state(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    await setup_entry(hass)
    assert hass.states.get(ENTITY_ID).state == STATE_ON
    # The strip was switched off at the wall remote; the next heartbeat echo says so.
    fake_client._notify(0, bytearray(protocol.build_frame(0x01, [0x00], frame_type=0xAA)))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_OFF


async def test_idle_disconnect_releases_connection(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    await setup_entry(hass, options={CONF_IDLE_DISCONNECT: 0, CONF_POLL_INTERVAL: 3600})
    # idle_disconnect=0 schedules the disconnect on the next loop turn.
    for _ in range(5):
        await asyncio.sleep(0)
    await hass.async_block_till_done()
    assert fake_client.disconnects == 1
    assert fake_client.is_connected is False


async def test_connection_failure_raises_and_keeps_entity_available(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    await setup_entry(hass)
    with patch(
        "custom_components.govee_bluetooth.device.establish_connection",
        side_effect=BleakError("busy"),
    ):
        await fake_client.disconnect()
        with pytest.raises(HomeAssistantError, match="Could not connect"):
            await hass.services.async_call(
                LIGHT_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
            )
    # Still advertised, so the entity stays available for the next attempt.
    assert hass.states.get(ENTITY_ID).state == STATE_ON


async def test_setup_succeeds_when_first_poll_fails(hass: HomeAssistant, bluetooth_ready) -> None:
    """The strip being busy with the Govee app must not block HA startup."""
    with patch(
        "custom_components.govee_bluetooth.device.establish_connection",
        side_effect=BleakError("busy"),
    ):
        await setup_entry(hass)
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "unknown"


async def test_entity_unavailable_when_not_advertising(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    entry = await setup_entry(hass)
    assert hass.states.get(ENTITY_ID).state == STATE_ON
    # Bluetooth marks a device unavailable once it has not been seen for a while.
    future = dt_util.utcnow() + timedelta(minutes=16)
    with patch(
        "habluetooth.manager.monotonic_time_coarse",
        return_value=asyncio.get_running_loop().time() + 16 * 60,
    ):
        async_fire_time_changed(hass, future)
        await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE

    # Home Assistant reloads the entry when a previously unavailable device
    # is rediscovered, so wait for the reloaded entry's first status read.
    inject_advertisement(hass, make_service_info())
    await hass.async_block_till_done()
    await entry.runtime_data.initial_refresh
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_ON


async def test_diagnostics_and_device_registry(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient, device_registry
) -> None:
    from custom_components.govee_bluetooth.diagnostics import async_get_config_entry_diagnostics

    entry = await setup_entry(hass)
    # Look the device up via the entity so this works on cores before and
    # after the device registry lookup API changed in 2026.
    entity_entry = er.async_get(hass).async_get(ENTITY_ID)
    assert entity_entry is not None and entity_entry.device_id
    device = device_registry.async_get(entity_entry.device_id)
    assert device is not None
    assert device.manufacturer == "Govee"
    assert device.model == "H6159"
    assert device.sw_version == "1.00.17"

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["state"]["is_on"] is True
    assert diag["profile"]["brightness_scale"] == 255
    assert diag["bluetooth"]["seen_by_connectable_adapter"] is True


async def test_setup_does_not_wait_for_slow_bluetooth(hass: HomeAssistant, bluetooth_ready) -> None:
    """A connect that hangs must not delay or cancel config entry setup."""

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(3600)

    with patch("custom_components.govee_bluetooth.device.establish_connection", side_effect=_hang):
        inject_advertisement(hass, make_service_info())
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=ADDRESS,
            title=LOCAL_NAME,
            data={CONF_ADDRESS: ADDRESS, CONF_NAME: LOCAL_NAME, CONF_MODEL: "H6159"},
        )
        entry.add_to_hass(hass)
        assert await asyncio.wait_for(hass.config_entries.async_setup(entry.entry_id), 5)
        await hass.async_block_till_done()
        assert hass.states.get(ENTITY_ID) is not None
        assert hass.states.get(ENTITY_ID).state == "unknown"
        # Unload cancels the hanging background read.
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_entity_icon_and_brand_assets(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    from pathlib import Path

    from homeassistant.helpers.icon import async_get_icons

    await setup_entry(hass)
    icons = await async_get_icons(hass, "entity", {DOMAIN})
    assert icons[DOMAIN]["light"]["strip"]["default"] == "mdi:led-strip-variant"
    brand = Path(__file__).parents[1] / "custom_components/govee_bluetooth/brand"
    assert (brand / "icon.png").is_file() and (brand / "icon@2x.png").is_file()
