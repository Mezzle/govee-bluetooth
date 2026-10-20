#!/usr/bin/env python3
"""Inspect a local HCI capture with tshark; never print ATT values or keys."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from functools import reduce
from operator import xor
from pathlib import Path


def frame_kind(value: str) -> str:
    """Describe structure only, including truncated secrets, without exposing data."""
    try:
        frame = bytes.fromhex(value.replace(":", ""))
    except ValueError:
        return "unparsed value (withheld)"
    if not frame:
        return "no value"
    if frame[:2] in (b"\x33\xb2", b"\xaa\xb1"):
        return "pairing-secret exchange candidate (payload withheld)"
    if len(frame) == 20 and frame[0] in (0x33, 0xAA):
        if reduce(xor, frame) == 0:
            return f"20-byte XOR-valid plaintext candidate, opcode {frame[1]:02X}"
        return "20-byte plaintext-like value, invalid XOR"
    return f"unclassified {len(frame)}-byte value (withheld; encryption not established)"


def summarise(rows: str) -> str:
    counts: Counter[tuple[str, str, str, str]] = Counter()
    packets = 0
    for row in rows.splitlines():
        fields = row.split("\t")
        if len(fields) != 4:
            raise ValueError("unexpected tshark field layout")
        connection, opcode, handle, value = fields

        # Numeric fields only: do not echo arbitrary dissector output or identifiers.
        def numeric(text: str) -> str:
            try:
                return f"0x{int(text, 0):04x}" if text else "unknown"
            except ValueError:
                return "unknown"

        counts[(numeric(connection), numeric(opcode), numeric(handle), frame_kind(value))] += 1
        packets += 1
    lines = [f"ATT packets: {packets}"]
    if not packets:
        lines.append("No ATT traffic: this capture cannot show the app's GATT commands.")
        lines.append(
            "Check phone logging was enabled before connecting; Pi-only btmon cannot capture it."
        )
    for (connection, opcode, handle, kind), count in counts.items():
        lines.append(f"  link {connection}, ATT {opcode}, attribute {handle}: {count} x {kind}")
    lines.extend(
        [
            "ATT opcodes: 0x12 write request, 0x52 write command, 0x1b notification, 0x1d indication.",
            "Links/handles are local to this capture and may be reused after reconnecting.",
            "Candidates are structural hints across all captured links, not verified Govee support.",
            "A write does not prove acceptance, authentication or an observed effect.",
            "Values and keys withheld. Keep the original capture private; inspect it locally in Wireshark.",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "capture", type=Path, help="extracted btsnoop, pcap/pcapng or PacketLogger file"
    )
    args = parser.parse_args(argv)
    if not args.capture.is_file():
        print("Capture file not found.", file=sys.stderr)
        return 1
    command = [
        "tshark",
        "-n",
        "-r",
        str(args.capture.resolve()),
        "-Y",
        "btatt",
        "-T",
        "fields",
        "-E",
        "occurrence=f",
    ]
    for field in ("bthci_acl.handle", "btatt.opcode", "btatt.handle", "btatt.value"):
        command.extend(["-e", field])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        if result.returncode:
            # Dissector stderr can include capture contents; do not echo it.
            print(
                "tshark could not read the capture. Extract the HCI log from the bug report first, "
                "and check that your tshark version supports the file and fields.",
                file=sys.stderr,
            )
            return 1
        print(summarise(result.stdout))
    except FileNotFoundError:
        print(
            "Install tshark first (Debian/Pi OS: sudo apt install tshark). No capture privileges "
            "are needed for this offline analysis.",
            file=sys.stderr,
        )
        return 1
    except (subprocess.TimeoutExpired, ValueError):
        print(
            "Capture analysis timed out or returned an unsupported field layout.", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
