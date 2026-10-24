# BLE readback implementation, 2026-10-24

This first implementation follows the four preserved [research reports](ble-readback-and-control-research-2026-10-24.md)
against baseline `e69b84693dbaddfbf4abd3b38d27238fc1b887d7`. The reports remain
unchanged historical references. Implementation work and verification were offline:
no BLE scan/connection, rig access, device network request, account access or new
hardware qualification was performed.

## Runtime selection

`readback.py` selects a read contract independently of `ModelProfile`'s existing
writer and brightness scale. Missing identity selects conservative mode-only or
opaque decoding. A submode does not enable pages, bulk reads, topology or pushes.
Manual writer/brightness overrides remain authoritative. Runtime writer migration
is restricted to the historical H6159/H6181/H6006 path; the setup probe and stored
writer choices retain their earlier behavior and are not new qualifications.

| Identity gate | Additional behavior | Evidence and limits |
| --- | --- | --- |
| H6159 | Hardware version `AA 07 03`; legacy RGB/white mode observations | Existing saved census and colour captures. No IC/bulb/page reads enabled. |
| H6181 | Hardware version; RGBWW mode observations | Existing saved captures, notably firmware 2.03.11. No IC/bulb/page reads enabled. |
| H6006 | Hardware version; RGBWW bulb colour slot used as CCT tint | Existing bench and source evidence. Reader does not require trailing tint. |
| H617A, advertised pact 10/1, firmware 3.02.24 | One signed IC/segment pair; static-colour A5 pages, five groups of three brightness/RGB records | Source contract plus the previously referenced GENZERO example and H617A report. No new local hardware qualification; a different/unknown pact or firmware disables this collector. Counts do not size masks. |
| H6062 | Signed IC/section pair through `AA 41`, once per identity | Source-derived family section-count contract. This opcode is **not** queried on other families for section count. No inferred mask mapping. |
| H60A6 | Bounded `AC 03 02 41 30` burst; partial power/brightness/mode TLVs; EE20 brightness and EE30 main power bit 0 of flags A | Source-derived model dispatch; no narrower firmware/pact gate established by these reports. Unknown TLVs, zone bytes and tails stay raw. No bulk colour/scene-program export. No new discovery or writer support is granted to this SKU. |
| Existing RGBIC model table | Existing nonzero Kelvin mode-only observation | Compatibility path, not qualification of pages, topology or all firmware revisions. Zero Kelvin leaves retained colour/white state untouched. |
| Other models | Basic power/brightness and opaque mode body/selector | No speculative colour geometry or notification masks. |

H6047/H6641 colour pages remain disabled because source prose and executable
code disagree on record width. The generic A2 RGB and A5 brightness/RGB decoder
supports explicit record counts and final-page remainders; A2 has no runtime
model allowlist yet. H6199's 4+4+4+3 schema is retained as an offline contract,
not an enabled complete reader. The speculative H6125 A2 format is not enabled.
Family-specific two-pair IC and bulb-count parsers exist as offline contracts;
no additional runtime allowlist was inferred for them.

## State and completeness

The light's `field_provenance` attribute and diagnostics snapshot distinguish
`observed`, `derived`, `optimistic`, and `unknown`, with a UTC timestamp per field.
Retained fields keep their original source and timestamp when another field
changes. No arbitrary age cutoff rewrites an old observation as a new one.
Missing values start unknown. Desired command state is separately retained in
`desired_state` for the integration lifetime. Successful writes update presentation
optimistically; ATT success is not observation. Application ACK acceptance/rejection
is tracked separately and is not a physical-output measurement or correlation to
a particular command. No exact restoration of resident scene/music/DIY programs
is claimed from a selector.

Legacy white tint is observed RGB with an estimated, **derived** Kelvin value.
RGB calculated from a segmented Kelvin report is derived. The historical H6181
RGBWW parser's missing-tail fallback is marked derived; parser
helpers by themselves do not qualify other bulb layouts. Multi-colour page sets
remain segment data and clear the whole-light RGB scalar; uniform complete sets
may supply that scalar. Zero brightness and all-black records remain valid.

Every selected page is required, with a maximum of 16 groups and at most four
records of four bytes per frame. The enabled H617A walk is five groups, stops on
the first missing/invalid reply, and only runs in a static colour mode without
reported Kelvin. The ordinary 2.5-second query timeout bounds each group.
Incomplete sets are preserved as partial diagnostics and never supply aggregate
RGB. No partial colour observation creates new segment controls.

AC assembly is scoped to one pending query and connection, with a 2.5-second total
timeout, a maximum of 32 packets (539 bytes), strict ordering/checksums and both
FF and positional final tags. Final-packet data is retained. Unsolicited AC chunks
are not merged. `bulk_complete` describes assembly, not completeness of every
light setting; `bulk_tlv_complete` describes the TLV walk. Empty mode TLVs do not
select mode zero. Unknown TLVs and truncated tails are retained. Qualified EE and
unsolicited AA power/brightness/mode observations use the same partial merge;
unqualified pushes never match pending AA queries or change a writer.

## Collection and Home Assistant diagnostics

Hardware/topology reads are bounded optional requests cached for the integration
lifetime, including unanswered/invalid outcomes. Model, pact, firmware or read
profile changes invalidate the cache and old page/bulk snapshots. Reconnecting
alone does not retry identity/topology silence. Firmware itself is attempted once
per connection, including an unanswered attempt. Reloading the integration resets
these in-memory caches. A failed optional page/bulk read is also cached until
identity changes or reload, avoiding repeated waits on every poll; successful
state collectors run on subsequent normal refreshes. Optional silence never means
zero, unsupported, unpaired or unavailable. Basic query/transport failures still
use the existing availability/error path.

Device registry metadata includes observed hardware and firmware versions.
Disabled-by-default diagnostic sensors expose the selected profile, last state
observation, last application acceptance, page completeness, host optional-read
failure/rejection counters and reported IC/segment/section/bulb counts where
available. They all use the coordinator snapshot and make no BLE requests.
Counts retain pair semantics; multi-pair counts are not automatically summed.
Host counters are software observations, not electrical/thermal measurements.
RSSI/source and additional host connection counters are not added in this pass.

Automatic collection has no IC detection/check, calibration, current-limit,
factory-reset, OTA or secret-acquisition commands and no opcode enumeration.
`33 46` is never an automatic diagnostic. No count changes the existing write
mask. BGC format 1/2 decoding now reads encryption version at byte 1, after the
format discriminator; unknown or short formats remain unknown. This does not wire
runtime session authentication or authorize secret retrieval.

The separate opt-in census now needs an explicit `PageLayout` to use page colours
as write-echo evidence. Unqualified pages remain raw. Its other historical active
probe and secret-reading behavior is outside normal automatic collection.

## Offline verification

`tests/test_readback.py` covers synthetic A2/A5 contracts, zero records, bounds,
signed topology, opcode collisions, source-specific EE masks, TLV tails and AC
ordering/final data/timeouts/disconnects. `tests/test_device_readback.py` exercises
normal refresh, full and partial page sets, silence caching, identity invalidation,
section topology, AC/EE state merging and provenance through an in-memory
transport. Home Assistant tests cover registry hardware metadata, diagnostic
entity registration, shared cached values and retained manual overrides. Existing
capture replay tests continue to cover saved H6159/H6181 observations. Synthetic
contracts establish software behavior only, never new device qualification.
