#!/usr/bin/env python3
"""Bench test rig for Govee BLE lights: bleak on BlueZ, no Home Assistant.

Imports ``protocol``, ``models`` and ``probe`` straight from this repo's
``custom_components/govee_bluetooth`` so the frames it sends are the ones the
integration sends. The package's ``__init__`` needs Home Assistant, so the
modules are loaded as submodules of a stub package instead (see
:func:`load_integration`).

Subcommands::

    scan                      list Govee lights with model and table dialect
    status ADDR [--active]    the four status reads (and the active probe)
    send ADDR CMD...          named or raw frames, every TX/RX printed
    verify ADDR CMD...        send, read back, report whether the light echoed
    shell ADDR                one connection, commands typed interactively

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
import shlex
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


def load_integration() -> tuple[Any, Any, Any, Any]:
    """Return ``(const, protocol, models, probe)`` from the checkout.

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
        importlib.import_module(base + m) for m in ("const", "protocol", "models", "probe")
    )


const, protocol, models, probe = load_integration()


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
    """Prints every frame and optionally appends JSON lines (HA diagnostics shape)."""

    path: Path | None = None
    address: str = ""
    quiet_heartbeat: bool = True
    records: list[dict[str, str]] = field(default_factory=list)

    def record(self, direction: str, frame: bytes, note: str) -> None:
        decoded = describe_tx(frame) if direction == "tx" else describe_rx(frame)
        entry = {
            "at": now_iso(),
            "direction": direction,
            "frame": hexs(frame),
            "note": note,
            "decoded": decoded,
            "address": self.address,
        }
        self.records.append(entry)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        if self.quiet_heartbeat and note == "heartbeat":
            return
        stamp = entry["at"][11:23]
        arrow = "TX" if direction == "tx" else "RX"
        print(f"{stamp} {arrow} {hexs(frame)}  {note:<11} {decoded}")


class RigLink:
    """A connected Govee light. Implements probe.ProbeLink."""

    def __init__(self, address: str, log: FrameLog, adapter: str | None = None) -> None:
        self.address = address.upper()
        self.log = log
        self.adapter = adapter
        self.client: BleakClient | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._heartbeat: asyncio.Task[None] | None = None
        self._busy = asyncio.Lock()
        self.unsolicited: list[Any] = []

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
        print(f"connected (mtu {getattr(self.client, 'mtu_size', '?')})", flush=True)
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())

    async def disconnect(self) -> None:
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
                const.CONTROL_CHARACTERISTIC_UUID, frame, response=True
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
        try:
            self.log.record("tx", frame, note)
            await self.client.write_gatt_char(
                const.CONTROL_CHARACTERISTIC_UUID, frame, response=True
            )
            return await asyncio.wait_for(future, QUERY_TIMEOUT)
        except TimeoutError:
            print(
                f"            -- no reply to {CMD_NAMES.get(command, hex(command))} query within {QUERY_TIMEOUT}s"
            )
            return None
        except (BleakError, OSError) as err:
            raise probe.ProbeConnectionLost(str(err)) from err
        finally:
            if self._pending.get(command) is future:
                del self._pending[command]

    # internals ----------------------------------------------------------

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        frame = bytes(data)
        reply = protocol.parse_reply(frame)
        if reply is None:
            self.log.record("rx", frame, "unparsed")
            return
        future = self._pending.get(reply.command)
        if future is not None and not future.done():
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
                    const.CONTROL_CHARACTERISTIC_UUID, frame, response=False
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
    found = await BleakScanner.discover(timeout=args.seconds, return_adv=True, **kwargs)
    rows = []
    for device, adv in found.values():
        name = adv.local_name or device.name
        model = models.model_from_name(name)
        govee = model is not None or 0xEC88 in adv.manufacturer_data
        if not govee and not args.all:
            continue
        profile = models.table_profile_for(model) if model else None
        rows.append(
            (
                device.address,
                name or "",
                model or "-",
                f"{profile.color_submode:#04x}" if profile else "-",
                str(profile.brightness_scale) if profile else "-",
                str(adv.rssi),
                "table" if model and model in models.KNOWN_MODELS else ("default" if model else ""),
            )
        )
    if not rows:
        print("no Govee lights seen (is the adapter up? bluetoothctl power on)")
        return
    rows.sort(key=lambda r: -int(r[5]))
    header = ("address", "name", "model", "submode", "scale", "rssi", "profile")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(header)]
    print("  ".join(h.ljust(w) for h, w in zip(header, widths, strict=True)))
    for row in rows:
        print("  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)))
    print("\n'default' means the model is not in models.py and the H6159 dialect is assumed.")


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
    log = FrameLog(path=args.log, address=args.address, quiet_heartbeat=not args.show_heartbeat)
    async with RigLink(args.address, log, args.adapter) as link:
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


async def cmd_send(args: argparse.Namespace, verify: bool) -> None:
    settings = await resolve_settings(args)
    try:
        actions = parse_tokens(args.tokens, settings)
    except ValueError as err:
        sys.exit(f"bad command: {err}\n\n{COMMAND_HELP}")
    log = FrameLog(path=args.log, address=args.address, quiet_heartbeat=not args.show_heartbeat)
    async with RigLink(args.address, log, args.adapter) as link:
        verdicts = await run_actions(link, actions, verify)
        if args.hold:
            await asyncio.sleep(args.hold)
    if verify and verdicts:
        print("\nverify summary:")
        for line in verdicts:
            print(f"  {line}")


async def cmd_shell(args: argparse.Namespace) -> None:
    settings = await resolve_settings(args)
    log = FrameLog(path=args.log, address=args.address, quiet_heartbeat=not args.show_heartbeat)
    print(COMMAND_HELP)
    print("extra shell words: verify on|off, quit. Blank line repeats nothing.\n")
    verify = args.verify
    async with RigLink(args.address, log, args.adapter) as link:
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
    parser.add_argument("-v", "--verbose", action="store_true", help="bleak debug logging")
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
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    try:
        if args.command == "scan":
            asyncio.run(cmd_scan(args))
        elif args.command == "status":
            asyncio.run(cmd_status(args))
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


if __name__ == "__main__":
    main()
