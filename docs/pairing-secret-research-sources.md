# Public sources for pairing-secret research

Checked 2026-10-23. These are research leads, not evidence of a universal light bypass.

## H5083: strongest directly relevant source

The [Brady-Woods firmware specification](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/firmware.yaml) identifies an RTL8720CF H5083 smart-plug image, hardware 1.02.00/software 1.00.30:

`def58f4ab490f41bbb43feb7c4cbccce-H5083_WIFI_BLE_HW1.02.00_SW1.00.30_OTA.bin`

Its public artifact is a structured reverse-engineered specification with firmware function addresses and references to decompiled C. The inspected public checkout does **not** contain that binary or the referenced decompiled firmware tree. Trying the filename under the public S3 upgrade-pack path used by another Govee model returned HTTP 403; that is an inferred location, not a confirmed download.

The specification documents three particularly useful facts:

- The eight-byte secret is persistent and is created on the first `AA B1` read inside the physical-button window. The simulator uses `random_bytes(8)`; that abstraction does not establish the real firmware's entropy source or random-generator quality. Trace `FUN_9b021894` and `FUN_9b021748` in an actual image.
- `AA B1` works in any pairing state and returns the stored secret within 5000ms of a button press. Outside the window the reply has selector zero and random bytes. This is a documented physical-presence retrieval route, including an already stored secret, rather than proof of remote recovery.
- While no stored secret exists, `33 B2` compares against a **keyed hash of the BLE MAC string** (`FUN_9b021928`, especially `0x9b021962–0x9b0219a0`). The spec intentionally models that value as opaque. Recovering the hash routine and its key could make this fallback computable, but the documented fallback does not replace an existing bound secret.

The [public H5083 simulator tests](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/tests/test_h5083_firmware.py) exercise button-window retrieval and authorization failure. They validate the library against this model, not independently against a physical firmware dump. Obtain the exact image/decompilation from its researcher or an owner's update capture before treating implementation internals as independently verified. H5083 is a plug; transfer to light models requires matching handlers/firmware.

### Artifact search and provenance follow-up

The [original firmware-spec commit](https://github.com/Brady-Woods/govee-ble-local/commit/a3c09f3d758d8e2f9d4339998b2d31e07a25b8d6) introduces the same opaque fallback and simulated random-secret abstractions. Its message describes a throwaway simulator outside the repository; it does not disclose the hash algorithm, hash key, or hardware RNG routine. A full reachable Git-history filename scan found no tracked `.bin`, `.apk`, or decompiled firmware tree. Exact filename/hash web searches did not locate an additional public copy.

The [public fork listing](https://api.github.com/repos/Brady-Woods/govee-ble-local/forks?per_page=100) returned one fork, [ternarybits/govee-ble-local](https://github.com/ternarybits/govee-ble-local); its inspected HEAD tree had no firmware/decompiled artifacts. The [release asset listing](https://api.github.com/repos/Brady-Woods/govee-ble-local/releases?per_page=100) contained Python wheels and source distributions, with no separately attached firmware images. These checks narrow the available public artifacts; they do not establish that the researcher's original files are unavailable elsewhere.

HEAD requests for that exact filename under the inferred `upgrade-pack/` paths on `govee-public.s3.amazonaws.com`, `govee-public.s3.us-east-1.amazonaws.com`, and `d1f2504ijhdyjw.cloudfront.net` all returned 403. A [Wayback CDX query for the exact filename hash](https://web.archive.org/cdx/search/cdx?url=*def58f4ab490f41bbb43feb7c4cbccce*&output=json&filter=statuscode:200) returned an empty list. A 403 does not distinguish an inaccessible existing object from a nonexistent object, and the empty archive query is not proof that no copy exists.

The [historical firmware-validation notes](https://github.com/Brady-Woods/govee-ble-local/blob/a3e3e92dca9b4e91aee5af3b3e22a8e76a7cdaee/spec/docs/FIRMWARE_VALIDATION.md) provide a practical reconstruction lead: Ghidra ARM Cortex-M Thumb, `ltchiptool` section maps, RTL flash XIP at `0x9B000000` and SRAM at `0x10000480`. They place the H5083 dispatcher at `0x9b01ee34` and identify the original decompile under a local `govee-apks/firmware-h5083-wifi/decompiled/` directory. That documentation explicitly distinguishes static firmware analysis from live-hardware confirmation.

## egold555: protocol captures and scripts, not a firmware collection

The [public repository](https://github.com/egold555/Govee-Reverse-Engineering/tree/900b003174f5ecd6708b3d5266ea0514926aefa1) contains product notes, packet examples and Python clients. [H5080](https://github.com/egold555/Govee-Reverse-Engineering/blob/900b003174f5ecd6708b3d5266ea0514926aefa1/Products/H5080.md) describes physical-button authentication-key retrieval; its packet naming differs from Brady's later H5083 specification, so do not silently combine their byte descriptions. [H600B](https://github.com/egold555/Govee-Reverse-Engineering/blob/900b003174f5ecd6708b3d5266ea0514926aefa1/Products/H600B.md) is useful for transport encryption/handshake comparison. Neither proves the eight-byte account secret is predictable. No APK, firmware binary or pcap was found in the inspected repository tree.

## H7135: published OTA firmware lead

[Include Security's first-party research](https://blog.includesecurity.com/2025/02/replacing-a-space-heater-firmware-over-wifi/) publishes the H7135 OTA response and firmware URL, image layout/reverse-engineering guidance, and use of ltchiptool for a physical dump. The article identifies the downloaded firmware as running on the Wi-Fi/Bluetooth Realtek module; a separate UART-connected MCU controls heater hardware. Thus the downloaded image could contain wireless pairing logic; it is not merely the appliance-control MCU.

This is a different model and chip from H5083, and the article investigates OTA verification, not eight-byte BLE-secret generation. The [published OTA image](https://s3.amazonaws.com/govee-public/upgrade-pack/62c9486b3b668992ebb413cc2ee0ad4e-H7135_CMS726_WIFI_BLE_HW1.02.00_SW1.00.13_OTA.bin) was reachable during this investigation (HTTP HEAD 200, Content-Length 874820). The following section analyzes its BLE-secret routines directly. Do not confuse the firmware integrity key described in the article with the account-pairing secret.

### H7135 static analysis: only eight bits are hidden

The reachable OTA was downloaded and hashed locally:

- size: `874820` bytes
- SHA-256: `e50fef03e5532d4f452d4943b3204a3ef7169e405ca5b25c2297409338df53a2`
- hardware/software: H7135 HW 1.02.00 / SW 1.00.13, as named by the published OTA response

`ltchiptool`'s AmebaZ2 parser confirms that the image is unencrypted ARM Thumb
code. The relevant XIP section starts at file offset `0x4140`, mapped at
`0x9b000140`. The binary contains the debug strings `first get encrypt code,
gen encrypt pair code`, `_ble_msg_device_pair_pwd`, `generate_uuid`, and
`Govee_UserConfig_GetPairPwd`.

The firmware's device-configuration record contains a 23-character UUID string
representing eight colon-separated bytes. `AA 07 02` (`ble_msg_get_sn`, function
starting at `0x9b040808`) parses that same string and returns its eight bytes in
reverse order. The pairing-code generator at `0x9b0256d4` parses the UUID,
restores its display order, obtains one byte through the platform random-byte
API, and stores this eight-byte value:

```text
q = random_byte()
U = UUID bytes in display order
pair[0] = (q + U[0]) mod 256
mask = q OR 0xA5
pair[i] = U[i] XOR mask, for i = 1..7
```

The `33 B2` handler at `0x9b0213fc` compares all eight supplied bytes directly
with this stored value. The `AA B1` path at `0x9b021138` uses the familiar
five-second physical-button window and returns the same stored bytes. The
validator at `0x9b025364` independently reconstructs the UUID relationship,
which corroborates the generator interpretation.

Because `q` is only one byte, an observer who can read the UUID has at most 256
candidate pairing values, not 2^64. Given the eight bytes returned by `AA 07 02`
as `R`, the candidates can be enumerated as:

```text
U = reverse(R)
for q in 0..255:
    candidate = [(q + U[0]) mod 256]
    candidate += [U[i] XOR (q OR 0xA5) for i in 1..7]
```

This is a static-analysis result for one H7135 firmware image. It has not yet
been tested on an H7135, and it does not establish that H5083 or any light uses
the same derivation. The firmware's random-byte source may be strong; that does
not restore the lost entropy because only one byte of its output affects the
pairing value. Whether repeated failed `33 B2` attempts trigger throttling,
disconnects, or another lockout also needs a live test.

## Best next analysis

Start with the exact H5083 image because its specification already identifies the secret-generation and MAC-fallback functions. Resolve the true RNG and its initialization, persistence/reset rules, keyed-hash algorithm and constant, then compare against a light image known to enforce `33 B2`. For a device we own, retain before/after pairing and factory-reset traces plus its exact firmware versions. A generic V1 handshake capture alone cannot establish secret-generation entropy or recover a separate authorization secret.

For H7135, the shortest validation is read-only until the final authorization
check: record `AA 07 02`, enumerate the 256 candidates above, obtain the true
secret once through the physical-button `AA B1` window, and verify that it is in
the candidate set. That proves or falsifies the derivation without sending 256
guesses. Only then test whether the same construction appears in another model.
