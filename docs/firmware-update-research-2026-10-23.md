# Firmware update mechanisms — 2026-10-23

Both bench controllers expose plausible update transports. We can build an offline
image inspector and investigate update availability; we cannot yet establish a
compatible image, usable flash capacity, successful transfer, or recovery path.
This investigation performed no device operations, authenticated cloud calls,
update preparation, or firmware downloads/flashes.

## Evidence levels and measured inventory

**Measured** means the Discoball census; **app-derived** means community notes
attributing behavior to decompiled Govee app classes; **generic** means a silicon
vendor's SDK, not this controller's implementation; **unknown** means not established.
The app's Java sources/APK were not available in the inspected local clones.

- **Measured H6159:** firmware `1.07.02`, hardware `1.00.02`; service
  `00010203-0405-0607-0809-0a0b0c0d1912`, characteristic
  `00010203-0405-0607-0809-0a0b0c0d2b12`, properties `read` and
  `write-without-response`. This is a complete UUID match for the V1 candidate.
- **Measured H6181:** firmware `2.03.11`, hardware `2.01.01`; service
  `f000ffc0-0451-4000-b000-000000000000`, characteristics `ffc1` and `ffc2`
  with the same suffix, each supporting notify, write, and write without response.
  This is a complete UUID match for the TI OAD candidate.

Source: [bench report](bench-evidence/2026-10-23-firmware/report.md) and its
adjacent raw JSONL captures. A complete service means the expected UUIDs exist;
it does not establish that updates are enabled or accepted.

Govee's own [specifications](https://community.govee.com/support/faqs/specs)
list H6159 as Bluetooth plus Wi-Fi, and H6181 as Bluetooth. Consequently,
Wi-Fi OTA discovery cannot be assumed to cover the H6181 or the BLE component
of the H6159.

## H6159: V1 / Telink-style candidate

**App-derived, unverified here:** the pinned community
[OTA notes, section 10](https://github.com/Brady-Woods/govee-ble-local/blob/374a3a30bb0216e6cbeca0bcaebe57e00ebc2b54/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md#10-ota-firmware-update)
describe Govee preparation opcode `0xEE`, V1 start bytes `01 FF`,
and 20-byte data packets: little-endian 16-bit index, 16 image bytes, and
little-endian CRC-16/MODBUS (`0xA001` reflected polynomial). Final payload
padding is `FF`. They describe retries and a final index/complement command,
but their end-packet length/CRC description is ambiguous. The same notes call
TI `FFC1` “image block/identify” and `FFC2` “image control”; those labels conflict
with TI's standard mapping below. Treat both points as unresolved rather than
an executable specification. The upstream library does not implement this updater.

**Generic primary-source cross-check:** Telink's
[BLE SDK handbook, OTA sections](https://doc.telink-semi.cn/doc/en/software/res/sdk/ble/tl_ble_sdk_multi_connection_en/tl_ble_sdk_multi_connection_en/)
documents the same OTA characteristic UUID, legacy `FF01` start and `FF02`
end commands, and data indexes. Its end command has six meaningful bytes
(opcode, final index, complemented index); its worked description says no CRC
is needed on that command. This does not resolve whether Govee pads or modifies
it. The command range also reserves index values: do not infer a usable 1 MiB
image capacity by multiplying every possible 16-bit index by 16 bytes. The
modern SDK offers optional version checks and secure boot features, which do
not prove those features exist on our older controller.

**Unknown:** actual chipset variant, image envelope/header, total flash,
OTA slot size, image authentication, minimum/maximum accepted size, timeout,
whether Govee preparation is mandatory, and what read/callback indicates
successful persistence. Our H6159 characteristic does not advertise notify;
an Android write callback is not proof that firmware was validated or booted.

## H6181: TI OAD candidate

**Generic primary-source baseline:** TI's
[CC2640R2 OAD guide](https://software-dl.ti.com/lprf/simplelink_cc2640r2_sdk/1.35.00.33/exports/docs/blestack/ble_sw_dev_guide/html/oad/oad.html)
specifies `FFC1` as **Image Identify** and `FFC2` as **Image Block**. The
downloader enables notifications and proposes metadata; acceptance leads to
block requests. Responses contain a two-byte little-endian block number and
16 image bytes. Its 16-byte metadata includes CRC, CRC shadow, version,
16-bit image length in four-byte words, UID, start address, image type and state.
That length field represents at most 262,140 bytes, a format limit, not measured
flash capacity. On-chip and off-chip implementations have different storage,
validation and recovery behavior despite sharing the profile.

**Unknown for our device:** SoC and SDK generation, exact header layout/offset,
whether metadata is sent whole or as a subset, request pacing, final-block
semantics, CRC algorithm/coverage, acceptance rules and slot layout. The service
UUID does not identify the SDK version. Implementing the cited CC2640R2 header
unconditionally could misparse an older or customized image. A normal app
update capture or direct app source is needed to resolve this and the upstream
characteristic-label discrepancy.

## Where a newer image might come from

The official [developer device API](https://developer.govee.com/reference/get-you-devices)
documents devices and control capabilities. It does not document a firmware
download or update-check capability on that page. The inspected community
[cloud client](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/src/govee_ble_local/cloud/account.py)
implements account/device/scene access, not a firmware availability checker.
No verified public firmware feed for these hardware revisions was found.

**Original observation on a different model:** Include Security's
[H7135 investigation](https://blog.includesecurity.com/2025/02/replacing-a-space-heater-firmware-over-wifi/)
records a 2024 `app.govee.com/device/rest/devices/v1/wifiCheckVersion` exchange.
It sent SKU, hardware/software version and device identity; the response included
`needUpdate`, target version, `downloadUrl`, size and an `md5` field, with an
image in Govee's public S3 upgrade-pack storage. This is a concrete discovery
lead, not a supported API contract or evidence that these lights use it today.
Their example also had separate transfer checks and bootloader checks: passing
one did not guarantee a bootable image. Do not import its checksum, signature
handling, firmware file or security conclusions into our lights.

Govee's [software update policy](https://www.govee.com/software-updates) says
actual updates received by a device take precedence and delivery varies by
region/model. Its publicly rendered table returned “No data” during review;
that is not evidence that no update exists. Newer firmware for either bench
light remains **unknown**. A version number alone cannot establish eligibility.

## Capacity, signatures, downgrades and recovery

We have measured transport presence, not storage capacity. Separate total chip
flash, available update slot, container size, decoded image size and protocol
address range in every report. There is no defensible numeric usable capacity
for either controller yet. BLE connection parameters, write completion and
bootloader pacing also prevent a reliable duration estimate from packet size alone.

Image CRC/checksum validity only answers a corruption question. It cannot establish
manufacturer authenticity, correct hardware targeting or rollback permission.
Signature enforcement, anti-rollback counters, A/B slots, interrupted-update
recovery, bootloader access and a factory restore procedure are all unknown on
these two units. Generic SDK recovery features are not recovery guarantees here.
The presence of H6159 Wi-Fi additionally leaves open whether BLE and Wi-Fi
firmware are separate update targets.

## Concrete next work

1. **Offline image inspector:** accept a local file plus explicit SKU, hardware
   revision, source URL/date and manifest. Calculate SHA-256, report exact size,
   preserve provenance and identify candidate formats. Report header interpretations
   with a named format/version and confidence; report unknown when ambiguous.
   Check declared length, truncation, bounds, padding and documented CRC only
   for a recognized format. Never label a file compatible from a filename alone.
   Keep `integrity`, `authenticity` and `device compatibility` separate fields.
2. **Availability evidence:** inspect the official app's firmware page for each
   exact bench device and record app version, region, offered version and hardware
   revision. If necessary, inspect an authorized local app cache or capture its
   ordinary metadata/download traffic. Preserve the original response and image
   hash; keep account tokens and device identifiers private. Do not guess package
   URLs, enumerate buckets or assume the historical Wi-Fi endpoint is suitable.
3. **Resolve the protocol:** obtain directly inspectable app code or an official
   update capture for one exact model. Establish preparation, characteristic
   roles, header bytes, acknowledgment meaning, end command, error/retry behavior,
   reboot and post-update version checks before designing a live writer.
4. **Qualify one model:** only after a matched image and recovery plan exist,
   consider an explicitly authorized bench transfer. Success requires reconnect,
   expected firmware version and functional checks, not a completed byte counter.

The first two items can progress without entering OTA mode. This research adds
documentation only; it does not authorize an update or establish an available release.

## Staged test of our own firmware acceptance

The follow-up objective is to determine whether an owned controller can execute
our firmware. The following is a proposed validation sequence; no acceptance test
has been performed. Keep evidence for the H6159 and H6181 separate.

1. **Collect without updating.** Record the official app's update page and obtain
   metadata and a stock image, if available, without starting installation. Hash
   the APK and image and record app version, exact model/hardware and provenance.
   Analyze the local APK's manifest, resources and DEX for update routes, request
   fields, response models and OTA handlers. Android's
   [APK Analyzer](https://developer.android.com/studio/debug/apk-analyzer)
   supports those artifacts. Static analysis can identify a candidate request
   schema without account credentials; it cannot establish server eligibility or
   runtime behavior, and obfuscation/native code may leave gaps.
2. **Analyze offline.** Identify the exact image format, load addresses, entry
   point, image/slot limits, hardware identifier, checksums and any authenticated
   regions. Distinguish a header's signature-like bytes from verified cryptographic
   enforcement. Trace the updater and bootloader checks where source or binaries
   permit; record remaining uncertainty rather than trying malformed files on
   the light. Compare the image's claimed version with what the app offers.
3. **Establish recovery before a live experiment.** Identify the MCU, debug/programming
   interface, voltage and tool support. Obtain a restorable stock image and,
   where possible, a full backup including bootloader, configuration and calibration.
   Demonstrate the recovery procedure on equivalent spare hardware first. A power
   cycle or vendor factory-reset button is not established firmware recovery.
   Scope the test to an application update slot; preserve bootloader and factory data.
4. **Prove each boundary separately.** Writable GATT permissions show transport
   eligibility only. A successful write callback shows host/link progress. An
   accepted identify header or requested next block shows progression in the
   updater, not final image acceptance. A completed transfer and validation result
   show more, but execution requires reboot evidence tied to the test build.
   If appropriate to the verified mechanism, first transfer an unchanged stock
   image to establish the normal path; same-version or downgrade rejection may
   prevent that baseline. No individual stage proves unsigned code execution.
5. **Use a valid, benign application.** A random short payload is unsuitable:
   unknown vectors, memory layout, peripheral initialization and watchdog behavior
   can prevent boot. Prefer a minimal application built for the confirmed MCU and
   exact OTA slot, with a unique observable build marker over an established
   diagnostic channel. Leave LED outputs inactive and avoid persistent writes
   beyond the update mechanism. Its size must satisfy the actual container and
   slot rules; “small” does not mean valid. Successful execution of independently
   built code without a manufacturer signature would establish custom/unsigned
   execution for that specific hardware and boot state, not for every firmware.

**Choose the capture by question.** Android
[HCI snoop logs](https://source.android.com/docs/core/connect/bluetooth/verifying_debugging)
capture host/controller Bluetooth packets: useful for ATT handles, writes,
notifications and timing. They do not expose the phone's HTTPS update response;
application-layer encrypted BLE payloads may also require separate decoding.
Cloud metadata needs an app-visible response/cache or an authorized network
capture with plaintext available. HTTPS packet capture alone generally provides
encrypted traffic. Android's
[network security configuration](https://developer.android.com/privacy-and-security/security-config)
supports app-specific trust anchors and pinning; installing a proxy CA does not
guarantee a release app trusts it, and debug trust overrides require a debuggable
build. Govee's actual pinning behavior has not been checked. Prefer local static
analysis and ordinary app output first; capture credentials privately. See
[phone capture workflow](../tools/phone-capture.md) for the operational steps.
