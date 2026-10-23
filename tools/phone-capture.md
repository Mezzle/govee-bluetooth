# Capture the phone's Govee conversation

`btmon` on the Pi records the Pi's own controller traffic. It can capture
advertisements and debug the rig's connections, but cannot record GATT traffic
between your phone and a light. To see the app's commands, record on the phone.
Only reproduce operations on a light you own or have permission to test.

## Android

1. Enable Developer options, then **Enable Bluetooth HCI snoop log**. Select
   **Full** if the phone offers logging modes. Restart Bluetooth before testing.
2. Start with the app disconnected so the capture includes connection setup.
   Open Govee Home, pair/connect the selected light, then make a short sequence
   of known changes (e.g. red, green, off). Note the times and visible effects,
   model and firmware separately. Do not reset an already bound light just to
   obtain a capture: connecting again can still capture session establishment.
3. Use **Take bug report** in Developer options and copy the resulting archive
   to your computer. Extract the HCI capture locally. A common archive path is
   `FS/data/misc/bluetooth/logs/btsnoop_hci.log`, but inclusion and paths vary
   by Android version and vendor. A bug-report ZIP is not itself a capture.
4. If no HCI file is present, follow [AOSP's bug-report extraction instructions](https://source.android.com/docs/core/connect/bluetooth/verifying_debugging)
   using `btsnooz.py` on the extracted **text** bug report. A filtered/in-memory
   log may omit values; extraction cannot recover data that was never recorded.
5. Turn snoop logging off afterwards. Keep both the bug report and capture private.

## iPhone and Mac

Follow [Apple's Bluetooth diagnostic logging instructions](https://developer.apple.com/bug-reporting/profiles-and-logs/?platform=ios&name=bluetooth)
for your current iOS version. Install the official Bluetooth logging profile on
the phone and use PacketLogger from [Additional Tools for Xcode](https://developer.apple.com/bluetooth/)
on the Mac. Connect the phone and choose **File → New iOS Trace** where supported,
then reproduce connection and colour changes as above. Apple demonstrates this
workflow in [What's New in Core Bluetooth](https://developer.apple.com/videos/play/wwdc2019/901/).
Save the capture, then remove/disable logging as Apple's instructions describe.
Availability can vary with the installed iOS and developer tools versions.

## Copy and inspect locally

Copy the extracted capture to the Pi, for example under the git-ignored `bench/`
directory, or analyse it on your computer. Both bench wizards offer to inspect
an existing local file after the app step. Press Enter to skip and run later:

```sh
sudo apt install tshark             # Debian / Raspberry Pi OS; once only
python3 tools/analyse_capture.py bench/phone-pairing.btsnoop
```

The analyser runs offline without sudo. It accepts files supported by the local
tshark (including btsnoop, pcap/pcapng and supported PacketLogger formats). It
prints ATT counts grouped by connection handle, opcode and attribute handle,
and structural hints for 20-byte XOR frames and pairing-secret exchanges.
It withholds **all** ATT values, including unknown/encrypted values and truncated
secrets; it does not decrypt, recover credentials or add capabilities to a device
report. A capture with no ATT traffic is explicitly called out as insufficient.

The summary covers all links in the capture; link handles can be reused across
connections. It does not identify which link is your light. Inspect the original
locally in Wireshark with `btatt`, using connection setup and timestamps to find
your device. Unknown bytes do not establish encryption, and an observed write
is not proof that a command was accepted. Capture-derived findings need a model,
firmware, timestamps, matching responses and visible effects before claiming support.

Do not publish a bug report or raw capture: it can contain pairing secrets,
session keys, device identifiers and unrelated phone traffic. The summary avoids
payloads but still needs review before sharing. Keep private captures separate
from the rig's shareable JSONL evidence. No phone capture is automatically copied
into a generated compatibility report.


## Firmware availability and update acceptance

This session targets the owned H6159 (hardware 1.00.02, firmware 1.07.02)
and H6181 (hardware 2.01.01, firmware 2.03.11). See the
[mechanism research](../docs/firmware-update-research-2026-10-23.md).
There are two independent conversations to capture:

- **App to Govee servers:** update-check request/response, eligibility, offered
  version and firmware download metadata.
- **App to controller:** Bluetooth connection/security setup, preparation and,
  during a later qualified update, image transfer and device responses.

A Bluetooth HCI capture contains the second conversation, not HTTPS request
bodies. A network packet capture alone generally leaves HTTPS bodies encrypted.
[AOSP describes HCI logging](https://source.android.com/docs/core/connect/bluetooth/verifying_debugging);
[Android's network security documentation](https://developer.android.com/privacy-and-security/security-config)
explains CA trust and certificate pinning. Installing a user CA does not guarantee
the release app trusts it. We have not established whether Govee pins certificates
or uses an additional application encryption envelope on its update API.

### First pass: check availability without installing

1. Record phone OS, Govee Home version, account region, controller model,
   hardware/firmware versions and UTC time. Save each device's run separately
   under the git-ignored `bench/firmware-capture/` directory.
2. Start phone Bluetooth logging using the platform instructions above, before
   opening the device. Pause any integration/bench connection to that device
   so the official app can connect. Do not unpair or factory-reset it.
3. Open the device's firmware/settings page and invoke its update check if one
   is offered. Record the exact path used and the displayed result, including
   “no update” or “unsupported”. The app UI varies by version. Do not install
   an update during this metadata pass.
4. Collect the phone log and any supported network diagnostic export.
   For update metadata, inspect an app-provided export/cache if accessible,
   or a local HTTPS inspection setup on the test phone when the app supports
   it. A failed TLS connection proves neither “no update” nor absence of an API.
5. Preserve privately: request host/path/method, field names, response status,
   SKU/hardware match, offered version, size, checksum/signature metadata,
   file URL and eligibility result. Keep tokens, cookies, device identities
   and signed URLs in the private capture; publish a reviewed redacted summary.
   If only a UI result was obtained, label endpoint/schema unknown.
6. Stop logging and restore any paused integration connection.

An installed Android APK and feature splits are another useful artifact:
local static inspection can locate endpoint strings, request models and OTA
classes without account access. Record the app/package version and SHA-256.
Strings establish candidates; only a matching observed request establishes the
live API. No APK is available in this workspace yet.

### Later pass: transfer and signature validation

A successful official transfer is useful baseline evidence only after a matching
image has been saved, its target established and recovery investigated. Capture
both metadata/download and Bluetooth traffic for the same run, preserving the
original file and SHA-256. A “no update” result does not authorize replaying an
older or unrelated image to manufacture a capture.

Our eventual custom-image experiment must distinguish:

- connection/write access with no existing bond or device secret;
- permission to enter update mode;
- header acceptance and requests for image blocks;
- image storage and integrity validation;
- bootloader authentication and execution of our own benign test image.

Header acceptance or an ATT write response does not prove unsigned execution.
A corrupted-file rejection may be an ordinary CRC failure rather than signature
enforcement. Resolve the exact chipset, image format, recovery procedure and a
restorable original before constructing a test image. Use a minimal observable
marker with no credential collection, network access or persistence changes
beyond the firmware experiment. Do not send random bytes or use another model's
image as the test.
