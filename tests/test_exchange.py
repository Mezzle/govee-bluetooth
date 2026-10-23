"""Behavior at the frame-exchange interface, without Bluetooth."""

import asyncio

import pytest

from custom_components.govee_bluetooth import protocol
from custom_components.govee_bluetooth.exchange import ConnectionLost, FrameExchange


class Wire:
    def __init__(self):
        self.writes = []
        self.sent = asyncio.Queue()

    async def send(self, frame, response, note):
        self.writes.append((frame, response, note))
        await self.sent.put(frame)


def reply(command, *payload):
    return protocol.build_frame(command, payload, frame_type=protocol.TYPE_READ)


async def test_request_matching_and_query_serialization():
    wire = Wire()
    link = FrameExchange(wire.send, 1)
    hardware = protocol.hardware_query_frame()
    first = asyncio.create_task(link.query(hardware))
    await wire.sent.get()
    second = asyncio.create_task(link.query(protocol.power_query_frame()))
    await asyncio.sleep(0)
    assert len(wire.writes) == 1
    wrong = reply(hardware[1], hardware[2] ^ 1)
    assert link.receive(wrong)[0] == "unsolicited"
    assert not first.done()
    assert link.receive(reply(hardware[1], hardware[2], 1, 2, 3))[0] == "reply"
    assert await first is not None
    await wire.sent.get()
    link.receive(reply(protocol.CMD_POWER, 1))
    assert (await second).power is True


async def test_timeout_and_cancellation_release_query_slot():
    wire = Wire()
    link = FrameExchange(wire.send, 0.01)
    assert await link.query(protocol.power_query_frame()) is None
    assert link.receive(reply(protocol.CMD_POWER, 1))[0] == "unsolicited"
    await wire.sent.get()
    pending = asyncio.create_task(link.query(protocol.power_query_frame()))
    await wire.sent.get()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    next_query = asyncio.create_task(link.query(protocol.power_query_frame()))
    await wire.sent.get()
    link.receive(reply(protocol.CMD_POWER, 0))
    assert (await next_query).power is False


async def test_disconnect_fails_active_and_queued_queries_even_after_reconnect():
    wire = Wire()
    link = FrameExchange(wire.send, 1)
    first = asyncio.create_task(link.query(protocol.power_query_frame()))
    await wire.sent.get()
    second = asyncio.create_task(link.query(protocol.brightness_query_frame()))
    await asyncio.sleep(0)
    link.disconnected()
    link.connected(["write-without-response"])
    for task in (first, second):
        with pytest.raises(ConnectionLost):
            await task
    assert len(wire.writes) == 1
    assert link.receive(reply(protocol.CMD_POWER, 1))[0] == "unsolicited"
    await link.write(protocol.power_frame(True))
    assert wire.writes[-1][1] is False


@pytest.mark.parametrize(
    "properties,response", [(["write"], True), (["write-without-response"], False)]
)
async def test_every_write_uses_connection_procedure_and_cipher_override(properties, response):
    wire = Wire()
    link = FrameExchange(wire.send, 0.001)
    link.connected(properties)
    await link.write(protocol.power_frame(True))
    await link.write(protocol.heartbeat_frame(), "heartbeat")
    await link.query(protocol.power_query_frame())
    assert all(entry[1] is response for entry in wire.writes)
    link.encrypted = True
    await link.write(protocol.heartbeat_frame(), "heartbeat")
    assert wire.writes[-1][1] is False
    link.disconnected()
    with pytest.raises(ConnectionLost):
        await link.write(protocol.power_frame(False))


def test_invalid_frames_and_acks_cannot_complete_queries():
    link = FrameExchange(Wire().send, 1)
    frame = reply(protocol.CMD_POWER, 1)
    assert link.receive(frame[:-1] + bytes([frame[-1] ^ 1])) == ("bad_checksum", None)
    assert link.receive(protocol.power_frame(True)) == ("ack_error", None)
