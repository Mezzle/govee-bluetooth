"""Config flow tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
    CONF_PROBE_HISTORY,
    DOMAIN,
    PROBE_HISTORY_LIMIT,
)

from .conftest import (
    ADDRESS,
    LOCAL_NAME,
    FakeGoveeClient,
    inject_advertisement,
    make_service_info,
)

UNKNOWN_NAME = "ihoment_H6999_02DB"


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


async def _setup_entry(hass: HomeAssistant, data: dict | None = None) -> MockConfigEntry:
    inject_advertisement(hass, make_service_info())
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title=LOCAL_NAME,
        data=data or {CONF_ADDRESS: ADDRESS, CONF_NAME: LOCAL_NAME, CONF_MODEL: "H6159"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await entry.runtime_data.initial_refresh
    await hass.async_block_till_done()
    return entry


async def _finish_progress(hass: HomeAssistant, result: dict, manager) -> dict:
    """Wait for a progress step's task and fetch the step that follows it."""
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    return await manager.async_configure(result["flow_id"])


async def test_options_flow(hass: HomeAssistant, bluetooth_ready: None, fake_client) -> None:
    entry = await _setup_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["settings", "probe", "active_consent"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "settings"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"
    assert result["description_placeholders"]["table_submode"] == "0x02"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_POLL_INTERVAL: 30,
            CONF_IDLE_DISCONNECT: 0,
            CONF_BRIGHTNESS_SCALE: "100",
            CONF_COLOR_SUBMODE: "auto",
        },
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


async def test_options_flow_manual_submode_override(
    hass: HomeAssistant, bluetooth_ready: None, fake_client
) -> None:
    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_POLL_INTERVAL: 120,
            CONF_IDLE_DISCONNECT: 8,
            CONF_BRIGHTNESS_SCALE: "auto",
            CONF_COLOR_SUBMODE: "13",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_COLOR_SUBMODE] == 0x0D
    assert CONF_BRIGHTNESS_SCALE not in entry.options
    profile = entry.runtime_data.device.profile
    assert profile.color_submode == 0x0D
    assert profile.brightness_scale == 100  # derived from the override


# -- unknown models: read-only probe --------------------------------------------


@pytest.mark.parametrize(
    ("mode_payload", "expected"),
    [
        ([0x02, 1, 2, 3, 0], 0x02),
        ([0x0D, 1, 2, 3, 0, 0], 0x0D),
        ([0x15, 0x01, 0, 0], 0x15),
    ],
)
async def test_unknown_model_detected_by_read_only_probe(
    hass: HomeAssistant,
    bluetooth_ready: None,
    fake_client: FakeGoveeClient,
    mode_payload: list[int],
    expected: int,
) -> None:
    fake_client.set_mode(mode_payload[0], mode_payload[1:])
    inject_advertisement(hass, make_service_info(name=UNKNOWN_NAME))
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=make_service_info(name=UNKNOWN_NAME),
    )
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"]["model"] == "H6999"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_NAME: "Mystery"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "unknown_model"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["progress_action"] == "probe"
    result = await _finish_progress(hass, result, hass.config_entries.flow)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    history = result["data"].pop(CONF_PROBE_HISTORY)
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_NAME: "Mystery",
        CONF_MODEL: "H6999",
        CONF_DETECTED_SUBMODE: expected,
        CONF_FIRMWARE: "1.00.17",
    }
    # Read-only: no 0x33 frames went out, and the connection was released.
    assert fake_client.commands == []
    assert fake_client.disconnects == 1
    # The run is on record with the raw frames it exchanged: four queries
    # (version, power, brightness, mode) and their replies.
    assert len(history) == 1
    record = history[0]
    assert record["kind"] == "read_only"
    assert record["outcome"] == "color"
    assert record["detected_submode"] == expected
    assert record["firmware"] == "1.00.17"
    assert record["error"] is None
    tx = [f for f in record["frames"] if f["direction"] == "tx"]
    rx = [f for f in record["frames"] if f["direction"] == "rx"]
    assert [f["note"] for f in tx] == ["query"] * 4
    assert [f["note"] for f in rx] == ["reply"] * 4
    assert all(f["frame"].startswith("aa") for f in tx + rx)
    assert rx[-1]["frame"] == fake_client.replies[protocol.CMD_MODE].hex()


async def test_unknown_model_in_scene_offers_fallback_and_skip(
    hass: HomeAssistant, bluetooth_ready: None, fake_client: FakeGoveeClient
) -> None:
    fake_client.set_mode(protocol.SUBMODE_SCENE, [0x04])
    inject_advertisement(hass, make_service_info(name=UNKNOWN_NAME))
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=make_service_info(name=UNKNOWN_NAME),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_NAME: "Mystery"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
    result = await _finish_progress(hass, result, hass.config_entries.flow)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "probe_fallback"
    placeholders = result["description_placeholders"]
    assert placeholders["firmware"] == "1.00.17"
    assert placeholders["mode"] == "scene (0x04)"
    choices = [o["value"] for o in result["data_schema"].schema["choice"].config["options"]]
    assert choices == ["retry", "active", "skip"]

    # Retry while still in scene mode lands on the same screen.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"choice": "retry"}
    )
    result = await _finish_progress(hass, result, hass.config_entries.flow)
    assert result["step_id"] == "probe_fallback"

    # Declining completes setup with the unverified H6159 dialect.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"choice": "skip"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    history = result["data"].pop(CONF_PROBE_HISTORY)
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_NAME: "Mystery",
        CONF_MODEL: "H6999",
        CONF_FIRMWARE: "1.00.17",
    }
    assert fake_client.commands == []
    # Both inconclusive runs are on record, which is when the frames matter most.
    assert [r["outcome"] for r in history] == ["scene", "scene"]
    assert all(r["observed_submode"] == protocol.SUBMODE_SCENE for r in history)


async def test_unknown_model_connect_failure_falls_through(
    hass: HomeAssistant, bluetooth_ready: None
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=make_service_info(name=UNKNOWN_NAME),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_NAME: "Mystery"}
    )
    # No advertisement was injected, so the connect fails before its first
    # await and the eager progress task is already done when the step returns.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
    if result["type"] is FlowResultType.SHOW_PROGRESS:
        result = await _finish_progress(hass, result, hass.config_entries.flow)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "probe_fallback"
    assert result["description_placeholders"]["mode"] == "no reply"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"choice": "skip"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (record,) = result["data"][CONF_PROBE_HISTORY]
    assert record["outcome"] == "error"
    assert "not visible" in record["error"]
    assert record["frames"] == []


# -- unknown models: active probe ---------------------------------------------


async def _reach_consent(hass: HomeAssistant, fake_client: FakeGoveeClient) -> dict:
    fake_client.set_mode(protocol.SUBMODE_SCENE, [0x04])
    inject_advertisement(hass, make_service_info(name=UNKNOWN_NAME))
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=make_service_info(name=UNKNOWN_NAME),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_NAME: "Mystery"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
    result = await _finish_progress(hass, result, hass.config_entries.flow)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"choice": "active"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "active_consent"
    return result


async def test_active_consent_screen_states_the_risk(
    hass: HomeAssistant, bluetooth_ready: None, fake_client: FakeGoveeClient
) -> None:
    import json
    from pathlib import Path

    result = await _reach_consent(hass, fake_client)
    strings = json.loads(
        (Path(__file__).parents[1] / "custom_components/govee_bluetooth/strings.json").read_text()
    )
    for section in ("config", "options"):
        text = strings[section]["step"]["active_consent"]["description"]
        assert "change colour briefly" in text
        assert "cannot be guaranteed harmless" in text
        assert "H6159, H6181" in text
        assert "power cycle" in text and "reload" in text

    # The box must be ticked; an untouched form stays on the consent screen.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"consent": False}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "active_consent"
    assert result["errors"] == {"consent": "consent_required"}
    assert fake_client.commands == []


async def test_active_probe_detects_rgbww_firmware(
    hass: HomeAssistant, bluetooth_ready: None
) -> None:
    """An H6181-like light: ignores 0x02, echoes 0x0D. The table prior is 0x02 for unknowns."""
    client = FakeGoveeClient(speaks=protocol.SUBMODE_COLOR_RGBWW, brightness=40)

    async def _establish(_cls, _device, _name, **_kwargs):
        client.is_connected = True
        return client

    with patch(
        "custom_components.govee_bluetooth.device.establish_connection", side_effect=_establish
    ):
        result = await _reach_consent(hass, client)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"consent": True}
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        assert result["progress_action"] == "active_probe"
        result = await _finish_progress(hass, result, hass.config_entries.flow)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DETECTED_SUBMODE] == 0x0D
    assert result["data"][CONF_FIRMWARE] == "1.00.17"
    mode_writes = [c for c in client.commands if c[1] == protocol.CMD_MODE]
    assert [c[2] for c in mode_writes] == [0x02, 0x0D]  # table prior first, then the echoing one
    assert all(c[2] != protocol.SUBMODE_SCENE for c in mode_writes)
    brightness_writes = [c for c in client.commands if c[1] == protocol.CMD_BRIGHTNESS]
    assert brightness_writes == [protocol.build_frame(protocol.CMD_BRIGHTNESS, [40])]
    assert client.disconnects >= 1
    # Read-only run first, then the active one, each with its own frames.
    history = result["data"][CONF_PROBE_HISTORY]
    assert [r["kind"] for r in history] == ["read_only", "active"]
    active = history[-1]
    assert active["outcome"] == "detected"
    assert active["detected_submode"] == 0x0D
    assert active["attempted"] == [0x02, 0x0D]
    assert active["test_kelvin"] is None  # 0x15 was never reached
    assert active["restored"] is False and "scene" in active["restore_note"]
    probe_frames = [f["frame"] for f in active["frames"] if f["note"] == "probe"]
    assert probe_frames == [c.hex() for c in client.commands]


async def test_active_probe_detects_rgbic_firmware(
    hass: HomeAssistant, bluetooth_ready: None
) -> None:
    """An H61B5/H61E5-like strip in a scene: ignores 0x02 and 0x0D, echoes the 0x15 white's kelvin."""
    client = FakeGoveeClient(speaks=protocol.SUBMODE_COLOR_RGBIC, brightness=40)

    async def _establish(_cls, _device, _name, **_kwargs):
        client.is_connected = True
        return client

    with patch(
        "custom_components.govee_bluetooth.device.establish_connection", side_effect=_establish
    ):
        result = await _reach_consent(hass, client)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"consent": True}
        )
        result = await _finish_progress(hass, result, hass.config_entries.flow)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DETECTED_SUBMODE] == 0x15
    mode_writes = [c for c in client.commands if c[1] == protocol.CMD_MODE]
    assert [c[2] for c in mode_writes] == [0x02, 0x0D, 0x15]
    assert mode_writes[-1] == protocol.color_temp_frame(4300, 0x15)
    active = result["data"][CONF_PROBE_HISTORY][-1]
    assert active["outcome"] == "detected"
    assert active["test_kelvin"] == 4300
    assert "scene" in active["restore_note"]


async def test_active_probe_disconnect_reports_partial_result(
    hass: HomeAssistant, bluetooth_ready: None
) -> None:
    client = FakeGoveeClient(speaks=protocol.SUBMODE_COLOR_RGBWW, fail_after_writes=1)

    async def _establish(_cls, _device, _name, **_kwargs):
        client.is_connected = True
        return client

    with patch(
        "custom_components.govee_bluetooth.device.establish_connection", side_effect=_establish
    ):
        result = await _reach_consent(hass, client)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"consent": True}
        )
        result = await _finish_progress(hass, result, hass.config_entries.flow)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "active_failed"
    placeholders = result["description_placeholders"]
    assert placeholders["attempted"] == "0x02, 0x0d"
    assert "Stopped early" in placeholders["detail"]
    assert len(client.commands) == 2  # the dropped write was the last one; no retry

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"choice": "skip"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_DETECTED_SUBMODE not in result["data"]
    active = result["data"][CONF_PROBE_HISTORY][-1]
    assert active["outcome"] == "disconnected"
    assert active["attempted"] == [0x02, 0x0D]
    assert "dropped" in active["error"]
    # The frames show the write that killed the link was the last thing sent.
    assert active["frames"][-1]["direction"] == "tx"
    assert active["frames"][-1]["frame"] == client.commands[-1].hex()


# -- options flow: re-run probes ---------------------------------------------


async def test_options_rerun_read_only_probe_stores_detected_submode(
    hass: HomeAssistant, bluetooth_ready: None, fake_client: FakeGoveeClient
) -> None:
    entry = await _setup_entry(
        hass, data={CONF_ADDRESS: ADDRESS, CONF_NAME: LOCAL_NAME, CONF_MODEL: "H6999"}
    )
    assert entry.runtime_data.device.profile.color_submode == 0x02
    # Firmware update moved the light to the RGBWW dialect.
    fake_client.set_mode(0x0D, [5, 6, 7, 0, 0])
    fake_client.writes.clear()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "probe"}
    )
    result = await _finish_progress(hass, result, hass.config_entries.options)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.data[CONF_DETECTED_SUBMODE] == 0x0D
    assert entry.data[CONF_FIRMWARE] == "1.00.17"
    assert entry.runtime_data.device.profile.color_submode == 0x0D
    assert entry.runtime_data.device.profile.brightness_scale == 100
    assert fake_client.commands == []
    (record,) = entry.data[CONF_PROBE_HISTORY]
    assert record["kind"] == "read_only" and record["detected_submode"] == 0x0D
    assert len(record["frames"]) == 8


async def test_options_probe_history_persists_failures_and_is_bounded(
    hass: HomeAssistant, bluetooth_ready: None, fake_client: FakeGoveeClient
) -> None:
    """Inconclusive re-runs from Configure land in the entry too, newest last, capped."""
    entry = await _setup_entry(
        hass, data={CONF_ADDRESS: ADDRESS, CONF_NAME: LOCAL_NAME, CONF_MODEL: "H6999"}
    )
    fake_client.set_mode(protocol.SUBMODE_SCENE, [0x04])
    for _ in range(PROBE_HISTORY_LIMIT + 1):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"next_step_id": "probe"}
        )
        result = await _finish_progress(hass, result, hass.config_entries.options)
        assert result["step_id"] == "probe_fallback"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"choice": "skip"}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    history = entry.data[CONF_PROBE_HISTORY]
    assert len(history) == PROBE_HISTORY_LIMIT
    assert all(r["outcome"] == "scene" for r in history)
    assert CONF_DETECTED_SUBMODE not in entry.data
    # A probe that changed nothing does not reload the entry.
    assert entry.runtime_data.device.profile.color_submode == 0x02


async def test_options_active_probe_requires_consent_then_runs(
    hass: HomeAssistant, bluetooth_ready: None
) -> None:
    client = FakeGoveeClient(speaks=protocol.SUBMODE_COLOR_RGBWW)
    client.set_mode(protocol.SUBMODE_SCENE, [0x04])

    async def _establish(_cls, _device, _name, **_kwargs):
        client.is_connected = True
        return client

    with patch(
        "custom_components.govee_bluetooth.device.establish_connection", side_effect=_establish
    ):
        entry = await _setup_entry(
            hass, data={CONF_ADDRESS: ADDRESS, CONF_NAME: LOCAL_NAME, CONF_MODEL: "H6999"}
        )
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"next_step_id": "active_consent"}
        )
        assert result["step_id"] == "active_consent"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"consent": True}
        )
        result = await _finish_progress(hass, result, hass.config_entries.options)
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert entry.data[CONF_DETECTED_SUBMODE] == 0x0D
    assert entry.runtime_data.device.profile.color_submode == 0x0D
