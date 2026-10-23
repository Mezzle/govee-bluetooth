# Cheapest hardware for pairing-secret experiments

Checked 2026-10-23 for the US market. Prices and stock are snapshots; marketplace
listings can disappear, and tax or shipping may change the delivered price.

## Recommendation

Check the delivered total on the exact **H5083 four-pack listed at $19.50** first
if the goal is an inexpensive, repeatable bench for the Govee `AA B1` / `33 B2`
pairing-secret lifecycle. That is
about **$4.88 per specimen before shipping**, gives four units for reset/pair/update
comparisons, and avoids the H7135 heater recall. The listing identifies the MPN as
H5083, is new, is sold by a US seller, and shows shipping only after the delivery
location is supplied; it does not accept returns. [Current eBay listing](https://www.ebay.com/itm/298388581645)
If checkout shipping takes it above $24.99, eBay search currently also shows an
exact-H5083 four-pack around $23.99-$24.99 with free US delivery and an exact two-pack
at $19.99 delivered. Those search offers are especially volatile. The more stable
H5083 product page currently shows a new four-pack at $33.55 with free delivery.
[H5083 product page](https://www.ebay.com/p/15062363950)

This purchase does **not** guarantee the H7135's 256-candidate weakness. The public
H5083 firmware specification proves that this plug enforces the same eight-byte
authorization exchange, but its pre-secret fallback is described as a keyed hash
of the BLE MAC and its stored secret is modelled as eight random bytes. The actual
H5083 image needed to resolve those implementations is not public. [Pinned H5083
firmware specification](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/firmware.yaml)

If the sole objective is to reproduce the **known H7135 UUID + one-byte derivation**,
the exact H7135 is the only evidence-backed target. A used one is currently listed
for $15 plus calculated shipping, but it is a recalled heater. It should not be
recommended as a powered appliance or used to produce heat. See the H7135 section
for the narrow case in which acquiring one as an electronics specimen may still be
useful.

## Price and evidence comparison

| Model and current offer | Condition and delivered-price caveat | What it can establish | Verdict |
| --- | --- | --- | --- |
| **H5083**, four for **$19.50** ([eBay](https://www.ebay.com/itm/298388581645)) | New; shipping calculated at checkout; US seller; no returns | Hardware test of encrypted session setup, physical-button `AA B1`, persistent eight-byte secret, wrong/correct `33 B2`, and the undocumented H5083 generation/fallback behavior | **Best overall purchase**; $4.88 each before shipping |
| **H5083**, four for **$20.99** ([eBay](https://www.ebay.com/itm/236839917206)) | New; shipping calculated at checkout; US seller | Same as above; useful fallback if the $19.50 lot disappears | **Second H5083 offer** |
| **H5080**, apparent two-pack for **$19.98 delivered** ([eBay](https://www.ebay.com/itm/800774622255)) | Open box and seller-tested; free USPS shipping and returns; title says H5080, item specifics say two outlets | Independent hardware comparison for physical-button key disclosure and mandatory `33 B2`; no public evidence that it uses the H7135 formula | **Best single-order H5080 comparison**, about $9.99 each if the photographed two-pack is supplied |
| **H5080**, four for **$24.99 delivered** ([eBay](https://www.ebay.com/itm/326615276871)) | New; free shipping; 30-day returns | Same H5080 comparison with four reset/pairing samples | Good if H5083 stock vanishes |
| **H7135**, one for **$15 + calculated shipping** ([Pawn1](https://shop.pawn1.com/Item/Details/The-GoveeLife-Smart-Space-Heater-Lite-H7135/a59b95a3321844de93ea1bdef21b0467)) | Used/very good; active Buy It Now; 14-day return; ships from Boise; checkout determines shipping | Direct live validation of the known 256-candidate UUID derivation on the firmware family analyzed | **Research specimen only; do not recommend as a powered heater** |

The $19.50 H5083 lot is cheaper per device than the $15 H7135 even before the
heater's likely higher shipping cost. A single generic “Govee smart plug” listing
can be cheaper, but it is not useful evidence unless the seller's underside-label
photo shows `H5083` or `H5080`. Govee has several visually similar plug revisions.

The old plug models no longer have a discoverable product page in Govee's current
US store, while current retail stock is marketplace stock. Treat H5080/H5083 as
discontinued or legacy availability rather than as a stable manufacturer-supplied
SKU. As a cross-check, Walmart still has an H5083 page but marks its $40 four-pack
out of stock. [Walmart H5083 listing](https://www.walmart.com/ip/20130520438)

## Why H5083 is the best bench target

The pinned H5083 specification is based on H5083 HW 1.02.00 / SW 1.00.30 and gives
firmware addresses for the whole flow:

- `AA B1` is accepted in any pair state. Within five seconds of a physical button
  press it returns selector `01` plus the stored eight-byte secret, creating and
  persisting one if absent. Outside the window it returns selector `00` and decoy
  random bytes.
- `33 B2` compares all eight supplied bytes. A match enters paired state; a mismatch
  does not.
- Relay writes are gated on paired state, so the result has an observable control
  consequence rather than only an acknowledgement.
- Before a stored secret exists, the firmware compares `33 B2` against an opaque
  keyed hash of the BLE MAC. Recovering that constant/function from a device dump is
  the strongest H5083 route to a computable fallback.

Those claims are in the [firmware specification](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/firmware.yaml),
which cites the original firmware functions and decompilation offsets. The same
repository's device table labels H5080/H5083/H5085 goods type 43 as
`account_lock: true` and hardware-verifies an H5083 session sending `33 B2` before
control. [Pinned device table](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/devices.yaml)

Four cheap samples are materially useful: leave one factory-fresh, bind one with
the app, factory-reset one repeatedly, and reserve one for firmware extraction or
destructive board access. Record the underside label and the BLE-reported hardware
and software versions before allowing the app to update anything.

## H5080 is a useful independent comparison

The public H5080 protocol work documents live behavior rather than merely assigning
the model to a family: control writes receive no response until a correct `33 B2`
is sent, `AA B1` returns selector `01` and the key after the physical button is
pressed, and an incorrect key receives no response. It includes example frames for
both commands. [Pinned H5080 protocol notes](https://github.com/egold555/Govee-Reverse-Engineering/blob/900b003174f5ecd6708b3d5266ea0514926aefa1/Products/H5080.md)

That source assumes the H5080 key is a persistent random manufacturing value. It
does not analyze generation or establish the H7135's one-byte entropy collapse.
An H5080 therefore answers a valuable generalization question, but H5083 is the
better first purchase because it is cheaper per specimen and has firmware-level
function addresses for follow-up work.

## H7135: exact formula target, recalled appliance

The downloadable H7135 HW 1.02.00 / SW 1.00.13 OTA image contains the code from
which the 256 candidates were recovered. The image remains directly available from
Govee's public firmware bucket. [H7135 OTA image](https://s3.amazonaws.com/govee-public/upgrade-pack/62c9486b3b668992ebb413cc2ee0ad4e-H7135_CMS726_WIFI_BLE_HW1.02.00_SW1.00.13_OTA.bin)
The image provenance and layout were published by the researchers who obtained and
analyzed it. [Include Security firmware analysis](https://blog.includesecurity.com/2025/02/replacing-a-space-heater-firmware-over-wifi/)

H7135 is among the heater models recalled by Govee and the US Consumer Product
Safety Commission. The CPSC reports 113 overheating incidents, including seven
fires and one minor burn, and instructs owners to stop use, unplug the heater, cut
the cord, and obtain a refund. [CPSC recall](https://www.cpsc.gov/Recalls/2025/GoveeLife-and-Govee-Smart-Electric-Space-Heaters-Recalled-Due-to-Fire-and-Burn-Hazards-Imported-by-Govee)

For that reason the live $15 listing is relevant to price research but is not the
recommended powered target. If acquired at all, use it only under a deliberately
controlled electronics-research plan that cannot energize the heating element.
Shipping also makes its delivered price likely to exceed the H5083 pack, and a
recent used H7135 sale closed at $35 plus $25.04 shipping. [Sold eBay comparison](https://www.ebay.com/itm/178309218245)

## Cheaper lights do not improve the first purchase

Some older lights have app-level evidence that the client sends `33 B2`, but none
currently offers a cheaper, equally strong path to the H7135 derivation:

- H6050 and H6051 are marked `account_lock: true` in the pinned device table, and
  their app control path sends the secret check before the normal reads. The live
  market is materially dearer: an H6050 is currently about $34.99 plus $17.77
  shipping, and an H6051 about $33.23 plus $10.05 shipping. [Current eBay lamp
  results](https://www.ebay.com/shop/govee-aura-table-lamp?_nkw=govee+aura+table+lamp)
- H6071 and H6075 also have app-traced `account_lock: true` control paths, but the
  public table explicitly says they are not hardware-verified, and floor lamps are
  substantially more expensive to buy and ship.
- H6168/H619A and similar strips are tagged as pairing-secret families in app data,
  but this repository has not demonstrated that a physical unit ignores control
  and then obeys after the correct `33 B2`. A cheap strip can be a second-stage
  generalization test, not the primary proof target.

The primary source for those model classifications is the [pinned device table](https://github.com/Brady-Woods/govee-ble-local/blob/e052b1732d42019675437f91efca0bf870f5d190/spec/devices.yaml).
Current marketplace listings can also mislabel H6051 as H6052 or omit the model
entirely, so require a label photograph before buying any light for this work.

## Purchase checklist

1. Prefer the $19.50 exact-H5083 four-pack if its delivered total beats the current
   $23.99-$24.99 free-delivery offers; check again immediately before paying.
2. Ask for or retain a photo of every device's underside label. The title alone is
   insufficient when a listing mixes H5080 and H5083 or uses stock photographs.
3. Do not pair all specimens with Govee Home on arrival. Preserve at least one
   untouched unit and record advertisements, `AA 06`, `AA 07 02`, `AA 07 03`, and
   the physical-button `AA B1` exchange first.
4. Record hardware and software versions. A formula failure on a newer revision is
   useful evidence only if the exact revision is known.
5. Treat raw `AA B1` / `33 B2` captures as credentials and keep them private.
6. Do not use an H7135 as a heater; the official recall says to stop use.

The practical acquisition order is therefore **H5083 first**, **H5080 second if a
cross-generation comparison is wanted**, and **H7135 only if direct validation of
the already-recovered formula justifies handling recalled hardware**.

## UK availability update

Checked 2026-10-23. The US recommendation does not transfer cleanly to Britain:
H5080 and H5083 are 120 V Type-B products, so they should not be connected to UK
mains through a simple travel adaptor. No credible native-UK H5080/H5083 stock was
found on Amazon UK or eBay UK.

The strongest UK research purchase is **H713C**. Govee's own specification table
identifies H713C as the 220–240 V UK/EU counterpart of H7135. An active eBay UK
listing identifies an exact H713C at **£30.24** before destination-dependent
postage; another registered-business listing offers a new exact H713C for
**£50.99**. The cheaper listing is the better experimental purchase if its UK
checkout total remains reasonable.

- [£30.24 H713C listing](https://www.ebay.co.uk/itm/257616998706)
- [£50.99 H713C listing](https://www.ebay.co.uk/itm/366227050786)
- [Govee model/voltage table](https://community.govee.com/support/faqs/specs)

H713C is a high-value **hypothesis test**, not a confirmed instance of the
H7135 weakness. The recovered 256-candidate formula came from an H7135
HW 1.02.00 / SW 1.00.13 image; the UK/EU H713C firmware has not been recovered or
compared. A positive result would show that the weak construction crossed the
regional firmware boundary. Avoid deliberately enabling the heating element while
testing the BLE control path.

The cheapest low-voltage alternative with app-derived pairing-secret-family
evidence is **H619A**, a 5 m RGBIC strip with a UK plug. Govee UK currently lists it
for **£28.11**, while an exact-model eBay UK listing is currently **£27.75** before
destination-dependent postage; auctions can be lower. It is cheaper and easier to instrument than a
heater, but no hardware capture yet proves that a retail H619A enforces `33 B2`,
and there is no evidence that it uses the H7135 derivation.

- [Official Govee UK H619A](https://uk-store.govee.com/products/govee-rgbic-wi-fi-bluetooth-led-strip-lights-with-protective-coating?variant=41395532824749)
- [Exact-model H619A marketplace listing](https://www.ebay.co.uk/itm/366635044658)

For a UK purchase, the revised order is therefore **H713C first when the goal is
testing whether the known H7135 formula generalises**, or **H619A first when the
goal is the cheapest, low-voltage search for secret-gated behaviour**. H5080/H5083
remain useful only if an isolated 120 V bench supply and an imported exact model
are already available.
