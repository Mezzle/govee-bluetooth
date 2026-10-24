# Govee firmware replacement: Tasmota and WLED — 2026-10-24

There is credible evidence of **running WLED with an H6159 strip by replacing its controller**, including a firsthand modification retaining part of the original power electronics. This research found **no verified stock-controller Tasmota/WLED flash procedure for our H6159 or H6181 revisions**. That is a search result, not proof that custom firmware is impossible. The Reddit comment the user originally saw was not supplied, so the examples below cannot be identified as that particular comment.

This is documentation-only research. No controller was opened, programmed, placed in OTA mode or otherwise operated. See the [prior OTA research](firmware-update-research-2026-10-23.md) for measured services and unresolved image/recovery requirements.

## What the Reddit claims actually demonstrate

- **Directly relevant H6159 experiment, October 2022:** the author of [“Govee WLED mod”](https://www.reddit.com/r/Govee/comments/xsox43/govee_wled_mod/) reports a TLSR8269 and RTL8710BN on their original PCB. They cut the board, retaining its buck regulator and RGB MOSFETs, and connected a Wemos D1 running WLED. They report working lights, enclosure fit problems and audible noise. This is firsthand evidence for a controller replacement using the original output stage, **not** WLED executing on either original chip. Hardware revision was not reported and the chip markings were not independently verified against our unit. Their [photo album](https://imgur.com/a/EkViMnB) is linked as supporting material; the textual account was inspected here, not the album's chip images.
- **Tasmota question, July 2020:** a [follow-up in November 2021](https://www.reddit.com/r/homeassistant/comments/hjs7i7/) says the light would not Tasmotize and the participant bought an Athom controller. Model/revision and detailed failure evidence are absent. This documents a replacement outcome, not universal incompatibility.
- **WLED discussion, December 2024:** [“Anyone flashed wled in Govee controllers?”](https://www.reddit.com/r/WLED/comments/1hbycw0/) begins with an assumption that the controllers are ESPs. Replies instead describe removing the controller and adding an ESP, including a firsthand permanent-outdoor-light conversion. Those outdoor lights are different products; their wiring and protocol must not be imported into our bench strips.

## Applicability to our two controllers

Our [bench inventory](bench-evidence/2026-10-23-firmware/report.md) records H6159 firmware `1.07.02`, hardware `1.00.02`, and H6181 firmware `2.03.11`, hardware `2.01.01`. Neither exact MCU nor flash arrangement has been physically identified.

**H6159:** the measured Telink-style OTA UUID is consistent with a Telink BLE component, and an independent [H6159 BLE-control project](https://github.com/jurassic-marc/govee-h6159-light-strip-reverse-engineer) reports Bluetooth manufacturer Telink. Neither establishes an exact chip or identifies the Wi-Fi processor. The 2022 board above is a useful hypothesis for inspection, not a confirmed BOM for ours. An additional revision trap: a manufacturer [2023 FCC similarity declaration](https://fcc.report/FCC-ID/2A7VD-H615E/6875840.pdf) includes an H6159 variant specified at 24 V, whereas the firsthand 2022 modification describes 12 V. Determine our supply and board revision from actual labels rather than the SKU alone.

**H6181:** its measured TI OAD-style service is a transport hint, not a silicon identification. It does not justify claiming CC2541, CC2640 or any exact TI part. Govee's [specification table](https://community.govee.com/support/faqs/specs) identifies this model as Bluetooth, 5 V USB and RGB; its [manufacturer manual](https://m.media-amazon.com/images/I/B17SccgQYGS.pdf) calls it an RGB TV backlight. No firsthand stock-MCU WLED/Tasmota conversion for this exact revision was located.

## Flashing an existing project versus porting one

[Tasmota's own description](https://tasmota.github.io/docs/About/) targets Espressif devices. WLED's [build configuration](https://github.com/wled/WLED/blob/main/platformio.ini) likewise supplies Espressif build targets. An OTA transport does not translate an ESP binary into a Telink, Realtek or TI application. Tasmota/WLED on a confirmed supported ESP would still require GPIO mapping, compatible power/output circuitry and a working installation path.

The [WLED controller documentation](https://kno.wled.ge/basics/compatible-controllers/) contains an explicitly summer-2023 platform-status paragraph alongside newer entries; use current build configuration rather than treating that paragraph as an exhaustive 2026 support matrix. No supported TLSR8269, RTL8710BN or unidentified TI target was established in this investigation.

**Engineering inference:** porting the full WLED experience to an unsupported original MCU would require a board support layer, GPIO/PWM drivers, timers, storage, boot/update support and sufficient RAM/flash; its network/UI features additionally need a supported network stack and appropriate radio hardware. If H6181's actual MCU has only BLE, a software port cannot give it Wi-Fi. A small native BLE-to-RGB application could be a much narrower experiment: initialize the verified MCU, expose a few BLE commands and drive the three identified channels. This still needs a toolchain, correct memory layout, peripheral mapping and demonstrated recovery. It is a proposed project, not an available Govee firmware package.

WLED explicitly supports [analog RGB strips](https://kno.wled.ge/basics/compatible-led-strips/#non-addressable-led-strips) using a GPIO and MOSFET per color. This corroborates the architecture of the H6159 modification. For a truly analog strip, all attached LEDs share the same channel values: WLED cannot create individually addressable pixels by changing the controller. Confirm our wiring before classifying either physical strip. For these models, an ESP32 controller with suitable RGB PWM outputs is a more relevant candidate than a data-only addressable-pixel controller. Compatibility depends on voltage, polarity, current, connectors and topology, all still to verify.

## Obtaining stock firmware and restoring it

A downloadable OTA image and a restorable full backup are different artifacts. The prior research found no verified public image feed or recovery procedure for these exact units. Include Security's firsthand [Govee H7135 heater investigation](https://blog.includesecurity.com/2025/02/replacing-a-space-heater-firmware-over-wifi/) demonstrates obtaining and modifying a Realtek-based device's update, but does not establish matching chips, unsigned updates or recovery on either light.

For a confirmed ESP device with a usable programming interface, Espressif documents [reading flash to a file](https://docs.espressif.com/projects/esptool/en/latest/esp32/esptool/basic-commands.html#read-flash-contents-read-flash), and Tasmota's [installation guide](https://tasmota.github.io/docs/Getting-Started/) recommends preserving manufacturer firmware. **Those ESP tools do not establish readback support on our unconfirmed chips.** On our units we still need to determine debug/boot-ROM access, read protection, storage layout and whether there are multiple independently programmed MCUs. Preserve bootloaders, device identity/configuration and calibration as applicable; an application OTA file may omit them. Restoration also needs the correct offsets, programmer and unchanged security state, not merely possession of a `.bin`.

## Practical next investigation

1. Preserve the exact device/supply labels and identify the PCB revision, chips and programming pads from an authorized inspection of a spare controller. Correlate them with our measured hardware versions.
2. Resolve backup/readback and recovery before writing the original electronics. Use the existing app-image/static-analysis work to establish formats and acceptance rules; OTA service presence remains insufficient.
3. If the goal is dependable local lighting, investigate a reversible controller swap with a correctly matched analog RGB driver and preserve the intact original controller. With compatible detachable wiring, returning to stock becomes reconnection rather than firmware restoration.
4. If the goal is proving custom execution on the original hardware, pursue the minimal MCU-native application first. Revisit a full WLED port only after MCU resources, toolchain, GPIO control and recovery are known.

## Follow-up: possible spare controller

The [spare-controller investigation plan](spare-controller-investigation-plan-2026-10-24.md) adds a staged identification and stock-backup workflow. A further concrete software route is [ESPHome through LibreTiny](https://esphome.io/components/libretiny/), which officially supports RTL8710BN/BX. If our controller contains that Realtek chip, this could avoid porting WLED. A Govee board definition and LED/processor wiring still need to be established: replacing Wi-Fi-side firmware alone may not drive RGB if the Telink processor handles it.
