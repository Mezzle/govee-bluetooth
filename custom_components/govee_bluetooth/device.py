"""Connection management and state for one Govee BLE light.

Design notes
------------
* All Bluetooth access goes through Home Assistant's bluetooth integration, so
  ESPHome/Shelly Bluetooth proxies and local adapters are interchangeable.
* Govee firmware drops an idle GATT connection after roughly ten seconds unless
  the client sends ``AA 01`` every couple of seconds. While a connection is
  open this class sends that heartbeat, and the reply doubles as a power-state
  update. The connection is closed after ``idle_disconnect`` seconds without a
  command so a proxy's connection slots are not held hostage by idle strips.
* Commands and status reads are serialised through one lock. A Home Assistant
  ``light.turn_on`` with power, colour and brightness goes out as one batch on
  one connection.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    establish_connection,
)
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback

from . import protocol
from .const import CONTROL_CHARACTERISTIC_UUID, STATUS_CHARACTERISTIC_UUID
from .models import ModelProfile

_LOGGER = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 2.0
QUERY_TIMEOUT = 2.5
INTER_FRAME_DELAY = 0.06
CONNECT_ATTEMPTS = 3


class GoveeConnectionError(Exception):
    """Raised when the light cannot be reached or a write fails."""


@dataclass(frozen=True, slots=True)
class GoveeState:
    """Last known state of the light."""

    is_on: bool | None = None
    brightness: int | None = None  # Home Assistant 1-255
    rgb_color: tuple[int, int, int] | None = None
    color_temp_kelvin: int | None = None
    white_mode: bool = False
    effect: str | None = None
    firmware: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class GoveeBleDevice:
    """Talk to a single Govee light over BLE through Home Assistant's stack."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        profile: ModelProfile,
        idle_disconnect: float,
    ) -> None:
        self._hass = hass
        self.address = address.upper()
        self.name = name
        self.profile = profile
        self.idle_disconnect = idle_disconnect
        self.state = GoveeState()

        self._lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._expected_disconnect = False
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._idle_handle: asyncio.TimerHandle | None = None
        self._last_command_at = 0.0
        self._pending: dict[int, asyncio.Future[protocol.Reply]] = {}
        self._listeners: list[Callable[[GoveeState], None]] = []
        self._scene_names = {v: k for k, v in profile.scenes.items()}

    # ------------------------------------------------------------------
    # Listeners
    # ------------------------------------------------------------------

    @callback
    def async_add_listener(self, listener: Callable[[GoveeState], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def _remove() -> None:
            self._listeners.remove(listener)

        return _remove

    @callback
    def _set_state(self, **changes: Any) -> None:
        new_state = replace(self.state, **changes)
        if new_state == self.state:
            return
        self.state = new_state
        for listener in list(self._listeners):
            listener(new_state)

    # ------------------------------------------------------------------
    # Public commands
    # ------------------------------------------------------------------

    async def async_turn_on(
        self,
        *,
        brightness: int | None = None,
        rgb_color: tuple[int, int, int] | None = None,
        color_temp_kelvin: int | None = None,
        effect: str | None = None,
    ) -> None:
        """Turn on and optionally set brightness / colour / white / scene."""
        # Always lead with power-on: our cached state may be stale if the strip
        # was switched off by its remote or the Govee app between polls.
        frames: list[bytes] = [protocol.power_frame(True)]
        changes: dict[str, Any] = {"is_on": True}

        if effect is not None and effect in self.profile.scenes:
            frames.append(protocol.scene_frame(self.profile.scenes[effect]))
            changes.update(effect=effect, white_mode=False)
        elif color_temp_kelvin is not None:
            frames.append(protocol.color_temp_frame(color_temp_kelvin, self.profile.color_submode))
            changes.update(
                color_temp_kelvin=color_temp_kelvin,
                rgb_color=protocol.kelvin_to_rgb(color_temp_kelvin),
                white_mode=True,
                effect=None,
            )
        elif rgb_color is not None:
            frames.append(protocol.rgb_frame(*rgb_color, submode=self.profile.color_submode))
            changes.update(rgb_color=tuple(rgb_color), white_mode=False, effect=None)

        if brightness is not None:
            frames.append(protocol.brightness_frame(brightness, self.profile.brightness_scale))
            changes["brightness"] = brightness

        await self._async_write_frames(frames)
        self._set_state(**changes)

    async def async_turn_off(self) -> None:
        await self._async_write_frames([protocol.power_frame(False)])
        self._set_state(is_on=False)

    async def async_update(self) -> GoveeState:
        """Read power, brightness and mode from the light."""
        async with self._lock:
            client = await self._async_connect()
            try:
                power = await self._async_query(
                    client, protocol.power_query_frame(), protocol.CMD_POWER
                )
                brightness = await self._async_query(
                    client, protocol.brightness_query_frame(), protocol.CMD_BRIGHTNESS
                )
                mode = await self._async_query(
                    client, protocol.mode_query_frame(), protocol.CMD_MODE
                )
                firmware = None
                if self.state.firmware is None:
                    firmware = await self._async_query(
                        client, protocol.version_query_frame(), protocol.CMD_VERSION
                    )
            except (BleakError, OSError) as err:
                await self._async_disconnect_locked()
                raise GoveeConnectionError(f"Status read from {self.name} failed: {err}") from err
            finally:
                self._touch()

        if power is None and brightness is None and mode is None:
            raise GoveeConnectionError(f"{self.name} did not answer any status query")

        changes: dict[str, Any] = {}
        if power is not None and power.power is not None:
            changes["is_on"] = power.power
        if brightness is not None and brightness.brightness_level is not None:
            changes["brightness"] = protocol.device_to_ha_brightness(
                brightness.brightness_level, self.profile.brightness_scale
            )
        if mode is not None and mode.mode is not None:
            changes.update(self._changes_from_mode(mode.mode))
        if firmware is not None and firmware.version:
            changes["firmware"] = firmware.version
        self._set_state(**changes)
        return self.state

    async def async_disconnect(self) -> None:
        async with self._lock:
            await self._async_disconnect_locked()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _changes_from_mode(self, mode: protocol.ModeReport) -> dict[str, Any]:
        changes: dict[str, Any] = {"extra": {**self.state.extra, "submode": mode.submode}}
        if mode.submode == protocol.SUBMODE_SCENE:
            changes["effect"] = self._scene_names.get(mode.scene_id, f"Scene {mode.scene_id}")
            changes["white_mode"] = False
        elif mode.rgb is not None:
            changes["effect"] = None
            if mode.is_white and mode.white_tint is not None:
                changes["white_mode"] = True
                changes["rgb_color"] = mode.white_tint
                changes["color_temp_kelvin"] = protocol.rgb_to_kelvin(mode.white_tint)
            else:
                changes["white_mode"] = False
                changes["rgb_color"] = mode.rgb
        else:
            changes["effect"] = None
        return changes

    def _ble_device(self) -> BLEDevice | None:
        return bluetooth.async_ble_device_from_address(self._hass, self.address, connectable=True)

    async def _async_connect(self) -> BleakClientWithServiceCache:
        """Return a connected client, connecting if required. Caller holds the lock."""
        if self._client is not None and self._client.is_connected:
            return self._client

        ble_device = self._ble_device()
        if ble_device is None:
            raise GoveeConnectionError(
                f"{self.name} ({self.address}) is not visible to any connectable Bluetooth adapter or proxy"
            )
        _LOGGER.debug("Connecting to %s via %s", self.name, ble_device.details)
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self.name,
                disconnected_callback=self._on_disconnected,
                max_attempts=CONNECT_ATTEMPTS,
                ble_device_callback=lambda: self._ble_device() or ble_device,
            )
        except BleakNotFoundError as err:
            raise GoveeConnectionError(f"{self.name} not found: {err}") from err
        except (BleakError, TimeoutError, OSError) as err:
            raise GoveeConnectionError(f"Could not connect to {self.name}: {err}") from err

        try:
            await client.start_notify(STATUS_CHARACTERISTIC_UUID, self._on_notify)
        except (BleakError, OSError) as err:
            # Not fatal: writes still work, we just lose read-back.
            _LOGGER.debug("Could not subscribe to %s notifications: %s", self.name, err)

        self._client = client
        self._expected_disconnect = False
        self._touch()
        self._heartbeat_task = self._hass.async_create_background_task(
            self._async_heartbeat_loop(client), f"{self.name} heartbeat"
        )
        return client

    async def _async_write_frames(self, frames: list[bytes]) -> None:
        if not frames:
            return
        async with self._lock:
            client = await self._async_connect()
            try:
                for index, frame in enumerate(frames):
                    _LOGGER.debug("%s -> %s", self.name, frame.hex())
                    await client.write_gatt_char(CONTROL_CHARACTERISTIC_UUID, frame, response=True)
                    if index < len(frames) - 1:
                        await asyncio.sleep(INTER_FRAME_DELAY)
            except (BleakError, TimeoutError, OSError) as err:
                await self._async_disconnect_locked()
                raise GoveeConnectionError(f"Command to {self.name} failed: {err}") from err
            finally:
                self._touch()

    async def _async_query(
        self, client: BleakClientWithServiceCache, frame: bytes, command: int
    ) -> protocol.Reply | None:
        """Write a read request and wait for the matching reply."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[protocol.Reply] = loop.create_future()
        self._pending[command] = future
        try:
            _LOGGER.debug("%s -> %s (query)", self.name, frame.hex())
            await client.write_gatt_char(CONTROL_CHARACTERISTIC_UUID, frame, response=True)
            return await asyncio.wait_for(future, QUERY_TIMEOUT)
        except TimeoutError:
            _LOGGER.debug("%s: no reply to query %#04x", self.name, command)
            return None
        finally:
            if self._pending.get(command) is future:
                del self._pending[command]

    @callback
    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        frame = bytes(data)
        _LOGGER.debug("%s <- %s", self.name, frame.hex())
        reply = protocol.parse_reply(frame)
        if reply is None:
            return
        future = self._pending.get(reply.command)
        if future is not None and not future.done():
            future.set_result(reply)
            return
        # Unsolicited reply, most likely to a heartbeat: keep power state fresh.
        if reply.power is not None:
            self._set_state(is_on=reply.power)

    @callback
    def _on_disconnected(self, _client: Any) -> None:
        if not self._expected_disconnect:
            _LOGGER.debug("%s disconnected unexpectedly", self.name)
        self._cleanup_connection()

    @callback
    def _cleanup_connection(self) -> None:
        self._client = None
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    async def _async_disconnect_locked(self) -> None:
        client = self._client
        self._expected_disconnect = True
        self._cleanup_connection()
        if client is not None:
            try:
                await client.disconnect()
            except (BleakError, OSError) as err:  # pragma: no cover - best effort
                _LOGGER.debug("%s: error during disconnect: %s", self.name, err)

    @callback
    def _touch(self) -> None:
        """Record activity and (re)arm the idle-disconnect timer."""
        self._last_command_at = time.monotonic()
        if self._idle_handle is not None:
            self._idle_handle.cancel()
        if self._client is None:
            return
        loop = asyncio.get_running_loop()
        self._idle_handle = loop.call_later(
            max(0.0, self.idle_disconnect), self._schedule_idle_disconnect
        )

    @callback
    def _schedule_idle_disconnect(self) -> None:
        self._idle_handle = None
        self._hass.async_create_background_task(
            self._async_idle_disconnect(), f"{self.name} idle disconnect"
        )

    async def _async_idle_disconnect(self) -> None:
        if self._lock.locked():
            # Something is in flight; it will re-arm the timer when it finishes.
            return
        async with self._lock:
            if time.monotonic() - self._last_command_at < self.idle_disconnect:
                return
            _LOGGER.debug("%s idle, disconnecting", self.name)
            await self._async_disconnect_locked()

    async def _async_heartbeat_loop(self, client: BleakClientWithServiceCache) -> None:
        """Keep the GATT link alive while it is open."""
        try:
            while client.is_connected:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if not client.is_connected or self._client is not client:
                    return
                if self._lock.locked():
                    continue  # a command is in flight, that counts as activity
                try:
                    await client.write_gatt_char(
                        CONTROL_CHARACTERISTIC_UUID, protocol.heartbeat_frame(), response=False
                    )
                except (BleakError, OSError) as err:
                    _LOGGER.debug("%s heartbeat failed: %s", self.name, err)
                    return
        except asyncio.CancelledError:
            raise
