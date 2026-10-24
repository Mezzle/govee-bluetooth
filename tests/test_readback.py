"""Offline source contracts; synthetic packets do not qualify physical devices."""

import asyncio
from dataclasses import replace

import pytest

from custom_components.govee_bluetooth import protocol, readback
from custom_components.govee_bluetooth.exchange import ConnectionLost, FrameExchange


def test_profile_qualification_and_disputes():
    assert readback.resolve("H617A", (10, 1), "3.02.24").pages
    for model, pact, firmware in (
        ("H617A", None, "3.02.24"),
        ("H617A", (1, 1), "3.02.24"),
        ("H617A", (10, 1), "future"),
        ("H6047", (1, 1), "any"),
        ("H6641", (10, 1), "any"),
    ):
        assert readback.resolve(model, pact, firmware).pages is None


@pytest.mark.parametrize("command,width,count", [(0xA2, 3, 4), (0xA5, 4, 3), (0xA5, 4, 4)])
def test_explicit_page_geometry_keeps_black(command, width, count):
    layout = readback.PageLayout(command, (count, 1), width)
    record = [0, 12, 34, 56] if width == 4 else [12, 34, 56]
    frame = protocol.build_frame(
        command, [1, *([0] * width), *(record * (count - 1))], frame_type=0xAA
    )
    records = layout.decode(frame)
    assert len(records) == count
    assert records[0].rgb == (0, 0, 0)
    assert records[1].rgb == (12, 34, 56)
    assert records[1].brightness == (0 if width == 4 else None)
    assert len(layout.decode(protocol.build_frame(command, [2], frame_type=0xAA))) == 1
    assert layout.decode(protocol.build_frame(command, [3], frame_type=0xAA)) is None
    with pytest.raises(ValueError):
        layout.query(3)
    bad = bytearray(frame)
    bad[-1] ^= 1
    assert layout.decode(bad) is None


def test_signed_topology_and_collisions():
    frame = protocol.build_frame(0x40, [0x01, 0x2C, 0, 0x00, 0x64, 5], frame_type=0xAA)
    assert readback.topology(frame, "ic_pair") == {"pairs": [{"ic_count": 300, "segment_count": 0}]}
    assert len(readback.topology(frame, "ic_two_pairs")["pairs"]) == 2
    assert (
        readback.topology(protocol.build_frame(0x40, [255, 255, 1], frame_type=0xAA), "ic_pair")
        is None
    )
    assert (
        readback.topology(protocol.build_frame(0x41, [0, 12, 4], frame_type=0xAA), "section")[
            "pairs"
        ][0]["section_count"]
        == 4
    )
    assert readback.topology(frame, "section") is None
    assert readback.topology(frame, "unknown") is None


def burst(data=b"abcdefghijklmnopqrst", final_tag=255):
    assert 12 <= len(data) <= 29
    return [
        protocol.build_frame(0, [2, 0, len(data) - 12, 3, 0x41, *data[:12]], frame_type=0xAC),
        protocol.build_frame(final_tag, data[12:], frame_type=0xAC),
    ]


@pytest.mark.parametrize("tag", [255, 1])
def test_bulk_final_contains_data(tag):
    assembler = readback.BulkAssembler()
    first, last = burst(final_tag=tag)
    assert assembler.feed(first) is None
    assert assembler.feed(last) == b"abcdefghijklmnopqrst"
    assert assembler.feed(last) is None
    assert assembler.feed(
        protocol.build_frame(0, [1, 0, 3, 3, 0x41, 1, 2, 3], frame_type=0xAC)
    ) == bytes([1, 2, 3])


def test_bulk_rejects_missing_corrupt_and_unbounded():
    assembler = readback.BulkAssembler()
    first, last = burst()
    assert assembler.feed(last) is None
    assembler.feed(first)
    assert assembler.feed(protocol.build_frame(2, [], frame_type=0xAC)) is None
    assert assembler.feed(last) is None
    assert assembler.feed(protocol.build_frame(0, [255, 255, 17], frame_type=0xAC)) is None
    assembler.feed(first)
    bad = bytearray(last)
    bad[-1] ^= 1
    assert assembler.feed(bad) is None
    assert assembler.feed(last) is None
    records, tail = readback.tlvs(bytes([1, 1, 1, 5, 0, 0xFE, 2, 3, 4, 7, 5, 9]))
    assert records == [(1, b"\x01"), (5, b""), (254, b"\x03\x04")]
    assert tail == bytes([7, 5, 9])


def test_push_masks_are_profile_specific():
    profile = readback.resolve("H60A6", None, None)
    event = protocol.build_frame(0x30, [7, 2, 1, 0], frame_type=0xEE)
    assert readback.push(event, profile).power is False  # bit 1 is not main power
    assert readback.push(event, replace(profile, ee30=None)) is None
    event = protocol.build_frame(0x20, [0], frame_type=0xEE)
    assert readback.push(event, profile).brightness_level == 0
    assert readback.push(event, readback.resolve("H6159", None, None)) is None


async def test_bulk_exchange_timeout_and_connection_scope():
    async def send(frame, response, note):
        pass

    exchange = FrameExchange(send, 0.01)
    exchange.connected(["write"])
    request = protocol.build_frame(3, [2, 0x41, 0x30], frame_type=0xAC)
    task = asyncio.create_task(exchange.query_bulk(request))
    await asyncio.sleep(0)
    first, last = burst()
    exchange.receive(first)
    exchange.receive(last)
    assert await task == b"abcdefghijklmnopqrst"
    assert await exchange.query_bulk(request) is None
    assert exchange.receive(last)[0] == "unsolicited_bulk"
    task = asyncio.create_task(exchange.query_bulk(request))
    await asyncio.sleep(0)
    exchange.receive(first)
    exchange.disconnected()
    with pytest.raises(ConnectionLost):
        await task
    exchange.connected(["write"])
    task = asyncio.create_task(exchange.query_bulk(request))
    await asyncio.sleep(0)
    exchange.receive(last)
    assert await task is None
