# Bench test rig

`govee_rig.py` talks to a Govee light from a plain Linux box (a Raspberry Pi
Zero W next to the strip is the intended setup) using bleak on BlueZ. It
imports `protocol.py`, `models.py` and `probe.py` from this checkout, so the
frames it sends and the way it decodes replies are exactly what the Home
Assistant integration does. There is no second implementation to drift.

Use it to confirm a model's colour sub-mode and brightness scale before adding
it to the table in `models.py`, to try frames the integration does not send
yet (music, segments, scene IDs on RGBIC firmware), and to capture a clean
TX/RX log that can be diffed against an HA diagnostics download.

## Setting up the Pi

Raspberry Pi OS Bookworm (Python 3.11) or newer. The rig needs 3.11+.

```sh
sudo apt install -y git python3-venv bluez
git clone https://github.com/Mezzle/govee-bluetooth.git
cd govee-bluetooth
python3 -m venv ~/rig && ~/rig/bin/pip install bleak
bluetoothctl power on
~/rig/bin/python tools/govee_rig.py scan
```

Two Pi Zero W notes. The onboard radio is Bluetooth 4.1 and shares its
antenna with Wi-Fi, so a busy Wi-Fi link makes BLE flaky; SSH in over USB
gadget Ethernet or accept the odd dropped connection. And make sure the Govee
app on your phone is closed: the light accepts one connection at a time.

## Commands

```sh
RIG="~/rig/bin/python tools/govee_rig.py"

$RIG scan --seconds 10                     # address, name, model, table dialect, RSSI
$RIG status A4:C1:38:12:34:56              # the four reads, decoded next to the hex
$RIG status A4:C1:38:12:34:56 --active     # write-and-echo probe, same code HA runs
$RIG status A4:C1:38:12:34:56 --active --candidates 15,0D,02
$RIG send   A4:C1:38:12:34:56 on rgb 255 0 0 bri 50
$RIG verify A4:C1:38:12:34:56 rgb 18 52 86 ct 4300 scene 5 bri 30
$RIG send   A4:C1:38:12:34:56 raw 33 05 15 01 ff 00 00 00 00 00 00 00 ff 7f
$RIG shell  A4:C1:38:12:34:56 --verify     # one connection, type commands
```

Every connect first does a short scan for the advertised name so the model
and its table profile are known. `--model H6167` skips that, `--submode 0B`
overrides the colour sub-mode the table gives, and `--no-scan` connects blind.

The command vocabulary is the same for `send`, `verify` and `shell`:

| token | what goes out |
|-------|---------------|
| `on` / `off` | `33 01 01` / `33 01 00` |
| `bri N` | `33 04 N`, N is the raw device byte, not the HA 1-255 value |
| `rgb R G B` | `protocol.rgb_frame` in the current sub-mode |
| `ct K` | `protocol.color_temp_frame` in the current sub-mode (`0x0B` has none) |
| `scene ID` | `33 05 04 ID` |
| `raw HEX...` | padded to 19 bytes and checksummed, or sent as-is if 20 bytes |
| `read` | version, power, brightness and mode queries |
| `mode`, `power`, `brightness`, `version` | one query |
| `submode 02/0B/0D/15` | switch the sub-mode for later `rgb` / `ct` tokens |
| `sleep S` | pause, heartbeat still running |

`verify` reads back after each write and prints one of `ECHOED`, `DIFFERS`
or `NO REPLY` per field, using the same echo rules as `probe.py`. A `0x15`
`rgb` write only confirms the sub-mode byte, because that family's mode reply
carries kelvin and no RGB; follow it with a `ct` write for a real echo.

`shell` keeps one connection open with the 2 s heartbeat, which saves the
few seconds a reconnect costs between experiments. `verify on` / `verify off`
toggles read-back, `quit` leaves.

## Logging

`--log bench.jsonl` appends every frame in both directions as one JSON object
per line with `at`, `direction`, `frame`, `note`, `decoded` and `address`.
The first four keys match the `frames` list in a Home Assistant diagnostics
download, so the two can be compared directly. Heartbeats are logged but not
printed unless `--show-heartbeat` is given.

For a full HCI capture, run `btmon` in a second terminal before connecting:

```sh
sudo btmon -w govee-$(date +%Y%m%d-%H%M).btsnoop
```

Open the `.btsnoop` file in Wireshark; filter `btatt` to see just the GATT
writes and notifications. `btmon` with no arguments prints live to the
terminal instead.

## Confirming a new model

1. `scan` to get the address and the model from the advertised name.
2. `status ADDR` with the light showing a plain colour set from the Govee app.
   The mode reply names the sub-mode without writing anything.
3. If the light was in a scene or music mode, `status ADDR --active` writes a
   test colour in each candidate sub-mode and reports which one echoed.
4. `verify ADDR bri 100` then `verify ADDR bri 200`: a light on the 0-100
   scale reports the second as unchanged or clamped.
5. `verify ADDR scene 0 scene 1 scene 4 ...` to check which legacy scene IDs
   an RGBIC firmware accepts.
6. Add the model to `models.py` with what you found, and put the rig's log in
   the pull request.

Music mode is not in `protocol.py` yet because the decompiled app shows six
different music sub-mode bytes across families. Use `raw` to try candidates
(`33 05 03 ...` legacy, `33 05 11 ...` and `33 05 13 ...` for the RGBIC
families) and watch the mode reply.

## If the light connects but ignores every write

Some families (the dreamcolor RGBIC strips, h61d3 neon/net lights, string
lights, hollow lamps) gate control on an 8-byte per-device secret that the
app writes as `33 B2 <8 bytes>` right after every connect. See
`docs/govee-ble-protocol.md` section 10. In order of effort:

1. `send ADDR raw aa b1 00`: a light in its pairing window answers `AA B1 01`
   followed by the 8 secret bytes. A factory-reset light should answer.
2. Run `btmon -w` while the Govee app sets a colour: the `33 B2` write is the
   first or second frame after connect and is plaintext on these families.
3. The Govee account device record carries it as `bleExt.secretCode` (base64),
   through the app's private API on `device.govee.com`, not the public key API.

Then `send ADDR raw 33 b2 <8 hex bytes> rgb 255 0 0` to test whether the
secret unlocks writes.

## Characterising a new light end to end

`tools/characterise.sh LABEL` is a guided bench session for one light, from
the box to a filled-in record. It runs every rig command itself and stops
only for the physical steps: power on, pair in the app, pick a colour. It
reads the pairing secret with `AA B1` before the app ever binds the light,
runs `btmon` while you pair as a backup capture of the app's first frames,
reads back what the app set for red, white, a scene and music, runs the
active probe, tries a colour write in all four dialects with an `AA A5`
segment read, then brightness scale, colour temperature and scene codes.

Findings go to `bench/LABEL.env`, frame logs to `bench/LABEL-*.jsonl` and
captures to `bench/LABEL-*.btsnoop`. `bench/` is git-ignored because the
record may hold the secret. Re-running offers each recorded value as the
default. `PYTHON=/path/to/python` overrides the default `~/rig/bin/python`.
