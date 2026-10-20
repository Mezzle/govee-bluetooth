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
