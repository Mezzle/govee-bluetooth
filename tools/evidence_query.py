#!/usr/bin/env python3
"""Read values back out of a govee-evidence/1 log, for scripts such as tools/discover.sh.

Only the records after the last ``session`` header count, since a re-run
appends to the same file. Prints plain text, one value per line; prints
nothing (and exits 0) when the value is not there, so a shell caller can
fall back to asking.

    evidence_query.py FILE light FIELD          a field of the last light summary
    evidence_query.py FILE ads                  scanned lights: address|name|model|pact|enc|rssi, strongest first
    evidence_query.py FILE verify ACTION [ARG]  yes/no: did the last ACTION (with ARG among its args) echo?
    evidence_query.py FILE echoed ACTION        first args of every ACTION that echoed, comma-separated

Dict fields come out as ``key=value`` pairs joined with commas
(``02=rejected,0D=accepted``), booleans as ``yes`` / ``no``, null as nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def records(path: Path) -> list[dict[str, Any]]:
    """The records of the latest run in ``path``."""
    out: list[dict[str, Any]] = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("summary") == "session":
            out = []
        out.append(rec)
    return out


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict):
        return ",".join(f"{k}={text(v)}" for k, v in value.items())
    if isinstance(value, list):
        return ",".join(text(v) for v in value)
    return str(value)


def light_field(recs: list[dict[str, Any]], field: str) -> str:
    lights = [r for r in recs if r.get("summary") == "light"]
    return text(lights[-1].get(field)) if lights else ""


def ads(recs: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, dict[str, Any]] = {}
    for r in recs:
        if r.get("summary") == "advertisement":
            seen[str(r.get("address"))] = r
    ordered = sorted(seen.values(), key=lambda r: -(r.get("rssi") or -999))
    enc = {True: "yes", False: "no", None: "?"}
    return [
        "|".join(
            (
                str(r.get("address", "")),
                str(r.get("name") or ""),
                str(r.get("model") or "-"),
                str(r.get("pact") or "-"),
                enc.get(r.get("encrypted"), "?"),
                str(r.get("rssi", "")),
            )
        )
        for r in ordered
    ]


def _first_arg(rec: dict[str, Any]) -> str:
    args = rec.get("args") or []
    return text(args[0]) if args else ""


def verify(recs: list[dict[str, Any]], action: str, arg: str | None) -> str:
    matches = [
        r
        for r in recs
        if r.get("summary") == "verify"
        and r.get("action") == action
        and (arg is None or arg in [text(a) for a in (r.get("args") or [])])
    ]
    return text(matches[-1].get("echoed")) if matches else ""


def echoed(recs: list[dict[str, Any]], action: str) -> str:
    hits = [
        _first_arg(r)
        for r in recs
        if r.get("summary") == "verify" and r.get("action") == action and r.get("echoed")
    ]
    return ",".join(dict.fromkeys(hits))


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    recs = records(Path(argv[0]))
    what = argv[1]
    if what == "light" and len(argv) == 3:
        out = light_field(recs, argv[2])
    elif what == "ads":
        out = "\n".join(ads(recs))
    elif what == "verify" and len(argv) in (3, 4):
        out = verify(recs, argv[2], argv[3] if len(argv) == 4 else None)
    elif what == "echoed" and len(argv) == 3:
        out = echoed(recs, argv[2])
    else:
        print(__doc__, file=sys.stderr)
        return 2
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
