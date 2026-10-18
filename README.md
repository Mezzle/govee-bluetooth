<img src="assets/icon.svg" width="64" height="64" alt="">

# Govee Bluetooth for Home Assistant

Local control of Govee LED strips over Bluetooth LE. No cloud, no Wi-Fi, no
Govee account. Works with any Bluetooth adapter Home Assistant can use,
including **ESPHome and Shelly Bluetooth proxies**, so the strips only need to
be in range of a proxy rather than the Home Assistant host.

Built for the **H6159**, whose Wi-Fi radio tends to drop off the network after
a day or two while its Bluetooth side keeps answering. The same wire protocol
is used by the rest of that generation of strips (see
[Supported models](#supported-models)).

## What you get

- Automatic discovery when a strip advertises near any connectable adapter or proxy.
- `light` entity with on/off, brightness, RGB colour, colour temperature
  (emulated with the RGB LEDs, the way the Govee app does it) and the
  firmware's built-in scenes as effects.
- Real state read-back, not just optimistic state: power, brightness and
  colour are polled on a configurable interval, and the keep-alive echo keeps
  the power state fresh while a connection is open.
- Connections are short-lived by default so a proxy's limited connection
  slots are shared fairly between strips, with a configurable linger so a
  burst of commands reuses one connection.
- Diagnostics download with the last known state and Bluetooth reachability.

## Requirements

- Home Assistant 2025.6 or newer.
- A **connectable** Bluetooth source. For ESPHome proxies that means
  `bluetooth_proxy: active: true` in the device config. A proxy without
  active connections can discover the strip but cannot control it.
- The strip must not be connected to the Govee app at the same time. Govee
  firmware accepts one BLE connection; close the app if commands fail.

## Installation

### HACS

1. HACS → three-dot menu → **Custom repositories**.
2. Add this repository URL with category **Integration**.
3. Install **Govee Bluetooth** and restart Home Assistant.

### Manual

Copy `custom_components/govee_bluetooth` into your `config/custom_components/`
directory and restart Home Assistant.

## Setup

Strips are discovered automatically. Once a supported strip advertises, a
notification appears under **Settings → Devices & services**. Confirm it,
give it a name, done.

To add one by hand, use **Add integration → Govee Bluetooth**. It lists every
Govee light currently visible to a connectable adapter. You can override the
model if the advertised name does not include it.

### Options

Open the integration's **Configure** dialog to tune:

| Option | Default | Notes |
| --- | --- | --- |
| Poll interval | 120 s | Each poll opens a brief connection and reads power, brightness and mode. |
| Keep connection open after a command | 8 s | Consecutive commands (e.g. dragging a slider) reuse the connection. `0` disconnects immediately. |
| Brightness scale | 0-255 | Confirmed on the H6159: the strip echoes raw values above 100 back unchanged. Some newer firmware, including some H616x/H618x units, uses 0-100. If brightness looks wrong, switch. |

## Supported models

Discovered automatically (advertised as `ihoment_`, `Govee_`, `GBK_` or `GVH` + model):

| Model | Status |
| --- | --- |
| H6159 | Verified on hardware (firmware 1.07.02) through an ESPHome Bluetooth proxy: power, brightness, RGB, colour temperature and status read-back. |
| H6110, H6109, H614A, H614B, H614E | Same app module as the H6159 in the decompiled Govee app. Untested on hardware. |
| H6127, H6107, H6116, H6161, H6129, H6139, H6141, H6142 | Same legacy colour dialect per the decompiled app and community captures. Untested on hardware. |
| H6104, H6117, H6125, H6126, H6135, H6137, H6138, H6160, H6163, H6170, H6171, H6172, H6182, H6188, H6195 | Same legacy colour dialect per community captures. Untested on hardware. |
| H6181 | Verified on hardware (firmware 2.03.11) through an ESPHome proxy: power, brightness, RGB, colour temperature, scenes and read-back. Uses the 0x0D colour scheme and 0-100 brightness, unlike the rest of this table. |
| H7020, H7021 (outdoor string lights) | Same legacy colour dialect per community captures. Untested on hardware. |
| H6143, H6144, H619A, H619B, H619C, H619E, H619Z, H61A0, H61A1, H61A2, H61E1 (RGBIC strips) | Segmented `0x15` colour dialect with a whole-strip mask, brightness 0-100, per the decompiled Govee app. Whole-strip colour and white only; no scenes yet. Untested on hardware. |
| H6199 (RGBIC TV backlight) | Same `0x15` dialect. Untested on hardware. |
| H6052, H6056, H6059, H6061, H6062, H6065, H6072, H6076 (RGBIC light bars, lamps and panels) | Same `0x15` dialect. Untested on hardware. |

Some H616x and H618x firmware takes brightness as 0-100 rather than 0-255. If
a strip from that range jumps to full brightness at low slider values, switch the
**Brightness scale** option to 0-100.

RGBIC models drive every segment the same colour. Per-segment colour, music
mode and the RGBIC scene tables are not implemented yet.

Any other Govee light can be added manually and will be driven with the H6159
dialect. An RGBIC light that is not in the table will switch on and off but
ignore colour; open an issue with the model and it can be added as a profile.

## How it talks to the strip

All frames are 20 bytes: type, command, payload, zero padding, XOR checksum.
Writes go to characteristic `…2b11`, replies arrive as notifications on `…2b10`.

| Action | Frame |
| --- | --- |
| Power on / off | `33 01 01` / `33 01 00` |
| Brightness | `33 04 <level>` |
| Colour | `33 05 02 R G B 00` |
| White (colour temperature) | `33 05 02 FF FF FF 01 R G B` with a kelvin-derived tint |
| Colour (RGBIC) | `33 05 15 01 R G B 00 00 00 00 00 FF 7F`, the trailing bytes being a whole-strip segment mask |
| White (RGBIC) | `33 05 15 01 FF FF FF <kelvin hi> <kelvin lo> R G B FF 7F` with a kelvin-derived tint |
| Scene | `33 05 04 <id>` |
| Keep-alive / power query | `AA 01 00`, answered with `AA 01 <0|1>` |
| Brightness query | `AA 04 00` |
| Mode query | `AA 05 01`; an RGBIC reply is `AA 05 15 01 <kelvin hi> <kelvin lo>`, kelvin 0 meaning colour mode |

Govee firmware drops a GATT connection that stays silent for about ten
seconds, so the integration sends the keep-alive every two seconds while a
connection is open. The reply doubles as a power-state update, which is how a
change made with the physical remote shows up in Home Assistant between polls.

Sources: the H6127 and H6159 reverse-engineering repositories by
BeauJBurroughs and jurassic-marc, the H617A work by rnodern, and the
decompiled-app protocol reference in Brady-Woods/govee-ble-local.

## Troubleshooting

- **"not visible to any connectable Bluetooth adapter or proxy"**: nothing
  that can make connections currently sees the strip. Check the proxy has
  `active: true`, or move it closer. The diagnostics download includes Home
  Assistant's own reachability explanation.
- **Commands time out while the Govee app is open**: close the app. The strip
  only accepts one connection.
- **Brightness looks wrong**: switch the brightness scale in Options.
- **Enable debug logging** to see every frame:

  ```yaml
  logger:
    logs:
      custom_components.govee_bluetooth: debug
  ```

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements_test.txt
constraints="$(.venv/bin/python -c 'import homeassistant, pathlib; print(pathlib.Path(homeassistant.__file__).parent / "package_constraints.txt")')"
.venv/bin/pip install -c "$constraints" -r requirements_test_bluetooth.txt
.venv/bin/pytest
.venv/bin/ruff check .
```

The protocol layer (`protocol.py`) has no Home Assistant dependency and its
tests pin every frame to published captures. The entity and config flow tests
run against Home Assistant's real Bluetooth manager with a fake H6159 that
answers status queries the way real firmware does.
