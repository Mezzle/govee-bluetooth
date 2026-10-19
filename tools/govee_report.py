#!/usr/bin/env python3
"""Turn Govee BLE evidence logs into a compatibility report.

Consumes, in any mix:

* ``govee_rig.py --log`` JSON lines (source ``pi``);
* Dance Party ``census.jsonl`` from the Flipper (source ``dance_party``) and
  the ESP32 image's USB log (source ``esp32``, other serial chatter ignored);
* a Home Assistant diagnostics download (``.json`` with ``frames`` and
  ``probe_history``, source ``home_assistant``);
* ``--observe ADDRESS=TEXT`` for what a human saw the strip do.

and produces a machine-readable evidence document (``--json``) and a
Markdown report (``--markdown``, or stdout). Every conclusion carries the
sources it came from and a confidence; secrets are redacted on ingest.

    python3 tools/govee_report.py census.jsonl bench/*.jsonl --markdown report.md --json evidence.json

Python 3.11+, no third-party packages: the integration's pure modules are
loaded straight from this checkout, as ``govee_rig.py`` does.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import subprocess
import sys
import types
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "custom_components" / "govee_bluetooth"

REPORT_SCHEMA = "govee-evidence-report/1"
BADGE = "🪩 Dance Party Certified"
SOURCE_LABELS = {
    "pi": "Pi bench rig",
    "dance_party": "Dance Party (Flipper)",
    "esp32": "Dance Party ESP32 image",
    "home_assistant": "Home Assistant diagnostics",
    "manual": "operator observation",
}


def load_integration() -> tuple[Any, Any, Any, Any]:
    """``(protocol, models, census, advertisement)`` without importing Home Assistant."""
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
        importlib.import_module(base + m) for m in ("protocol", "models", "census", "advertisement")
    )


protocol, models, census, advertisement = load_integration()


def software_version() -> str:
    try:
        sha = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        # A copied checkout without .git (the Pi bench) records its commit here.
        marker = REPO_ROOT / ".rig-commit"
        sha = marker.read_text().strip() if marker.is_file() else "unknown"
    return f"govee_report.py {sha}"


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


@dataclass
class Device:
    address: str
    names: set[str] = field(default_factory=set)
    models: set[str] = field(default_factory=set)
    summaries: list[dict[str, Any]] = field(default_factory=list)
    frames: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)

    @property
    def model(self) -> str | None:
        return sorted(self.models)[0] if self.models else None

    @property
    def name(self) -> str | None:
        return sorted(self.names)[0] if self.names else None


@dataclass
class Evidence:
    sessions: list[dict[str, Any]] = field(default_factory=list)
    devices: dict[str, Device] = field(default_factory=dict)
    inputs: list[str] = field(default_factory=list)
    keep_secrets: bool = False

    def device(self, address: str) -> Device:
        key = address.upper()
        if key not in self.devices:
            self.devices[key] = Device(address=key)
        return self.devices[key]


def _guess_source(record: dict[str, Any], default: str | None) -> str:
    if default:
        return default
    if record.get("source"):
        return str(record["source"])
    if str(record.get("at", "")).startswith("T+"):
        return "esp32"
    return "pi"


def _redact_frame_record(record: dict[str, Any], keep_secrets: bool) -> dict[str, Any]:
    try:
        raw = bytes.fromhex(record["frame"])
    except (KeyError, ValueError):
        return record
    if keep_secrets or len(raw) != protocol.FRAME_LENGTH:
        return record
    redacted, changed = protocol.redact_frame(raw)
    if changed:
        record = dict(record)
        record["frame"] = redacted.hex()
        record["redacted"] = True
    return record


def ingest_records(
    evidence: Evidence, records: list[dict[str, Any]], *, source: str | None, label: str
) -> None:
    """Add records from one file; ``source`` overrides what the records say."""
    evidence.inputs.append(label)
    current_source = source
    for record in records:
        kind = record.get("summary")
        if kind == "session":
            current_source = source or str(record.get("source") or current_source or "pi")
            session = dict(record)
            session["source"] = current_source
            session["input"] = label
            evidence.sessions.append(session)
            continue
        if kind == "light":
            src = source or str(record.get("source") or current_source or "pi")
            dev = evidence.device(str(record.get("address", "?")))
            summary = dict(record)
            summary["source"] = src
            summary["input"] = label
            if not evidence.keep_secrets:
                summary.pop("secret", None)
            dev.summaries.append(summary)
            dev.sources.add(src)
            if record.get("name"):
                dev.names.add(str(record["name"]))
            if record.get("model"):
                dev.models.add(str(record["model"]).upper())
            continue
        if kind == "observation":
            dev = evidence.device(str(record.get("address", "?")))
            obs = dict(record)
            obs["source"] = "manual"
            dev.observations.append(obs)
            dev.sources.add("manual")
            continue
        if kind == "dump":
            continue
        if "frame" in record and "direction" in record:
            src = _guess_source(record, source or current_source)
            dev = evidence.device(str(record.get("address") or "?"))
            frame = _redact_frame_record(record, evidence.keep_secrets)
            frame = dict(frame)
            frame["source"] = src
            dev.frames.append(frame)
            dev.sources.add(src)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSON objects one per line; anything else (serial chatter) is skipped."""
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        # The ESP32 bridge echoes "CENSUS {...}" over the line protocol.
        if line.startswith("CENSUS {"):
            line = line[len("CENSUS ") :]
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def read_ha_diagnostics(path: Path) -> list[dict[str, Any]]:
    """Home Assistant diagnostics download -> frame records plus one summary."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    data = doc.get("data", doc)
    entry = data.get("entry", {}).get("data", {})
    address = str(entry.get("address") or data.get("address") or "?")
    model = entry.get("model")
    records: list[dict[str, Any]] = [
        {
            "summary": "session",
            "schema": census.SCHEMA,
            "source": "home_assistant",
            "software": f"home assistant diagnostics {doc.get('home_assistant', {}).get('version', '')}".strip(),
        }
    ]
    for frame in data.get("frames", []):
        rec = dict(frame)
        rec.setdefault("address", address)
        rec.setdefault("decoded", "")
        records.append(rec)
    for run in data.get("probe_history", []):
        for frame in run.get("frames", []):
            rec = dict(frame)
            rec.setdefault("address", address)
            rec.setdefault("decoded", "")
            rec["note"] = rec.get("note") or "probe"
            records.append(rec)
    state = data.get("state", {})
    summary: dict[str, Any] = {
        "summary": "light",
        "schema": census.SCHEMA,
        "source": "home_assistant",
        "address": address,
        "name": entry.get("name", ""),
        "model": model or "",
        "connected": bool(data.get("last_update_success")),
        "subscribed": bool(data.get("last_update_success")),
        "silent": False,
        "probed": bool(data.get("probe_history")),
    }
    color = data.get("color_submode", {})
    in_use = color.get("in_use")
    if in_use is not None:
        summary["dialect"] = {0x02: "RGB", 0x0D: "RGBWW", 0x15: "RGBIC", 0x0B: "Dream"}.get(
            in_use, str(in_use)
        )
        if color.get("manual_override") is not None:
            summary["dialect_source"] = "manual"
        elif color.get("detected") is not None:
            summary["dialect_source"] = "detected"
        else:
            summary["dialect_source"] = "table"
    if entry.get("firmware"):
        summary["firmware"] = entry["firmware"]
    if state.get("power") is not None:
        summary["power"] = state["power"]
    records.append(summary)
    return records


def load_input(evidence: Evidence, path: Path, *, source: str | None) -> None:
    if path.suffix.lower() == ".json":
        ingest_records(
            evidence, read_ha_diagnostics(path), source=source or "home_assistant", label=path.name
        )
    else:
        ingest_records(evidence, read_jsonl(path), source=source, label=path.name)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def _parse_frame(record: dict[str, Any]) -> bytes | None:
    try:
        raw = bytes.fromhex(record["frame"])
    except (KeyError, ValueError):
        return None
    return raw if len(raw) == protocol.FRAME_LENGTH else None


def facts_from_frames(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """What the frame log alone shows: replies, acks, last known state, probe writes."""
    facts: dict[str, Any] = {
        "tx": 0,
        "rx": 0,
        "replies": 0,
        "timeouts": 0,
        "acks": 0,
        "bad_frames": 0,
        "unsolicited": 0,
        "probe_writes": [],
        "commands": [],
        "redacted": 0,
        "secret_reply": False,
    }
    mode = None
    for record in frames:
        raw = _parse_frame(record)
        note = str(record.get("note", ""))
        if record.get("redacted"):
            facts["redacted"] += 1
        if record.get("direction") == "tx":
            facts["tx"] += 1
            if raw is not None and raw[0] == protocol.TYPE_WRITE:
                facts["commands"].append(raw[1])
                if note == "probe" and raw[1] == protocol.CMD_MODE:
                    facts["probe_writes"].append(raw[2])
            if note == "timeout":
                facts["timeouts"] += 1
            continue
        facts["rx"] += 1
        if note == "timeout":
            facts["timeouts"] += 1
            continue
        if note == "bad_checksum" or (raw is not None and not protocol.verify_frame(raw)):
            facts["bad_frames"] += 1
            continue
        if note in ("ack", "ack_error") or (raw is not None and raw[0] == protocol.TYPE_WRITE):
            facts["acks"] += 1
            continue
        if raw is None:
            continue
        reply = protocol.parse_reply(raw)
        if reply is None:
            facts["unsolicited"] += 1
            continue
        facts["replies"] += 1
        if reply.power is not None:
            facts["power"] = reply.power
        if reply.brightness_level is not None:
            facts["brightness"] = reply.brightness_level
        if reply.version:
            facts["firmware"] = reply.version
        if reply.hardware:
            facts["hardware"] = reply.hardware
        if reply.ic_count is not None:
            facts["ic_count"], facts["segments"] = reply.ic_count, reply.segments
        if reply.secret_present:
            facts["secret_reply"] = True
        if reply.mode is not None:
            mode = reply.mode
            facts["submode"] = reply.mode.submode
    if mode is not None:
        facts["mode"] = mode
    return facts


def _result_from_summary(summary: dict[str, Any], facts: dict[str, Any]) -> Any:
    """Rebuild a CensusResult from a summary line (plus the frames, for the mode)."""
    result = census.CensusResult(
        connected=bool(summary.get("connected")),
        subscribed=bool(summary.get("subscribed")),
        aborted=bool(summary.get("aborted")),
        reads=int(summary.get("reads", 0) or 0),
        replies=int(summary.get("replies", facts.get("replies", 0)) or 0),
        bad_frames=int(summary.get("bad_frames", 0) or 0),
        acks=int(summary.get("acks", facts.get("acks", 0)) or 0),
        power=summary.get("power"),
        brightness=summary.get("brightness"),
        firmware=summary.get("firmware"),
        hardware=summary.get("hardware"),
        ic_count=summary.get("ic_count"),
        segments=summary.get("segments"),
        light_count=summary.get("light_count"),
        secret_present=bool(summary.get("secret_present")),
        probed=bool(summary.get("probed")),
    )
    if result.replies == 0 and result.connected and result.subscribed and summary.get("silent"):
        result.reads = max(result.reads, 1)
    submode_text = str(summary.get("submode", "")).split(" ")[0]
    if submode_text:
        with contextlib.suppress(ValueError):
            result.mode = protocol.ModeReport(submode=int(submode_text, 16))
    if result.mode is None and facts.get("mode") is not None:
        result.mode = facts["mode"]
    echoed = str(summary.get("echoed", "") or "")
    result.echoed = [int(part, 16) for part in echoed.split(",") if part.strip()]
    result.restored = summary.get("restored")
    result.restore_note = summary.get("restore_note")
    result.error = summary.get("error")
    return result


CAPABILITIES = ("power", "brightness", "rgb", "rgbww", "segments", "scene", "music")


def capabilities(
    result: Any, facts: dict[str, Any], observations: list[dict[str, Any]]
) -> dict[str, dict[str, str]]:
    """Per capability: ``{"level": verified|inferred|observed|untested|failed, "basis": text}``."""
    caps: dict[str, dict[str, str]] = {
        c: {"level": "untested", "basis": "no evidence"} for c in CAPABILITIES
    }

    def set_level(cap: str, level: str, basis: str) -> None:
        order = {"untested": 0, "failed": 1, "inferred": 2, "verified": 3, "observed": 4}
        if order[level] >= order[caps[cap]["level"]]:
            caps[cap] = {"level": level, "basis": basis}

    if result.power is not None:
        set_level("power", "inferred", "AA 01 reply read back the power state")
    if result.brightness is not None:
        set_level("brightness", "inferred", "AA 04 reply read back the brightness")
    if protocol.CMD_POWER in facts.get("commands", []) and facts.get("acks"):
        set_level("power", "verified", "33 01 write followed by an application ack")
    for submode in result.echoed:
        if submode in (0x02, 0x0B, 0x15):
            set_level("rgb", "verified", f"probe colour echoed in dialect {submode:#04x}")
        if submode == 0x0D:
            set_level("rgb", "verified", "probe colour echoed in dialect 0x0d")
            set_level(
                "rgbww",
                "inferred",
                "0x0D firmware carries kelvin in the same frame; CCT write not tried",
            )
        if submode in (0x0B, 0x15):
            set_level(
                "segments",
                "inferred",
                "segment dialect echoed; whole-strip mask only, per-segment not tried",
            )
    if result.mode is not None and not result.echoed:
        if result.mode.submode in (0x02, 0x0B, 0x0D, 0x15):
            set_level(
                "rgb",
                "inferred",
                f"mode reply names colour sub-mode {result.mode.submode:#04x}; no write tried",
            )
        if result.mode.submode == 0x04:
            set_level("scene", "inferred", "light was in a scene when read; scene writes not tried")
        if census.submode_name(result.mode.submode).startswith("music"):
            set_level(
                "music", "inferred", "light was in a music mode when read; music writes not tried"
            )
    if result.probed and not result.echoed and result.replies:
        set_level("rgb", "failed", "all four colour dialects were written; none echoed")
    if result.segments:
        set_level("segments", "inferred", f"AA 40 reports {result.segments} segments")
    for obs in observations:
        cap = obs.get("capability")
        outcome = obs.get("outcome", "")
        if cap in caps:
            level = (
                "observed"
                if outcome in ("worked", "partial")
                else "failed"
                if outcome == "failed"
                else "observed"
            )
            set_level(cap, level, f"operator: {obs.get('observed', '')}")
    return caps


def analyse(evidence: Evidence) -> dict[str, Any]:
    """The evidence document: sessions, per-device facts, verdicts, provenance."""
    devices_out: list[dict[str, Any]] = []
    for address in sorted(evidence.devices):
        dev = evidence.devices[address]
        facts = facts_from_frames(dev.frames)
        summary = dev.summaries[-1] if dev.summaries else None
        adv = None
        if summary and summary.get("pact"):
            try:
                ptype, pcode = (int(x) for x in str(summary["pact"]).split("/"))
                flags = summary.get("flags")
                raw = bytes([flags & 0xFF]) if isinstance(flags, int) else b""
                raw += b"\x88\xec" + bytes([ptype >> 8, ptype & 0xFF, pcode & 0xFF])
                adv = advertisement.parse_raw(raw)
            except (ValueError, TypeError):
                adv = None
        bgc = None
        if summary and summary.get("bgc"):
            try:
                bgc = bytes.fromhex(str(summary["bgc"]))
            except ValueError:
                bgc = None
        if summary is not None:
            result = _result_from_summary(summary, facts)
        else:
            result = census.CensusResult(
                connected=facts["tx"] > 0 or facts["rx"] > 0,
                subscribed=facts["replies"] > 0 or facts["acks"] > 0,
                reads=facts["tx"],
                replies=facts["replies"],
                acks=facts["acks"],
                bad_frames=facts["bad_frames"],
                power=facts.get("power"),
                brightness=facts.get("brightness"),
                firmware=facts.get("firmware"),
                hardware=facts.get("hardware"),
                ic_count=facts.get("ic_count"),
                segments=facts.get("segments"),
                secret_present=facts["secret_reply"],
                mode=facts.get("mode"),
                probed=bool(facts["probe_writes"]),
            )
        model = dev.model
        verdict = census.classify(result, model=model, adv=adv, bgc=bgc)
        caps = capabilities(result, facts, dev.observations)
        profile = models.table_profile_for(model) if model else None
        timeline = sorted(
            {str(f.get("at", "")) for f in dev.frames}
            | {str(s.get("at", "")) for s in dev.summaries}
        )
        devices_out.append(
            {
                "address": address,
                "name": dev.name,
                "model": model,
                "known_model": bool(model and model in models.KNOWN_MODELS),
                "table_profile": {
                    "color_submode": f"{profile.color_submode:#04x}",
                    "brightness_scale": profile.brightness_scale,
                    "description": profile.description,
                }
                if profile
                else None,
                "sources": sorted(dev.sources),
                "dance_party_certified": "dance_party" in dev.sources,
                "firmware": result.firmware,
                "hardware": result.hardware,
                "pact": summary.get("pact") if summary else None,
                "encrypted_flag": summary.get("encrypted") if summary else None,
                "gatt": summary.get("gatt") if summary else None,
                "bgc": summary.get("bgc") if summary else None,
                "verdict": {
                    "family": verdict.family,
                    "security": verdict.security,
                    "confidence": verdict.confidence,
                    "reasons": verdict.reasons,
                    "control_dialects": [f"{d:#04x}" for d in verdict.control_dialects],
                },
                "capabilities": caps,
                "reads": {
                    "sent": result.reads,
                    "answered": result.replies,
                    "bad_frames": result.bad_frames,
                    "acks": result.acks,
                    "timeouts": facts["timeouts"],
                },
                "state_seen": {
                    k: v
                    for k, v in (
                        ("power", result.power),
                        ("brightness", result.brightness),
                        ("submode", f"{result.mode.submode:#04x}" if result.mode else None),
                        ("segments", result.segments),
                        ("ic_count", result.ic_count),
                        ("light_count", result.light_count),
                    )
                    if v is not None
                },
                "secret_present": result.secret_present,
                "restoration": {"restored": result.restored, "note": result.restore_note}
                if result.probed
                else None,
                "probe": {
                    "writes": [f"{s:#04x}" for s in facts["probe_writes"]],
                    "echoed": [f"{s:#04x}" for s in result.echoed],
                }
                if result.probed
                else None,
                "observations": dev.observations,
                "summaries": dev.summaries,
                "frames": dev.frames,
                "frame_counts": {
                    "tx": facts["tx"],
                    "rx": facts["rx"],
                    "redacted": facts["redacted"],
                },
                "first_seen": timeline[0] if timeline else None,
                "last_seen": timeline[-1] if timeline else None,
                "error": result.error,
            }
        )
    return {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "generator": software_version(),
        "inputs": evidence.inputs,
        "sessions": evidence.sessions,
        "dance_party_certified": any(d["dance_party_certified"] for d in devices_out),
        "secrets_redacted": not evidence.keep_secrets,
        "devices": devices_out,
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


_CONFIDENCE_TEXT = {
    "verified": "verified by a write whose effect the light reported back",
    "probable": "consistent with every reading, not proven by a controlled write",
    "inferred": "read from the light's own report or from the tables, no write tried",
    "unknown": "not enough evidence",
    "observed": "seen by the operator",
}


def _md_table(rows: list[tuple[str, str]]) -> list[str]:
    out = ["| | |", "| --- | --- |"]
    out += [f"| {k} | {v} |" for k, v in rows]
    return out


def render_markdown(doc: dict[str, Any]) -> str:
    lines: list[str] = ["# Govee BLE compatibility report", ""]
    if doc["dance_party_certified"]:
        lines += [
            f"**{BADGE}** (at least one device below was observed from a Dance Party session; a provenance mark, not a test pass)",
            "",
        ]
    lines += [
        f"Generated {doc['generated_at']} by `{doc['generator']}` from {len(doc['inputs'])} input file(s): "
        + ", ".join(f"`{name}`" for name in doc["inputs"]),
        "",
        "Secrets: "
        + (
            "redacted on ingest (AA B1 / 33 B2 payloads zeroed)."
            if doc["secrets_redacted"]
            else "**kept; do not share this file.**"
        ),
        "",
    ]
    if doc["sessions"]:
        lines += [
            "## Sessions",
            "",
            "| source | software | board / transport | at | lights | probe |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for s in doc["sessions"]:
            lines.append(
                f"| {SOURCE_LABELS.get(s.get('source'), s.get('source'))} | {s.get('software', '')} | "
                f"{s.get('board') or s.get('transport') or s.get('adapter') or ''} | {s.get('at', '')} | "
                f"{s.get('lights', '')} | {s.get('probe', '')} |"
            )
        lines.append("")
    lines += ["## Devices", ""]
    for dev in doc["devices"]:
        v = dev["verdict"]
        badge = f" {BADGE.split(' ')[0]}" if dev["dance_party_certified"] else ""
        lines += [f"### {dev['model'] or 'unknown model'} at {dev['address']}{badge}", ""]
        rows = [
            ("advertised name", dev["name"] or "-"),
            (
                "model",
                (dev["model"] or "-") + ("" if dev["known_model"] else " (not in the model table)"),
            ),
            ("firmware / hardware", f"{dev['firmware'] or '?'} / {dev['hardware'] or '?'}"),
            (
                "pact / encryption flag",
                f"{dev['pact'] or '?'} / {dev['encrypted_flag'] if dev['encrypted_flag'] is not None else '?'}",
            ),
        ]
        if dev["gatt"] or dev["bgc"]:
            rows.append(("GATT / 2b12", f"{dev['gatt'] or '?'} / {dev['bgc'] or '?'}"))
        if dev["table_profile"]:
            rows.append(
                (
                    "table profile",
                    f"{dev['table_profile']['description']}: colour {dev['table_profile']['color_submode']}, brightness 0-{dev['table_profile']['brightness_scale']}",
                )
            )
        rows += [
            ("evidence sources", ", ".join(SOURCE_LABELS.get(s, s) for s in dev["sources"])),
            ("seen", f"{dev['first_seen'] or '?'} to {dev['last_seen'] or '?'}"),
        ]
        lines += [*_md_table(rows), ""]
        lines += [
            f"**Verdict: {v['family']}**, session security `{v['security']}`, confidence **{v['confidence']}** "
            f"({_CONFIDENCE_TEXT.get(v['confidence'], '')}).",
            "",
        ]
        lines += [f"- {r}" for r in v["reasons"]] + [""]
        if v["control_dialects"]:
            lines += [f"Control dialects with an echo: {', '.join(v['control_dialects'])}.", ""]
        lines += ["| capability | evidence | basis |", "| --- | --- | --- |"]
        for cap, info in dev["capabilities"].items():
            lines.append(f"| {cap} | {info['level']} | {info['basis']} |")
        lines.append("")
        r = dev["reads"]
        lines += [
            f"Reads: {r['answered']} answered of {r['sent']} sent, {r['timeouts']} timed out, "
            f"{r['bad_frames']} bad checksums, {r['acks']} write acks. "
            f"State seen: {json.dumps(dev['state_seen']) if dev['state_seen'] else 'none'}. "
            f"Secret handed out: {'yes (unbound light)' if dev['secret_present'] else 'no'}.",
            "",
        ]
        if dev["probe"]:
            lines += [
                f"Probe writes: {', '.join(dev['probe']['writes']) or 'none logged'}; echoed: {', '.join(dev['probe']['echoed']) or 'none'}.",
                f"Restoration: {dev['restoration']['restored'] or '?'}"
                + (f" ({dev['restoration']['note']})" if dev["restoration"]["note"] else "")
                + ".",
                "",
            ]
        if dev["observations"]:
            lines += ["Operator observations:", ""]
            lines += [
                f"- {o.get('observed')} ({o.get('capability', 'other')}: {o.get('outcome', '?')})"
                for o in dev["observations"]
            ]
            lines.append("")
        if dev["error"]:
            lines += [f"Session error: {dev['error']}", ""]
        untested = [c for c, i in dev["capabilities"].items() if i["level"] == "untested"]
        lines += [
            "Not established by this evidence: "
            + (", ".join(untested) if untested else "nothing in the capability list")
            + ". A `verified` colour dialect means the firmware stored the probe colour; whether every segment lit is an operator observation.",
            "",
        ]
    lines += [
        "## How to read this",
        "",
        "- *verified*: a write followed by the light reporting that exact value back.",
        "- *probable*: every reading agrees, but no controlled write proved it.",
        "- *inferred*: from the light's own status reply or the model tables, nothing written.",
        "- *observed*: an operator saw it on the strip.",
        "- *untested*: no evidence either way; an opcode existing in another family is not evidence.",
        "",
        f"{BADGE} marks evidence gathered while a Flipper was running Dance Party, Live DJ or Census. "
        "It does not mean every capability was tested or that any suite passed.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_observation(text: str) -> dict[str, Any]:
    """``ADDRESS=what you saw[;capability=rgb;outcome=worked]``."""
    address, _, rest = text.partition("=")
    if not rest:
        raise argparse.ArgumentTypeError("observation must look like ADDRESS=text")
    parts = rest.split(";")
    obs: dict[str, Any] = {
        "summary": "observation",
        "schema": census.SCHEMA,
        "source": "manual",
        "address": address.strip().upper(),
        "observed": parts[0].strip(),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    for extra in parts[1:]:
        key, _, value = extra.partition("=")
        if key.strip() in ("capability", "outcome", "dialect"):
            obs[key.strip()] = value.strip()
    return obs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "inputs", nargs="+", type=Path, help="JSONL logs and/or HA diagnostics .json files"
    )
    parser.add_argument(
        "--source", choices=sorted(SOURCE_LABELS), help="override the source of every input"
    )
    parser.add_argument(
        "--observe",
        action="append",
        type=parse_observation,
        default=[],
        help="ADDRESS=what you saw[;capability=rgb;outcome=worked]",
    )
    parser.add_argument("--json", type=Path, help="write the evidence document here")
    parser.add_argument("--markdown", type=Path, help="write the report here (default: stdout)")
    parser.add_argument(
        "--keep-secrets",
        action="store_true",
        help="do not redact pairing secrets (private captures only)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    evidence = Evidence(keep_secrets=args.keep_secrets)
    for path in args.inputs:
        if not path.exists():
            print(f"no such file: {path}", file=sys.stderr)
            return 2
        load_input(evidence, path, source=args.source)
    for obs in args.observe:
        ingest_records(evidence, [obs], source="manual", label="--observe")
    doc = analyse(evidence)
    if args.json:
        args.json.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    markdown = render_markdown(doc)
    if args.markdown:
        args.markdown.write_text(markdown, encoding="utf-8")
    if not args.markdown:
        print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
