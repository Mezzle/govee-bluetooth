# Spare Govee controller investigation plan — 2026-10-24

A duplicate controller from a damaged strip would be a useful candidate: we could identify the hardware and investigate stock-firmware backup without disturbing the working installation. First establish which model and hardware revision the spare actually is, and whether its controller still operates. Strip damage does not establish that the controller is undamaged.

This is a proposed workflow and source review, with no physical or device operations performed. It follows the [firmware research](firmware-update-research-2026-10-23.md) and [Tasmota/WLED feasibility notes](tasmota-wled-feasibility-2026-10-24.md).

## Spare identification update

The user confirmed the spare is the same model as the under-bed lights. Existing bed-light bench notes associate those lights with H6159, so H6159 is the working identification for this plan. Confirm the spare's label and PCB revision before selecting tools; the working unit's hardware version does not establish the spare's revision.

## Existing guides: useful pieces, no complete verified recipe

**Model-specific firsthand H6159 hardware work:** [Govee WLED mod](https://www.reddit.com/r/Govee/comments/xsox43/govee_wled_mod/) and its [photo album](https://imgur.com/a/EkViMnB) document an original board reportedly containing TLSR8269 and RTL8710BN. The author cuts the PCB to reuse its power stage with a Wemos running WLED. This is helpful visual context, but destructive controller substitution, not stock-chip readback or a recovery guide. Their board revision is unspecified; the photo album's individual chip images were not independently inspected in this research.

**Manufacturer-submitted internal photographs:** the [H615E-family FCC internal-photos filing](https://fccid.io/2A7VDH615E/Internal-Photos/Internal-Photos-2-6875849) is a source of board comparison photographs. The manufacturer's [model-similarity declaration](https://fcc.report/FCC-ID/2A7VD-H615E/6875840.pdf) includes an H6159 variant. It does not establish that these photographs match our earlier controller; compare the spare's actual FCC ID and PCB markings. Do not derive a programming pinout from a family name.

**iFixit:** targeted searches for Govee, H6159 and Govee device/repair pages found no relevant model-specific teardown or flashing guide. This is not a claim that iFixit has no Govee content anywhere. No iFixit recovery recipe was established.

**Telink primary documentation:** the official [BDT user guide](https://wiki.telink-semi.cn/doc/an/AN-18101200-E_Telink%20Burning%20and%20Debugging%20Tool_BDT_User%20Guide.pdf) explicitly lists 8267/8269 support and memory-access functions. The newer [BDT documentation](https://doc.telink-semi.cn/doc/en/software/res/tools/bdt_wins/bdt_wins_en/) documents reading flash as well as separate write/erase/unlock functions. These are generic development-board instructions; select a programmer and tool version explicitly compatible with the confirmed chip. Current tools cover multiple generations and not every command applies to every chip.

**Telink community source:** [pvvx/TlsrComSwireWriter](https://github.com/pvvx/TlsrComSwireWriter), including [TLSR826xComFlasher.py](https://github.com/pvvx/TlsrComSwireWriter/blob/master/TLSR826xComFlasher.py), implements flash reads over SWS/Swire. The source also implements flash unlock, writing and erasing. It is an inspectable candidate, not a demonstrated Govee backup procedure. Read operations may involve halting/resetting the CPU and writing volatile controller registers; “read flash” is not electrically passive observation.

**Realtek RTL8710B project documentation:** [LibreTiny's RTL8710BN/BX guide](https://docs.libretiny.eu/docs/platform/realtek-ambz/) identifies the relevant UART interface and links its supported workflow. [ltchiptool](https://docs.libretiny.eu/docs/flashing/tools/ltchiptool/) explicitly documents full-flash dumping and stock restoration and supports RTL8710B. This makes backup a concrete possibility if the spare contains that chip and access works. It has not been demonstrated on our Govee PCB. Do not copy similarly named older RTL8710 OpenOCD recipes: family/suffix, memory map and programming mechanism need to match. LibreTiny itself marks older ImageTool/rtltool guides as discouraged alternatives.

**TI conditional resources:** if inspection identifies CC2540/CC2541, TI's [development-kit guide, §5.3](https://www.ti.com/lit/ug/swru270c/swru270c.pdf) documents CC Debugger/SmartRF Flash Programmer and reading flash into a hex file. Its [CC253x/CC2540/41 user guide, §3.4](https://www.ti.com/lit/ug/swru191f/swru191f.pdf) states that clearing debug lock requires CHIP_ERASE. That destroys the stock flash we want to preserve. This applies only to the named family: our H6181's OAD UUID does not identify it as CC2541. If it is a CC26xx or another part, obtain that exact part's guide and compatible programmer instead.

## Phase 1 — inventory and opening, all power disconnected

1. Photograph the controller front/back, supply label, connectors, strip markings and any FCC/regulatory ID. Record SKU, purchase age and app firmware/hardware versions if already available. Keep the working controller as the reference; do not open it initially.
2. Disconnect the spare from supply, USB and strip. Photograph enclosure seams and screws before opening. Determine whether clips, screws, glue or potting are present; pause if access requires cutting a PCB or damaging unreadable components.
3. Photograph both PCB faces in focus with orientation and scale. Transcribe every MCU/module/flash/regulator marking, PCB revision and test-pad label. Record component orientation before moving anything.
4. With the board unpowered, use inspection and continuity measurements to trace likely ground, supply paths and pad-to-chip connections. Establish pin numbers from the exact package datasheet. A pad marked TX or an apparent ground plane is a clue to verify, not a ready-to-connect instruction.

**Gate:** no programmer purchase or pin connection until chip identity, package, board revision and candidate interface are documented. H6159 may contain two independently programmed processors. H6181 remains unidentified. Do not infer logic voltage from the strip supply or supply the MCU rail from a generic adapter.

## Phase 2 — electrical baseline and readback design

1. Confirm the intended input supply from the spare's label and circuit, then establish the MCU rail and interface voltage using appropriate instruments. Start with the strip disconnected; ensure its damaged end cannot short the controller if later attached.
2. Choose one power source and a documented grounding/reference arrangement. Prevent the programmer, USB and original input from feeding each other; an adapter's voltage-selector switch alone does not establish safe target power or signal levels.
3. If useful, observe an identified serial output with a suitable high-impedance receiver first. Passive boot logs can help confirm silicon and flash layout; they are optional, not proof of recovery.
4. Select the exact vendor/project read path and review its source/defaults. Record reset/boot-mode requirements, flash geometry and protection-state queries. Disable automatic program, erase, unlock and security changes. Entering download mode or halting a CPU may change operation even without persistent flash writes.

**Gate:** stop if identification, rail voltage or pad mapping remains uncertain, supply current is abnormal, the board heats unexpectedly, or a tool proposes erase/unlock/recover as a connection remedy. For confirmed CC254x, debug unlock explicitly entails erase. Telink's documented “flash unlock” concerns write protection; do not conflate it with TI debug unlock or assume it is needed for reading. No exact RTL8710BN/Govee read-protection removal behavior was verified here; modern Realtek security documentation for different SoCs does not answer that question. Protection failure is evidence to record, not an instruction to clear it.

## Phase 3 — preserve stock contents

1. Read each accessible flash/address space using the confirmed capacity and mapping. For a two-MCU H6159, inventory and back up both processors; a Realtek dump alone is not a full-controller backup. Preserve accessible factory/configuration/calibration information and document anything stored in unreadable OTP/eFuses or external memory.
2. Make two dumps and calculate SHA-256 hashes. Identical results establish repeatability, not guaranteed completeness; inspect size, nontrivial contents and plausible layout. If hashes differ, compare regions and determine whether normal firmware updates state between reads. Do not silently pick one file.
3. Store original dumps unchanged, with tool version, command/settings, flash IDs, electrical notes, chip/PCB photographs and the spare's model/revision. Keep potentially embedded Wi-Fi credentials, account data and unique device identifiers private; do not commit raw dumps to the public repository.
4. Confirm the controller still boots and behaves as before the read session. That validates survival of readback, not restoration.

**Gate:** a file is not yet a proven recovery path. Document programming access after a bad application, offsets, security dependencies and every persistent area not captured. Restore testing writes flash and belongs to a separately scoped experiment after backup validation; demonstrate on the spare before considering the working installation.

## Phase 4 — choose the project after identification

- **Replacement controller:** preserve the original intact and determine strip voltage, current, channel wiring and polarity. An analog RGB strip needs an appropriate PWM power stage. A detachable adapter could make reversal a reconnection operation; cutting the existing PCB would remove that advantage.
- **Original hardware:** first map which processor drives RGB and how the processors communicate. LibreTiny [lists RTL8710BN support](https://docs.libretiny.eu/docs/status/supported/), and [ESPHome officially supports RTL8710BN/BX through LibreTiny](https://esphome.io/components/libretiny/), making ESPHome a promising alternative to a WLED port if that chip is confirmed. This does not establish a ready-made Govee board definition, or that replacing Wi-Fi firmware alone controls the LEDs: RGB may be handled by the Telink processor over an undocumented link.
- **Minimal firmware:** once recovery is demonstrated, build a small native application with a unique observable marker and LED outputs initially inactive. Establish basic execution and channel control before adding BLE/Wi-Fi services or attempting a full WLED port.

The immediate useful input is the spare controller's model/label photograph, followed by clear PCB photographs if it can be opened without destructive work. That determines which of these generic guides can become a board-specific procedure. Nothing in this plan assumes that damaged strip LEDs imply a flashable controller.
