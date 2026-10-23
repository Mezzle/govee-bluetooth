# Additional strip bench session — 2026-10-23 UTC

Independent hardware session on discoball, using its BlueZ adapter through
`homelab-operator@discoball`. No production integration code changed here.
Local starting revision: `535e1e936a0dd4e3eb9d99100f311674ffc75f93`.
Parent implementation tested separately: `7558bcad99a732d7133b9758017e711de6ebe2d3`
(based on `58093df`). No merge/cherry-pick into this artifact branch.

## Devices and restoration

- H6159 `ihoment_H6159_8E17`, `A4:C1:38:9C:8E:17`: pact `1/1`, plaintext,
  firmware **1.07.02** when AA06 answered. Actual session-start state was
  **ON, raw brightness 26, RGB (214,226,255)**, confirmed by initial reads.
  User explicitly agreed to restoring this state instead of the earlier OFF
  snapshot. Final exact-commit status/heartbeat capture confirms restoration.
- H6181 `GBK_H6181_FB1E`, `60:74:F4:6A:FB:1E`: pact `10/1`, plaintext,
  firmware **2.03.11**. Start/end **ON, raw brightness 100, RGB (255,255,255)**.
- Both report control characteristic 2b11 properties `read`,
  `write-without-response`; both rigs selected `response=False`.
  H6159's 2b12 read was `00` (plaintext); H6181 did not report 2b12.
  Advertisements were filtered to these two devices before saving.

## Findings

1. Native writes worked with protocol ACKs and independent state readback.
   H6159 raw brightness 10,50,100,128,255 all echoed; restored to 26.
   H6181 10,50,100 all echoed; restored to 100. This establishes storage/
   readback of native values, **not** a photometric brightness curve.
2. RGB (18,52,86) echoed on native dialects 02 / 0D. Native-only active probes
   detected 02 / 0D and reported `restored=True`. No cross-dialect sweep,
   scenes, reset, firmware update, pairing or credential commands were sent.
3. CCT 2700/4300/6500 K echoed. H6159 returns white flag + tint rather than a
   Kelvin scalar: (255,167,87), (255,213,179), (255,254,250). H6181 returns
   Kelvin values directly. All experiments ended back in original RGB mode.
   Readback validates wire behavior; no human visual/colorimeter assessment.
4. H6159 AA06 is intermittent: initial and later first-read AA06 timeouts,
   but later reads in the same session returned 1.07.02. Do not classify its
   firmware query as unsupported from a single timeout.
5. Reconnect/read baseline, installed rig, three fresh CLI processes/device:
   H6159 first power read timed out **3/3**, following brightness/mode replied
   **3/3**. H6181 all three reads replied **3/3**. Exit code alone misses this
   failure: the rig exits 0 on unanswered reads. Captures and JSON verdicts
   preserve that distinction. End-to-end CLI times include SSH and discovery:
   H6159 12.658–16.587 s, H6181 10.148–14.466 s. Pre-connected disconnect
   callbacks were H6159 [0,1,1], H6181 [1,2,0]; these are not proven retry counts.
6. Same installed rig with only `sleep 1` before reads: H6159 first power and
   following reads passed **3/3** (11.032–18.668 s). Supports readiness timing
   as a hypothesis; small sequential sample, order effects and competition
   from existing HA remain uncontrolled. No readiness fix added in this branch.

## Exact parent commit and power recovery

`git archive 7558bcad...` was extracted into isolated remote directory
`/tmp/govee-bench-7558bcad`; installed `~/govee-bluetooth` was not overwritten.
File hashes are in `exact-7558bcad-provenance.txt`; installed hashes, Python
3.13.5 and bleak 3.0.2 are in `remote-provenance.txt`. Installed `.rig-commit`
contains only `8bbd66f`; git is absent remotely, so hashes are authoritative,
not a claim that the installed tree exactly equals that historical revision.

The exact parent rig selected WNR, read both strips, ACKed/echoed native
brightness/RGB/CCT writes and restoration. H6159 exact verification included
one-second pre-read delay. Exact `status --hold 6 --show-heartbeat` captures
show three AA01 TX/RX heartbeat pairs for each strip. `sleep` action itself
holds the rig busy lock, so it is **not** evidence of heartbeat execution.

HA mapping: `light.tv_bias_light` exposes H6181's Bluetooth address;
`switch.tv_bias_light` is the confirmed mains switch. It was OFF for about
five seconds, then restored ON via a `finally` block; off/on states were
confirmed. H6181 recovered and exact-commit writes/readback succeeded.
`tv-mains-cycle.jsonl` records only the target/service/state/timestamps.
User confirmed bedside sockets power H6159 **and other devices**. Therefore
`switch.bedroom_martin_bedside_sockets` was left ON throughout. No unrelated
outlet was cycled. `ha-final-state.json` records both switches ON and HA's
H6181 ON/white/255 UI brightness (device raw100).

One H6181 heartbeat process was initially launched before the preceding
verification process exited. Its connection-setup callbacks are confounded
and excluded from reconnect statistics. `h6181-exact-clean.txt` is a fresh,
serial exact-commit write/read/hold repeat to remove that overlap ambiguity.

## HA/proxy validation remains blocked

Existing local HA helper/token enabled core API reads and the narrowly
scoped authorised TV switch services. Credentials were never printed or
copied into this worktree. HA reports 2026.10.2 and config directory `/config`.
Read-only homelab infrastructure describes a separate Dell HA host; charm
hosts supporting services, not the HA installation. No dedicated HA SSH
host/install/reload path was established. Supervisor info/addons/core API
requests returned HTTP401 with the existing core token. No live files were
replaced and no HA restart/reload was attempted. These are **direct BlueZ rig
results**, not proof of the HA device class or ESPHome proxy path. Parent
thread was notified that live proxy verification remains pending.

## Artifacts and validation

`additional-*.jsonl` are installed-rig raw frames + verification summaries;
`exact-*.jsonl` are parent-commit records. Console captures preserve commands'
read/write verdicts, properties, firmware and restoration. `fixtures.json`
contains selected real read replies for downstream decoder tests. Collection validated **348 frames** and generated **29 fixture cases**. It
accepted only 20-byte AA/33 frames for commands 01/04/05/06, verified every XOR
checksum, and excluded census/session/other-device records. No secret reads,
handshakes, BLE pairing traffic or full HA responses were saved.

`reconnect.py` and `reconnect-delay1.py` reproduce the read-only comparison
from repo root; they require the stated SSH host/installed rig and do not
control mains power. They save per-trial verdicts, including missing replies.
