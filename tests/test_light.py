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
    CONF_BRIGHTNESS_SCALE,
    CONF_COLOR_SUBMODE,
    CONF_DETECTED_SUBMODE,
    CONF_FIRMWARE,
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
        b"\xaa\x07",  # bounded optional hardware version read
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
    # The raw conversation so far: the initial poll plus one unanswered optional identity query.
    assert [f["note"] for f in diag["frames"] if f["direction"] == "tx"] == ["query"] * 5
    assert [f["note"] for f in diag["frames"] if f["direction"] == "rx"] == ["reply"] * 4
    assert protocol.mode_query_frame().hex() in [f["frame"] for f in diag["frames"]]
    assert all(f["at"].endswith("+00:00") for f in diag["frames"])
    assert diag["probe_history"] == []
    assert "probe_history" not in diag["entry"]["data"]


async def test_diagnostics_frames_cover_commands_and_are_bounded(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    from custom_components.govee_bluetooth.device import FRAME_LOG_SIZE
    from custom_components.govee_bluetooth.diagnostics import async_get_config_entry_diagnostics

    entry = await setup_entry(hass)
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_RGB_COLOR: (1, 2, 3)},
        blocking=True,
    )
    diag = await async_get_config_entry_diagnostics(hass, entry)
    commands = [f["frame"] for f in diag["frames"] if f["note"] == "command"]
    assert commands == [c.hex() for c in fake_client.commands]
    assert protocol.rgb_frame(1, 2, 3).hex() in commands

    device = entry.runtime_data.device
    for _ in range(FRAME_LOG_SIZE + 10):
        device._record_frame("tx", protocol.heartbeat_frame(), "heartbeat")
    assert len(device.frames) == FRAME_LOG_SIZE
    assert all(f["note"] == "heartbeat" for f in device.frames)


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


RGBIC_ADDRESS = "A4:C1:38:F5:03:EE"
RGBIC_LOCAL_NAME = "GVH619A_03EE"
RGBIC_ENTITY_ID = "light.gvh619a_03ee"


async def setup_rgbic_entry(hass: HomeAssistant) -> MockConfigEntry:
    inject_advertisement(hass, make_service_info(address=RGBIC_ADDRESS, name=RGBIC_LOCAL_NAME))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=RGBIC_ADDRESS,
        title=RGBIC_LOCAL_NAME,
        data={CONF_ADDRESS: RGBIC_ADDRESS, CONF_NAME: RGBIC_LOCAL_NAME, CONF_MODEL: "H619A"},
        options={CONF_IDLE_DISCONNECT: 60},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await entry.runtime_data.initial_refresh
    await hass.async_block_till_done()
    return entry


async def test_rgbic_profile_round_trips_rgb_and_colour_temperature(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    """An H619A profile sends 0x15 frames and percent brightness, and reads kelvin back."""
    # The fake answers the mode read as an RGBIC strip in white mode at 2700 K.
    fake_client.replies[protocol.CMD_MODE] = protocol.build_frame(
        protocol.CMD_MODE, [protocol.SUBMODE_COLOR_RGBIC, 0x01, 0x0A, 0x8C], frame_type=0xAA
    )
    fake_client.replies[protocol.CMD_BRIGHTNESS] = protocol.build_frame(
        protocol.CMD_BRIGHTNESS, [50], frame_type=0xAA
    )
    entry = await setup_rgbic_entry(hass)
    state = hass.states.get(RGBIC_ENTITY_ID)
    assert state is not None and state.state == STATE_ON
    assert state.attributes[ATTR_COLOR_MODE] == "color_temp"
    assert state.attributes[ATTR_COLOR_TEMP_KELVIN] == 2700
    assert state.attributes[ATTR_BRIGHTNESS] == 128  # 50 % of 255
    assert "effect_list" not in state.attributes  # no scene table yet

    fake_client.writes.clear()
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBIC_ENTITY_ID, ATTR_RGB_COLOR: [255, 0, 0], ATTR_BRIGHTNESS: 255},
        blocking=True,
    )
    assert fake_client.commands == [
        protocol.power_frame(True),
        bytes.fromhex("33051501ff00000000000000ff7f00000000005d"),
        bytes.fromhex("3304640000000000000000000000000000000053"),  # 100 %
    ]
    state = hass.states.get(RGBIC_ENTITY_ID)
    assert state.attributes[ATTR_COLOR_MODE] == "rgb"
    assert state.attributes[ATTR_RGB_COLOR] == (255, 0, 0)

    fake_client.writes.clear()
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBIC_ENTITY_ID, ATTR_COLOR_TEMP_KELVIN: 6500},
        blocking=True,
    )
    frame = fake_client.commands[-1]
    assert frame[:9] == bytes([0x33, 0x05, 0x15, 0x01, 0xFF, 0xFF, 0xFF, 0x19, 0x64])
    assert frame[12:14] == bytes([0xFF, 0x7F])
    state = hass.states.get(RGBIC_ENTITY_ID)
    assert state.attributes[ATTR_COLOR_MODE] == "color_temp"
    assert state.attributes[ATTR_COLOR_TEMP_KELVIN] == 6500

    # A colour-mode reply (kelvin 0) carries no RGB, so the optimistic colour
    # survives the next poll instead of being overwritten with black.
    fake_client.replies[protocol.CMD_MODE] = protocol.build_frame(
        protocol.CMD_MODE, [protocol.SUBMODE_COLOR_RGBIC, 0x01, 0x00, 0x00], frame_type=0xAA
    )
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBIC_ENTITY_ID, ATTR_RGB_COLOR: [0, 255, 0]},
        blocking=True,
    )
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(RGBIC_ENTITY_ID)
    assert state.attributes[ATTR_COLOR_MODE] == "rgb"
    assert state.attributes[ATTR_RGB_COLOR] == (0, 255, 0)


async def test_known_model_firmware_disagreeing_with_table_is_believed(
    hass: HomeAssistant, bluetooth_ready, caplog
) -> None:
    """The H6181 case: table says 0x02 / 0-255, the light answers in 0x0D."""
    client = FakeGoveeClient(speaks=protocol.SUBMODE_COLOR_RGBWW, brightness=50)
    client.set_mode(protocol.SUBMODE_COLOR_RGBWW, [1, 2, 3, 0, 0])

    async def _establish(_cls, _device, _name, **_kwargs):
        client.is_connected = True
        return client

    with patch(
        "custom_components.govee_bluetooth.device.establish_connection", side_effect=_establish
    ):
        entry = await setup_entry(hass)
        await hass.async_block_till_done()
        device = entry.runtime_data.device
        assert device.profile.color_submode == 0x0D
        assert device.profile.brightness_scale == 100
        assert "using the light's value" in caplog.text
        # Persisted, so the next setup starts from the observed dialect.
        assert entry.data[CONF_DETECTED_SUBMODE] == 0x0D
        assert entry.data[CONF_FIRMWARE] == "1.00.17"
        # Brightness 50 was decoded on the 0-100 scale in the same poll.
        state = hass.states.get(ENTITY_ID)
        assert state.attributes[ATTR_BRIGHTNESS] == round(50 * 255 / 100)
        # And colour commands now go out in 0x0D.
        client.writes.clear()
        await hass.services.async_call(
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: ENTITY_ID, ATTR_RGB_COLOR: [255, 0, 0]},
            blocking=True,
        )
        assert client.commands[1] == protocol.rgb_frame(255, 0, 0, submode=0x0D)


async def test_manual_submode_override_beats_observation(
    hass: HomeAssistant, bluetooth_ready, caplog
) -> None:
    client = FakeGoveeClient(speaks=protocol.SUBMODE_COLOR_RGBWW)
    client.set_mode(protocol.SUBMODE_COLOR_RGBWW, [1, 2, 3, 0, 0])

    async def _establish(_cls, _device, _name, **_kwargs):
        client.is_connected = True
        return client

    with patch(
        "custom_components.govee_bluetooth.device.establish_connection", side_effect=_establish
    ):
        entry = await setup_entry(
            hass, options={CONF_IDLE_DISCONNECT: 60, CONF_COLOR_SUBMODE: 0x02}
        )
        await hass.async_block_till_done()
    assert entry.runtime_data.device.profile.color_submode == 0x02
    assert CONF_DETECTED_SUBMODE not in entry.data
    assert "keeping the override" in caplog.text


async def test_manual_brightness_scale_survives_dialect_detection(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    fake_client.set_mode(protocol.SUBMODE_COLOR_RGBWW, [1, 2, 3, 0, 0])
    entry = await setup_entry(hass, options={CONF_IDLE_DISCONNECT: 60, CONF_BRIGHTNESS_SCALE: 255})
    device = entry.runtime_data.device
    assert device.profile.color_submode == protocol.SUBMODE_COLOR_RGBWW
    assert device.profile.brightness_scale == 255
    assert hass.states.get(ENTITY_ID).attributes[ATTR_BRIGHTNESS] == 128
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_BRIGHTNESS: 200},
        blocking=True,
    )
    assert fake_client.commands[-1] == protocol.brightness_frame(200, 255)


async def test_diagnostics_show_table_and_observed_submode(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    from custom_components.govee_bluetooth.diagnostics import async_get_config_entry_diagnostics

    entry = await setup_entry(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["color_submode"] == {
        "static_table": 0x02,
        "detected": None,
        "manual_override": None,
        "last_observed": 0x02,
        "in_use": 0x02,
    }


async def test_write_without_response_only_control_characteristic(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    """The H6006 offers 2b11 as write-without-response only and drops a write request.

    Bench, 2026-10-19: fw 1.00.38 answered a write request with Write Not
    Permitted and disconnected. Every write must go out without response.
    """
    fake_client.control_properties = ("read", "write-without-response")
    await setup_entry(hass)
    state = hass.states.get(ENTITY_ID)
    assert state is not None and state.state == "on"  # the initial poll's reads went through
    fake_client.writes.clear()
    fake_client.write_responses.clear()
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_RGB_COLOR: [255, 0, 0]},
        blocking=True,
    )
    assert fake_client.commands[0] == protocol.power_frame(True)
    assert fake_client.write_responses and not any(fake_client.write_responses)


async def test_lights_offering_write_requests_keep_getting_them(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    """H6159 / H6181 were verified with write requests; that stays the default."""
    await setup_entry(hass)
    fake_client.write_responses.clear()
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_RGB_COLOR: [0, 255, 0]},
        blocking=True,
    )
    assert fake_client.write_responses and all(fake_client.write_responses)


@pytest.mark.parametrize(
    ("properties", "response"),
    [
        (["write-without-response"], False),
        (["write"], True),
        (["write", "write-without-response"], True),
    ],
)
async def test_all_operations_use_supported_write_procedure(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient, properties, response
) -> None:
    from custom_components.govee_bluetooth.diagnostics import async_get_config_entry_diagnostics

    fake_client.control_properties = properties
    with patch("custom_components.govee_bluetooth.device.HEARTBEAT_INTERVAL", 0.01):
        entry = await setup_entry(hass)
        device = entry.runtime_data.device
        await device.async_turn_on(rgb_color=(10, 20, 30), brightness=80)
        assert (await device.async_safe_probe()).answered
        assert (await device.async_active_probe((0x02,))).detected_submode == 0x02
        async with asyncio.timeout(2):
            while not any(frame["note"] == "heartbeat" for frame in device.frames):
                await asyncio.sleep(0.01)
        assert fake_client.write_responses
        assert all(actual is response for actual in fake_client.write_responses)
        diag = await async_get_config_entry_diagnostics(hass, entry)
        assert diag["transport"] == {"control_properties": properties, "write_response": response}
        await device.async_disconnect()
        # A new connection must re-read properties rather than reusing the old method.
        fake_client.control_properties = ["write"] if not response else ["write-without-response"]
        await device.async_update()
        assert fake_client.write_responses[-1] is not response


async def test_unwritable_control_disconnects_before_sending(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    fake_client.control_properties = ["read"]
    entry = await setup_entry(hass)
    assert not entry.runtime_data.last_update_success
    assert fake_client.disconnects == 1
    assert not fake_client.writes


async def test_status_query_ignores_wrong_selector_reply(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    entry = await setup_entry(hass)
    device = entry.runtime_data.device
    request = protocol.hardware_query_frame()
    sent = asyncio.Event()

    async def send(_uuid, frame, **_kwargs):
        if frame == request:
            sent.set()

    with patch.object(fake_client, "write_gatt_char", side_effect=send):
        query = asyncio.create_task(device._exchange.query(request))
        await sent.wait()
        device._on_notify(
            None,
            bytearray(
                protocol.build_frame(request[1], [request[2] ^ 1], frame_type=protocol.TYPE_READ)
            ),
        )
        assert not query.done()
        device._on_notify(
            None,
            bytearray(
                protocol.build_frame(
                    request[1], [request[2], 1, 2, 3], frame_type=protocol.TYPE_READ
                )
            ),
        )
        assert await query is not None


async def test_firmware_refreshes_after_reconnect(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient
) -> None:
    entry = await setup_entry(hass)
    device = entry.runtime_data.device
    fake_client.replies[protocol.CMD_VERSION] = protocol.build_frame(
        protocol.CMD_VERSION, list(b"1.00.18"), frame_type=protocol.TYPE_READ
    )
    await device.async_update()
    assert device.state.firmware == "1.00.17"  # same connection: cached
    assert not device.firmware_history

    await device.async_disconnect()
    fake_client.is_connected = True
    await device.async_update()
    assert device.state.firmware == "1.00.18"
    assert len(device.firmware_history) == 1
    assert device.firmware_history[0]["previous"] == "1.00.17"
    assert device.firmware_history[0]["current"] == "1.00.18"
    await device.async_update()
    assert len(device.firmware_history) == 1


async def test_hardware_registry_and_cached_diagnostic_entities(
    hass: HomeAssistant, bluetooth_ready, fake_client: FakeGoveeClient, device_registry
) -> None:
    # Hardware string from the saved H6159 census, delivered by the fake transport.
    fake_client.replies[protocol.CMD_DEVICE_INFO] = protocol.build_frame(
        7, [3, *b"1.00.02"], frame_type=0xAA
    )
    entry = await setup_entry(hass)
    registry = er.async_get(hass)
    light = registry.async_get(ENTITY_ID)
    assert device_registry.async_get(light.device_id).hw_version == "1.00.02"
    sensor_id = "sensor.ihoment_h6159_02db_readback_profile"
    sensor = registry.async_get(sensor_id)
    assert sensor is not None and sensor.disabled_by == er.RegistryEntryDisabler.INTEGRATION
    assert sensor.entity_category.value == "diagnostic"
    assert sensor.device_id == light.device_id
    registry.async_update_entity(sensor_id, disabled_by=None)
    await hass.async_block_till_done()
    # Disabled entities are added on reload; no per-sensor BLE query is required.
    from custom_components.govee_bluetooth.sensor import GoveeDiagnostic

    count = len(fake_client.writes)
    assert (
        GoveeDiagnostic(entry.runtime_data, "profile", "Profile").native_value == "h6159-captured"
    )
    assert GoveeDiagnostic(entry.runtime_data, "ic_count", "IC count").native_value is None
    assert len(fake_client.writes) == count
