"""Shared fixtures: a running Home Assistant with a mocked Bluetooth stack."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Generator
from unittest.mock import patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError
from habluetooth import get_manager
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.govee_bluetooth import protocol

ADDRESS = "A4:C1:38:F5:02:DB"
LOCAL_NAME = "ihoment_H6159_02DB"
SCANNER_SOURCE = "00:00:00:00:00:01"  # the mocked hci0 adapter


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ from this repo."""


@pytest.fixture
async def bluetooth_ready(hass: HomeAssistant, mock_bluetooth: None) -> None:
    """Start the bluetooth integration against the mocked adapter."""
    assert await async_setup_component(hass, "bluetooth", {})
    await hass.async_block_till_done()


def make_service_info(
    address: str = ADDRESS, name: str = LOCAL_NAME, connectable: bool = True
) -> BluetoothServiceInfoBleak:
    device = BLEDevice(address, name, {"source": SCANNER_SOURCE})
    adv = AdvertisementData(
        local_name=name,
        manufacturer_data={0xEC88: b"\x00\x01\x02"},
        service_data={},
        service_uuids=[],
        rssi=-60,
        tx_power=-127,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak.from_device_and_advertisement_data(
        device, adv, SCANNER_SOURCE, time.monotonic(), connectable
    )


def inject_advertisement(hass: HomeAssistant, info: BluetoothServiceInfoBleak) -> None:
    """Feed an advertisement into Home Assistant's bluetooth manager."""
    get_manager().scanner_adv_received(info)


class _FakeCharacteristic:
    def __init__(self, properties: tuple[str, ...]) -> None:
        self.properties = list(properties)


class _FakeServices:
    def __init__(self, client: FakeGoveeClient) -> None:
        self._client = client

    def get_characteristic(self, uuid: str) -> _FakeCharacteristic | None:
        if uuid.endswith("2b11"):
            return _FakeCharacteristic(self._client.control_properties)
        if uuid.endswith("2b10"):
            return _FakeCharacteristic(("read", "notify"))
        return None


class FakeGoveeClient:
    """Stand-in for a BleakClient connected to an H6159.

    Records every write and answers ``AA`` read requests with canned frames by
    calling the notification handler, the same way real firmware would.
    """

    def __init__(
        self,
        *,
        power: bool = True,
        brightness: int = 0x80,
        rgb=(16, 32, 48),
        speaks: int | None = protocol.SUBMODE_COLOR_LEGACY,
        fail_after_writes: int | None = None,
        control_properties: tuple[str, ...] = ("write", "write-without-response"),
    ) -> None:
        self.is_connected = True
        self.writes: list[bytes] = []
        self.write_responses: list[bool | None] = []
        # What the control characteristic offers. The H6006 bulb on the bench
        # offers ("read", "write-without-response") and refuses a write request.
        self.control_properties = control_properties
        self.services = _FakeServices(self)
        self.disconnects = 0
        self._notify: Callable | None = None
        # The colour sub-mode this firmware understands. A ``33 05 <speaks>``
        # write updates the mode reply the way real firmware echoes it; writes
        # in any other sub-mode are ignored, which is what both tested lights
        # do with frames they do not understand.
        self.speaks = speaks
        # Simulate the link dropping after this many command writes.
        self.fail_after_writes = fail_after_writes
        self.replies: dict[int, bytes] = {
            protocol.CMD_POWER: protocol.build_frame(
                protocol.CMD_POWER, [int(power)], frame_type=protocol.TYPE_READ
            ),
            protocol.CMD_BRIGHTNESS: protocol.build_frame(
                protocol.CMD_BRIGHTNESS, [brightness], frame_type=protocol.TYPE_READ
            ),
            protocol.CMD_MODE: protocol.build_frame(
                protocol.CMD_MODE,
                [protocol.SUBMODE_COLOR_LEGACY, *rgb, 0x00],
                frame_type=protocol.TYPE_READ,
            ),
            protocol.CMD_VERSION: protocol.build_frame(
                protocol.CMD_VERSION, list(b"1.00.17"), frame_type=protocol.TYPE_READ
            ),
        }

    async def start_notify(self, _uuid: str, handler: Callable) -> None:
        self._notify = handler

    async def write_gatt_char(self, _uuid: str, data: bytes, response: bool | None = None) -> None:
        frame = bytes(data)
        if not self.is_connected:
            raise BleakError("not connected")
        if response and "write" not in self.control_properties:
            # What the H6006 did on the bench: ATT error, then it drops the link.
            self.is_connected = False
            raise BleakError("GATT Protocol Error: Write Not Permitted")
        if response is False and "write-without-response" not in self.control_properties:
            raise BleakError("write without response unsupported")
        self.writes.append(frame)
        self.write_responses.append(response)
        if frame[0] == protocol.TYPE_WRITE:
            if self.fail_after_writes is not None and len(self.commands) > self.fail_after_writes:
                self.is_connected = False
                raise BleakError("link dropped")
            if frame[1] == protocol.CMD_MODE and frame[2] == self.speaks:
                if frame[2] == protocol.SUBMODE_COLOR_RGBIC:
                    # ``33 05 15 01 FF FF FF kHi kLo ...`` -> ``AA 05 15 01 kHi kLo``
                    self.set_mode(frame[2], (frame[3], frame[7], frame[8]))
                else:
                    self.set_mode(frame[2], frame[3:6])
        if frame[0] == protocol.TYPE_READ and self._notify is not None:
            reply = self.replies.get(frame[1])
            if reply is not None:
                asyncio.get_running_loop().call_soon(self._notify, 0, bytearray(reply))

    async def disconnect(self) -> None:
        self.is_connected = False
        self.disconnects += 1

    def set_mode(self, submode: int, payload: bytes | tuple[int, ...] = ()) -> None:
        """Change what the ``AA 05`` query reports."""
        self.replies[protocol.CMD_MODE] = protocol.build_frame(
            protocol.CMD_MODE, [submode, *payload], frame_type=protocol.TYPE_READ
        )

    @property
    def commands(self) -> list[bytes]:
        """Writes that are commands (``0x33``), ignoring reads and heartbeats."""
        return [w for w in self.writes if w[0] == protocol.TYPE_WRITE]


@pytest.fixture
def fake_client() -> Generator[FakeGoveeClient]:
    client = FakeGoveeClient()

    async def _establish(_cls, _device, _name, **_kwargs):
        client.is_connected = True
        return client

    with patch(
        "custom_components.govee_bluetooth.device.establish_connection", side_effect=_establish
    ):
        yield client
