#!/usr/bin/env python3
"""Bench test rig for Govee BLE lights: bleak on BlueZ, no Home Assistant.

Imports ``protocol``, ``models`` and ``probe`` straight from this repo's
``custom_components/govee_bluetooth`` so the frames it sends are the ones the
integration sends. The package's ``__init__`` needs Home Assistant, so the
modules are loaded as submodules of a stub package instead (see
:func:`load_integration`).

Subcommands::

    scan                      list Govee lights with model, pact, encryption flag
    status ADDR [--active]    the four status reads (and the active probe)
    census ADDR [--probe]     the shared read battery; govee-evidence/1 records
    send ADDR CMD...          named or raw frames, every TX/RX printed
    verify ADDR CMD...        send, read back, report whether the light echoed
    shell ADDR                one connection, commands typed interactively

``--log FILE`` writes every frame as JSON lines in the shape the Dance Party
census and the HA diagnostics use; pairing secrets are redacted unless
``--keep-secrets``. ``--cipher v1`` runs the AES-ECB+RC4 handshake first
(experimental; see docs/encrypted-protocol-matrix.md).

Run ``govee_rig.py --help`` or ``govee_rig.py send --help`` for the command
vocabulary. Python 3.11+, ``pip install bleak``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import json
import logging
import platform
import shlex
import subprocess
import sys
import types
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError
except ImportError:  # pragma: no cover - import-time help for the bench
    sys.exit("bleak is not installed: python3 -m pip install bleak")

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "custom_components" / "govee_bluetooth"

# Mirror device.py so bench timings match what Home Assistant does.
HEARTBEAT_INTERVAL = 2.0
QUERY_TIMEOUT = 2.5
INTER_FRAME_DELAY = 0.06
SETTLE_BEFORE_READBACK = 0.25

_LOGGER = logging.getLogger("govee_rig")


# ---------------------------------------------------------------------------
# Loading the integration's pure modules without Home Assistant
# ---------------------------------------------------------------------------


def load_integration() -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    """Return ``(const, protocol, models, probe, census, advertisement, cipher)`` from the checkout.

    Registers ``custom_components`` and ``custom_components.govee_bluetooth``
    as namespace-style stub packages whose ``__path__`` points at the repo, so
    the relative ``from . import protocol`` inside ``probe`` resolves and the
    real ``__init__`` (which imports homeassistant) never runs.
    """
    if not PACKAGE_DIR.is_dir():
        sys.exit(f"integration not found at {PACKAGE_DIR}; run from a repo checkout")
    for name, path in (
        ("custom_components", REPO_ROOT / "custom_components"),
        ("custom_components.govee_bluetooth", PACKAGE_DIR),
    ):
        if name not in sys.modules:
            stub = types.ModuleType(name)
            stub.__path__ = [str(path)]  # type: ignore[attr-defined]
            sys.modules[name] = stub
    base = "custom_components.govee_bluetooth."
    return tuple(
        importlib.import_module(base + m)
        for m in ("const", "protocol", "models", "probe", "census", "advertisement", "cipher")
    )


const, protocol, models, probe, census, advertisement, cipher = load_integration()

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blinkt  # noqa: E402  (tools/blinkt.py, next to this script)

# The Blinkt status strip; a no-op unless --blinkt opened it.
LIGHTS: blinkt.NullBlinkt = blinkt.NullBlinkt()


def _frame_colour(frame: bytes) -> tuple[int, int, int] | None:
    """The RGB a colour write carries, for mirroring on the Blinkt."""
    if len(frame) < 8 or frame[0] != protocol.TYPE_WRITE or frame[1] != protocol.CMD_MODE:
        return None
    if frame[2] == protocol.SUBMODE_COLOR_RGBIC:
        return (frame[4], frame[5], frame[6])
    if frame[2] in (
        protocol.SUBMODE_COLOR_LEGACY,
        protocol.SUBMODE_COLOR_RGBWW,
        protocol.SUBMODE_COLOR_DREAMCOLOR,
    ):
        if (
            frame[3:6] == b"\x00\x00\x00"
            and frame[2] == protocol.SUBMODE_COLOR_RGBWW
            and any(frame[6:8])
        ):
            return (255, 180, 110)  # a 0x0D white: show warm white, not black
        return (frame[3], frame[4], frame[5])
    return None


def _lights_for(direction: str, frame: bytes, note: str) -> None:
    if note == "heartbeat":
        return
    if direction == "tx":
        colour = _frame_colour(frame)
        if colour is not None:
            LIGHTS.fill(blinkt.visible(colour))
        else:
            LIGHTS.flash(7, (60, 60, 60))
        return
    if note == "reply":
        LIGHTS.flash(6, (0, 0, 255))
    elif note in ("ack", "ack_error"):
        LIGHTS.flash(5, (0, 255, 0) if note == "ack" else (255, 80, 0))
    elif note == "bad_checksum":
        LIGHTS.flash(6, (255, 0, 255))
    else:
        LIGHTS.flash(6, (0, 40, 80))


def software_version() -> str:
    try:
        sha = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        # A copied checkout without .git (the Pi bench) records its commit here.
        marker = REPO_ROOT / ".rig-commit"
        sha = marker.read_text().strip() if marker.is_file() else "unknown"
    return f"govee_rig.py {sha}"


# ---------------------------------------------------------------------------
# Frame pretty-printing (rig-only; the wire format lives in protocol.py)
# ---------------------------------------------------------------------------

TYPE_NAMES = {
    protocol.TYPE_WRITE: "write",
    protocol.TYPE_READ: "read",
    protocol.TYPE_NOTIFY: "notify",
}
CMD_NAMES = {
    protocol.CMD_POWER: "power",
    protocol.CMD_BRIGHTNESS: "brightness",
    protocol.CMD_MODE: "mode",
    protocol.CMD_VERSION: "version",
    protocol.CMD_DEVICE_INFO: "info",
    protocol.CMD_IC_COUNT: "icnum",
    protocol.CMD_LIGHT_COUNT: "lightnum",
    protocol.CMD_SEGMENT_COLORS: "segcolour",
    protocol.CMD_SECRET_READ: "secret",
    protocol.CMD_SECRET_WRITE: "secretwrite",
}
SUBMODE_ALIASES = {
    "02": protocol.SUBMODE_COLOR_LEGACY,
    "0b": protocol.SUBMODE_COLOR_DREAMCOLOR,
    "0d": protocol.SUBMODE_COLOR_RGBWW,
    "15": protocol.SUBMODE_COLOR_RGBIC,
}


def submode_label(submode: int) -> str:
    if submode == protocol.SUBMODE_COLOR_DREAMCOLOR:
        return "colour (0x0B, dreamcolor dialect)"
    return probe.submode_name(submode)


def describe_tx(frame: bytes) -> str:
    """One-line human reading of an outgoing frame."""
    kind = TYPE_NAMES.get(frame[0], f"type {frame[0]:#04x}")
    cmd = CMD_NAMES.get(frame[1], f"cmd {frame[1]:#04x}")
    payload = frame[2:19]
    if frame[0] == protocol.TYPE_READ:
        return f"{kind} {cmd}" + (" (heartbeat)" if frame[1] == protocol.CMD_POWER else "")
    if frame[1] == protocol.CMD_POWER:
        return f"{kind} power {'on' if payload[0] else 'off'}"
    if frame[1] == protocol.CMD_BRIGHTNESS:
        return f"{kind} brightness level {payload[0]}"
    if frame[1] == protocol.CMD_MODE:
        sub = payload[0]
        if sub == protocol.SUBMODE_SCENE:
            return f"{kind} scene {payload[1]:#04x}"
        if sub in SUBMODE_ALIASES.values():
            return f"{kind} {submode_label(sub)}"
        return f"{kind} mode sub-mode {sub:#04x}"
    return f"{kind} {cmd}"


def describe_rx(frame: bytes) -> str:
    """Decode an incoming frame through protocol.parse_reply."""
    if not protocol.verify_frame(frame):
        return f"BAD CHECKSUM or length {len(frame)}"
    reply = protocol.parse_reply(frame)
    if reply is None:
        kind = TYPE_NAMES.get(frame[0], f"type {frame[0]:#04x}")
        return f"{kind} cmd {frame[1]:#04x} (not a read reply, undecoded)"
    if reply.power is not None:
        return f"power {'on' if reply.power else 'off'}"
    if reply.brightness_level is not None:
        return f"brightness level {reply.brightness_level}"
    if reply.version is not None:
        return f"firmware {reply.version}"
    if reply.mode is not None:
        return "mode " + describe_mode(reply.mode)
    if reply.hardware is not None:
        return f"hardware {reply.hardware}"
    if reply.ic_count is not None:
        return f"ic {reply.ic_count} segments {reply.segments}"
    if reply.light_count is not None:
        return f"lights {reply.light_count}"
    if reply.secret_present is not None:
        return "secret " + ("present" if reply.secret_present else "absent")
    if reply.segment_records is not None:
        return f"segcolour group {reply.segment_group}, {len(reply.segment_records)} records"
    return f"reply to cmd {reply.command:#04x} (no decoder)"


def describe_mode(mode: Any) -> str:
    parts = [submode_label(mode.submode)]
    if mode.scene_id is not None:
        parts.append(f"scene {mode.scene_id:#04x}")
    if mode.rgb is not None:
        parts.append("rgb " + " ".join(f"{v:3d}" for v in mode.rgb))
    if mode.color_temp_kelvin is not None:
        parts.append(f"{mode.color_temp_kelvin} K")
    if mode.is_white:
        parts.append("white")
    return ", ".join(parts)


def hexs(frame: bytes) -> str:
    return frame.hex()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------
# The link: one connected light, heartbeat, frame log, probe.ProbeLink
# ---------------------------------------------------------------------------


@dataclass
class FrameLog:
    """Prints every frame and optionally appends JSON lines (govee-evidence/1 frame records).

    Pairing secrets (``AA B1 01 ...`` replies, ``33 B2`` writes) are zeroed in
    the written record unless ``keep_secrets``; the console shows them.
    """

    path: Path | None = None
    address: str = ""
    quiet_heartbeat: bool = True
    keep_secrets: bool = False
    records: list[dict[str, Any]] = field(default_factory=list)

    def write_line(self, entry: dict[str, Any]) -> None:
        self.records.append(entry)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")

    def session(self, **fields: Any) -> None:
        """A ``{"summary":"session"}`` header so the report knows who gathered what."""
        self.write_line(
            {"summary": "session", "schema": census.SCHEMA, "source": "pi", "at": now_iso(),
             "software": software_version(), "host": platform.node(), **fields}
        )  # fmt: skip

    def record(self, direction: str, frame: bytes, note: str) -> None:
        decoded = describe_tx(frame) if direction == "tx" else describe_rx(frame)
        stored, redacted = (frame, False) if self.keep_secrets else protocol.redact_frame(frame)
        entry: dict[str, Any] = {
            "at": now_iso(),
            "direction": direction,
            "frame": hexs(stored),
            "note": note,
            "decoded": decoded,
            "address": self.address,
        }
        if redacted:
            entry["redacted"] = True
        self.write_line(entry)
        _lights_for(direction, frame, note)
        if self.quiet_heartbeat and note == "heartbeat":
            return
        stamp = entry["at"][11:23]
        arrow = "TX" if direction == "tx" else "RX"
        print(f"{stamp} {arrow} {hexs(frame)}  {note:<11} {decoded}")


class RigLink:
    """A connected Govee light. Implements probe.ProbeLink."""

    def __init__(
        self,
        address: str,
        log: FrameLog,
        adapter: str | None = None,
        cipher_version: str | None = None,
    ) -> None:
        self.address = address.upper()
        self.log = log
        self.adapter = adapter
        self.cipher_version = cipher_version
        self.session: Any = None  # cipher.V1Session once the handshake is done
        self.client: BleakClient | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._pending_request: dict[int, bytes] = {}
        self._heartbeat: asyncio.Task[None] | None = None
        self._busy = asyncio.Lock()
        self.unsolicited: list[Any] = []
        self.acks = 0
        self.bad_frames = 0
        self.bgc: bytes | None = None
        self.write_response = True  # set from the control characteristic's properties on connect
        self.ack_log: list[tuple[int, int]] = []  # (cmd, status byte) of every 33 xx ack, in order

    async def __aenter__(self) -> RigLink:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.disconnect()

    async def connect(self, timeout: float = 20.0) -> None:
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "disconnected_callback": self._on_disconnected,
        }
        if self.adapter:
            kwargs["adapter"] = self.adapter
        print(f"connecting to {self.address} ...", flush=True)
        self.client = BleakClient(self.address, **kwargs)
        await self.client.connect()
        try:
            await self.client.start_notify(const.STATUS_CHARACTERISTIC_UUID, self._on_notify)
        except BleakError as err:
            print(f"warning: could not subscribe to status notifications: {err}")
        control = self.client.services.get_characteristic(const.CONTROL_CHARACTERISTIC_UUID)
        props = list(control.properties) if control is not None else []
        self.write_response = protocol.control_write_response(props)
        print(
            f"connected (mtu {getattr(self.client, 'mtu_size', '?')}); 2b11 {props or 'not found'}, "
            f"writing {'with' if self.write_response else 'without'} response",
            flush=True,
        )
        LIGHTS.set_base(0, (0, 80, 0))
        await self._read_bgc()
        if self.cipher_version == "v1":
            await self._handshake_v1()
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())

    async def _read_bgc(self) -> None:
        """The BGC-info characteristic (2b12): where the app reads encryptVersion."""
        assert self.client is not None
        if self.client.services.get_characteristic(cipher.BGC_CHARACTERISTIC_UUID) is None:
            return
        try:
            self.bgc = bytes(await self.client.read_gatt_char(cipher.BGC_CHARACTERISTIC_UUID))
        except (BleakError, OSError, TimeoutError) as err:
            print(f"warning: 2b12 read failed: {err}")
            return
        version, rule = cipher.bgc_encrypt_version(self.bgc)
        print(f"2b12 (BGC info): {self.bgc.hex()} -> encryptVersion {version} ({rule})")

    async def _handshake_v1(self) -> None:
        """AES-ECB+RC4 session: E7 01 under the PSK, then E7 02. Experimental."""
        assert self.client is not None
        loop = asyncio.get_running_loop()
        reply: asyncio.Future[bytes] = loop.create_future()
        self._raw_hook = lambda data: (not reply.done()) and reply.set_result(data)
        step1 = cipher.v1_build_step1()
        self.log.record("tx", step1, "handshake")
        await self.client.write_gatt_char(const.CONTROL_CHARACTERISTIC_UUID, step1, response=False)
        try:
            data = await asyncio.wait_for(reply, QUERY_TIMEOUT)
        except TimeoutError:
            self._raw_hook = None
            raise probe.ProbeConnectionLost("no reply to the E7 01 handshake request") from None
        key = cipher.v1_parse_session_key(data)
        self.log.record("rx", bytes(data)[:20], "handshake")
        if key is None:
            self._raw_hook = None
            raise probe.ProbeConnectionLost("E7 01 reply did not decrypt under the PSK")
        self.session = cipher.V1Session(key)
        print("cipher v1: session key negotiated (not logged)")
        ack: asyncio.Future[bytes] = loop.create_future()
        self._raw_hook = lambda data: (not ack.done()) and ack.set_result(data)
        step2 = cipher.v1_build_step2()
        self.log.record("tx", step2, "handshake")
        await self.client.write_gatt_char(const.CONTROL_CHARACTERISTIC_UUID, step2, response=False)
        try:
            data = await asyncio.wait_for(ack, 1.0)
            print(
                "cipher v1: E7 02 "
                + ("acked" if cipher.v1_is_confirm_ack(data) else "answered (not an ack)")
            )
        except TimeoutError:
            print("cipher v1: no E7 02 ack (the H600B capture shows none is required)")
        self._raw_hook = None

    _raw_hook: Callable[[bytes], Any] | None = None

    def _wrap(self, frame: bytes) -> bytes:
        return self.session.wrap(frame) if self.session is not None else frame

    def _unwrap(self, data: bytes) -> bytes:
        return self.session.unwrap(data) if self.session is not None else bytes(data)

    async def disconnect(self) -> None:
        LIGHTS.set_base(0, (0, 0, 0))
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat
            self._heartbeat = None
        if self.client is not None and self.client.is_connected:
            with contextlib.suppress(BleakError, OSError):
                await self.client.disconnect()
        self.client = None

    @property
    def connected(self) -> bool:
        return self.client is not None and self.client.is_connected

    # probe.ProbeLink ---------------------------------------------------

    async def write(self, frame: bytes, note: str = "command") -> None:
        if not self.connected:
            raise probe.ProbeConnectionLost("link closed")
        assert self.client is not None
        self.log.record("tx", frame, note)
        try:
            await self.client.write_gatt_char(
                const.CONTROL_CHARACTERISTIC_UUID,
                self._wrap(frame),
                response=self.write_response and self.session is None,
            )
            await asyncio.sleep(INTER_FRAME_DELAY)
        except (BleakError, TimeoutError, OSError) as err:
            raise probe.ProbeConnectionLost(str(err)) from err

    async def query(self, frame: bytes, command: int, note: str = "query") -> Any:
        if not self.connected:
            raise probe.ProbeConnectionLost("link closed")
        assert self.client is not None
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[command] = future
        self._pending_request[command] = frame
        try:
            self.log.record("tx", frame, note)
            await self.client.write_gatt_char(
                const.CONTROL_CHARACTERISTIC_UUID,
                self._wrap(frame),
                response=self.write_response and self.session is None,
            )
            return await asyncio.wait_for(future, QUERY_TIMEOUT)
        except TimeoutError:
            LIGHTS.flash(6, (255, 0, 0), 0.3)
            print(
                f"            -- no reply to {CMD_NAMES.get(command, hex(command))} query within {QUERY_TIMEOUT}s"
            )
            return None
        except (BleakError, OSError) as err:
            raise probe.ProbeConnectionLost(str(err)) from err
        finally:
            if self._pending.get(command) is future:
                del self._pending[command]
                self._pending_request.pop(command, None)

    # internals ----------------------------------------------------------

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        if self._raw_hook is not None:
            self._raw_hook(bytes(data))
            return
        frame = self._unwrap(bytes(data))
        if len(frame) == protocol.FRAME_LENGTH and frame[0] == protocol.TYPE_WRITE:
            # The firmware's own acknowledgement of a write: 33 <cmd> with byte 2 == 0.
            self.log.record("rx", frame, "ack" if frame[2] == 0 else "ack_error")
            self.ack_log.append((frame[1], frame[2]))
            if frame[2] == 0:
                self.acks += 1
            return
        if len(frame) == protocol.FRAME_LENGTH and not protocol.verify_frame(frame):
            self.log.record("rx", frame, "bad_checksum")
            self.bad_frames += 1
            return
        reply = protocol.parse_reply(frame)
        if reply is None:
            self.log.record("rx", frame, "unparsed")
            return
        future = self._pending.get(reply.command)
        request = self._pending_request.get(reply.command)
        if (
            future is not None
            and not future.done()
            and (request is None or protocol.reply_matches(request, frame))
        ):
            self.log.record("rx", frame, "reply")
            future.set_result(reply)
            return
        self.log.record("rx", frame, "unsolicited")
        self.unsolicited.append(reply)

    def _on_disconnected(self, _client: Any) -> None:
        print("** light disconnected **", flush=True)
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    async def _heartbeat_loop(self) -> None:
        assert self.client is not None
        while self.connected:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if not self.connected or self._busy.locked():
                continue
            frame = protocol.heartbeat_frame()
            self.log.record("tx", frame, "heartbeat")
            try:
                await self.client.write_gatt_char(
                    const.CONTROL_CHARACTERISTIC_UUID, self._wrap(frame), response=False
                )
            except (BleakError, OSError) as err:
                print(f"heartbeat failed: {err}")
                return


# ---------------------------------------------------------------------------
# Bench commands: a tiny token language shared by send / verify / shell
# ---------------------------------------------------------------------------

COMMAND_HELP = """\
command tokens (several may follow each other on one line):
  on | off                 power
  bri N                    brightness as the raw device byte (0-255 or 0-100)
  rgb R G B                solid colour in the selected sub-mode
  ct KELVIN                white at a colour temperature (0x02/0x0D/0x15 only)
  scene ID                 built-in scene, ID in decimal or 0x hex
  raw HEX                  any frame: 'raw 33 05 15 01 ff 00 00' is padded and
                           checksummed; a full 20-byte frame is sent as given
  read                     the four status reads (version, power, brightness, mode)
  mode | power | brightness | version   one status read
  submode 02|0B|0D|15      change the colour sub-mode for later rgb/ct tokens
  sleep SECONDS            pause (the heartbeat keeps running)
"""


@dataclass
class Settings:
    submode: int
    scale: int
    model: str | None = None


@dataclass
class Action:
    name: str
    frame: bytes | None = None
    args: tuple[Any, ...] = ()


def parse_int(text: str) -> int:
    return int(text, 0)


def parse_tokens(tokens: Sequence[str], settings: Settings) -> list[Action]:
    """Turn command tokens into frames via protocol.py. Mutates settings on 'submode'."""
    actions: list[Action] = []
    i = 0
    while i < len(tokens):
        word = tokens[i].lower()
        try:
            if word in ("on", "off"):
                actions.append(Action("power", protocol.power_frame(word == "on"), (word == "on",)))
                i += 1
            elif word == "bri":
                level = parse_int(tokens[i + 1])
                if not 0 <= level <= 255:
                    raise ValueError("brightness byte must be 0-255")
                actions.append(
                    Action(
                        "brightness",
                        protocol.build_frame(protocol.CMD_BRIGHTNESS, [level]),
                        (level,),
                    )
                )
                i += 2
            elif word == "rgb":
                rgb = tuple(parse_int(t) for t in tokens[i + 1 : i + 4])
                if len(rgb) != 3:
                    raise ValueError("rgb needs three values")
                frame = protocol.rgb_frame(*rgb, submode=settings.submode)
                actions.append(Action("rgb", frame, (settings.submode, rgb)))
                i += 4
            elif word == "ct":
                kelvin = parse_int(tokens[i + 1])
                frame = protocol.color_temp_frame(kelvin, settings.submode)
                actions.append(Action("ct", frame, (settings.submode, kelvin)))
                i += 2
            elif word == "scene":
                scene_id = parse_int(tokens[i + 1])
                actions.append(Action("scene", protocol.scene_frame(scene_id), (scene_id,)))
                i += 2
            elif word == "raw":
                hex_tokens: list[str] = []
                i += 1
                while i < len(tokens) and all(c in "0123456789abcdefABCDEF" for c in tokens[i]):
                    hex_tokens.append(tokens[i])
                    i += 1
                data = bytes.fromhex("".join(hex_tokens))
                if len(data) < 2:
                    raise ValueError("raw needs at least a type and command byte")
                if len(data) == protocol.FRAME_LENGTH:
                    frame = data
                else:
                    frame = protocol.build_frame(data[1], data[2:], frame_type=data[0])
                actions.append(Action("raw", frame))
            elif word == "read":
                actions.append(Action("read"))
                i += 1
            elif word in ("mode", "power", "brightness", "version"):
                actions.append(Action("query", args=(word,)))
                i += 1
            elif word == "submode":
                key = tokens[i + 1].lower().removeprefix("0x").zfill(2)
                if key not in SUBMODE_ALIASES:
                    raise ValueError(f"unknown sub-mode {tokens[i + 1]}; use 02, 0B, 0D or 15")
                settings.submode = SUBMODE_ALIASES[key]
                actions.append(Action("submode", args=(settings.submode,)))
                i += 2
            elif word == "sleep":
                actions.append(Action("sleep", args=(float(tokens[i + 1]),)))
                i += 2
            else:
                raise ValueError(f"unknown token {tokens[i]!r}")
        except IndexError:
            raise ValueError(f"{word!r} is missing an argument") from None
    return actions


QUERY_FRAMES: dict[str, tuple[Callable[[], bytes], int]] = {
    "version": (protocol.version_query_frame, protocol.CMD_VERSION),
    "power": (protocol.power_query_frame, protocol.CMD_POWER),
    "brightness": (protocol.brightness_query_frame, protocol.CMD_BRIGHTNESS),
    "mode": (protocol.mode_query_frame, protocol.CMD_MODE),
}


async def run_query(link: RigLink, which: str) -> Any:
    build, cmd = QUERY_FRAMES[which]
    return await link.query(build(), cmd)


async def run_actions(link: RigLink, actions: list[Action], verify: bool) -> list[str]:
    """Execute actions in order. Returns the verify verdict lines."""
    verdicts: list[str] = []
    for action in actions:
        async with link._busy:
            if action.name == "sleep":
                await asyncio.sleep(action.args[0])
                continue
            if action.name == "submode":
                print(f"-- colour sub-mode now {submode_label(action.args[0])}")
                continue
            if action.name == "read":
                for which in ("version", "power", "brightness", "mode"):
                    await run_query(link, which)
                continue
            if action.name == "query":
                await run_query(link, action.args[0])
                continue
            assert action.frame is not None
            await link.write(action.frame, "command")
            if verify and action.name != "raw":
                await asyncio.sleep(SETTLE_BEFORE_READBACK)
                verdict = await verify_action(link, action)
                print(f"   => {verdict}")
                verdicts.append(verdict)
    return verdicts


async def verify_action(link: RigLink, action: Action) -> str:
    """Read back the state an action should have changed and compare."""
    if action.name == "power":
        reply = await run_query(link, "power")
        want = action.args[0]
        if reply is None:
            return "power: NO REPLY"
        return (
            f"power: {'ECHOED' if reply.power == want else f'DIFFERS (light says {reply.power})'}"
        )
    if action.name == "brightness":
        reply = await run_query(link, "brightness")
        want = action.args[0]
        if reply is None:
            return "brightness: NO REPLY"
        got = reply.brightness_level
        return (
            f"brightness: {'ECHOED' if got == want else f'DIFFERS (sent {want}, light says {got})'}"
        )
    reply = await run_query(link, "mode")
    if reply is None or reply.mode is None:
        return f"{action.name}: NO REPLY"
    mode = reply.mode
    if action.name == "scene":
        want = action.args[0]
        if mode.submode != protocol.SUBMODE_SCENE:
            return f"scene: DIFFERS (light in {submode_label(mode.submode)})"
        return f"scene: {'ECHOED' if mode.scene_id == want else f'DIFFERS (sent {want:#04x}, light says {mode.scene_id:#04x})'}"
    submode = action.args[0]
    if mode.submode != submode:
        return f"{action.name}: DIFFERS (sent sub-mode {submode:#04x}, light reports {submode_label(mode.submode)})"
    if action.name == "rgb":
        want = action.args[1]
        if submode == protocol.SUBMODE_COLOR_RGBIC:
            return (
                "rgb: SUB-MODE ECHOED (0x15 mode reply carries no RGB; use a ct write to confirm)"
            )
        if submode == protocol.SUBMODE_COLOR_DREAMCOLOR:
            return "rgb: SUB-MODE ECHOED (0x0B mode reply carries no RGB; try 'raw aa a5 01')"
        if probe.echoes(reply, submode, want):
            return "rgb: ECHOED"
        return f"rgb: DIFFERS (sent {want}, light says {mode.rgb})"
    if action.name == "ct":
        want = action.args[1]
        if submode == protocol.SUBMODE_COLOR_LEGACY:
            tint = protocol.kelvin_to_rgb(want)
            if mode.is_white and mode.white_tint == tint:
                return f"ct: ECHOED (white flag + tint {tint})"
            return f"ct: DIFFERS (white={mode.is_white}, tint {mode.white_tint}, expected {tint})"
        snapped = round(want / 100) * 100
        if mode.color_temp_kelvin in (want, snapped):
            return f"ct: ECHOED ({mode.color_temp_kelvin} K)"
        return f"ct: DIFFERS (sent {want} K, light says {mode.color_temp_kelvin})"
    return f"{action.name}: no verifier"


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


async def find_name(address: str, adapter: str | None, timeout: float = 8.0) -> str | None:
    kwargs = {"adapter": adapter} if adapter else {}
    device = await BleakScanner.find_device_by_address(address, timeout=timeout, **kwargs)
    return device.name if device is not None else None


async def resolve_settings(args: argparse.Namespace) -> Settings:
    """Work out sub-mode and scale from --model/--submode or a quick scan for the name."""
    model = args.model
    if model is None and not getattr(args, "no_scan", False):
        name = await find_name(args.address, args.adapter)
        model = models.model_from_name(name)
        print(f"advertised name: {name!r} -> model {model or 'unknown'}")
    profile = models.table_profile_for(model)
    settings = Settings(submode=profile.color_submode, scale=profile.brightness_scale, model=model)
    if args.submode:
        key = args.submode.lower().removeprefix("0x").zfill(2)
        if key not in SUBMODE_ALIASES:
            sys.exit(f"unknown --submode {args.submode}; use 02, 0B, 0D or 15")
        settings.submode = SUBMODE_ALIASES[key]
    print(
        f"profile: {profile.description}; colour sub-mode {submode_label(settings.submode)}, "
        f"brightness scale {settings.scale}"
    )
    return settings


async def cmd_scan(args: argparse.Namespace) -> None:
    kwargs = {"adapter": args.adapter} if args.adapter else {}
    print(f"scanning for {args.seconds}s ...", flush=True)
    LIGHTS.sweep((0, 200, 255))
    try:
        found = await BleakScanner.discover(timeout=args.seconds, return_adv=True, **kwargs)
    finally:
        LIGHTS.off()
    rows = []
    for device, adv in found.values():
        name = adv.local_name or device.name
        model = models.model_from_name(name)
        mfr = advertisement.parse_manufacturer_data(adv.manufacturer_data)
        govee = model is not None or mfr is not None
        if not govee and not args.all:
            continue
        profile = models.table_profile_for(model) if model else None
        enc = "?" if mfr is None or mfr.encrypted is None else ("yes" if mfr.encrypted else "no")
        rows.append(
            (
                device.address,
                name or "",
                model or "-",
                f"{profile.color_submode:#04x}" if profile else "-",
                str(profile.brightness_scale) if profile else "-",
                mfr.pact if mfr else "-",
                enc,
                str(adv.rssi),
                "table" if model and model in models.KNOWN_MODELS else ("default" if model else ""),
            )
        )
    if not rows:
        print("no Govee lights seen (is the adapter up? bluetoothctl power on)")
        return
    rows.sort(key=lambda r: -int(r[7]))
    header = ("address", "name", "model", "submode", "scale", "pact", "enc", "rssi", "profile")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(header)]
    print("  ".join(h.ljust(w) for h, w in zip(header, widths, strict=True)))
    for row in rows:
        print("  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)))
    print(
        "\n'default' means the model is not in models.py and the H6159 dialect is assumed; "
        "'enc yes' means the advertisement flags an AES-wrapped link (plaintext frames are ignored)."
    )


def print_safe_result(result: Any) -> None:
    print("\nsafe probe result:")
    print(f"  firmware   : {result.firmware}")
    print(f"  power      : {result.power}")
    print(f"  brightness : {result.brightness_level} (raw device byte)")
    print(f"  mode       : {describe_mode(result.mode) if result.mode else None}")
    print(f"  outcome    : {result.outcome}")
    if result.detected_submode is not None:
        print(f"  detected   : {submode_label(result.detected_submode)}")


async def cmd_status(args: argparse.Namespace) -> None:
    settings = await resolve_settings(args)
    log = FrameLog(
        path=args.log,
        address=args.address,
        quiet_heartbeat=not args.show_heartbeat,
        keep_secrets=args.keep_secrets,
    )
    log.session(adapter=args.adapter or "default", command="status", cipher=args.cipher)
    async with RigLink(args.address, log, args.adapter, args.cipher) as link:
        async with link._busy:
            if args.active:
                candidates = probe.candidate_order(settings.submode)
                if args.candidates:
                    candidates = tuple(
                        SUBMODE_ALIASES[c.lower().removeprefix("0x").zfill(2)]
                        for c in args.candidates.split(",")
                    )
                print(f"active probe, candidates in order: {[f'{c:#04x}' for c in candidates]}")
                result = await probe.async_active_probe(link, candidates)
                print_safe_result(result.recorded)
                print("\nactive probe result:")
                print(f"  attempted  : {[f'{c:#04x}' for c in result.attempted]}")
                print(
                    f"  detected   : {submode_label(result.detected_submode) if result.detected_submode is not None else 'none echoed'}"
                )
                print(f"  test kelvin: {result.test_kelvin}")
                print(f"  restored   : {result.restored} {result.restore_note or ''}")
                if result.error:
                    print(f"  error      : {result.error}")
            else:
                result = await probe.async_safe_probe(link)
                print_safe_result(result)
        if args.hold:
            print(f"holding the connection {args.hold}s to watch for unsolicited frames ...")
            await asyncio.sleep(args.hold)


PROBE_DESCRIPTIONS = {
    protocol.SUBMODE_COLOR_LEGACY: "0x02, the H6159 legacy colour",
    protocol.SUBMODE_COLOR_DREAMCOLOR: "0x0B, the dreamcolor RGBIC colour",
    protocol.SUBMODE_COLOR_RGBWW: "0x0D, the RGBWW bulb/lamp colour",
    protocol.SUBMODE_COLOR_RGBIC: "0x15, the segmented RGBIC colour",
}
ANSWERS = {"y": "yes", "yes": "yes", "n": "no", "no": "no", "w": "wrong", "wrong": "wrong",
           "p": "partial", "partial": "partial", "q": None, "quit": None}  # fmt: skip
OUTCOMES = {"yes": "worked", "partial": "partial", "wrong": "partial", "no": "unchanged"}


async def prompt_keeping_alive(link: RigLink, prompt: str) -> str | None:
    """input() off the event loop, sending AA 01 heartbeats so the light keeps the link."""
    loop = asyncio.get_running_loop()
    answer = loop.run_in_executor(None, _read_line, prompt)
    while True:
        done, _ = await asyncio.wait({answer}, timeout=HEARTBEAT_INTERVAL)
        if done:
            return answer.result()
        if link.connected and link.client is not None:
            frame = protocol.heartbeat_frame()
            link.log.record("tx", frame, "heartbeat")
            with contextlib.suppress(BleakError, OSError):
                await link.client.write_gatt_char(
                    const.CONTROL_CHARACTERISTIC_UUID, link._wrap(frame), response=False
                )


def _read_line(prompt: str) -> str | None:
    try:
        return input(prompt)
    except EOFError:
        return None


def make_asker(link: RigLink, address: str):  # type: ignore[no-untyped-def]
    """The census ``ask`` callback: one question per probe write, logged as an observation."""

    async def ask(submode: int, rgb: tuple[int, int, int]) -> str | None:
        hex_rgb = " ".join(f"{c:02X}" for c in rgb)
        print(
            f"\n  >>> wrote {PROBE_DESCRIPTIONS.get(submode, hex(submode))}: dark colour {hex_rgb}"
        )
        while True:
            raw = await prompt_keeping_alive(
                link,
                "  >>> Did the light change? [y]es / [n]o / [w]rong colour / [p]artial / [q]uit: ",
            )
            if raw is None:
                return None
            key = raw.strip().lower()
            if key in ANSWERS:
                answer = ANSWERS[key]
                break
            print("      (y, n, w, p or q)")
        if answer is None:
            print("  >>> stopping the probe; restoring the light")
            return None
        link.log.write_line(
            {
                "summary": "observation",
                "schema": census.SCHEMA,
                "source": "manual",
                "at": now_iso(),
                "address": address,
                "observed": f"probe 0x{submode:02X} wrote {hex_rgb}: {answer}",
                "capability": "rgb",
                "outcome": OUTCOMES[answer],
                "dialect": f"0x{submode:02X}",
            }
        )
        return answer

    return ask


def _manufacturer_data(device: Any) -> dict[int, bytes] | None:
    """Manufacturer data from a BLEDevice found by address (BlueZ keeps it in details["props"])."""
    if device is None:
        return None
    details = getattr(device, "details", None)
    props = details.get("props") if isinstance(details, dict) else getattr(details, "props", None)
    mfr = props.get("ManufacturerData") if isinstance(props, dict) else None
    return mfr if isinstance(mfr, dict) else None


async def cmd_census(args: argparse.Namespace) -> None:
    """The shared read battery; one govee-evidence/1 summary line per run."""
    settings = await resolve_settings(args)
    adv = None
    if not args.no_scan:
        kwargs = {"adapter": args.adapter} if args.adapter else {}
        device = await BleakScanner.find_device_by_address(args.address, timeout=8.0, **kwargs)
        mfr = _manufacturer_data(device)
        if isinstance(mfr, dict):
            adv = advertisement.parse_manufacturer_data({int(k): bytes(v) for k, v in mfr.items()})
        if adv is not None:
            print(f"advertisement: pact {adv.pact}, flags {adv.flags}, encrypted {adv.encrypted}")
    log = FrameLog(
        path=args.log,
        address=args.address,
        quiet_heartbeat=not args.show_heartbeat,
        keep_secrets=args.keep_secrets,
    )
    log.session(
        adapter=args.adapter or "default",
        command="census",
        probe=bool(args.probe),
        lights=1,
        cipher=args.cipher,
    )
    if adv is not None and adv.encrypted and not args.cipher:
        print(
            "the advertisement flags an AES-wrapped link; plaintext reads will be ignored (try --cipher v1 if 2b12 says version 1)"
        )
    async with RigLink(args.address, log, args.adapter, args.cipher) as link:
        async with link._busy:
            result = await census.async_census(
                link,
                probe_writes=bool(args.probe),
                ask=make_asker(link, args.address.upper()) if args.ask else None,
            )
        result.acks = link.acks
        result.bad_frames = link.bad_frames
        verdict = census.classify(result, model=settings.model, adv=adv, bgc=link.bgc)
        record = census.summary_record(
            result,
            address=args.address.upper(),
            name=None,
            model=settings.model,
            adv=adv,
            rssi=None,
            source="pi",
            at=now_iso(),
            software=software_version(),
            dialect=settings.submode,
            dialect_source="table" if settings.model in models.KNOWN_MODELS else "default",
            known_model=settings.model in models.KNOWN_MODELS,
            include_secret=args.keep_secrets,
        )
        if link.bgc is not None:
            record["bgc"] = link.bgc.hex()
        log.write_line(record)
    print("\ncensus result:")
    for key in ("firmware", "hardware", "power", "brightness", "submode", "submode_name", "ic_count", "segments",
                "light_count", "secret_present", "segment_page", "reads", "replies", "acks", "bad_frames",
                "echoed", "probe_acks", "observed", "restored", "restore_note", "error"):  # fmt: skip
        if key in record:
            print(f"  {key:<15}: {record[key]}")
    print(f"  verdict        : {verdict.family} / {verdict.security} / {verdict.confidence}")
    for reason in verdict.reasons:
        print(f"                   - {reason}")
    if LIGHTS.available:
        print(
            "  (verdict on the Blinkt: sparkle verified, amber inferred, red silent, purple encrypted)"
        )
        await asyncio.to_thread(LIGHTS.verdict, verdict.family, verdict.confidence)
    if result.secret_present and not args.keep_secrets:
        print(
            "  (the AA B1 secret was read; it is not in the log. --keep-secrets keeps it, for a private file)"
        )
    if args.log:
        print(f"\nevidence appended to {args.log}; run tools/govee_report.py on it for the report")


async def cmd_send(args: argparse.Namespace, verify: bool) -> None:
    settings = await resolve_settings(args)
    try:
        actions = parse_tokens(args.tokens, settings)
    except ValueError as err:
        sys.exit(f"bad command: {err}\n\n{COMMAND_HELP}")
    log = FrameLog(
        path=args.log,
        address=args.address,
        quiet_heartbeat=not args.show_heartbeat,
        keep_secrets=args.keep_secrets,
    )
    log.session(
        adapter=args.adapter or "default",
        command="verify" if verify else "send",
        cipher=args.cipher,
    )
    async with RigLink(args.address, log, args.adapter, args.cipher) as link:
        verdicts = await run_actions(link, actions, verify)
        if args.hold:
            await asyncio.sleep(args.hold)
    if verify and verdicts:
        print("\nverify summary:")
        for line in verdicts:
            print(f"  {line}")


async def cmd_shell(args: argparse.Namespace) -> None:
    settings = await resolve_settings(args)
    log = FrameLog(
        path=args.log,
        address=args.address,
        quiet_heartbeat=not args.show_heartbeat,
        keep_secrets=args.keep_secrets,
    )
    log.session(adapter=args.adapter or "default", command="shell", cipher=args.cipher)
    print(COMMAND_HELP)
    print("extra shell words: verify on|off, quit. Blank line repeats nothing.\n")
    verify = args.verify
    async with RigLink(args.address, log, args.adapter, args.cipher) as link:
        while link.connected:
            try:
                line = await asyncio.to_thread(
                    input, f"{args.address[-5:]}{' [verify]' if verify else ''}> "
                )
            except (EOFError, KeyboardInterrupt):
                print()
                break
            tokens = shlex.split(line)
            if not tokens:
                continue
            if tokens[0] in ("quit", "exit", "q"):
                break
            if tokens[0] == "verify" and len(tokens) == 2:
                verify = tokens[1] == "on"
                continue
            try:
                actions = parse_tokens(tokens, settings)
            except ValueError as err:
                print(f"bad command: {err}")
                continue
            try:
                await run_actions(link, actions, verify)
            except probe.ProbeConnectionLost as err:
                print(f"link lost: {err}")
                break


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--adapter", help="BlueZ adapter, e.g. hci0 (default: BlueZ's default)")
    parser.add_argument("--log", type=Path, help="append every TX/RX frame as JSON lines here")
    parser.add_argument("--show-heartbeat", action="store_true", help="print AA 01 heartbeats too")
    parser.add_argument(
        "--keep-secrets",
        action="store_true",
        help="do not redact AA B1 / 33 B2 payloads in --log (private captures only)",
    )
    parser.add_argument(
        "--cipher",
        choices=["v1"],
        help="run the AES-ECB+RC4 handshake and wrap every frame (experimental)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="bleak debug logging")
    parser.add_argument(
        "--blinkt", action="store_true", help="show progress on a Pimoroni Blinkt (tools/blinkt.py)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="list Govee lights in range")
    p_scan.add_argument("--seconds", type=float, default=8.0)
    p_scan.add_argument("--all", action="store_true", help="list every BLE device, not just Govee")

    def add_connect_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("address", help="Bluetooth address, e.g. A4:C1:38:12:34:56")
        p.add_argument("--model", help="override the model (skips the name scan), e.g. H6167")
        p.add_argument("--submode", help="colour sub-mode to use: 02, 0B, 0D or 15")
        p.add_argument("--no-scan", action="store_true", help="do not scan for the name first")
        p.add_argument("--hold", type=float, default=0.0, help="keep the link open N seconds after")

    p_status = sub.add_parser(
        "status", help="run the integration's safe (and optionally active) probe"
    )
    add_connect_args(p_status)
    p_status.add_argument(
        "--active", action="store_true", help="write test colours and look for echoes"
    )
    p_status.add_argument("--candidates", help="active-probe order, e.g. 15,0D,02")

    p_census = sub.add_parser(
        "census",
        help="the shared read battery, with an optional probe; writes govee-evidence/1 records",
    )
    add_connect_args(p_census)
    p_census.add_argument(
        "--ask",
        action="store_true",
        help="with --probe: stop after each write and ask whether the light changed",
    )
    p_census.add_argument(
        "--probe",
        action="store_true",
        help="also write one colour per dialect and restore what can be restored",
    )

    p_send = sub.add_parser(
        "send",
        help="send commands",
        epilog=COMMAND_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_connect_args(p_send)
    p_send.add_argument("tokens", nargs="+")

    p_verify = sub.add_parser(
        "verify",
        help="send commands and read back whether the light echoed each one",
        epilog=COMMAND_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_connect_args(p_verify)
    p_verify.add_argument("tokens", nargs="+")

    p_shell = sub.add_parser("shell", help="interactive session on one connection")
    add_connect_args(p_shell)
    p_shell.add_argument("--verify", action="store_true", help="start with read-back on")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    global LIGHTS
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    if args.blinkt:
        LIGHTS = blinkt.open_blinkt()
        if not LIGHTS.available:
            print(f"warning: --blinkt: {LIGHTS.reason}")
    try:
        if args.command == "scan":
            asyncio.run(cmd_scan(args))
        elif args.command == "status":
            asyncio.run(cmd_status(args))
        elif args.command == "census":
            asyncio.run(cmd_census(args))
        elif args.command == "send":
            asyncio.run(cmd_send(args, verify=False))
        elif args.command == "verify":
            asyncio.run(cmd_send(args, verify=True))
        elif args.command == "shell":
            asyncio.run(cmd_shell(args))
    except KeyboardInterrupt:
        print("\ninterrupted")
    except (BleakError, probe.ProbeConnectionLost, OSError) as err:
        sys.exit(f"bluetooth error: {err}")
    finally:
        LIGHTS.close()


if __name__ == "__main__":
    main()
