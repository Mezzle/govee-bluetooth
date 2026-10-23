"""One connected light's frame exchange, independent of Bluetooth and Home Assistant.

Adapters supply the wire write and feed decrypted notifications. Connection lifetime,
heartbeat scheduling and presentation remain with the adapter. Queries are serialized;
a heartbeat power reply cannot be distinguished from an explicit power-query reply.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable

from . import protocol


class ConnectionLost(Exception):
    """The connection closed while exchanging frames; never retry implicitly."""


class FrameExchange:
    """Own write selection, request matching and pending-query lifetime."""

    def __init__(
        self,
        send: Callable[[bytes, bool, str], Awaitable[None]],
        timeout: float,
    ) -> None:
        self._send = send
        self._timeout = timeout
        self._queries = asyncio.Lock()
        self._pending: tuple[bytes, asyncio.Future[protocol.Reply]] | None = None
        self._generation = 0
        self._closed = False
        self.write_response = True
        self.encrypted = False

    def connected(self, properties: Iterable[str] | None) -> None:
        """Start a fresh connection using its advertised write procedures."""
        response = protocol.control_write_response(properties)
        self.disconnected()
        self.write_response = response
        self.encrypted = False
        self._closed = False

    def disconnected(self) -> None:
        """Fail active and queued queries, without cancelling their caller tasks."""
        self._closed = True
        self._generation += 1
        if self._pending is not None:
            _, future = self._pending
            self._pending = None
            if not future.done():
                future.set_exception(ConnectionLost("link closed"))

    async def write(self, frame: bytes, note: str = "command") -> None:
        if self._closed:
            raise ConnectionLost("link closed")
        await self._send(frame, self.write_response and not self.encrypted, note)

    async def query(self, frame: bytes, note: str = "query") -> protocol.Reply | None:
        generation = self._generation
        async with self._queries:
            if self._closed or generation != self._generation:
                raise ConnectionLost("link closed")
            future: asyncio.Future[protocol.Reply] = asyncio.get_running_loop().create_future()
            self._pending = (frame, future)
            try:
                await self.write(frame, note)
                return await asyncio.wait_for(future, self._timeout)
            except TimeoutError:
                return None
            finally:
                if self._pending is not None and self._pending[1] is future:
                    self._pending = None
                # A write can fail after the disconnect callback has failed the future.
                if future.done() and not future.cancelled():
                    future.exception()
                else:
                    future.cancel()

    def receive(self, frame: bytes) -> tuple[str, protocol.Reply | None]:
        """Validate and route a plaintext frame; return its presentation category."""
        if len(frame) == protocol.FRAME_LENGTH and not protocol.verify_frame(frame):
            return "bad_checksum", None
        if len(frame) == protocol.FRAME_LENGTH and frame[0] == protocol.TYPE_WRITE:
            return ("ack" if frame[2] == 0 else "ack_error"), None
        reply = protocol.parse_reply(frame)
        if reply is None:
            return "unparsed", None
        if self._pending is not None:
            request, future = self._pending
            if not future.done() and protocol.reply_matches(request, frame):
                future.set_result(reply)
                return "reply", reply
        return "unsolicited", reply
