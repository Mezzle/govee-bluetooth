"""Connection management and state for one Govee BLE light.

Design notes
------------
* All Bluetooth access goes through Home Assistant's bluetooth integration, so
  ESPHome/Shelly Bluetooth proxies and local adapters are interchangeable.
* While a connection is open this class sends ``AA 01`` every two seconds
  as a keep-alive, and the reply doubles as a power-state update. Device-side
  idle timeouts vary and are not established for every model. The connection
  is closed after ``idle_disconnect`` seconds without a command so a proxy's
  connection slots are not held hostage by idle strips.
* Commands and status reads are serialised through one lock. A Home Assistant
  ``light.turn_on`` with power, colour and brightness goes out as one batch on
  one connection.
* Every frame in either direction is kept in a bounded ring buffer
  (:attr:`GoveeBleDevice.frames`) so the diagnostics download carries the raw
  conversation without anyone having to turn on debug logging first.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
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

from . import advertisement, probe, protocol, readback
from .const import CONTROL_CHARACTERISTIC_UUID, STATUS_CHARACTERISTIC_UUID
from .exchange import ConnectionLost, FrameExchange
from .models import ModelProfile, brightness_scale_for_submode

_LOGGER = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 2.0
QUERY_TIMEOUT = 2.5
INTER_FRAME_DELAY = 0.06
CONNECT_ATTEMPTS = 3
# Frames kept for diagnostics. Heartbeats go out every two seconds while a
# connection is open, so this holds a few minutes of an idle link or a great
# many commands.
FRAME_LOG_SIZE = 200


@dataclass(slots=True)
class FrameRecord:
    """One frame as sent or received, for the diagnostics download."""

    at: str  # ISO-8601 UTC, millisecond precision
    direction: str  # "tx" or "rx"
    frame: str  # hex
    note: str  # "command", "query", "probe", "heartbeat", "reply" or "unsolicited"

    def as_dict(self) -> dict[str, str]:
        return {"at": self.at, "direction": self.direction, "frame": self.frame, "note": self.note}


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
    hardware: str | None = None
    provenance: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            key: {"source": "unknown", "at": None}
            for key in (
                "is_on",
                "brightness",
                "rgb_color",
                "color_temp_kelvin",
                "effect",
                "white_mode",
                "firmware",
                "hardware",
            )
        }
    )


class GoveeBleDevice:
    """Talk to a single Govee light over BLE through Home Assistant's stack."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        profile: ModelProfile,
        idle_disconnect: float,
        *,
        on_submode_observed: Callable[[int, str | None], None] | None = None,
        submode_locked: bool = False,
        brightness_scale_locked: bool = False,
    ) -> None:
        self._hass = hass
        self.address = address.upper()
        self.name = name
        self.profile = profile
        self.idle_disconnect = idle_disconnect
        self.state = GoveeState()
        # Called when a mode reply names a colour sub-mode the profile did not
        # expect, after the profile has been switched to it. ``submode_locked``
        # (a manual override in the options) only logs the disagreement.
        self._on_submode_observed = on_submode_observed
        self._submode_locked = submode_locked
        self._brightness_scale_locked = brightness_scale_locked

        self._lock = asyncio.Lock()
        self._in_flight: asyncio.Task[Any] | None = None
        self._client: BleakClientWithServiceCache | None = None
        self._expected_disconnect = False
        self._firmware_checked = False
        self.pact: tuple[int, int] | None = None
        self.read_profile = readback.resolve(profile.model, None, None)
        self.optional_reads: dict[str, dict[str, Any]] = {}
        self._identity_key: tuple | None = None
        self.optional_read_failures = 0
        self.command_rejections = 0
        self.last_observation: str | None = None
        self.last_command_acceptance: str | None = None
        self.desired_state: dict[str, Any] = {}
        self.firmware_history: deque[dict[str, str]] = deque(maxlen=10)
        self.control_properties: tuple[str, ...] = ()
        self.write_response: bool | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._idle_handle: asyncio.TimerHandle | None = None
        self._last_command_at = 0.0
        self._exchange = FrameExchange(self._send_frame, QUERY_TIMEOUT)
        self._listeners: list[Callable[[GoveeState], None]] = []
        self._scene_names = {v: k for k, v in profile.scenes.items()}
        self._frame_log: deque[FrameRecord] = deque(maxlen=FRAME_LOG_SIZE)

    # ------------------------------------------------------------------
    # Frame log
    # ------------------------------------------------------------------

    @property
    def frames(self) -> list[dict[str, str]]:
        """The most recent frames in both directions, oldest first."""
        return [record.as_dict() for record in self._frame_log]

    @callback
    def _record_frame(self, direction: str, frame: bytes, note: str) -> None:
        self._frame_log.append(
            FrameRecord(
                at=datetime.now(UTC).isoformat(timespec="milliseconds"),
                direction=direction,
                frame=frame.hex(),
                note=note,
            )
        )

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
    def _set_state(self, *, source: str = "observed", **changes: Any) -> None:
        now = datetime.now(UTC).isoformat(timespec="milliseconds")
        provenance = {**changes.get("provenance", self.state.provenance)}
        for key in changes:
            if key not in ("extra", "provenance"):
                provenance[key] = {
                    "source": "unknown" if changes[key] is None else source,
                    "at": now,
                }
        changes["provenance"] = provenance
        if source == "observed" and any(
            k in changes
            for k in (
                "is_on",
                "brightness",
                "rgb_color",
                "color_temp_kelvin",
                "white_mode",
                "effect",
            )
        ):
            self.last_observation = now
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
            changes.update(effect=effect, white_mode=False, rgb_color=None, color_temp_kelvin=None)
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
            changes.update(
                rgb_color=tuple(rgb_color), white_mode=False, color_temp_kelvin=None, effect=None
            )

        if brightness is not None:
            frames.append(protocol.brightness_frame(brightness, self.profile.brightness_scale))
            changes["brightness"] = brightness

        await self._async_write_frames(frames)
        self.desired_state.update(changes)
        self._set_state(source="optimistic", **changes)
        if changes.get("white_mode") and "color_temp_kelvin" in changes:
            self._mark_derived("rgb_color")

    async def async_turn_off(self) -> None:
        await self._async_write_frames([protocol.power_frame(False)])
        self.desired_state["is_on"] = False
        self._set_state(source="optimistic", is_on=False)

    async def async_update(self) -> GoveeState:
        """Refresh basic state followed by the selected optional read contract."""
        async with self._lock:
            self._in_flight = asyncio.current_task()
            await self._async_connect()
            try:
                power = await self._exchange.query(protocol.power_query_frame())
                brightness = await self._exchange.query(protocol.brightness_query_frame())
                mode = await self._exchange.query(protocol.mode_query_frame())
                firmware = None
                if not self._firmware_checked:
                    firmware = await self._exchange.query(protocol.version_query_frame())
                    self._firmware_checked = True
                self._merge_poll(power, brightness, mode, firmware)
                await self._collect_optional(
                    firmware.version if firmware else self.state.firmware,
                    mode.mode if mode else None,
                )
                if (
                    power is None
                    and brightness is None
                    and mode is None
                    and not self.state.extra.get("bulk_complete")
                ):
                    raise GoveeConnectionError(f"{self.name} did not answer any status query")
            except (BleakError, OSError, ConnectionLost) as err:
                await self._async_disconnect_locked()
                raise GoveeConnectionError(f"Status read from {self.name} failed: {err}") from err
            finally:
                self._touch()
        return self.state

    def _merge_poll(self, power, brightness, mode, firmware) -> None:
        changes: dict[str, Any] = {}
        if firmware is not None and firmware.version:
            changes["firmware"] = firmware.version
            self._firmware_checked = True
            if self.state.firmware and self.state.firmware != firmware.version:
                self.firmware_history.append(
                    {
                        "at": datetime.now(UTC).isoformat(timespec="seconds"),
                        "previous": self.state.firmware,
                        "current": firmware.version,
                    }
                )
                _LOGGER.info(
                    "%s firmware changed from %s to %s",
                    self.name,
                    self.state.firmware,
                    firmware.version,
                )
        if mode is not None and mode.mode is not None:
            self._apply_observed_submode(mode.mode.submode, changes.get("firmware"))
        if power is not None and power.power is not None:
            changes["is_on"] = power.power
        if brightness is not None and brightness.brightness_level is not None:
            changes["brightness"] = protocol.device_to_ha_brightness(
                brightness.brightness_level, self._read_brightness_scale
            )
        if mode is not None and mode.mode is not None:
            changes.update(self._changes_from_mode(mode.mode))
        if brightness is not None and brightness.brightness_level is not None:
            extra = changes.get("extra", self.state.extra)
            changes["extra"] = {**extra, "brightness_raw": brightness.brightness_level}
        self._set_state(**changes)
        if mode is not None and mode.mode is not None:
            self._mark_mode_derivations(mode.mode, changes)

    @callback
    def _apply_observed_submode(self, submode: int, firmware: str | None = None) -> None:
        """Prefer the colour sub-mode the light reports over the static table.

        Runs on every status read, so a known model whose firmware disagrees
        with the table (the H6181 case) is corrected on first contact, and a
        firmware update that changes dialect is picked up on the next poll.
        """
        # Preserve the existing captured legacy migration path, but never infer
        # a writer/brightness scale for additional RGBIC families from one byte.
        if self.profile.model not in ("H6159", "H6181", "H6006"):
            return
        if submode not in probe.COLOR_SUBMODES or submode == self.profile.color_submode:
            return
        if self._submode_locked:
            _LOGGER.warning(
                "%s reports colour sub-mode %#04x but the options force %#04x; keeping the override",
                self.name,
                submode,
                self.profile.color_submode,
            )
            return
        _LOGGER.warning(
            "%s reports colour sub-mode %#04x, profile %s expected %#04x; using the light's value",
            self.name,
            submode,
            self.profile.model,
            self.profile.color_submode,
        )
        self.profile = replace(
            self.profile,
            color_submode=submode,
            brightness_scale=(
                self.profile.brightness_scale
                if self._brightness_scale_locked
                else brightness_scale_for_submode(submode, self.profile.brightness_scale)
            ),
        )
        if self._on_submode_observed is not None:
            self._on_submode_observed(submode, firmware or self.state.firmware)

    # ------------------------------------------------------------------
    # Dialect probes (see probe.py)
    # ------------------------------------------------------------------

    async def async_safe_probe(self) -> probe.SafeProbeResult:
        """Run the read-only probe over this device's connection."""
        async with self._lock:
            self._in_flight = asyncio.current_task()
            client = await self._async_connect()
            try:
                return await probe.async_safe_probe(_ProbeLink(self, client))
            finally:
                self._touch()

    async def async_active_probe(self, candidates: tuple[int, ...]) -> probe.ActiveProbeResult:
        """Run the opt-in write-and-echo probe. Holds the lock for the whole sequence."""
        async with self._lock:
            self._in_flight = asyncio.current_task()
            client = await self._async_connect()
            try:
                result = await probe.async_active_probe(_ProbeLink(self, client), candidates)
            finally:
                self._touch()
        if result.error is not None:
            await self.async_disconnect()
        return result

    async def async_disconnect(self) -> None:
        """Drop the connection, even if an operation is stuck mid-connect.

        Used on unload. A connect through a proxy can hang for a long time,
        so do not queue behind the lock forever: cancel whatever holds it.
        """
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=2.0)
        except TimeoutError:
            if self._in_flight is not None and not self._in_flight.done():
                self._in_flight.cancel()
            await self._async_disconnect_locked()
            return
        try:
            await self._async_disconnect_locked()
        finally:
            self._lock.release()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @property
    def _read_brightness_scale(self) -> int:
        # Explicit user override wins; a read contract can otherwise specify a
        # scale independently of the chosen colour writer.
        if self._brightness_scale_locked:
            return self.profile.brightness_scale
        return self.read_profile.brightness_scale or self.profile.brightness_scale

    def _mark_derived(self, key: str) -> None:
        provenance = {**self.state.provenance}
        provenance[key] = {
            "source": "derived",
            "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        }
        self._set_state(provenance=provenance)

    def _mark_mode_derivations(self, mode: protocol.ModeReport, changes: dict) -> None:
        if "color_temp_kelvin" in changes and mode.is_white and mode.color_temp_kelvin is None:
            self._mark_derived("color_temp_kelvin")
        if "rgb_color" in changes and (
            mode.submode == 0x15 or (mode.tint_is_derived and self.profile.model != "H6006")
        ):
            self._mark_derived("rgb_color")

    def _changes_from_mode(self, mode: protocol.ModeReport) -> dict[str, Any]:
        layout = self.read_profile.mode_layout
        # Historical H6159/H6181 migration remains supported independently of
        # richer page/push selection and manual writer overrides.
        if self.profile.model in ("H6159", "H6181", "H6006"):
            layout = {
                0x02: "legacy",
                0x0D: "bulb_rgbww" if self.profile.model == "H6006" else "rgbww",
            }.get(mode.submode, layout)
        changes = readback.mode_changes(mode, layout)
        changes["extra"] = {
            **self.state.extra,
            "submode": mode.submode,
            "mode_body": mode.body.hex(),
        }
        if mode.submode == protocol.SUBMODE_SCENE:
            changes["effect"] = self._scene_names.get(mode.scene_id, f"Scene {mode.scene_id}")
        return changes

    async def _optional_query(self, name: str, frame: bytes) -> protocol.Reply | None:
        # Cache even silence. Never interpret it as zero or unsupported.
        at = datetime.now(UTC).isoformat(timespec="milliseconds")
        try:
            reply = await self._exchange.query(frame)
        except (BleakError, OSError, ConnectionLost):
            reply = None
        self.optional_reads[name] = {"outcome": "answered" if reply else "unanswered", "at": at}
        if reply is None:
            self.optional_read_failures += 1
        return reply

    async def _collect_optional(
        self, firmware: str | None, mode_report: protocol.ModeReport | None
    ) -> None:
        info = bluetooth.async_last_service_info(self._hass, self.address, connectable=True)
        adv = advertisement.parse_manufacturer_data(info.manufacturer_data) if info else None
        if adv:
            self.pact = (adv.pact_type, adv.pact_code)
        self.read_profile = readback.resolve(self.profile.model, self.pact, firmware)
        key = (self.profile.model, self.pact, firmware, self.read_profile.key)
        if key != self._identity_key:
            extra = {
                k: v
                for k, v in self.state.extra.items()
                if not k.startswith(("segment_", "pages_", "bulk_"))
            }
            self._set_state(extra=extra)
            self.optional_reads.clear()
            self._identity_key = key
        if self.read_profile.hardware and "hardware" not in self.optional_reads:
            reply = await self._optional_query("hardware", protocol.hardware_query_frame())
            if reply and reply.hardware:
                self._set_state(hardware=reply.hardware)
        form = self.read_profile.topology
        if form and "topology" not in self.optional_reads:
            command = {"ic_pair": 0x40, "ic_two_pairs": 0x40, "section": 0x41, "bulb": 0x0F}[form]
            reply = await self._optional_query(
                "topology", protocol.build_frame(command, frame_type=0xAA)
            )
            decoded = readback.topology(reply.raw, form) if reply else None
            self.optional_reads["topology"]["raw"] = reply.raw.hex() if reply else None
            self.optional_reads["topology"]["decoded"] = decoded
            if reply and decoded is None:
                self.optional_reads["topology"]["outcome"] = "invalid"
        if (
            self.read_profile.bulk
            and self.optional_reads.get("bulk", {}).get("outcome") != "unanswered"
        ):
            try:
                data = await self._exchange.query_bulk(
                    protocol.build_frame(0x03, [2, 0x41, 0x30], frame_type=0xAC)
                )
            except (BleakError, OSError, ConnectionLost):
                data = None
            self.optional_reads["bulk"] = {
                "outcome": "answered" if data is not None else "unanswered",
                "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            }
            extra = {
                "bulk_complete": data is not None,
                "bulk_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            }
            if data is not None:
                records, tail = readback.tlvs(data)
                extra.update(
                    bulk_raw=data.hex(),
                    bulk_tail=tail.hex(),
                    bulk_tlv_complete=not tail,
                    bulk_tlvs=[{"tag": tag, "value": value.hex()} for tag, value in records],
                )
                # Unknown TLVs remain diagnostic data. Empty mode is not mode zero.
                for tag, value in records:
                    if tag not in (0x01, 0x04, 0x05) or not value or len(value) > 17:
                        continue
                    if tag == 0x05 and len(value) < {0x02: 8, 0x0D: 6, 0x15: 4, 0x04: 3}.get(
                        value[0], 1
                    ):
                        continue  # A short TLV must not become zero-padded colour state.
                    reply = protocol.parse_reply(protocol.build_frame(tag, value, frame_type=0xAA))
                    if reply:
                        self._apply_partial_reply(reply)
            else:
                self.optional_read_failures += 1
            self._set_state(extra={**self.state.extra, **extra})
        layout = self.read_profile.pages
        if layout is None or self.optional_reads.get("colors", {}).get("outcome") == "unanswered":
            return
        # One bounded complete page set, only in static colour mode. Retain
        # partial pages as diagnostics, never assert whole-strip RGB from one.
        mode = mode_report.submode if mode_report else None
        if mode not in (0x0B, 0x15) or mode_report.color_temp_kelvin:
            return
        pages = {}
        for group in range(1, len(layout.counts) + 1):
            try:
                reply = await self._exchange.query(layout.query(group))
            except (BleakError, OSError, ConnectionLost):
                reply = None
            if reply is None:
                break
            records = layout.decode(reply.raw)
            if records is None:
                break
            pages[group] = [{"brightness": r.brightness, "rgb": r.rgb} for r in records]
        complete = len(pages) == len(layout.counts)
        self.optional_reads["colors"] = {
            "outcome": "answered" if complete else "unanswered",
            "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        }
        if not complete:
            self.optional_read_failures += 1
        extra = {
            **self.state.extra,
            "segment_pages": pages,
            "pages_complete": complete,
            "pages_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        }
        self._set_state(extra=extra)
        if complete:
            colors = [r["rgb"] for page in pages.values() for r in page]
            if len(set(colors)) == 1:
                self._set_state(
                    rgb_color=colors[0], white_mode=False, color_temp_kelvin=None, effect=None
                )
            else:
                self._set_state(
                    rgb_color=None, color_temp_kelvin=None, white_mode=False, effect=None
                )

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
            characteristic = client.services.get_characteristic(CONTROL_CHARACTERISTIC_UUID)
            if characteristic is None:
                raise ValueError("Govee control characteristic is missing")
            properties = tuple(characteristic.properties)
            self._exchange.connected(properties)
            response = self._exchange.write_response
        except (BleakError, ValueError) as err:
            await client.disconnect()
            raise GoveeConnectionError(
                f"Unsupported GATT transport for {self.name}: {err}"
            ) from err
        self.control_properties = properties
        self.write_response = response
        _LOGGER.debug(
            "%s control properties %s, write response=%s", self.name, properties, response
        )

        try:
            await client.start_notify(STATUS_CHARACTERISTIC_UUID, self._on_notify)
        except (BleakError, OSError) as err:
            # Not fatal: writes still work, we just lose read-back.
            _LOGGER.debug("Could not subscribe to %s notifications: %s", self.name, err)

        self._client = client
        self._firmware_checked = False
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
            self._in_flight = asyncio.current_task()
            await self._async_connect()
            try:
                for index, frame in enumerate(frames):
                    await self._exchange.write(frame)
                    if index < len(frames) - 1:
                        await asyncio.sleep(INTER_FRAME_DELAY)
            except (BleakError, TimeoutError, OSError, ConnectionLost) as err:
                await self._async_disconnect_locked()
                raise GoveeConnectionError(f"Command to {self.name} failed: {err}") from err
            finally:
                self._touch()

    async def _send_frame(self, frame: bytes, response: bool, note: str) -> None:
        client = self._client
        if client is None or not client.is_connected:
            raise ConnectionLost("link closed")
        _LOGGER.debug("%s -> %s (%s)", self.name, frame.hex(), note)
        self._record_frame("tx", frame, note)
        await client.write_gatt_char(CONTROL_CHARACTERISTIC_UUID, frame, response=response)

    @callback
    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        frame = bytes(data)
        _LOGGER.debug("%s <- %s", self.name, frame.hex())
        note, reply = self._exchange.receive(frame)
        if frame[:1] == b"\xee":
            push_reply = readback.push(frame, self.read_profile)
            if push_reply is not None:
                reply = push_reply
                note = "unsolicited"
        self._record_frame("rx", frame, note)
        if note == "ack":
            self.last_command_acceptance = datetime.now(UTC).isoformat(timespec="milliseconds")
        if note == "ack_error":
            self.command_rejections += 1
        if note != "unsolicited" or reply is None:
            return
        self._apply_partial_reply(reply)

    def _apply_partial_reply(self, reply: protocol.Reply) -> None:
        changes = {}
        if reply.power is not None:
            changes["is_on"] = reply.power
        if reply.brightness_level is not None:
            changes["brightness"] = protocol.device_to_ha_brightness(
                reply.brightness_level, self._read_brightness_scale
            )
        if reply.mode is not None:
            changes.update(self._changes_from_mode(reply.mode))
        if reply.brightness_level is not None:
            changes["extra"] = {
                **changes.get("extra", self.state.extra),
                "brightness_raw": reply.brightness_level,
            }
        if changes:
            self._set_state(**changes)
            if reply.mode:
                self._mark_mode_derivations(reply.mode, changes)

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
        self._exchange.disconnected()

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
                    await self._exchange.write(protocol.heartbeat_frame(), "heartbeat")
                except (BleakError, OSError, ConnectionLost) as err:
                    _LOGGER.debug("%s heartbeat failed: %s", self.name, err)
                    return
        except asyncio.CancelledError:
            raise


class _ProbeLink:
    """Adapt a connected client to :class:`probe.ProbeLink`."""

    def __init__(self, device: GoveeBleDevice, client: BleakClientWithServiceCache) -> None:
        self._device = device
        self._client = client

    async def write(self, frame: bytes) -> None:
        if not self._client.is_connected:
            raise probe.ProbeConnectionLost("link closed")
        try:
            await self._device._exchange.write(frame, "probe")
            await asyncio.sleep(INTER_FRAME_DELAY)
        except (BleakError, TimeoutError, OSError, ConnectionLost) as err:
            raise probe.ProbeConnectionLost(str(err)) from err

    async def query(self, frame: bytes, command: int) -> protocol.Reply | None:
        if not self._client.is_connected:
            raise probe.ProbeConnectionLost("link closed")
        try:
            return await self._device._exchange.query(frame)
        except (BleakError, OSError, ConnectionLost) as err:
            raise probe.ProbeConnectionLost(str(err)) from err
