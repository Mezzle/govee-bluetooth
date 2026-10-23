# Later authorised H6159 mains recovery test

This test followed the initial evidence commit `1fdcd31`. The earlier decision
in README.md to leave the shared bedside outlet ON was correct for that phase.
The user subsequently explicitly authorised cycling it, explaining that its
other loads were unimportant and nobody was at home. Only then was
`switch.bedroom_martin_bedside_sockets` cycled. Parent confirmed no hardware
activity during this follow-up; BLE ownership was reclaimed for the test.

The rig remained the isolated exact parent commit
`7558bcad99a732d7133b9758017e711de6ebe2d3`, under
`/tmp/govee-bench-7558bcad`, using the previously recorded hashes and environment.
No live HA installation files were replaced and no reload/restart was needed.

Pre-cycle H6159 reads confirmed ON, raw brightness26, RGB(214,226,255).
AA06 timed out again. The HA switch was confirmed ON before the cycle, OFF
at 20:16:34.961 UTC, turned ON at 20:16:38.114 UTC, and confirmed ON at
20:16:41.169 UTC. The off service was issued at 20:16:32.917 UTC: about five
seconds between off/on service calls. A `finally` block restored ON even if
the intermediate checks failed. The switch transition log is
`bedside-mains-cycle.jsonl`.

H6159 reconnected with one pre-connected disconnect callback, exposed
2b11 `read`/`write-without-response`, selected WNR, and reported 2b12 `00`.
A one-second readiness delay preceded the initial power/brightness/mode reads.
Those reads confirmed its saved ON/26/RGB(214,226,255) state before any writes.
Brightness30, RGB(18,52,86), CCT4300, RGB(214,226,255), brightness26, and ON
all echoed on readback. Final reads confirmed ON/26/RGB(214,226,255), with
firmware1.07.02. A real idle `--hold 6` then yielded three heartbeat TX/RX pairs.
The console capture `h6159-after-bedside-cycle.txt` preserves each result.

Both mains switches were separately confirmed ON after testing in
`bedside-cycle-final-mains.json`; TV mains was not cycled during this follow-up.
The H6159 connection closed normally and BLE ownership was released afterward.

`bedside-cycle-h6159.jsonl` contains only the target's permitted plaintext
01/04/05/06 frames plus verification summaries, covering pre/post-cycle reads.
The collector checked frame length and XOR checksum before saving. No HA
credentials, unrelated device responses or sensitive pairing traffic are saved.
These remain direct BlueZ rig results. The earlier HA/proxy deployment blocker
is unchanged; this mains test does not validate the HA transport implementation.
