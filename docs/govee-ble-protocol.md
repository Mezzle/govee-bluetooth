# Govee BLE control protocol for lights: reference

Research notes, 2026-10-19, for the `govee_bluetooth` Home Assistant integration.
Scope: everything the primary sources document about controlling Govee lights over
Bluetooth LE, with the colour sub-modes as the deepest section. Every claim carries
a citation; anything without one is marked unverified.

## Table of contents

1. [Sources and citation keys](#1-sources-and-citation-keys)
2. [Summary](#2-summary)
3. [Transport: GATT, MTU, connection, keep-alive](#3-transport)
4. [Framing: 20-byte frames, type bytes, checksum, multi-packet](#4-framing)
5. [Command opcodes under 0x33 / 0xAA](#5-command-opcodes)
6. [The mode system and the colour sub-modes (deep section)](#6-mode-system-and-colour-sub-modes)
7. [Scenes](#7-scenes)
8. [DIY, music and video sub-modes](#8-diy-music-and-video-sub-modes)
9. [Status reads, the 0xAC bulk burst and 0xEE pushes](#9-status-reads)
10. [Pairing secret and wire encryption](#10-pairing-and-encryption)
11. [Advertisements and the Home Assistant ecosystem](#11-advertisements-and-home-assistant)
12. [Per-family summary table](#12-per-family-summary)
13. [Implications for this repo](#13-implications-for-this-repo)
14. [Companion documents](#14-companion-documents)

---

## 1. Sources and citation keys

| Tag | What it is | Trust |
|-----|------------|-------|
| `SPEC:<line>` | `/tmp/govee_ref/govee-ble-local/spec/docs/GOVEE_BLE_GATT_PROTOCOL.md` (github.com/Brady-Woods/govee-ble-local, commit 374a3a3, 2026-10-15), derived from the decompiled Govee Home app (7.5.12 base APK plus 7.7.02 `pact_*` feature splits). Java class names are the spec's attributions. | app source; hardware only where it says so (H60A6, H61A8, H6641 captures) |
| `YAML:<line>` | `/tmp/govee_ref/govee-ble-local/spec/devices.yaml`, same repo: per-family `submodes:` maps, `opcodes:`, `specs:`, the `scenes:` model and the shared `command_catalog` | app source; `confidence:` per family |
| `KSY:<line>` | `/tmp/govee_ref/govee-ble-local/spec/govee_ble.ksy`; `ADV-KSY:<line>` = `govee_adv.ksy` | app source |
| `HGL:<path>:<line>` | `/tmp/govee_ref/ha-govee-led-ble` (github.com/teh-hippo/ha-govee-led-ble, commit b2cd788, 2026-10-18). `docs/*.md` are ledgers of direct BLE sessions on named hardware; `tools/ble/kaitai/*.ksy` are its wire grammars, `speculative/` = app-derived only | hardware where the doc says so |
| `H617A-REV` | github.com/rnodern/govee-h617a-ble, commit 4279e64 (captured H617A frames) | community capture |
| `GENZERO` | https://notes.generalzero.org/Reverse%20Engineering/Signals/Bluetooth/Govee_lights.html (H617A fw 3.02.24 / hw 3.01.01, direct probing) | community capture |
| `API-BLE` | github.com/softgrass/govee-api-ble, commit 59e7771, `src/govee_api_ble/device.py` | community code |
| `H6159-REV` | github.com/jurassic-marc/govee-h6159-light-strip-reverse-engineer, `README.md` | community capture (H6159, Telink) |
| `BTLED` | github.com/chvolkmann/govee_btled (H6001 captures) | community capture |
| `GBL` | github.com/Beshelmek/govee_ble_lights, `custom_components/govee-ble-lights/light.py` | community code |
| `GOVEE-BLE` | github.com/Bluetooth-Devices/govee-ble, `src/govee_ble/parser.py` (clone at `/tmp/govee_ref/govee-ble`, 2026-10-19), the library behind HA core's `govee_ble` | HA core dependency |
| `BENCH` | this repo's hardware sessions: H6159 (0x02), H6181 fw 2.03.11 (0x0D), 2026-10-18; H6006 fw 1.00.38 / hw 1.02.01 (0x0D), 2026-10-19/20 with the Pi rig's census; `custom_components/govee_bluetooth/models.py` | hardware verified |
| `REPO:<file>:<line>` | this repository, `custom_components/govee_bluetooth/` | - |

Byte offsets in payload tables count from the first byte after `33 05` (the sub-mode
byte is offset 0) unless a table says otherwise.

## 2. Summary

- One vendor GATT service (`...1910`), write on `...2b11`, notifications on `...2b10`,
  `encryptVersion` readable on `...2b12`; H6101/H6104 use the generic `ffe0/ffe1`
  UART service instead (`SPEC:63-113`).
- Every control frame is 20 bytes: `[proType, cmd, payload<=17, XOR]`. `0x33` write,
  `0xAA` read, `0xEE` push; `0xA1/0xA2/0xA3/0xA4/0xA6` multi-packet, `0xAC` bulk status,
  `0xE7` cipher handshake (`SPEC:193-230`, `SPEC:1435-1482`).
- Shared opcodes: power `0x01`, brightness `0x04`, mode `0x05`, version `0x06`, info
  `0x07`, timers `0x09/0x0A/0x0B/0x11/0x12/0x23`, limit `0x0E`, light-num `0x0F`,
  gradual `0x14`/`0xA3`, IC `0x40/0x42/0x43/0x46`, segment colour `0xA2/0xA5`, zone/
  direction `0x30`, secret `0xB1/0xB2` (`YAML:288-402`, `SPEC:596-692`).
- Four colour sub-modes under `33 05`: **0x02**, **0x0B**, **0x0D**, **0x15**
  (`SPEC:765`, `SPEC:1300-1305`). The repo builds and parses all four but only probes
  0x02/0x0D/0x15 (`REPO:protocol.py:111-182,269-307`, `REPO:probe.py:39-54`).
  Hardware verified here: 0x02 (H6159), 0x0D (H6181). Community hardware: 0x15 on
  H617A, H6199 pact 2, H60A1/H60A6.
- Mode replies are parsed per family, not per byte; under 0x15 the reply carries
  kelvin only, and one real H6199 returned zeros for it. RGB read-back on RGBIC
  lights is only available through `AA A5`/`AA A2` segment pages.
- Scenes: `33 05 04 <code u16 LE>` always; newer firmware additionally needs a
  `0xA3` multi-packet upload of the scene blob first (`YAML:148-163`,
  `SPEC:484-516`). Known single-byte IDs exist only for the legacy strips.
- Pairing: families tagged `secret_key_pairing` need the 8-byte `0xB1`/`0xB2` secret;
  the wire cipher (none / AES-ECB+RC4 / AES-GCM) is decided per device from the
  advertisement flag or `2b12`, not per SKU (`YAML:102-145`, `SPEC:835-907`).
- Advertisements: local name `ihoment_/Govee_/Minger_/GBK_/GVH/GVR/GV` + SKU;
  manufacturer data `[flags][88 EC][pactType u16][pactCode]` (`SPEC:1497-1507`,
  `SPEC:1592-1612`). HA core's `govee_ble` + the `govee-ble` library parse
  sensors only; `govee_light_local` is LAN UDP; `ha-govee-led-ble` is the only
  HA-side BLE light work with hardware ledgers.

---

## 3. Transport

### 3.1 GATT UUIDs

| Role | UUID | Properties | Source |
|------|------|------------|--------|
| Control service | `00010203-0405-0607-0809-0a0b0c0d1910` | - | `SPEC:68` (`AbsBle.java:103`) |
| Write characteristic | `00010203-0405-0607-0809-0a0b0c0d2b11` | Write, Write Without Response | `SPEC:69,78` (`AbsBle.java:106`, `AbsBle.z():800`) |
| Notify characteristic | `00010203-0405-0607-0809-0a0b0c0d2b10` | Notify, CCCD `2902` | `SPEC:70,79` (dead constant `encryp/ble/Constants.java:20`; live-confirmed) |
| BGC-info characteristic | `00010203-0405-0607-0809-0a0b0c0d2b12` | Read, Notify | `SPEC:71,80` (`BgcInfoReader.java:67,72`), byte0 = `encryptVersion` 0/1/2 (`SPEC:1518-1519`) |
| OTA V1 service / char | `...0d1912` / `...0d2b12` | - | `SPEC:93-94` (`OtaManager.java:24,27`) |
| OTA V2 (TI OAD) | `F000FFC0/FFC1/FFC2-0451-4000-B000-000000000000` | - | `SPEC:95-97` |
| Alternate UART service / char | `0000ffe0-...` / `0000ffe1-0000-1000-8000-00805f9b34fb` | same 20-byte framing | **H6104, H6101** only (`SPEC:105-113`, `YAML:690-697,832-838`) |
| Sensor service `INTELLI_ROCKS_HW` | `494e5445-4c4c-495f-524f-434b535f4857`, chars `...2011/2012/2014/2015` | thermo/BBQ/gateway, not lights | `SPEC:115-128` |
| Encrypted multi-connect (`ctlchannel`) | UUIDs supplied per message (`BgcMsg`) | H70B3/B4/B5, H6810/6811, H6800, H6840 | `SPEC:1110-1114`, `SPEC:903-907` |

The app subscribes to notifications on **every** characteristic of `1910`, which is
why it never names `2b10`; a lean client subscribes to `2b10` and writes `2b11`
(`SPEC:82-85`, `SPEC:162-168`). Community clients that address by handle use
`0x0014`/`0x0015` (API-BLE `device.py:54`; H6159-REV `README.md:217` `--handle 0x0015`).

### 3.2 MTU

Default ATT MTU 23 (20 payload); the app requests 512 (`BleController.J`,
`SPEC:169-171`, `YAML:96-99`, `MtuConfig.java:18,48,62-65` via `SPEC:362-366`).
Frames stay 20 bytes regardless; only the `0xA4` DIY builder and `0xA6`
"MTU multiple write" use a larger cached MTU (`SPEC:362-366`, `SPEC:288`).

### 3.3 Connection lifecycle and timing

Scan -> `connectGatt` -> discover -> enable notify on all chars (slow path writes CCCD
`01 00`; fast-connect path only `setCharacteristicNotification`, chosen by cached
broadcast version) -> optional `requestMtu` -> optional cipher handshake -> ready
(`SPEC:136-176`). Constants (`SPEC:1619-1634`, `YAML:70-99`): connect timeout 60 s,
service discovery 180 s, reconnect window 15 s, GATT status 19 = auto-disconnect;
read timeout 3 s / write timeout 6 s, 3 / 6 retries at 100 / 200 ms; > 10 failures ->
disconnect; 300 ms between multi-packet frames; OTA 30 ms.

Writes are **write-with-response** in the app (`SPEC:1638`, `YAML:85`); the write
characteristic also allows write-without-response (`SPEC:78`). Community clients
use `--char-write-req` (H6159-REV) or `response=True` (API-BLE `device.py:43`);
HGL uses `response=False` for the H6199 pact-1 frames (`HGL:tests/test_h6199_pact1.py:269`).
The app matches each reply by echoed `[proType, cmd]` and treats `value[2] == 0`
as success (`SPEC:214-215`, `SPEC:1646`).

### 3.4 Keep-alive

`AbsHeartRunnable` periodically sends `SINGLE_HEART = 0x01` so the device does not
drop the link (`SPEC:172-174`, `SPEC:1651-1652`); on the wire that is the power read
`AA 01 00 .. CK`, "device echoes status" (`SPEC:721-724`). The catalogue also lists
`0x00 SINGLE_HEART_BEAT` as a read-only keep-alive constant (`YAML:293`); no builder
is cited for it. Observed intervals: GENZERO sends `AA 01 00 ... AB` every 2 s on an
H617A; this repo uses 2 s (`tools/govee_rig.py` "Mirror device.py", `HEARTBEAT_INTERVAL = 2.0`);
HGL closes idle links after 3 s on H617A (`HGL:custom_components/ha_govee_led_ble/const.py:393`
`connection_idle_timeout=3.0`). The app's own period is "per-device" and not quoted
(`SPEC:1651`). Lights accept one central at a time (`SPEC:53`; `tools/README.md`
"make sure the Govee app on your phone is closed").

---

## 4. Framing

### 4.1 The 20-byte frame

```
byte 0    1     2 .. 18              19
[proType][cmd ][payload <= 17 bytes][XOR of bytes 0..18]
```
`SPEC:185-206` (`BleUtils.generate20Bytes`, `BleUtils.java:1369`; checksum
`BleUtils.v(packet,19)`, `BleUtils.java:1239`). ABNF: `SPEC:1435-1482`.
`REPO:protocol.py:53-72` implements the same.

Three-argument form `generate20Bytes(proType, cmd, subCmd, payload)` puts a
sub-command byte at index 2 and the payload from index 3 (`SPEC:249-250`,
`BleUtils.java:1363`); this is the shape of every mode frame (`33 05 <sub> ...`),
every info read (`AA 07 <sel>`) and the per-group colour read (`AA A5 <group>`).

### 4.2 proType bytes

| proType | meaning | source |
|---------|---------|--------|
| `0x33` `SINGLE_WRITE` | write | `SPEC:207` |
| `0xAA` `SINGLE_READ` | read request / read reply (device echoes `AA cmd`) | `SPEC:208`, `SPEC:214` |
| `0x3A` `SINGLE_WRITE_READ` | write-and-read variant; no light use documented | `SPEC:210` |
| `0xEE` `NOTIFY` | unsolicited push; also `SINGLE_OTA_PREPARE` as a command byte | `SPEC:209`, `SPEC:659` |
| `0xAB` | BBQ telemetry push (not lights); also `SINGLE_DYNAMIC_API_SUPPORT` as a cmd | `SPEC:211`, `SPEC:661` |
| `0xA1` `MULTIPLE_WRITE` | `MultiPackageManager` chunked write (legacy DIY/scene tables) | `SPEC:252-275` |
| `0xA2` `MULTIPLE_READ` | chunked read (`MultiPackageManager`) **and** the `BulbStringColorController` cmd byte under `0xAA` | `SPEC:266-272`, `YAML:858` |
| `0xA3` `MULTIPLE_WRITE_V1` | scene / DIY upload (`MultipleControllerCommV1`) | `SPEC:293-320` |
| `0xA4` `MULTIPLE_WRITE_V2` | MTU-sized DIY upload, graffiti only | `SPEC:348-381` |
| `0xA6` `MTU_MULTIPLE_WRITE` | single-shot write when MTU is large | `SPEC:288` |
| `0xAC` `MULTI_READ_AC` | single-request / multi-reply status burst | `SPEC:528-580` |
| `0xE7` | cipher handshake (V1 ops `01/02`, V2 ops `11/19/1A`) | `SPEC:850-856`, `SPEC:893-897` |

`0xB1`/`0xB2` are **command bytes** (secret read / write) under `0xAA`/`0x33`, not
proTypes (`SPEC:657-658`, `SPEC:1338-1343`).

### 4.3 Read requests vs replies

A read request is `AA <cmd> <selector>`: the byte after the command is a
per-command selector, default empty (`AbsSingleController.f()`, `SPEC:219-228`).
Source-confirmed selectors: mode `AA 05 01` (required), info `AA 07 02/03/04/10/11`;
power and brightness send `AA 01 00` / `AA 04 00`, and a trailing `01` seen in some
captures is tolerated (`SPEC:222-228`, `SPEC:1347-1353`). The reply is
`AA <cmd> <device state...>`, parsed per controller after stripping two header bytes
(`SPEC:216-217`, `SPEC:229-231`).

### 4.4 Multi-packet dialects

**`0xA1` (`MultiPackageManager`, up to 4080 bytes, 16-byte chunks, 300 ms apart)**
(`SPEC:252-275`):
```
START : A1 comType 00 packetCount 0.. CK
DATA_i: A1 comType i(1-based) <16 data bytes> CK
END   : A1 comType FF 0.. CK
READ  : A2 comType 00 ..  -> header A2 comType 00 amount, data A2 comType i <16>, end A2 comType FF
```
comType values: `MULTI_V1..V4_NEW_SCENES` = 1, 2, 7, 10; `MULTI_DIY`/`MULTI_V1_NEW_DIY`
= 2 / 4; graffiti 3; `MULTI_V1_NEW_COLOR` 0x40; `MULTI_V1_NEW_MUSIC` 0x41; `MULTI_WIFI`
0x11 (`SPEC:646-655`). Families declaring `0xA1`: h6159, h6160, h6102, h6104, ...,
dreamcolorlightv1/v2, stringlightv2, bulblightstringv1, barelightv1, h60ax, h6047
(`YAML:598,609,848,869,906,919,934,413,443`).

**`0xA3` (`MultipleControllerCommV1`, scene/DIY uploads)** (`SPEC:307-320`,
`SPEC:383-395`):
```
START : A3 00 01 packetCount commBytes... <15-L inline value bytes> CK   (byte 2 = 01: V1/V2 builder)
MIDDLE: A3 seq <17 value bytes> CK
END   : A3 FF <final <=17 value bytes> CK                                 (data-bearing)
```
Variants: byte 2 = `00` (`makeSendBytesV0`, empty END, rare) and `02`
(`makeSendBytesV3`, multi-controller scenes with an 8-byte verify block at bytes
7-14) (`SPEC:387-395`). Captured H61A8 START: `a3 00 01 10 03 09 00 64 00 00 00 1c 05 00 ff 00 00 01 02 39`
(`SPEC:405-410`). ACK for `0xA3` writes is byte 3 == 0, not byte 2
(`AbsMultipleControllerV2.java:24-31`, `HGL:docs/h617a-support-findings.md:437`).

**`0xA4`**: same purpose, commByte at byte 6, only for graffiti effects whose
protocol byte is in `{0x5E, 0x5D, 0x20}` and only wider than 20 bytes if an MTU was
cached (`SPEC:348-381`).

---

## 5. Command opcodes

Shared constants from `BleProtocolConstants.java` (`SPEC:592-595`), as listed in
`YAML:288-402` and `SPEC:596-692`. "W/R" = write `33 xx` and read `AA xx`. The byte
space is reused per feature; interpret `(proType, cmd, family)` together (`SPEC:663-666`).

### 5.1 Core control (all lights)

| cmd | name | W/R | payload / reply | notes | source |
|-----|------|-----|-----------------|-------|--------|
| `0x01` | `SINGLE_SWITCH` / `SINGLE_HEART` | W/R | `33 01 01/00`; reply `AA 01 <state>` | lights: 0 = off else on; plugs: relay bitmask `0x10|bit` (`SwitchControllerV2`) | `SPEC:699-704`, `SPEC:1325-1336`, `KSY:378` |
| `0x04` | `SINGLE_BRIGHTNESS` | W/R | `33 04 <level>`; reply `AA 04 <level>` | raw byte; 0-255 on Telink strips (H6159 BENCH), 1-100 on H617A/H6181/H6062 (`YAML:556`, BENCH), 20-254 register on H6188 and H6199 pact 1 (`YAML:631`, `HGL:docs/h6199-pact1.md:7-8`), bulblightv3 legacy maps 1-100 -> 20-254 (`YAML:498`). No rescale in the BLE codec (`KSY:382`) | `SPEC:706-710` |
| `0x05` | `SINGLE_MODE` | W/R | `33 05 <submode> ...`; read `AA 05 01` | section 6 | `SPEC:747-759` |
| `0x06` | `SINGLE_SOFT_VERSION` | R | `AA 06 00` -> ASCII, e.g. `3.02.24` | `SPEC:726-729`; GENZERO |
| `0x07` | `SINGLE_DEVICE_INFO` | R | `AA 07 <sel>`: `02` serial 8 B reversed; `03` hw version ASCII (reply `AA 07 03 "1.00.04"`); `04` sw; `07` dsp; `0A/0B` MCU; `10` UID(8, reversed)+sw(3)+hw(3); `11` Wi-Fi MAC(6, forward)+sw+hw | `SPEC:627-637`, `YAML:312`, `HGL:status_reply.ksy:118-127` |
| `0x09` | `SINGLE_SYNC_TIME` | W | `33 09 hh mm ss week 01 tzH tzM` (lights); plugs `33 B5 <epoch u32 BE> 01 tzH tzM` | tz hour is signed | `SPEC:712-719` |
| `0x0A` | `SINGLE_AUTO_TIME` | W/R | `[open, openH, openM, closeH, closeM, group, repeat]` | `AutoTimeController.java:59` | `YAML:305` |
| `0x0B` | `SINGLE_DELAY_CLOSE` | W/R | `[open, hours, minutes]` countdown off | `DelayCloseController.java:31` | `YAML:306` |
| `0x11` | `SINGLE_SLEEP` | W/R | `[enable, startBri, closeTime, curTime, ...]` (V2 adds colour temp) | `SleepController.java:53`; H617A `AA 11` -> `00 1E 0F 0F` | `YAML:307`; GENZERO |
| `0x12` | `SINGLE_WAKEUP` | W/R | `[enable, endBri, wakeH, wakeM, repeat, wakeTime, ...]` | `WakeUpController.java:71`; H617A `AA 12` -> `00 64 00 00 80 0A` | `YAML:308`; GENZERO |
| `0x13` | `NIGHT_MODE` | W/R | - | also a music sub-mode byte under 0x05 | `YAML:301`, `SPEC:610` |
| `0x23` | `SINGLE_NEW_TIME_V1` | W/R | `[group, enable|type, hour, minute, repeat]`; group `FF` reads 4 x 4 B | H617A `AA 23 FF` -> `AA 23 FF 00 00 00 80 ...` | `YAML:309`; GENZERO |
| `0x14` | `SINGLE_WIFI_MAC` (shared) / **gradual** (dreamcolor, h6119, h612526, barelight) | R / W | 6-byte MAC / `[0|1]` fade between colours | same byte, two meanings | `YAML:314`, `YAML:741,755,853,872,937` |
| `0x20/0x21/0x22` | Wi-Fi hw / sw / DSP version | R | ASCII | H6199 answers `AA 20`, `AA 21` | `YAML:315-317`, `HGL:h6199_status_reply.ksy:49-50` |
| `0x16` | `LIGHT_INDICATOR` | W/R | `[enable, sH, sM, eH, eM]` or `[enable, FF, FF, FF, FF]` | tablelampv1; also "energy saving" on H6057 | `YAML:295`, `YAML:968` |
| `0x35/0x36/0x37/0x38` | do-not-disturb / worry-free-night / prompt tone / init light | W/R | see yaml | H6057-centric settings; `0x36` is **also** the compose/bar switch on H6047/H60A6 (`33 36 <left> <right>`, read `AA 36` -> `[left, right, feast]`) | `YAML:298-303`, `YAML:447`, `SPEC:674-675` |
| `0x33` | `SINGLE_VOLUME` | W/R | `[volume]` | also `single_light` on tvlightv3 | `YAML:302`, `YAML:1174` |
| `0xA6` | `LOGO` | W/R | `[opType, brightness, r, g, b]` | `LogoController.java:43`; `auto_induction` on hollowlamp | `YAML:296`, `YAML:990` |

### 5.2 RGBIC capability, segment and zone opcodes

| cmd | name | W/R | payload / reply | who | source |
|-----|------|-----|-----------------|-----|--------|
| `0x0E` | `LimitController` / current limit / segment limit | W/R | `[flag]`; H617A `AA 0E` -> `0F` | H6102, dreamcolorlightv1, stringlightv2, H6179 | `YAML:678,851,908,1145`; GENZERO |
| `0x0F` | `LightNumController` light / bulb count | W/R | reply `AA 0F <count u8>` (positive signed byte) | h612526, dreamcolorlightv1, barelightv1, bulblightstringv1, H7022; H617A did **not** answer | `YAML:753,852,922,939,1132`; `HGL:speculative/h617a_control_payload.ksy:29-42`; `HGL:docs/h617a-support-findings.md:22` |
| `0x40` | `IC_NUM` (`ControllerOnlyReadIcSegmentNum`) | R | `AA 40 00` -> `AA 40 <ic u16 BE> <segment u8>`; H617A -> `00 0F` | h61d3 (groups = ceil(IC/5)), dreamcolorlightv1, h604a; "the ONLY live capability read" | `SPEC:1581-1585`, `KSY:361-369`, `YAML:516,855,1017`; GENZERO |
| `0x41` | `ON_OFF_MEMORY` `[2, mode]` (shared) / **section count** on H6062 | W/R | H6062: `AA 41 00` -> `[ic s16 BE, sectionNum u8]`, mandatory at connect | H60A6 memory; H6062 count | `YAML:297,420,548`, `KSY:89` |
| `0x42` | `WRITE_CHECK_IC` refresh IC / head calibration (H60A6) | W | `FF` start, `00` reset, `[2,2]` left, `[2,1]` right | dreamcolor, h60ax | `YAML:353,422,856` |
| `0x43` | `CHECK_IC_AMOUNT` / cut-calibration (H6640) / `ReadSubVersion` (H70Bx) | W/R | - | h61d3, h70bx | `YAML:354,518,1082` |
| `0x46` | `CHECK_IC` | W/R | `[ic, (sub), kLo, kHi]` | dreamcolor, h61d3 | `YAML:355,518,857` |
| `0x24` | `READ_IC_POINT` | R | IC point list | h61d3 | `YAML:356,519` |
| `0x30` | `LIGHT_DIRECTION` (TV backlights, R) / **zone power** `[zoneIndex, state]` (H60A6, W, readable via `EE 30` bit 1) / calibration | W/R | `33 30 <zone 0|1> <0|1>` | H60A6; H6198/9, H6049/54, H605B, H6047 family | `SPEC:676-684`, `YAML:416,453,1159,1188,1204` |
| `0x34/0x35` | swap light / start time | W | `[]` / - | tvlight v2-v4, h604a, h6047 | `YAML:448,1160,1173,1189-1190` |
| `0x31/0x32` | camera position / check camera | W/R | constants only in h6047 split | tvlightv2/v4, h605b, h6047 | `YAML:454-455,1157-1158,1186-1187,1202-1203` |
| `0x44` | calibration | W | `[on/off]` | h6047, h605b | `YAML:456,1205` |
| `0xA9` | video params / white balance / AI | W/R | H6199: `AA A9 <setting> <len> <payload>`: `00` white balance `[resetFlag, resetR, resetB, curFlag, curR, curB]`, `06` scalar, `0A` blank-screen policy | tvlightv2, h6047, h605b | `YAML:385,457,1206`; `HGL:h6199_status_reply.ksy:58-135` |
| `0xAE` | relative (edge) brightness | R | `AA AE` -> `[01, edgeCount 4|6, left, top, right, bottom, ...]` | H6199 | `HGL:h6199_status_reply.ksy:57,136-150` |
| `0xA2` | `BulbStringColorController` V1 colour pages | R | `AA A2 <batch>` -> `AA A2 <batch> 4 x [R G B]` | h6160, h612526, dreamcolor v1/v2, bulblightstringv1, H7022 | `YAML:611,754,858,874,923`; `KSY:325-330` |
| `0xA5` | `Controller4ColorInfoByGroup` / `BulbGroupColorV2` | R | `AA A5 <group>` -> `AA A5 <group> N x [bri R G B]` (N = 3 or 4); 3-byte `[R G B]` records on colour-only SKUs | h60ax, h6047, h61d3, striplight, h6061, hollowlamp (`local_color`), h6119, dreamcolor V2 fw, H617A | `SPEC:686-695`, `YAML:319-351` (comment block), `YAML:419,458,520,553,654,740,989`; GENZERO |
| `0xA3` | `GRADUAL_CHANGE_WIFI_BLE` | W/R | `33 A3 <0|1>`; `AA A3 00` -> `[state]` | h6047, dreamcolor, barelight, tvlightv2; H617A -> `01` | `YAML:399,452,854,873,938,1161`; `KSY:371-375`; GENZERO |
| `0xA7` | `sensitive` (hollowlamp) / `CaliBelt` (h604a) | W | - | | `YAML:991`, `SPEC:1071` |
| `0xAA` | `CheckLight` (h604a) | - | - | | `SPEC:1071` |
| `0x26` | take-photo `[1,100,R,G,B]` / `[0]` (h61d3) / `color_temp_type [1|2]` (H601E/F) | W | | | `YAML:496,521` |
| `0xED` | video recognition | W + notify | no inbound decoder in the app | h61d3 | `YAML:386,522` |
| `0x24-0x29` | presets (H6057) / scene delete `0x28`, sort `0x29`, preview `0x27` | W | | | `SPEC:1072`, `YAML:359-361` |
| `0x58` | DIY/graffiti protocol code (H60A6) | W via `0xA3/0xA4` | | h60ax | `YAML:423` |
| `0x70-0x7A` | music library / white noise | W/R/notify | `0x71` player state `[musicType, state, codeLo, codeHi]`, `0x79` volume, `0x7A` master on/off | lamps with speakers | `YAML:365-375` |
| `0x50/0x54/0x60-0x62` | movie / feast / carousel | W/R | | TV sync boxes | `YAML:377-380` |
| `0x17`, `0x11` (multi) | Wi-Fi link start / provisioning payload | W | | Wi-Fi lights | `YAML:397-398` |
| `0xB1` / `0xB2` | read / check secret key | R / W | `AA B1` -> `[01] + 8 B`; `33 B2 <8 raw B>` | section 10 | `SPEC:1338-1343`, `YAML:123-125` |
| `0xB3/0xB4/0xB5/0xB0` | plug spec / timer / sync-time / delay | | plugs only | h5080 | `SPEC:1675` |
| `0xEE` / `0xEF` / `0xAB` | OTA prepare / pact negotiation / dynamic-API query | W / W/R / R | H617A `AA EE` -> `00 0A 01` (unexplained) | | `SPEC:656-661`; GENZERO |
| `0x63-0x65, 0xF0`, `0x83-0x85` | daySync, alarm | | constants only | | `YAML:401-402` |

Opcodes that are **reads of settings** and worth polling on any light: `AA 01`, `AA 04`,
`AA 05 01`, `AA 06`, `AA 07 03`, `AA 0A`, `AA 0B`, `AA 11`, `AA 12`, `AA 23 FF`
(striplight connect order: versions, Wi-Fi, timers, wake-up, sleep, `aa 01`, `aa 04`,
`aa 41`, `aa 05`; `YAML:548`).

---

## 6. Mode system and colour sub-modes

Command `0x05` selects a mode; the payload starts with a sub-mode type byte and the
dispatch is `AbsMode.parse() -> parseSubMode(subType, data)` per family `Mode.java`
(`SPEC:749-759`). The bytes are **not globally constant** (`SPEC:757-759`).

Overview of the byte space (`SPEC:763-772`, `KSY:138-153`): colour `02 / 0B / 0D / 15`;
scene `04` (`09` on bulb strings); music `01, 03, 05, 0C, 0E, 0F, 11, 12, 13, 14, 19`;
DIY new `0A`, old `07`; video `00`; game `0B` (H6057); background colour `0D`
(`SubModeColor4Bk`).

Conventions shared by every colour sub-mode:

- Mode read request is `AA 05 01`; the `01` is a selector (`AbsModeController.p()`),
  not a sub-mode (`SPEC:222-228`, `SPEC:1353`). An H617A answered `AA 05 00` and
  `AA 05 01` identically (`HGL:tools/ble/kaitai/status_query.ksy:13`,
  `HGL:docs/h617a-support-findings.md:434`); so did an H6199
  (`HGL:docs/h6199-support-findings.md:312-314`).
- Reply is `AA 05 <submode> <body...>`; the app strips bytes `[2..18]` and routes
  on the sub-mode byte through the family's `Mode.parseSubMode` (`SPEC:216-217`,
  `SPEC:749-759`).
- A successful write is acked by the device echoing `33 05` with `value[2] == 0`
  (`SPEC:214-215`). This ack says nothing about whether the sub-mode was understood;
  H601C owners report ACKs without verified output (`HGL:docs/ceiling-support.md:30`).
- Kelvin is big-endian on the wire in 0x0D and 0x15 op 0x01
  (`BleUtil.getSignedBytesFor2(..., true)`, `SPEC:1255`, `SPEC:1267`). The one
  little-endian exception is 0x15 op 0x05 (`SPEC:1316`, `KSY:682-684`).
- The app only ever sends kelvin in whole 100 K steps, clamped to the SKU range,
  because the tint comes from an exact-hit lookup table (`Constant.Z1`,
  `Constant.getTemColorByKelvin`, `SPEC:1262-1267`). A non-multiple of 100 yields a
  `(0,0,0)` tint.
- Segment masks (0x0B and 0x15): bit i = segment i, 0-based, LSB first,
  little-endian across the bytes (`BleUtils.makeSelectedTwoBytes`, `SPEC:1318-1323`).
  Two bytes for up to 16 segments; above that `ceil(n/8)` bytes, at most 5-7
  (`BleUtils.X`, `YAML:552`). Whole device = all *existing* segment bits: 13
  segments -> `FF 1F` (`SPEC:1323`), 15 -> `FF 7F`, 14 -> `FF 3F`
  (`HGL:tests/test_h6099.py:116`), 23 panels -> `FF FF 7F` (`YAML:652`).

Summary of the four colour sub-modes:

- Four colour bytes exist: **0x02** (legacy RGB + white flag), **0x0B** (dreamcolor
  RGBIC, segment mask, no kelvin), **0x0D** (RGBWW: RGB + kelvin + tint) and **0x15**
  (op-tagged RGBIC: op, RGB, kelvin, tint, mask). `SPEC:765`, `SPEC:1300-1305`.
- This repo builds all four (`REPO:protocol.py:111-182`) and parses all four
  (`REPO:protocol.py:269-307`), but the probe only tries 0x02 / 0x0D / 0x15
  (`REPO:probe.py:39-54`).
- Hardware verified so far: 0x02 on H6159 (BENCH), 0x0D RGB + CCT + read-back on
  H6181 fw 2.03.11 (BENCH). Community hardware: 0x15 writes on H617A (GENZERO,
  H617A-REV, HGL), H6199 pact 2/1 (HGL), H60A1/H60A6 (HGL owner reports); 0x0B
  writes on H6199 pact 1/1 (HGL, app-derived vector, owner-pending); 0x02 on H6001
  (BTLED).
- The `AA 05` reply is parsed **per family**, not per sub-mode byte. The same
  `AA 05 0D ...` body is `[R,G,B,kHi,kLo,tR,tG,tB]` on H6188/H6181, `[R,G,B]` on H6052,
  and `[gradual, kHi, kLo]` on the shared "newdetail" stack (`KSY:301-319`,
  `KSY:1256-1281`). Under 0x15 the shared parser yields kelvin only, never RGB
  (`SPEC:232-238`, `SPEC:1355-1361`), and on an H6199 (pact 2/1, fw 1.10.04) the
  kelvin field came back **zero** after a successful 3000 K write
  (`HGL:docs/h6199-support-findings.md:308-311`). RGB must be read with `AA A5`
  (or `AA A2`) segment pages on every RGBIC family.
- Where the bench lights sit: H6159 -> family `h6159` (0x02 primary, 0x0D alt,
  `YAML:593-602`); H6181 -> family `h6181` (app says 0x02, `YAML:790-798`) but the
  firmware speaks 0x0D with the rgblight-style read-back (BENCH). The app's
  `stringlightv2` family (0x0D primary, 0x02 alt) covers H6109/H6110/H6141/H6142/
  H6159/H6160/H6170/H6195 (`YAML:900-911`), which is the likely explanation: newer
  firmware on "legacy" SKUs moved to 0x0D.

### 6.1 0x02 - legacy RGB ("SubModeColor", h6159 / h6001 stacks)

App name: `SubModeColor` in `com/govee/h6159/ble/` (`SPEC:737`) and
`h6001/ble/SubModeColor` (`SPEC:1302`); the `Mode.parseSubMode` default branch on
H6159 (`SPEC:777-782`).

#### Write layout

| offset | byte | meaning | source |
|--------|------|---------|--------|
| 0 | `02` | sub-mode | `SPEC:733` |
| 1-3 | `R G B` | colour; ignored by the firmware when the flag is 1 | `SPEC:733-735`, BTLED |
| 4 | `wholeColorFlag` | `00` = RGB LEDs, `01` = white (kelvin-derived tint applies) | `SPEC:733-735`, `KSY:612-620`, BTLED |
| 5-7 | `R2 G2 B2` | "segment/BK RGB" per the app; on bulbs the warm/cold-white tint from the app's fixed table | `SPEC:734`, BTLED |

RGB form: `02 R G B 00 00 00 00` (`REPO:protocol.py:116-117`; H6159-REV sends
`33 05 02 00 00 FF 00 ...` for blue). White form: `02 FF FF FF 01 tR tG tB`
(`SPEC:1273-1274` for H6001; BTLED captures `33 05 02 ffffff 01 d7e2ff` and
`... 01 ff932c`). API-BLE sends `02 R G B 00 FF AE 54` for the whole strip (bytes
5-7 carry a stale tint, harmless). There is no kelvin field; the kelvin lives only in
the app's tint table.

#### Read-back

`AA 05 02 R G B flag R2 G2 B2`. Verified on the H6159 by this repo
(`REPO:protocol.py:272-278`, BENCH). The decompiled app does not expose a dedicated
reader on the h6159 stack in the spec; the shared newdetail stack would parse a
0x02 reply as `[gradual, kHi, kLo]` (`YAML:554`: "0x0b / 0x02 / 0x0d take the same
parse"), so on a modern-stack device a 0x02 reply must not be trusted to carry RGB.
On the H6159 it does.

Carries: RGB, white flag, tint. Kelvin: no (derive from tint with
`REPO:protocol.py:334-342`).

#### Families

Primary (`color: 0x02`): `h6159` H6159/H6110/H6109/H614A/B/E (`YAML:593-602`);
`h6160` H6160/H6163/H6117 (`YAML:604-614`, 0x0B alt); `h6102` H6102 (`YAML:668-678`);
`h6104`, `h6105`, `h6113`, `h6114` (`YAML:680-731`); `h6119` (0x15 alt,
`YAML:733-744`); `h6127` H6107/H6116/H6127/H6161 (`YAML:760-768`); `h6129`;
`h613839` H6138/H6139/H613A-F (0x0D alt, `YAML:780-788`); `h6181`
(`YAML:790-798`, but see 0x0D); `h6182`, `h6185`, `h6101` (`YAML:800-838`).
Alternate (`color_alt: 0x02`): `bulblightv3` for the legacy H6001/H6006 path
(`YAML:493-494`); `stringlightv2` (`YAML:907`).

#### Probe

Write `02 12 34 56 00`, read `AA 05 01`, expect `AA 05 02 12 34 56 00`
(`REPO:probe.py:202-214`). Firmware that does not know 0x02 keeps reporting its
previous mode, so require both the sub-mode byte and the RGB. Hardware verified on
H6159 (BENCH) and H6001 (BTLED writes; read-back not captured there).

Confidence: hardware verified (H6159, this repo); app-confirmed for the rest.

---

### 6.2 0x0B - dreamcolor RGBIC ("SubModeColor", dreamcolorlightv1/v2, h612526, barelightv1, bulblightstringv1, pact_tvlightv2)

App name: `dreamcolorlightv1/ble/SubModeColor.getWriteBytes` (`SPEC:1303`);
`h612526/ble/SubModeColor.java:188` (`YAML:758`); `dreamcolorlightv1/ble/Mode.java:80-116`
dispatch (`YAML:862`). `SPEC:787` calls the family "dreamcolor (color = 0x0B,
color-v2 = 0x15, music = 0x11/0x13)".

#### Write layout - two forms are documented, and they disagree

Form A (spec, from `dreamcolorlightv1/ble/SubModeColor.getWriteBytes`):

| offset | byte | meaning | source |
|--------|------|---------|--------|
| 0 | `0B` | sub-mode | `SPEC:1303` |
| 1-3 | `R G B` | colour | `SPEC:1303`, `KSY:622-627` |
| 4-5 | `mask0 mask1` | segment mask, LSB first | `SPEC:1303`, `SPEC:1323` ("0x0B uses the same scheme inline at bytes 4-5") |
| 6-7 | `00 00` | pad | `SPEC:1303` |

This is what `REPO:protocol.py:130-134` builds and what API-BLE sends when
segments are given (`0b R G B l r AE 54`, `API-BLE device.py:73-95`; API-BLE puts
bits 0-7 in `l` and 8-14 in `r`, i.e. LSB-first, matching the spec).

Form B (ha-govee-led-ble, from the H6199 pact 1/1 legacy producer in Govee Android
7.6.01):

| offset | byte | meaning | source |
|--------|------|---------|--------|
| 0 | `0B` | sub-mode | `HGL:tools/ble/kaitai/speculative/h6199_pact1_wire_write.ksy:37-38` |
| 1-3 | `R G B` | colour | `...:57-60` |
| 4-5 | `kHi kLo` | kelvin, big-endian, 0 for RGB | `...:61-62` ("a two-byte Kelvin field") |
| 6-7 | `mask0 mask1` | 15-bit segment mask LE | `...:63-64` |

Test vector: `33 05 0b 20 60 a0 00 00 ff 7f ...` (`HGL:tests/test_h6199_pact1.py:100`).
`HGL:docs/h6199-pact1.md:6-8` says the writer sends "RGB, zero Kelvin and whole-strip
mask 0x7fff" and that "These app-derived writes remain candidates pending independent
official-app captures" (`...:28-29`). So Form B is app-derived, not captured.

Neither form has been confirmed on hardware by anyone in these sources. They are
not mutually exclusive: with kelvin 0, Form B is Form A with the mask shifted two
bytes right. A firmware reading Form A would see Form B's `00 00` as an empty mask
(nothing lit); a firmware reading Form B would see Form A's mask bytes as a kelvin.
A probe has to try both.

No colour-temperature path exists on 0x0B in the spec (`SPEC:1271-1272`,
`SPEC:1303`). Form B's kelvin field suggests at least the H6199 pact-1 producer
had one; `HGL:docs/h6199-pact1.md:10-11` keeps colour temperature disabled on that
profile.

#### Read-back

The app has no 0x0B-specific reader. On the old `AbsBle4Old` / `SubModeColor.parse`
stack only the op/gradual bit (`bArr[0] == 1`) is read (`SPEC:239-242`, said of
0x0D/0x15 on the legacy stack; `KSY:247-249`). On the shared newdetail stack a
`AA 05 0B` reply goes through the default `SubMode4Color` parse:
`[gradual (1 = on), kelvin s16be]` (`YAML:554`, `SPEC:1358-1361`). Nobody parses
RGB out of a 0x0B reply. `REPO:protocol.py:300-304` currently takes bytes 1-3 as
RGB; that is a guess with no source behind it, and the documented parsers say the
bytes after the sub-mode are `[gradual, kHi, kLo]`.

Carries: gradual flag and kelvin (per app parser); RGB: no. H6125 public logs
from 2020 (hw 1.00.04 / fw 1.04.08) show "legacy 0x0b static colour, aa a2
segment pages" (`HGL:docs/h6125-support.md:7`), so the colour itself is read with
`AA A2` (V1: 4 groups x `[R,G,B]` per frame, `YAML:858`, `KSY:325-330`).

#### Families

Primary (`color: 0x0b`): `dreamcolorlightv1` H6102, H611B, H611Z, H6123, H612A-F,
**H6168**, **H617A**, **H619A**, **H61A0**, H61A8, H61A9, H61E0, **H61E1**, H70A1-3
(`YAML:841-862`, confidence high); `dreamcolorlightv2` H6102, H6116, H6117, H611A,
H6125, H6126, H6127, **H6143**, **H6144**, H6145-7, H6161, H6163, H6171, H6184
(`YAML:864-877`, medium); `h612526` H6125/H6126 (`YAML:746-758`, high);
`barelightv1` H6145/6/7, H6171 (`YAML:929-942`); `bulblightstringv1` H7002/H7005
(`YAML:913-927`, scene 0x09); `pact_tvlightv2` **H6198/H6199** (`YAML:1150-1164`,
high). Alternate (`color_alt: 0x0b`): `h6160` (`YAML:610`).

Bold = models this repo tables as 0x15 primary (`REPO:models.py:116-142`).

Which of 0x0B / 0x15 a dreamcolor device actually speaks is decided by its
**pact** (advertised protocol version), not the SKU: H6199 pact 1/1 -> 0x0B
legacy producer, pact 2/1 -> 0x15 (`HGL:docs/h6199-pact1.md:3-19`; the tested
H6199 was pact 2/1, hw 3.02.01, fw 1.10.04, `HGL:docs/h6199-support-findings.md:12-15`).
H6102 goods 18 registers pacts 1/1, 1/2, 10/1, 10/2 and takes the modern
`SubModeColorV2` (0x15) route for pact 10, with the "extended static command" also
enabled on pact 1 from hw >= 1.00.03 / fw >= 1.06.00 (`HGL:docs/h6102-application-findings.md:12,30`;
`HGL:tools/ble/kaitai/speculative/H6102.md:21-24`). H617A advertised pact 10/1
(`HGL:docs/h617a-support-findings.md:182-183`).

#### Probe

1. Prefer deciding from the advertisement first (see "Capability reads": pact
   type/code in the manufacturer data). Pact 1/x on a dreamcolor SKU -> expect 0x0B;
   pact 2+ or 10/x -> expect 0x15.
2. Write Form A `0B 12 34 56 FF 7F`, then read `AA A2 01` (V1 pages) and/or
   `AA A5 01` (V2 pages, `[group, 3 x {bri,R,G,B}]`) and look for `12 34 56` in the
   records. The `AA 05` reply will not carry the RGB.
3. If nothing lit / no echo, write Form B `0B 12 34 56 00 00 FF 7F` and repeat.
4. Treat `AA 05 0B ...` as proof of the sub-mode byte only.

Confidence: app-confirmed (two conflicting app-derived layouts); no hardware
confirmation of either in any source consulted.

---

### 6.3 0x0D - RGBWW colour + CCT ("SubModeColor", bulblightv3 / tablelampv1 / homelightv1 / stringlightv2 / tvlight v1,v3,v4 / rgblight H6188)

App name: `bulblightv3/ble/SubModeColor.getWriteBytes` (`SPEC:1304`);
`makeSubModeColor4Kelvin` (`SPEC:1258`); `homelightv1/ble/SubModeColor.java:150`,
`carlightv1/ble/SubModeColor.java:132` (`YAML:979`, `YAML:1005`); on H6159 /
H613839 the same byte is `SubModeColor4Bk` "background colour" (`SPEC:772`).

#### Write layout

| offset | byte | meaning | source |
|--------|------|---------|--------|
| 0 | `0D` | sub-mode | `SPEC:1252` |
| 1-3 | `R G B` | colour; `FF FF FF` (white slot) or the tint for CCT | `SPEC:1252-1258` |
| 4-5 | `kHi kLo` | kelvin big-endian, `00 00` for RGB | `SPEC:1255`, `KSY:640` |
| 6-8 | `R2 G2 B2` | kelvin->RGB tint from `Constant.getTemColorByKelvin`; `00 00 00` only as out-of-table fallback | `SPEC:1257-1261` |

Two CCT forms exist in the app (`KSY:629-635`, `YAML:497`, `YAML:628`):

- white-slot: `0D FF FF FF kHi kLo tR tG tB` - H6188 (`Config.GM -> Bytes.A`,
  `YAML:628`). This is what `REPO:protocol.py:172-181` sends and what the H6181
  accepted and echoed (BENCH).
- both-slot: `0D tR tG tB kHi kLo tR tG tB` - H6008/H6006/H6052 (`Config.bu ->
  Bytes.p`, `YAML:497`). `HGL:tests/test_h601c_protocol.py:67` has the white-slot
  form for H601C (`33050dffffff0a8cffae54...` = 2700 K) from
  `bulblightv3/ble/SubModeColor.java:36-44,71-82,96-110` (`HGL:docs/ceiling-support.md:26`),
  so the split between the forms is by goodsType/config path, not SKU family.

RGB form: `0D R G B 00 00 00 00 00` (`SPEC:1304`, `REPO:protocol.py:125-129`,
`HGL:tests/test_light_commands.py:69` H6179 `33050d0a141e00...`). Kelvin range
2700-6500 on bulbs, 2000-9000 on H6188 and most strips (`SPEC:1255-1256`,
`YAML:632`).

An RGB pick that exactly equals a tint-table colour is sent as CCT instead
(`[0x0d, picked R,G,B, kelvin u2be, first tint of that kelvin]`, `YAML:497`,
`YAML:628`); a probe colour must not be one of those 178 colours. `12 34 56` is
not.

#### Read-back - three different bodies share `AA 05 0D`

| kind | body after `0D` | who | source |
|------|-----------------|-----|--------|
| rgb_kelvin_tint | `R G B kHi kLo tR tG tB`; kelvin != 0 -> CCT mode, colour shown = tint | rgblight H6188 (`ModeParseStrategyConfig.a`); **H6181 fw 2.03.11 on the bench**; **H6006 fw 1.00.38 on the bench** (`0D 000000 0A8C 000000` in white, `0D 283CC8 0000 ...` after an RGB write), against `YAML:477`'s cct kind for bulblightv3 | `YAML:629`, `KSY:313-314`, `KSY:1261-1265`, BENCH; H601C hypothesis `HGL:tools/ble/kaitai/speculative/h601c_status_reply.ksy:10-12` |
| rgb | `R G B` only, fanned across zones | H6052 (`H6052InfoDetail.parseModeValidBytes:323-346`) | `KSY:306-309` |
| cct (default `SubMode4Color`) | `gradual kHi kLo`, no RGB | shared newdetail stack; bulblightv3 (`mode_0d_kind: cct`, `YAML:477`); H6061 reports `[R,G,B,kHi,kLo]` with no tint | `KSY:310-312`, `YAML:643`, `YAML:653` |

`SPEC:239-242` adds that on the legacy `SubModeColorV1.parse` stack 0x0D is
"effectively write-only" (only the op/gradual bit is read). `REPO:protocol.py:291-299`
implements kind rgb_kelvin_tint, which is right for H6181/H6188 and wrong for the
other two kinds.

Note H60A6 and H6061 **answer `AA 05 0D` while being 0x15 writers**: H6061 "reads
colour back as 0x0d although it writes 0x15; mode 0x15 is not parsed"
(`YAML:653`), and H60A6 lists `color_alt: 0x0d` for part-colour and the pact
(1,<2) path (`YAML:414`, `YAML:418`). A safe probe that sees 0x0D in the mode
reply therefore cannot conclude the light wants 0x0D writes.

#### Families

Primary (`color: 0x0d`): `bulblightv3` H6001-H601F, H8015, H14xx (`YAML:475-504`);
`tablelampv1` **H6052**/H6078 (`YAML:945-969`); `homelightv1` H6148; `hollowlamp`
H6050/51/55/58/**59**/73 (`YAML:981-995`); `carlightv1` H6118/H6194; `h6057`
(`YAML:1025-1045`); `h70b1`/`h70b2` (`YAML:1058-1079`); `h6188` (`YAML:616-636`);
`stringlightv2` H6109/H6110/H6118/H6121/H6138/H6141/H6142/H6154/H6159/H6160/H6170/
H6178/H6192/H6194/H6195/H6196/H6197 (`YAML:900-911`, 0x02 alt); `tvlightv1` H6179
(`YAML:1139-1148`); `pact_tvlightv3` H6046/H6053/**H6056** (`YAML:1166-1177`, 0x15
alt); `pact_tvlightv4` H6049/H6054 (`YAML:1179-1193`, 0x15 alt). Alternate
(`color_alt: 0x0d`): `h6159`, `h613839`, `h60ax`, `h6061` (read-back only).

Bold = models this repo tables as 0x15 (`REPO:models.py:138-141`). H6181 is tabled
0x0D here on hardware evidence (`REPO:models.py:158-173`) against the app's 0x02.

#### Probe

Write `0D 12 34 56 00 00 00 00 00`, read `AA 05 01`, expect `AA 05 0D 12 34 56`
(`REPO:probe.py:202-214`). This works on kind rgb_kelvin_tint (H6181 BENCH) and
kind rgb (H6052). On kind cct the reply is `0D 00 00 00` after an RGB write and
`0D 01 kHi kLo` after a CCT write; so if the RGB echo fails, write
`0D FF FF FF 10 CC tR tG tB` (4300 K) and accept `AA 05 0D ?? 10 CC` or
`AA 05 0D FF FF FF 10 CC`. Kelvin position differs between kinds (bytes 2-3 vs 4-5);
search both.

Confidence: hardware verified (H6181, this repo; write + rgb_kelvin_tint
read-back). Other kinds app-confirmed only. H601C owner report
(`HGL:docs/ceiling-support.md:30`: "live 33 05 00 ACK and aa 05 0d register
confirmation", hw 1.00.01 / fw 1.00.07) is unattributed.

---

### 6.4 0x15 - op-tagged RGBIC ("SubModeColorV1 / SubModeColorV2 / SubMode4Color", h60ax, h6047, h61d3, striplight, h604a, pact_h605b, dreamcolor v2 route)

App name: `pact_h60a0/ble/v1/SubModeColorV1.getWriteBytes` with `OP_TYPE_*`
constants (`SPEC:1305-1316`); `dreamcolorlightv1/ble/SubModeColorV2` (reader,
`SPEC:232`, `YAML:744` for H6119 `SubModeColorV2.java:286`); shared
`base2light/pact/newdetail/config/fuc/color/Config.Yt -> Bytes.h / Bytes.r`
(`YAML:549-550`); `h6047/ble/SubModeColor` (`YAML:451`).

#### Write layout - op byte selects the body

| op | body after `15 op` | meaning | source |
|----|--------------------|---------|--------|
| `00` | - | "apply mode" | `SPEC:1311` |
| `01` basic | `R G B mask0 mask1` | colour, SubModeColorV1 i2==1 | `SPEC:1312`, `KSY:664` |
| `01` H60A1/H60A6 RGB form | `R G B 00 00 00 00 00 mask0 mask1` | colour, i2==12; **this is the form every modern device uses** | `SPEC:1313`, `KSY:665`, `YAML:549` (`Bytes.h`) |
| `01` CCT form | `R G B kHi kLo tR tG tB mask0 mask1` with `R G B` = `FF FF FF` (`Bytes.r` type 1 "white in bytes 2-4") | colour temperature, i2==11 | `SPEC:1314`, `KSY:666`, `YAML:418`, `YAML:517`, `YAML:550` |
| `02` | `pct mask0 mask1` | per-segment relative brightness 0-100 | `SPEC:1315`, `KSY:677-680`, `YAML:551` |
| `03` | `pct x N` | all-segment brightness list (H6102 `SubModeColorV2:521-588`) | `HGL:tools/ble/kaitai/speculative/H6102.md:24-26`, `HGL:tests/test_h6102_protocol.py:225` |
| `04` | `group R G B x<=4` | colour per group of 4 panels (H6061) | `YAML:651`, `KSY:168` |
| `05` | `kLo kHi` | "basic" colour temperature, **little-endian** | `SPEC:1316`, `KSY:682-684` |

H6061 uses yet another op-01 CCT form: `15 01 tR tG tB kHi kLo <24-bit mask>`
(tint in the colour slot, no white slot, `YAML:652`).

The RGB form and the CCT form are byte-identical in length and op; nothing in the
frame says which it is (`KSY:667-670`). `REPO:protocol.py:118-124,148-171`
builds the H60A1 RGB and CCT forms with a `FF 7F` mask.

The white slot is not strictly required: a direct H617A segment-kelvin write
`33 05 15 01 00 00 00 0b b8 ff b9 69 01 00` (RGB `00 00 00`, 3000 K, tint, mask
bit 0) was accepted on hardware (`HGL:tests/test_h617a_segment_kelvin.py:56`,
"Direct-device write used an explicitly supplied companion"). Masked kelvin on
H60A1 blanks the unselected zone (`HGL:docs/ceiling-support.md:35,69`).

Hardware captures of the RGB form: H617A `33 05 15 01 ff 00 00 00 00 00 00 00 ff 7f`
(H617A-REV `tests/test_protocol.py:42`; GENZERO `051501 RRGGBB 0000000000 MMMM`;
`HGL:tests/test_light_commands.py:40`); H6199 pact 2/1 (HGL grammar `H6199`);
H60A1 `33 05 15 01 ff 00 00 00 00 00 00 00 00 10` panel-only mask
(`HGL:tests/test_h60a_protocol.py:72`). GBL sends the same for H6053/H6072/H6102/H6199
(`GBL light.py:35,272-273`).

#### Read-back

Shared parser (`SubModeColorV2.parse` / `ComposeChange2ColorMode.result`;
`OpInfo4Detail.parseModeValidBytes -> SubMode4Color`):

```
AA 05 15 <op|gradual> <kHi> <kLo> 00...
```

kelvin at frame bytes 4-5, big-endian, **no RGB** (`SPEC:232-238`, `SPEC:1355-1361`,
`KSY:239-249`, `KSY:265-279`). Byte 3 is `01` in the spec's example and is the
`isColorTem` flag in `SubMode4Color.java:55-59` (`SPEC:1358-1359`); on H6047 it is
the gradual flag and two kelvins follow, left bar then right bar
(`[gradual][kelvin left u2be][kelvin right u2be]`, `SPEC:1363-1368`, `YAML:451`).
`HGL` models it as `[sub][kelvin u2be][tail]` for H617A/H6125/H6102
(`HGL:tools/ble/kaitai/status_reply.ksy:93-100`, `.../speculative/h6125_status_reply.ksy:98-105`)
and marks `static_readback_kelvin=True` for H6099/H6102/H6125
(`HGL:custom_components/ha_govee_led_ble/const.py:554,650,708`) but **not** for
H617A (`...:345-400` has no such flag; `HGL:docs/h617a-support-findings.md:92`
"This establishes RGB companions, not Kelvin readback").

Hardware counter-evidence: on the H6199 (pact 2/1, fw 1.10.04) "neither AA 05 00
nor AA 05 01 returned Kelvin after a successful 3000 K write; both returned static
detail zeroes. Rendered segment RGB changed correctly."
(`HGL:docs/h6199-support-findings.md:308-311`). GENZERO probed `aa0501` on the
H617A and listed it as unresolved; the only decoded mode reply there is a scene
(`0407`).

Carries: kelvin (sometimes), gradual/CCT flag. RGB: never. RGB and per-segment
state come from `AA A5 <group>` pages: `[group, N x {brightness, R, G, B}]`, N = 3
on H617A/H6125 (15 segments, 5 pages), 4 on H6199/H60A6/H6047/H6062
(`HGL:tools/ble/kaitai/govee_segment_page.ksy`, `HGL:status_reply.ksy:31`,
`YAML:419`, `YAML:458`, `YAML:553`; GENZERO `AA A5 01` -> `64ff8d0b 64aa5511 64ff8d0b`).
Some goodsTypes deliver those pages only inside the `0xAC` burst (`SPEC:683-695`).

#### Families

Primary (`color: 0x15`): `h60ax` H60A0/A1/A4/A6, H60C1, H1270 ... (`YAML:406-435`);
`h6047` H6047/43/42/39/38/48, H8048 (`YAML:437-473`); `h61d3` H61B0-H61F6 incl.
**H61E5**, H1A4x, H1AB1-3, H6640/41, H703A/B (`YAML:506-533`); `striplight`
**H6062** (`YAML:537-561`); `h6061` **H6061** writes only (`YAML:638-662`); `h604a`
H604A-D (`YAML:1007-1023`); `pact_h605b` H605B/C/D, H6601-04, H6608, H8604
(`YAML:1195-1212`). Alternate (`color_alt: 0x15`): `h6119`, `dreamcolorlightv1`,
`dreamcolorlightv2`, `barelightv1`, `pact_tvlightv2`, `pact_tvlightv3`,
`pact_tvlightv4` - and on every one of those the 0x15 route is what the modern
pact actually takes (H617A, H6102, H6125, H6199 pact 2 in HGL).

Not in devices.yaml at all: H618A, H619B/C/E/Z, H61A1, H61A2, H61B5, H6065,
H6072, H6076, H7020, H7021 (this repo tables most of them). HGL drives H6076 with
the H617A grammar (0x15) and the owner qualified power/brightness/RGB
(`HGL:docs/h6076-music-support.md`, `const.py:644`); GBL lists H6072 as segmented
(0x15) without evidence (`GBL light.py:35`). Web searches for H6167/H6168, H619x,
H70xx captures returned nothing with bytes (searched 2026-10-19; results were
retail pages and the generic guides already cited).

#### Probe

The repo's current test (`REPO:probe.py:106-110,217-236`): write the CCT form at
4300/6700 K and accept `AA 05 15 ?? kHi kLo`. That is app-correct but fails on at
least one real H6199 and is unproven on H617A. Stronger sequence:

1. Write RGB form `15 01 12 34 56 00 00 00 00 00 FF 7F`.
2. Read `AA 05 01`; accept the sub-mode byte `15` as weak evidence only.
3. Read `AA A5 01`; accept if any record's `R G B` == `12 34 56` (brightness byte
   precedes it; H617A pages are `[01, 64 12 34 56, 64 12 34 56, 64 12 34 56, 00 x4]`).
4. Only if `AA A5` is unanswered fall back to the CCT echo test, and treat
   `kelvin == 0` as "no evidence" rather than "not 0x15".
5. Mask width: `FF 7F` addresses 15 segments; lights with 13 (H60A6) or 14 (H60A1,
   H6099) segments will receive bits for segments that do not exist. HGL uses
   per-model masks (`whole_device_mask`, `const.py:391-482`). No source records
   whether an over-wide mask is rejected; read `AA 40` first where available (below)
   and build the mask from the segment count.

Confidence: write form hardware verified by community captures (H617A, H6199 pact
2, H60A1/H60A6); kelvin read-back app-confirmed but contradicted on one H6199;
RGB never in the reply.

---

### 6.5 Non-colour sub-modes a probe must recognise

Values of the byte after `AA 05` (or `33 05`) that mean "not a static colour".
Every one of these is family-specific; the same byte is music on one stack and
colour on another (`SPEC:757-772`, `KSY:139-140`).

| byte | meaning | families (app class) | reply body | source |
|------|---------|----------------------|------------|--------|
| `00` | video / ambient (camera, HDMI) | H6104, H6101, H6198/H6199, H6049/H6054, H6047 family, h604a, pact_h605b | H6199: `[source 0 movie/1 game][region 0 part/1 all]...` | `SPEC:770`, `YAML:690,832,1155,1184,445,1012,1200`; `HGL:h6199_status_reply.ksy:66-76` |
| `01` | music (Telink-era) | H6102, H6104, H6127 (`SubModeMusic`), H6101; alt on H6160 | not documented | `SPEC:767`, `YAML:674,690,765,832,610` |
| `03` | music | H6159 (`SubModeMusic`), H6105, H6113, H6114, H6129, H6160, H6182, H6188; alt on H6127 | not documented | `SPEC:778`, `YAML:599,704,715,727,775,805,626` |
| `04` | scene | nearly every family | `04 <code lo> <code hi>` (u16 LE; `SubMode4Scene`, `SubModeScenes.parse`) | `SPEC:742`, `SPEC:1176-1177`, `YAML:630`, `KSY:686-690`, `HGL:status_reply.ksy:101-108`; GENZERO reply `0407` on H617A; `REPO:protocol.py:305-306` reads only the low byte |
| `05` | mic (`MicController`) | H6185; bulblightv3; stringlightv2 | - | `YAML:816,494,907`, `SPEC:1048` |
| `07` | old DIY | H6127, H6129 | - | `SPEC:769`, `YAML:765,775` |
| `09` | scene on bulb strings | bulblightstringv1 H7002/H7005 | - | `SPEC:766`, `YAML:920` |
| `0A` | new DIY (`SubModeNewDiy`) | almost all | `0A <code u16 LE>` | `SPEC:768`, `SPEC:781`, `HGL:govee_common.ksy:16-19`, `HGL:h6125_status_reply.ksy:112-117` |
| `0B` | **game** on H6057 (otherwise colour) | h6057 | - | `SPEC:771`, `YAML:1031` |
| `0C` | music (`SubModeMusic` V1/V2) | H6119, H6125/H6126, barelight, tvlight v2/v3/v4 | - | `YAML:738,751,935,1155,1171,1184` (the spec's per-device table says `12` for H6119/H612526/barelight, `SPEC:1041-1042,1061`: unresolved discrepancy between spec and yaml) |
| `0D` | **background colour** `SubModeColor4Bk` on H6159/H613839 (otherwise colour) | h6159, h613839 | - | `SPEC:772`, `YAML:599,785` |
| `0E` | music (`ParamsSubMode4Music`) | H6159 alt, stringlightv2, hollowlamp, carlight, H6179, bulblightstringv1 | - | `SPEC:779`, `YAML:599,907,987,1002,1144,920` |
| `0F` | music | tablelampv1 H6052, homelightv1 H6148, hollowlamp alt, H1161, H6076 (HGL) | - | `YAML:952,976,987`, `SPEC:1073`, `HGL:CONTRIBUTING.md:33` |
| `11` | music (`SubModeMusicV2/V3`) | dreamcolorlightv1/v2 (H617A, H6125 ...) | `[mode_id][sensitivity]` for new modes; legacy modes (03/04/05/06) add `[style][fixedFlag][R G B]` | `YAML:849,870`, `HGL:govee_common.ksy:20-57`, `HGL:h6125_status_reply.ksy:57-70,118` |
| `12` | music | H6119 (spec table) | - | `SPEC:1041` |
| `13` | music (`SubModeNewMusic` / `SubModeMusicV1`) | h60ax, h6047, h61d3, striplight, h604a, h605b, h6057, h70b1; alt on dreamcolor, stringlightv2, tablelamp, carlight, tvlight v2-v4 | `[musicCode][value][dynamic][specColorFlag][R G B if flag]` on H60A6; `[musicCode][value]` on base2light; H617A answers `AA 05 13` too (`status_reply.ksy:49`) | `SPEC:242-245`, `KSY:281-299`, `REPO:protocol.py:27` names 0x03 "music" and misses this one |
| `14` | music / gradual | stringlightv2 and bulblightstringv1 per spec table (`14(+19)`); also opcode 0x14 "gradual" on dreamcolor | - | `SPEC:1059-1060`, `YAML:853` |
| `16` | `SubModeAbsMusic` | (reply `[u16 count LE][value]`) | - | `SPEC:245` |
| `17`,`19`,`22` | music variants | dreamcolorlightv2 (`17(+12,19)`), dreamcolorlightv1 (`11(+19,22)`) | - | `SPEC:1057-1058` |
| `FF` | mic stub | H6181 (`SubModeMic`, flag only) | - | `YAML:795` |

Rule for a probe: treat `00, 01, 03, 04, 05, 07, 09, 0A, 0C, 0E, 0F, 11, 12, 13,
14, 16, 17, 19, 22` as "not a colour mode"; treat `0B` and `0D` as colour unless
the model is H6057 (game) or the reply is a `SubModeColor4Bk` on an h6159/h613839
strip that otherwise writes 0x02. `REPO:probe.py:70-76,154-164` currently names
only 0x03 and 0x04.

---

### 6.6 Capability reads: what can be learned without trial writes

| frame | reply | what it tells you | who answers | source |
|-------|-------|-------------------|-------------|--------|
| advertisement, manufacturer AD `FF`, company `88 EC` | `+2` flags (bit6 encrypted, low nibble proto version), `+5..+6` pactType u16 BE, `+7` pactCode u8 | the pact selects the dialect: dreamcolor pact 1 -> 0x0B producer, pact 2/10 -> 0x15; H60A6 pact (1,<2) -> 0x0D form, (1,>=2) -> 0x15; (1,>=3) adds masked kelvin | every Govee light; no connection needed | `SPEC:1592-1600`, `SPEC:1509-1514`, `YAML:51-67`, `YAML:418` ("Config.kw"), `HGL:docs/h6199-pact1.md`, `HGL:docs/ceiling-support.md:47-53`; GENZERO H617A mfg data `0388ec000a0101` = pact 10/1 |
| `AA 05 01` | `AA 05 <submode> ...` | current sub-mode; colour only if 02/0B/0D/15 and not the family's alt use | all | `SPEC:1353-1361` |
| `AA 40 00` (`SINGLE_IC_SEGMENT_NUM`, `ControllerOnlyReadIcSegmentNum`) | `AA 40 <ic u16 BE> <segment u8>` | IC count and segment-group count; "the ONLY live-BLE capability read" | h61d3 (H6641: groups = ceil(IC/5)), dreamcolorlightv1 (`read_ic 0x40`), h604a; H617A answered `AA 40` -> `00 0F` (15) on hardware | `SPEC:637-640`, `SPEC:1581-1585`, `KSY:361-369`, `YAML:516,855,1017`; GENZERO |
| `AA 41 00` on H6062 only | `AA 41 <ic s16 BE> <sectionNum u8>` | segment count used for mask width and the `AA A5` walk; mandatory connect step on goodsType 41 | striplight H6062 (elsewhere 0x41 is on/off memory, `YAML:297`) | `YAML:548`, `KSY:89`, Brady-Woods issue #15 |
| `AA 0F 00` (`LightNumController`) | `AA 0F <count u8>` (positive signed byte) | light / bulb count | h612526, dreamcolorlightv1, barelightv1, bulblightstringv1 (`bulb_num`), H7022; H617A did **not** answer it in HGL's sessions ("AA0F remained absent"); GENZERO lists `aa0f00` unresolved | `YAML:753,852,922,939,1132`, `HGL:tools/ble/kaitai/speculative/h617a_control_payload.ksy:29-42`, `HGL:docs/h617a-support-findings.md:22` |
| `AA 0E 00` (`LimitController`) | `AA 0E <flag>` | current-limit / segment-limit flag | H6102, dreamcolorlightv1, stringlightv2, H6179; H617A -> `0F` | `YAML:678,851,908,1145`; GENZERO |
| `AA A3 00` (`ControllerGradual4BleWifi`) | `AA A3 <01 = gradual on>` | gradual flag; presence implies the modern RGBIC stack | h6047, dreamcolor, barelight, tvlightv2; H617A -> `01` | `KSY:371-375`, `YAML:452`; GENZERO |
| `AA A5 <group>` (`Controller4ColorInfoByGroup` / `BulbGroupColorV2`) | `AA A5 <group> N x [bri R G B]` | per-segment colour; also proves segment count and that the light is RGBIC | h60ax, h6047, h61d3, striplight, h6061, hollowlamp, H6119; dreamcolor V2 firmware; H617A (`AA A5` with no group -> `15 01` per GENZERO) | `SPEC:686-695`, `YAML:419,458,520,553,654,740,858`, `KSY:343-359` |
| `AA A2 <batch>` (`BulbStringColorController`) | `AA A2 <batch> 4 x [R G B]` | per-segment colour on V1 dreamcolor / h6160 / h612526 / bulb strings | `YAML:611,754,858,874,923`, `KSY:325-330` |
| `AC 03 02 41 30` / `AC 03 03 41 30 A5` (`Compose4BaseInfoSingleRead`) | burst of `AC` frames reassembled into TLVs: `01` power, `04` brightness, `05` `[submode, params]`, `07` device info, `30` zone, `41` seg/IC, `A5` colour group | one-shot bulk status including the mode block | **only** H60A6 and goodsTypes 277/288/298 (H6039/H6038/H6048) and 263 (h61d3); H6047 and H6641 "return zero frames deterministically" | `SPEC:528-580`, `SPEC:683-695`, `KSY:455-512`, `YAML:444,513` |
| `AA 07 02 / 10 / 11` | serial (reversed), UID + two versions, Wi-Fi MAC + versions | identity; hw version string helps pick revision-specific behaviour (H6199 pact1 hw 1.00.01; H60A6 hw 1.04.03 / fw 1.00.41) | all | `SPEC:619-630`, `SPEC:1534-1535` |
| `AA 06 00` | ASCII firmware | ditto | all | `SPEC:726-729` |
| BGC-info characteristic `...2b12` read | byte0 `encryptVersion` 0/1/2 | whether frames must be AES-wrapped before any of the above | all | `SPEC:1518-1519`, `YAML:102-113` |
| `AA EF` (`SINGLE_PACT`), `AA AB` (`SINGLE_DYNAMIC_API_SUPPORT`) | undocumented | pact negotiation / dynamic-API capability | listed only as constants; H617A `AA EE` -> `00 0A 01` (GENZERO, unexplained) | `SPEC:1522`, `SPEC:656-659` |

The app itself never reads "which colour sub-mode" from the device; it keys a
compiled per-goodsType table from the SKU in the name plus the advertised pact
(`SPEC:1564-1590`). The only live capability read it does is `0x40`
(`SPEC:1581`). So the closest thing to a write-free dialect probe is:
name -> SKU -> family table, plus advertised pactType/pactCode, plus whether
`AA 40` / `AA A3` / `AA A5` answer (modern RGBIC stack) and what sub-mode
`AA 05 01` is currently in.

---

## 7. Scenes

### 7.1 Activation frame

`33 05 04 <sceneCode lo> <sceneCode hi>`: the code is a **u16 little-endian**
(`SPEC:1176-1177`, `YAML:150`, `KSY:686-690`, `HGL:tools/ble/kaitai/speculative/H6102.md:29`
`SubModeScenes.getWriteBytes:39-42`). It is **always sent** when a scene is applied
(`BaseSceneViewMode.java:757-781`, `YAML:253`). Reply `AA 05 04 <lo> <hi>`
(`YAML:630` `SubMode4Scene`; GENZERO reply `04 07` for Romantic on H617A;
`HGL:status_reply.ksy:101-108`). `REPO:protocol.py:185-188,305-306` sends and reads a
single byte, which is correct only for codes < 256.

### 7.2 Upload-then-activate on newer firmware

For library scenes the app first decides whether to **upload** the scene blob with a
`0xA3` multi-packet write, then always activates (`SPEC:484-516`, `YAML:148-163,252-265`):

- Upload happens iff `ScenesOp.parseSceneV1(sceneType, sceneCode, scenceParam, versionArray)`
  returns a controller; `sceneType` (0 static, 1 rgb, 2 rgbic, 3 graffiti, 4 cube,
  5 diy, 6 compose) comes from the cloud scene DTO and `versionArray` is a static
  per-goodsType constant (`YAML:182-201`, `SPEC:438-473`). comByte at `0xA3` byte 4:
  type 1 -> `01`, 2 -> `02`, 3 -> `07` (strip 2 leading bytes), 6 -> `0A`, type 5 ->
  `0C`/`04`/`56`/`58` by version (`YAML:189-200`).
- Types 0 (static) and 4 (cube) never upload: activate-only (`YAML:201`).
- Dialect B (DIY/graffiti, H60A6 and newer): commByte = device protocol code
  (`0x58` on H60A6, `0x03` on H61A8), value re-serialised by the family's parser,
  `0xA3` or `0xA4` chosen by the blob (`SPEC:326-346`, `SPEC:434-436`, `YAML:169-176`).
- The blob is base64 `scenceParam` from `GET /bff-app/v2/devices/scenes?sku=&device=`
  and friends; `bigEffectStr == 1` stubs need `POST /bff-app/v1/devices/scenes/effect-strs`
  (`YAML:266-279`). Community mirrors of these catalogues: GBL ships per-model JSON
  (`jsons/<model>.json` with `scenceParam`, `GBL light.py:287-297`); HGL commits per-SKU
  catalogues (H617A 83 entries: 9 type0, 2 type1, 72 type2,
  `HGL:docs/h617a-support-findings.md:544`; H6102 240 entries,
  `HGL:docs/h6102-application-findings.md:16`).
- Which families need upload: everything with `scene_versions` beyond `[0]`
  (`YAML:428,466,525,556,632`); bulbs hard-code `{1}` on the apply path so type-1 RGB
  scenes upload even though capability says `{0}` (`YAML:204-211`); H6052 type-5 goes
  through `Diy.parseScenes4Diy` with comByte 3/4/9 (`YAML:212-218`).

### 7.3 Known scene ID tables

| family / SKU | codes | source |
|--------------|-------|--------|
| H6159 legacy strips (single byte, built-in) | Sunrise 0, Sunset 1, Movie 4, Dating 5, Romantic 7, Blinking 8, Candlelight 9, Snowflake 15 | API-BLE `device.py:6-15`; `REPO:models.py:36-47`; H6181 Candlelight 9 works (BENCH) |
| H617A (fw 3.02.24, built-in codes that activate without upload) | Sunrise 0, Sunset 1, Nightlight 2, Movie 4, Dating 5, Romantic 7, Illumination 0x3F, Cheerful 0x40 | GENZERO |
| H6188 (fixed list `Scenes4H6188.g`) | Movie 4, Dating 5, Reading 13, Bright 23, Aurora 24, Candlelight 9, Energetic 16, Romantic 7, Gradient 17 | `YAML:630` |
| H6179 | selector-only codes fit in 8 bits (`selector_only_scene_bits=8`) | `HGL:const.py:790` |
| H60A6 | 73 library effects, 59 graffiti (`0xA4`) / 14 DIY (`0xA3`), all commByte `0x58` | `SPEC:375-378,496-503` |
| everything else | cloud catalogue per SKU, codes u16, most need the `0xA3` upload | `YAML:266-272` |

No single-byte table exists for the RGBIC families; the repo's empty `scenes={}` for
`_RGBIC` (`REPO:models.py:108-114`) is the honest state.

---

## 8. DIY, music and video sub-modes

### 8.1 DIY `0x0A` (`SubModeNewDiy`) and old DIY `0x07`

Activate: `33 05 0A <code u16 LE>` (`SubModeNewDiy:65-82`,
`HGL:speculative/H6102.md:30`); reply `AA 05 0A <code lo> <code hi>`
(`HGL:govee_common.ksy:16-19`, `HGL:speculative/h6125_status_reply.ksy:112-117`).
The animation itself is uploaded with `0xA1` comType 2/4 (legacy, `SPEC:650-652`) or
`0xA3` subtype `02` (DIY v2) / `03` (graffiti, `DiyGraffiteV2.g()` record format at
`SPEC:397-427`) / `04` (`HGL:tools/ble/kaitai/diy_type03.ksy`, `diy_type04.ksy`). Old DIY
`0x07` is H6127/H6129 only (`SPEC:769`, `YAML:765,775`). Families: nearly all
(`diy: 0x0a` in `YAML`); absent on bulblightstringv1, H7004/H7017, H6101
(`YAML:920,832`).

### 8.2 Music sub-modes

| byte | class | families | write layout | reply | source |
|------|-------|----------|--------------|-------|--------|
| `0x01` | `SubModeMusic` (Telink) | H6102, H6104, H6127, H6101; alt H6160 | API-BLE for H6159-class: `33 05 01 <mode> 00 R G B` with modes energic 0, spectrum 1, rolling 2, rhythm 3 (RGB only for spectrum/rolling) | not documented | `YAML:674,690,765,832,610`; API-BLE `device.py:16-21` (note: API-BLE targets the H6159, whose app class uses 0x03; unverified which byte the strip accepts) |
| `0x03` | `SubModeMusic` | H6159, H6105, H6113, H6114, H6129, H6160, H6182, H6188; alt H6127 | not documented beyond the byte | not documented | `SPEC:778`, `YAML:599,...` |
| `0x05` | `MicController` / mic | H6185, bulblightv3, stringlightv2 | - | - | `YAML:816,494,907` |
| `0x0C` | `SubModeMusic` V1/V2 | H6119, H6125/6, barelight, tvlight v2/v3/v4 | - | - | `YAML:738,751,935,1155,1171,1184` (spec table says `12` for H6119/H612526/barelight, `SPEC:1041-1042,1061`: unresolved) |
| `0x0E` | `ParamsSubMode4Music` | H6159 alt, stringlightv2, hollowlamp, carlight, H6179, bulblightstringv1 | - | H6179 grammar: `[music_id][sensitivity]...` | `SPEC:779`, `YAML:599,907,987,1002,1144,920`; `HGL:speculative/h6179_status_reply.ksy:114-120` |
| `0x0F` | music | H6052, H6148, hollowlamp alt, H1161, H6076 | H6076: `33 05 0F <selector>` with `0xA3` companions | - | `YAML:952,976,987`, `SPEC:1073`, `HGL:CONTRIBUTING.md:33`, `HGL:docs/h6076-music-support.md` |
| `0x11` | `SubModeMusicV2/V3` (dreamcolor) | dreamcolorlightv1/v2: H617A, H6125, H6102 ... | `33 05 11 <mode> <sensitivity> [style] [fixedFlag] [R G B]` for legacy modes 03 rhythm / 04 spectrum / 05 energetic / 06 rolling; new modes 0x30-0x37 (bloom, shiny, separation, hopping, piano, fountain, day&night) carry only mode + sensitivity and need an `0xA3` subtype `41` upload first | same layout (`SubModeMusicV3.parse`); zero padding reads as "no colour" | `YAML:849,870`; `HGL:govee_common.ksy:20-57`; `HGL:docs/h617a-support-findings.md:312-318,345-348`; `HGL:speculative/h6125_status_reply.ksy:59-70` (H6125 mode ids 0x10-0x13 and 0x30-0x37) |
| `0x12` | music | H6119 (spec table) | - | - | `SPEC:1041` |
| `0x13` | `SubModeNewMusic` / `SubModeMusicV1` | h60ax, h6047, h61d3, striplight, h604a, h605b, h6057, h70b1; alt on dreamcolor, stringlightv2, tablelamp, carlight, tvlight v2-v4; H617A also answers `AA 05 13` | GENZERO H617A `33 05 13 06 63` (`set_music_mode`); `0xA1` comType `0x41` music table | `[musicCode][value][dynamic][specColorFlag][R G B if flag]` on H60A6; `[musicCode][value]` on base2light | `SPEC:242-245`, `KSY:281-299`; GENZERO; `HGL:status_reply.ksy:49` |
| `0x14` | music (+`19`) | stringlightv2, bulblightstringv1 per spec table | - | - | `SPEC:1059-1060` (yaml says `0x0e`: unresolved) |
| `0x16` | `SubModeAbsMusic` | - | - | `[u16 count LE][value]` | `SPEC:245` |
| `0x17`, `0x19`, `0x22` | music variants | dreamcolorlightv2 (`17(+12,19)`), dreamcolorlightv1 (`11(+19,22)`) | - | - | `SPEC:1057-1058` |
| `0xFF` | `SubModeMic` stub | H6181 | flag only | - | `YAML:795` |

### 8.3 Video `0x00`

TV backlights and camera / HDMI boxes: H6104, H6101, H6198/H6199, H6049/H6054, H6047
family, h604a, pact_h605b (`SPEC:770`, `YAML:690,832,1155,1184,445,1012,1200`). H6199
reply `AA 05 00 <source 0 movie / 1 game> <region 0 part / 1 all> ...`
(`HGL:tools/ble/kaitai/h6199_status_reply.ksy:65-76`); the write is modelled in
`HGL:tools/ble/kaitai/h6199_command_write.ksy` and `speculative/h66a0_video_command.ksy`.
Companion opcodes: `0x30` direction, `0x31` camera position, `0x32` camera check,
`0xA9` white balance / blank-screen, `0x44` calibration (section 5.2).

### 8.4 Rule for "the light is not in a colour mode"

Treat `00, 01, 03, 04, 05, 07, 09, 0A, 0C, 0E, 0F, 11, 12, 13, 14, 16, 17, 19, 22, FF`
as non-colour; treat `0B` and `0D` as colour unless the model is H6057 (`0B` = game) or
the reply is a `SubModeColor4Bk` on an h6159/h613839 strip (`0D` = background colour).
`REPO:probe.py:70-76,154-164` names only 0x03 and 0x04.

---

## 9. Status reads

### 9.1 Single `AA` replies

| request | reply body (after `AA cmd`) | source |
|---------|-----------------------------|--------|
| `AA 01 00` | `[state]`; lights 0/1, plugs relay bitmask | `KSY:378`, `SPEC:703` |
| `AA 04 00` | `[level]` raw | `KSY:382` |
| `AA 05 01` | `[submode][per-family body]` (section 6, 7, 8) | `SPEC:1353-1361` |
| `AA 06 00` | ASCII version, NUL-terminated | `SPEC:728` |
| `AA 07 <sel>` | see 5.1; `03` -> `03 "x.yy.zz"` | `SPEC:627-637` |
| `AA 0E/0F/11/12/23` | settings, see 5.1-5.2 | |
| `AA 36` | `[left, right, feast]` (H6047 heartbeat) | `KSY:387`, `YAML:447` |
| `AA 40 00` | `[ic u16 BE][segment u8]` | `KSY:361-369` |
| `AA 41 00` (H6062) | `[ic s16 BE][sectionNum]` | `YAML:548` |
| `AA A2 <batch>` | `4 x [R G B]` positional | `KSY:325-330` |
| `AA A3 00` | `[gradual]` | `KSY:371-375` |
| `AA A5 <group>` | `[group] N x [bri R G B]` | `KSY:343-359` |
| `AA A9 <sel>` | `[sel][len][payload]` white balance etc. (H6199) | `HGL:h6199_status_reply.ksy:78-96` |
| `AA AE` | edge brightness (H6199) | `HGL:h6199_status_reply.ksy:136-150` |
| `AA B1 00` | `[01] + 8-byte secret` | `KSY:397-398` |
| `AA B3` | plug spec byte | `KSY:404` |

### 9.2 The `0xAC` bulk status burst

Request: `AC <cmd> <N> <sub1> <sub2> ...` (length-prefixed list of TLV types wanted),
e.g. `AC 03 02 41 30` (power-on memory + zone) and `AC 03 03 41 30 A5` (+ colour groups)
(`SPEC:528-535`, `KSY:802-807`). Reply: a burst of `AC` frames, first
`[AC 00 totalLo totalHi lastLen cmd sub <12 data @7..18> CK]`, then `[AC tag <17 data>]`,
terminator tag `FF`; reassembled buffer is a TLV stream `[type, len, value]` with
`01` on/off, `04` brightness, `05` mode `[submode, params]`, `07` device info,
`11/12` sleep/wake, `23` timers, `30` zone, `41` seg/IC, `A5` colour group
(`SPEC:537-560`, `KSY:455-512`); stop on a `00` type byte (`SPEC:576-580`).
**Only** H60A6 and goodsTypes 277/288/298 (H6039/H6038/H6048) and h61d3 263 answer
it; H6047 and H6641 return nothing (`SPEC:562-574`, `YAML:444,513`).

### 9.3 `0xEE` unsolicited pushes

`[EE][subType][payload][CK]`, routed by `AbsNotify` (`SPEC:797-808`). Lighting SKUs
push three: `0x11` Wi-Fi connect result (`data[0] == 0` connected), `0x20` brightness
(`data[0]`), `0x30` switch / zone (H60A6: main = bit 0 of `value[1]`, zones at
`value[2..3]`, state in bit 1; e.g. `ee 30 01 01` after a zone change) (`SPEC:812-824`,
`KSY:171-185`, `YAML:430-433`). Others: `0x40` device status block, `0x01-0x07`
detail, `0xED` recognition, `0xEE` OTA (`SPEC:816-820`). H6199 pushes are logged but
"cannot install state" in HGL (`HGL:docs/h6199-pact1.md:12`). Mode writes are also
acked as `33 05 ...` echoes on the notify characteristic, which the repo's parser
ignores (`REPO:protocol.py:251-252`).

---

## 10. Pairing and encryption

### 10.1 The 8-byte secret (`secretCode`, "account lock")

Families tagged `encryption: secret_key_pairing` gate control on an 8-byte,
device-generated secret: read `AA B1 00` -> `[01] + 8 bytes` during pairing, write
`33 B2 <8 raw bytes>` after every connect to authorise the session; otherwise the
device ignores commands (`SPEC:1524-1535`, `YAML:114-145`, `SecretKeyControllerV1.parseValidBytes:44`).
Stored base64 in the account record (`bleExt.secretCode`, `device.govee.com`
`device/rest/devices/v1/settings`) and in the app's `SecretKeyConfig` map
(`SPEC:1537-1562`). Tagged families: h60ax, h6047, h61d3, stringlightv2,
dreamcolorlightv1, hollowlamp, h5080 plugs (`YAML:429,467,526,909,860,993`).
**Not** tagged (plain lights): h6159, h6160, h6181, h6182, h6185, h6102, h6104,
h612526, dreamcolorlightv2, barelightv1, bulblightv3 bulbs, h6188, h6061 (pairing
only), striplight H6062, tablelampv1, homelightv1, tvlight v1-v4, pact_h605b
(`YAML:600,612,756,875,940,499,633,659,557,...`). Whether a second client can talk
to a tagged device without the secret has not been tested by this repo; HGL drives
H617A (dreamcolorlightv1) without one, and GENZERO got "no data" from `AA B1` on it.

### 10.2 Wire cipher

Decided **per device at runtime**, never per SKU: advertisement flags bit 6 or
`2b12` byte 0 `encryptVersion` (0 none, 1 AES, 2 AES-GCM) (`YAML:102-113`, `SPEC:1518-1519`).
V1: `0xE7 01` request / `0xE7 02` confirm, frames AES-ECB on bytes 0-15 + RC4 on 16-19,
fixed PSK (`MakingLifeSmarte`, resource-decoded) for the handshake, then the 16-byte
session key from reply bytes 2-17 for everything (`SPEC:848-885`). V2: `0xE7 19/1A/11`,
AES-GCM, used by `ctlchannel` devices H70B3/B4/B5, H6810/6811, H6800, H6840
(`SPEC:887-907`). H61A8 and H6641 were observed plaintext despite being
secret-tagged (`YAML:110`). HGL carries a `govee_encryption` package
(`HGL:custom_components/ha_govee_led_ble/govee_encryption`). Nothing in this repo
handles either cipher; `govee_ble_local` raises on `encryptVersion == 2`
(`SPEC:1179-1182`).

---

## 11. Advertisements and Home Assistant

### 11.1 Local name -> SKU

`BaseBleProcessor` (`base2home/main/choose/BaseBleProcessor.java:98,145,181,233`,
`SPEC:1497-1507`, `YAML:38-49`):

| prefix | rule | example |
|--------|------|---------|
| `ihoment_`, `Govee_`, `Minger_` | `split('_')` -> `[brand, sku, tail]` | `ihoment_H6159_1A2B` (H6159-REV `ihoment_H6159_51324`) |
| `GBK_` | `split('_')` -> `[GBK, sku, tail]` | `GBK_H6181_FB1E` (BENCH) |
| `GVH`, `GVR` | `substring(2)` up to `_` or 5 chars | `GVH6159_XXXX`, `GVR6184_XXXX` |
| `GV` (fallback) | `"H" + substring(2,6)` | `GV5122ABCD` -> H5122 |

`REPO:models.py:16-18` matches all of these except the bare `GV` fallback (sensors).
HGL's manifest additionally matches `GVH_H617A*` with an underscore
(`HGL:custom_components/ha_govee_led_ble/manifest.json`), a form not in the app rule.

### 11.2 Manufacturer data

Within the AD structure of type `0xFF`, len >= 6 (`SPEC:1594-1608`, `ADV-KSY:38-72`,
`BleUtil.java:829 parseBleBroadcastPact`):

```
+0 flags      bit6 (0x40) = encrypted, low nibble = broadcast/protocol version (>= 1)
+1 +2 88 EC   company id 0xEC88 (little-endian on the wire)
+3 +4 pactType  u16 big-endian
+5    pactCode  u8
+6..  per-SKU state (on/off at data[3] via parseBleBroadOnOff, daySync at data[7], battery at data[7], multi-TH at data[6..8]) - no universal layout
```
Because the flags byte precedes `88 EC`, BlueZ / HA report the "company id" as
`0x8803`, `0x8843` etc. (flags `03`/`43` + `88`) and the payload as `EC 00 0A 01 01 ...`
(`GOVEE-BLE parser.py:378-386`; GENZERO H617A `0388ec000a0101` = flags 03, 88 EC, pact
type 0x000A, code 01). HGL parses exactly this to get pact type/code, broadcast
version and the encryption bit (`HGL:custom_components/ha_govee_led_ble/advertisement.py:17-31`),
and uses pact to select the H6199 0x0B vs 0x15 producer (`HGL:docs/h6199-pact1.md`).
Lights do advertise an on/off byte (`parseBleBroadOnOff -> data[3]`, `ADV-KSY:59`)
and a "passive state listener" exists in the app (`SPEC:1610-1611`), but the
per-SKU offsets are not enumerated in any source consulted: unverified beyond that
one pointer.

### 11.3 What Home Assistant already has

- **`govee_ble` (core) + `govee-ble` library (Bluetooth-Devices).** Sensor-only:
  `_process_mfr_data` dispatches on payload length and local name for H5051/52/71/72/
  74/75, H510x, H5121-30 (encrypted 24-byte buttons/motion), H5178/79, H5181-99 BBQ,
  H5106 (`GOVEE-BLE parser.py:257-860`). It recognises the flags-byte quirk
  (`0x8803/0x8843 are not real company ids`, `parser.py:378-386`) and explicitly
  guards against "unrelated lights and plugs" being published as presence sensors.
  No light model appears. The core manifest matches `local_name: Govee*` and
  `GVH5*` non-connectable plus specific sensor service UUIDs and manufacturer ids
  (`manufacturer_id: 34819` = 0x8803 with `manufacturer_data_start [236, 0, 0, 1]`)
  (https://raw.githubusercontent.com/home-assistant/core/dev/homeassistant/components/govee_ble/manifest.json,
  `requirements: govee-ble==1.4.0`). Reusable: the name-prefix handling
  (`Govee_`, `GV`) and the company-id quirk; the rest is sensor decoding.
- **`govee_light_local` (core).** LAN control of Wi-Fi lights ("Local API control",
  device must have LAN control enabled; https://www.home-assistant.io/integrations/govee_light_local/).
  It is UDP, not Bluetooth; irrelevant to this integration except that the same
  SKUs (H6043, H8840, ...) may be reachable both ways.
- **`ha-govee-led-ble` (teh-hippo, HACS).** BLE lights, H617A/C/E/F, H6076, H6125,
  H6179, H6199, H601C, H60A1/H60A6, H6099, H6102 with per-model Kaitai grammars and
  hardware ledgers; manifest matches `ihoment_/Govee_/GBK_/GVH_` per SKU
  (`HGL:manifest.json`). The most useful external reference for this repo.
- **`govee_ble_lights` (Beshelmek, HACS).** BLE + cloud; 0x02 for most, 0x15 for
  H6053/H6072/H6102/H6199, scene JSON uploads via `0xA3` (`GBL light.py:35-53,267-297`).
- **`govee-api-ble` (softgrass).** H6159-era 0x02/0x0B/0x04/0x01 frames and the
  legacy scene table (`API-BLE device.py`).

---

## 12. Per-family summary

Family ids are `devices.yaml` `families[].id`. Sub-mode columns are
`color / color_alt`, `scene`, `music (+alt)`, `diy`. UUID "default" = `1910/2b11/2b10`.
`conf` = yaml confidence.

| family | example SKUs | colour | scene | music | diy | special opcodes | encryption | UUID | conf | source |
|--------|--------------|--------|-------|-------|-----|-----------------|------------|------|------|--------|
| h6159 | H6159, H6110, H6109, H614A/B/E | 02 / 0D (bk) | 04 | 03 (+0E) | 0A | - | none | default | high | `YAML:593-602` |
| h6160 | H6160, H6163, H6117 | 02 / 0B | 04 | 03 (+01) | 0A | A2 colour read | none | default | medium | `YAML:604-614` |
| h6102 | H6102 | 02 | 04 | 01 | 0A | 0E limit | none | default | high | `YAML:668-678` |
| h6104 | H6104 | 02 (video 00) | 04 | 01 | 0A | direction 13, IP 15, multiDIY 02 | none | **ffe0/ffe1** | high | `YAML:680-697` |
| h6105 / h6113 / h6114 | H6105 / H6113 (no scene) / H6114 | 02 | 04 / - / 04 | 03 | 0A | multiDIY 02 | none | default | med/high | `YAML:699-731` |
| h6119 | H6119 | 02 / 15 | 04 | 0C (spec: 12) | 0A | A5 read, 14 gradual | none | default | medium | `YAML:733-744` |
| h612526 | H6125, H6126 | **0B** | 04 | 0C (spec: 12) | 0A | 0F light-num, A2 read, 14 gradual | none | default | high | `YAML:746-758` |
| h6127 / h6129 | H6107, H6116, H6127, H6161 / H6129 | 02 | 04 | 01 (+03) / 03 | 0A (+07 old) | - | none | default | medium | `YAML:760-778` |
| h613839 | H6138, H6139, H613A-F | 02 / 0D (bk) | 04 | 03 (+0E) | 0A | secretCode | - | default | medium | `YAML:780-788` |
| h6181 | H6181 | 02 (app) / **0D on fw 2.03.11 (BENCH)** | 04 | mic FF stub | 0A | - | none | default | medium | `YAML:790-798` |
| h6182 / h6185 | H6182 / H6185 | 02 | 04 | 03 / mic 05 | 0A | Wi-Fi notify 11 / MicController 05 | none | default | med/high | `YAML:800-820` |
| h6101 | H6101 | 02 (video 00) | - | 01 | - | calibration 08 | none | **ffe0/ffe1** | high | `YAML:822-838` |
| dreamcolorlightv1 | H6168, H617A, H619A, H61A0, H61A8, H61E0/E1, H611x, H612x, H70A1-3 | **0B / 15** (15 on pact 10) | 04 | 11 (+13) | 0A | 0E, 0F, 14/A3 gradual, 40/42/46 IC, A2/A5 colour read | secret_key_pairing | default | high | `YAML:841-862` |
| dreamcolorlightv2 | H6143, H6144, H6145-7, H6171, H6184, H611A, H6125/6 ... | **0B / 15** | 04 | 11 (+13) | 0A | 14/A3 gradual, A2 read | none | default | medium | `YAML:864-877` |
| stringlightv2 | H6109, H6110, H6141, H6142, H6159, H6160, H6170, H6178, H6192, H6195-7 | **0D / 02** | 04 | 0E (+13), mic 05 | 0A | 0E limit, group secret | secret_key_pairing | default (+group) | high | `YAML:900-911` |
| bulblightstringv1 | H7002, H7005 | 0B | **09** | 0E | - | 0F bulb-num, A2 read, A1 multi-scene cmd 01 | none | default | high | `YAML:913-927` |
| barelightv1 | H6145-7, H6171 (neon) | 0B / 15 | 04 | 0C | 0A | 14/A3 gradual, 0F | none | default; no notify path | medium | `YAML:929-942` |
| bulblightv3 | H6001-H601F, H8015, H14xx bulbs | **0D** / 02 (legacy H6001/H6006) | 04 | mic 05 | 0A | 26 colour-temp type | none | default | high | `YAML:475-504` |
| tablelampv1 / homelightv1 | H6052, H6078 / H6148 | 0D | 04 | 0F (+13) | 0A | 16 light indicator | none | default | high | `YAML:945-979` |
| hollowlamp | H6050, H6051, H6055, H6058, H6059, H6073 | 0D | 04 | 0E (+0F) | 0A | A5 local colour, A6, A7, 41 multi-music | secret_key_pairing | default (+group) | high | `YAML:981-995` |
| carlightv1 | H6118, H6194 | 0D | 04 | 0E (+13) | 0A | - | - | default (+group) | medium | `YAML:997-1005` |
| h6057 | H6057 night light | 0D (game 0B) | 04 | 13 | 0A | presets 24-29, 34, 31, 16, A5 | - | default | high | `YAML:1025-1045` |
| h6188 | H6188 | **0D** | 04 (fixed 9-code list) | 03 | 0A | brightness 20-254 | none | default | medium | `YAML:616-636` |
| h60ax | H60A0/A1/A4/**A6**, H60C1, H1270 ... | **15** / 0D (part colour, pact <(1,2)) | 04 | 13 | 0A | 30 zone power, AC bulk, A5, 41 memory, 36 child, 42 calibration, 58 DIY code | secret_key_pairing | default | high | `YAML:406-435` |
| h6047 | H6047, H6043, H6042, H6039, H6038, H6048 | 15 (video 00) | 04 | 13 | 0A | 36 bar switch, 34 swap, 30/31/32 camera, 44, A9, A3 gradual, A5 | secret_key_pairing | default | high | `YAML:437-473` |
| h61d3 | H61B0-H61F6 (incl. H61E5), H1A4x, H1AB1-3, H6640/41, H703A/B | 15 | 04 | 13 | 0A | 40 IC, 46/43 check, 24, 26 photo, ED, A5, B1/B2 | secret_key_pairing | default | high | `YAML:506-533` |
| striplight | H6062 | 15 | 04 (u16 LE) | 13 | 0A | **41 section count**, A5, masks up to 5 bytes | none | default | medium | `YAML:537-561` |
| h6061 | H6061 (23 panels) | 15 write / 0D read | 04 | - | - | A5, op 04 group colour | none (pairing-only B2) | default | medium | `YAML:638-662` |
| h604a | H604A-D | 15 (video 00) | 04 | 13 | 0A | 34, 35, 36, 40, 41, A7, AA | - | default | high | `YAML:1007-1023` |
| tvlightv1 | H6179 | 0D | 04 | 0E | 0A | 0E limit | none | default | medium | `YAML:1139-1148` |
| pact_tvlightv2 | H6198, **H6199** | **0B / 15** (video 00); pact 1 -> 0B, pact 2 -> 15 (HGL) | 04 | 0C (+13) | 0A | 32, 31, 30, 34, A3, A9, AE | none | default | high | `YAML:1150-1164` |
| pact_tvlightv3 | H6046, H6053, H6056 | **0D** / 15 | 04 | 0C (+13) | 0A | 34 swap, 33 single light | none | default | high | `YAML:1166-1177` |
| pact_tvlightv4 | H6049, H6054 | 0D / 15 (video 00) | 04 | 0C (+13) | 0A | 32, 31, 30, 34, 35 | none | default | high | `YAML:1179-1193` |
| pact_h605b | H605B/C/D, H6601-04, H6608, H8604 | 15 (video 00) | 04 | 13 | 0A | 32, 31, 30, 44, A9, 37, 23, 25 | none | default | high | `YAML:1195-1212` |
| h70b1 / h70b2 / h70bx | H70B1, H70B2, H70BC | 0D | 04 | 13 | 0A | splicing 40, 43 | - | default | medium | `YAML:1058-1091` |
| h705a | H705a-F, H3401, H61C2/3/5, H706x | shared newdetail (15 assumed) | | | | check IC | - | default | low | `YAML:1093-1101` |
| h7004 / h7017 / h7022 | grow lights / multi-bulb string | red/blue controller / plant mode / scenes cmd 01 | | | | 0F bulb-num | - | default | high/med | `YAML:1103-1136` |
| ctlchannel | H70B3/B4/B5, H6810/11, H6800, H6840 | unknown | | | | AES-GCM transport | V2 cipher | dynamic | - | `SPEC:1110-1114` |

Not in any family (unverified): H618A, H619B/C/E/Z, H61A1, H61A2, H61B5, H6065,
H6072, H6076 (HGL: H617A grammar, 0x15), H6135, H6137, H6172, H6167, H7020, H7021.

---

## 13. Implications for this repo

Concrete gaps against `protocol.py`, `probe.py`, `models.py` and `device.py` (line
numbers are the current working tree). Items 1-14 are from the colour-sub-mode
research; 15 onwards from the wider scope.

1. **0x15 table vs app family (`REPO:models.py:116-142`).** The app's primary
   sub-mode for H6168, H617A, H619A, H61A0, H61E1 (`dreamcolorlightv1`,
   `YAML:844-849`) and H6143, H6144 (`dreamcolorlightv2`, `YAML:866-870`) and
   H6199 (`pact_tvlightv2`, `YAML:1152-1155`) is **0x0B**, with 0x15 as the
   alternate taken on newer pacts. The table is probably right for current
   firmware (H617A pact 10 and H6199 pact 2 both run 0x15 in HGL), but an old-pact
   unit will need 0x0B. Decide from the advertised pact rather than hard-coding.
2. **Models tabled 0x15 whose app family is 0x0D:** H6052 (`tablelampv1`,
   `YAML:948-952`), H6059 (`hollowlamp`, `YAML:983-987`), H6056 (`pact_tvlightv3`,
   `YAML:1168-1171`, 0x15 alt). H6061 writes 0x15 but reads back 0x0D
   (`YAML:649-653`); H6062 is 0x15 but requires the `AA 41` segment count and
   masks wider than two bytes (`YAML:548-552`).
3. **Models tabled legacy 0x02 whose app family is not:** H6125 and H6126 are
   `h612526` **0x0B** (`YAML:746-751`); H6188 is `rgblight` **0x0D** with a
   20-254 brightness register (`YAML:616-632`); H6117/H6163/H6161/H6116 appear in
   both an 0x02 family and `dreamcolorlightv2` 0x0B (`YAML:606,762,866`);
   H6109/H6110/H6141/H6142/H6159/H6160/H6170/H6195 also sit in `stringlightv2`
   with **0x0D primary, 0x02 alt** (`YAML:902-907`) - the same move the H6181
   made on the bench. H6135, H6137, H6172, H7020, H7021 are in no family at all.
4. **0x0B read-back guess (`REPO:protocol.py:300-304`)** has no source: the app's
   parsers give `[gradual, kHi, kLo]` for a 0x0B reply (`YAML:554`) and the old
   stack reads only `bArr[0]`. Drop the RGB claim and get RGB from `AA A2`/`AA A5`.
5. **0x0B write layout** has two app-derived forms (spec: mask at bytes 4-5; HGL
   H6199 pact 1: kelvin at 4-5, mask at 6-7). The repo builds only the spec form.
   The probe should try both and the parser should tolerate either.
6. **0x15 read-back (`REPO:protocol.py:279-290`)**: byte 1 is the `isColorTem` /
   gradual flag, not an "op" that is always `01`; H6047 carries two kelvins; H6199
   returned zeros after a successful CCT write. `echoes_kelvin`
   (`REPO:probe.py:217-229`) is therefore not a safe sole discriminator. Add an
   `AA A5 01` page read after the RGB-form write and compare the records' RGB
   (the layout is `[group][bri R G B]...`, records of 4).
7. **0x0D read-back (`REPO:protocol.py:291-299`)** assumes the H6188/H6181 kind.
   bulblightv3 bulbs reply `[gradual, kHi, kLo]`, H6052 replies `[R, G, B]`,
   H6061 `[R, G, B, kHi, kLo]`. Parse kelvin at both candidate offsets and accept
   either echo. Also note some 0x15 writers (H60A6, H6061) answer `AA 05 0D`, so
   `SafeProbeResult.detected_submode` (`REPO:probe.py:147-151`) can return 0x0D
   for a light that wants 0x15 writes.
8. **0x0D CCT frame**: the repo sends the white-slot form (`FF FF FF`), which the
   H6181 and (per app source) H6188/H601C take; bulblightv3/H6052 use the
   both-slot form (tint in bytes 1-3 too, `YAML:497`). Harmless on H6181 but worth
   a per-profile switch if bulbs are added.
9. **Brightness-scale prior (`REPO:models.py:208-214`)** maps 0x0D/0x15 -> 100.
   H6188 (0x0D) and H6199 pact 1 (0x0B) use a 20-254 register (`YAML:631`,
   `HGL:docs/h6199-pact1.md:7-8`), bulblightv3 legacy scales 1-100 -> 20-254
   (`YAML:498`). 0x0B is not in the mapping at all.
10. **Sub-mode names (`REPO:probe.py:70-76`, `REPO:protocol.py:27-28`)** know only
    0x03 music and 0x04 scene; the modern RGBIC stack reports music as 0x13 (or
    0x11 on dreamcolor), DIY as 0x0A and video as 0x00. `outcome` will call all of
    those "unknown" and `_async_restore` (`REPO:probe.py:286-326`) will not
    recognise them.
11. **Scene reply (`REPO:protocol.py:305-306`)** reads one byte; the code is u16
    LE (`33 05 04 <lo> <hi>`, `SPEC:1176-1177`, `YAML:630`). `scene_frame` should
    accept 16-bit codes too.
12. **Whole-strip mask `FF 7F` (`REPO:protocol.py:50`)** is only right for 15
    segments. Read `AA 40` (or `AA 41` on H6062) when the light answers and build
    the mask from the segment count; otherwise keep a per-model mask as HGL does
    (`const.py:391,466,482`: 15 / 14 / 13).
13. **Pact from the advertisement** is not parsed anywhere in the repo
    (`REPO:models.py:14-18` only matches the name). Parsing manufacturer data
    `88 EC` `+5..+7` would let the integration pick 0x0B vs 0x15 for dreamcolor
    SKUs and 0x0D vs 0x15 for H60A6 without a write (`SPEC:1592-1600`); HGL's
    `advertisement.py:17-31` is a 15-line reference implementation. The flags byte
    also says whether the link will be AES-wrapped, which the repo cannot handle;
    surfacing "encrypted, unsupported" at config-flow time beats a silent timeout.
14. **Unverified list**: no byte-level capture was found for H6167/H6168 (web
    search 2026-10-19), H619B/C/E/Z, H618A, H61A1/A2, H61B5, H6065, H6072, H7020,
    H7021. Everything the repo says about them is table inference.
15. **Cheap additions, shared opcodes, no family quirks:** hardware version
    `AA 07 03` for diagnostics (`SPEC:627-633`); `0x0B` countdown-off
    `33 0B <open> <h> <m>` (`YAML:306`); sleep/wake timers `0x11`/`0x12`
    (`YAML:307-308`); `AA 40` IC/segment count to size masks and to recognise
    RGBIC firmware (`KSY:361-369`); `AA A5 <group>` to populate RGB after any
    0x15/0x0B write instead of trusting optimistic state (`SPEC:686-695`);
    `33 A3 <0|1>` gradual fade (`YAML:399`). Each is one frame and one parser.
16. **Secret-key families.** H6168, H617A, H619A, H61A0, H61E1 (dreamcolorlightv1),
    H61B5/H61E5 (h61d3), H6059 (hollowlamp) and H7020/H7021 if they turn out to be
    stringlightv2 are tagged `secret_key_pairing`. If a light accepts the
    connection but ignores every write, the `33 B2 <8 bytes>` authorisation is the
    first suspect (`SPEC:1524-1535`); the secret can only be read with `AA B1`
    during the device's pairing window or fetched from the Govee account. Worth a
    config-flow note and a diagnostics check (`AA B1` reply present or not).
17. **Keep-alive and the H6181 wedges.** The app heartbeat is `AA 01 00`
    (`SPEC:721-724`) with write-with-response and a 6 s write timeout / 6 retries
    (`SPEC:1627-1629`); the bench rig uses 2 s. No source documents an idle
    timeout value on the device side. The H6181 "stops answering every 4-6
    connections" (BENCH memory) has no counterpart in the sources; HGL's
    `connection_idle_timeout=3.0` on H617A suggests dropping the link when idle
    rather than holding it, which is one experiment to run.
18. **Mode write ACKs.** The device echoes `33 05 ...` with byte 2 = 0 on the
    notify characteristic (`SPEC:214-215`); `parse_reply` discards non-`AA` frames
    (`REPO:protocol.py:251-252`). Logging these in diagnostics would show whether
    a probe write was accepted at all, independent of the mode read.
19. **Discovery.** HA core's `govee_ble` already claims `local_name: Govee*`
    non-connectable for sensors; a light advertising `Govee_H6159_...` will be
    offered to both integrations. The repo's manifest matchers should stay
    SKU-specific (as `tests/test_models.py` enforces) and could add
    `manufacturer_id` 0x8803/0x8843 with `manufacturer_data_start [0xEC]` as a
    second signal, mirroring `GOVEE-BLE parser.py:378-386`.
20. **Out of scope and why.** `govee_light_local` is LAN UDP; `govee-ble` is
    sensors; the `ctlchannel` AES-GCM devices (H70B3/B4/B5, H6810/11, H6800, H6840)
    and all `encryptVersion != 0` units cannot be driven by this repo's plaintext
    frames (`SPEC:1110-1114`, `SPEC:1179-1182`).

---

## 14. Companion documents

Added 2026-10-19 with the census work:

- `census-gap-analysis.md`: what the two repositories and the external
  sources establish, the defects found in the Dance Party census, and how
  protocol knowledge is shared (fixture -> generated headers -> host tests).
- `encrypted-protocol-matrix.md`: the four session mechanisms (plaintext,
  pairing secret, AES-ECB+RC4, AES-GCM) with per-model provenance, and the
  hardware plan. `custom_components/govee_bluetooth/cipher.py` implements
  both ciphers against hardware-derived vectors.
- `evidence-schema.md` and `schema/govee-evidence-v1.schema.json`: the
  record format every producer writes and `tools/govee_report.py` reads.
- `fixtures/govee-models.json`: the model classification with provenance;
  `tools/export_fixtures.py` generates the C tables Dance Party vendors.

Items 13 and 16 of section 13 are now implemented (`advertisement.py`,
`census.needs_secret`, the census and the rig's `2b12` read); item 4 was
already applied in `protocol.py`; the rest stand.

### 14.1 H6006 bench findings (2026-10-19/20)

From the Pi rig's census and stepped tests (`tools/govee_rig.py census --probe
--ask`, `scale`, `verify`; evidence in a bench log, not committed):

- `2b11` is **read + write-without-response only**; a write request gets
  ATT "Write Not Permitted" and the bulb drops the link. egold555 reports the
  same on the H6004. `protocol.control_write_response()` picks the mode from
  the characteristic's properties. No `2b12` characteristic.
- The firmware acks every write on `2b10`: `33 05 00` for a `0x0D` colour,
  `33 05 01` for `0x02`, `0x0B` and `0x15`. The ack is an application-level
  accept / reject, a stronger signal than an echo; the census records it.
- The `0x0D` reply is the rgb_kelvin_tint kind, RGB zero in white mode.
- Kelvin: 4300 K echoed; 2000 K and 9000 K come back as 2700 K and 6500 K
  (app: bulbs 2700-6500 K, `SPEC:1255-1256`); warm and cool confirmed by eye
  with `govee_rig.py whites`. `ModelProfile.min_kelvin` / `max_kelvin` carry it.
- Brightness: `33 04 <n>` is acked and stored for any byte, but above 100
  renders *dimmer* than 100 (255 dimmer; 200 hard to tell from 100, which
  fits a modulo-101 wrap). Scale 0-100; never send more.
- Legacy scene IDs: stored and reported back, none visibly ran.
