"""Pi adapter behavior through the connected frame exchange."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from .test_rig_scale import rig as rig


@pytest.mark.parametrize("response", [True, False])
async def test_prompt_heartbeat_uses_selected_procedure(rig, monkeypatch, response):
    released = threading.Event()

    def read_line(_prompt):
        assert released.wait(2)
        return "yes"

    async def send(*_args, **_kwargs):
        released.set()

    link = rig.RigLink("AA:BB", rig.FrameLog())
    link.client = SimpleNamespace(is_connected=True, write_gatt_char=AsyncMock(side_effect=send))
    link._exchange.connected(["write"] if response else ["write-without-response"])
    monkeypatch.setattr(rig, "HEARTBEAT_INTERVAL", 0.001)
    monkeypatch.setattr(rig, "_read_line", read_line)
    try:
        assert await rig.prompt_keeping_alive(link, "question") == "yes"
    finally:
        released.set()
    assert link.client.write_gatt_char.await_args.kwargs["response"] is response
    assert link.log.records[-1]["note"] == "heartbeat"


async def test_rig_disconnect_reports_connection_loss_and_can_query_again(rig):
    link = rig.RigLink("AA:BB", rig.FrameLog())
    sent = asyncio.Queue()

    async def send(*_args, **_kwargs):
        await sent.put(True)

    link.client = SimpleNamespace(is_connected=True, write_gatt_char=AsyncMock(side_effect=send))
    query = asyncio.create_task(link.query(rig.protocol.hardware_query_frame(), 7))
    await sent.get()
    link._on_disconnected(link.client)
    with pytest.raises(rig.probe.ProbeConnectionLost):
        await query
    link._exchange.connected(["write"])
    query = asyncio.create_task(link.query(rig.protocol.power_query_frame(), 1))
    await sent.get()
    link._on_notify(None, bytearray(rig.protocol.build_frame(1, [1], frame_type=0xAA)))
    assert (await query).power is True
