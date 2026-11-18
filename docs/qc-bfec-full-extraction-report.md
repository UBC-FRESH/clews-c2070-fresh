# QC BFEC/Woodstock Full Extraction Report

**Date:** 2026-11-18  
**Source index:** `https://diffusion.mffp.gouv.qc.ca/diffusion/fec/diffusion/`  
**Parser:** ws3 `1.0.5` at `7943cc34aa53533c0a136d4459ddb1b5bd3d64f4`  
**Scope:** all public BFEC model bundles; 52 with `Modeles_compresses/*.zip` plus notice-bearing units `08462` and `08562`, whose bundles are at the unit root. Thirteen confidential cartography-only units were not downloaded.

## 1. Summary

| Metric | Value |
|---|---:|
| Public model units | 54 |
| Units parsed | 54 |
| Units failed | 0 |
| No-model units skipped | 13 |
| Inventory polygons | 9,857,465 |
| Inventory development types | 880,096 |
| Model development types | 843,348 |
| Unique yield tables | 2,622 |
| Curve rows | 107,502 |
| DT rows | 939,720 |
| Crosswalk rows | 9,857,465 |
| Inventory area (ha) | 37,852,030.7 |
| Model `.are` area (ha) | 37,852,030.7 |
| Restricted units | 3 |
| Notice-bearing units | 9 |
| Download size | 8.8 GiB |
| Extracted size | 41.4 GiB |
| Wall runtime | 11.8 min (final parse) |

**Pipeline runtime and storage.** The 54 bundles total 9,422,193,928 bytes
(8.8 GiB) and expanded to 44,481,620,668 bytes (41.4 GiB). Download plus
extraction completed in 14.8 min on the first pass; the final 54-unit parse
took 11.8 min with six workers. Cumulative wall time including parser retries
was about 49 min. Scratch storage for downloads, extracted bundles, and
per-unit intermediate CSVs was about 56 GB (52 GiB); only the 1.75 GB of public
derivatives are published.

## 2. Parser hardening

The sibling ws3 parser was extended before the full run:

- `.con` values accept comma decimal separators and, when requested with `on_invalid='skip'`, text-valued partial declarations are skipped with a warning.
- `#CONSTANT` references resolve in tabular and row-based yield cells.
- Undefined theme mask codes are explicit skips with per-unit warnings instead of `KeyError`; over-long masks truncate trailing comment tokens with a warning.
- Unsupported `FOREACH` yield macro blocks are skipped with a warning.
- The five-unit sample (`03451`, `02651`, `03152`, `03551`, `03151`) and the three previous failures (`05151`, `06151`, `09551`) were re-parsed successfully.

| Retry unit | Polygons | Yield tables | Inventory ha | Runtime s | Warnings | Result |
|---|---:|---:|---:|---:|---:|---|
| 03451 | 13,325 | 16 | 24,064.7 | 7.2 | 4 | parsed |
| 02651 | 39,390 | 20 | 221,555.2 | 21.0 | 13 | parsed |
| 03152 | 76,958 | 57 | 169,386.9 | 28.8 | 4 | parsed |
| 03551 | 45,105 | 71 | 114,187.6 | 31.0 | 4 | parsed |
| 03151 | 85,416 | 72 | 170,864.8 | 44.5 | 4 | parsed |
| 05151 | 32,817 | 47 | 80,667.6 | 27.8 | 5 | parsed |
| 06151 | 33,436 | 55 | 88,260.0 | 38.9 | 5 | parsed |
| 09551 | 76,242 | 22 | 329,163.9 | 16.8 | 21 | parsed |

## 3. Validation

| Check | Result |
|---|---|
| Area mismatches > 1e-6 | 0 |
| Units with DT coverage < 100% | 2 |
| Max curve split error (m3/ha) | 0.00500000 |
| Negative-volume tables | 0 |
| Units with post-peak monotonicity flags | 52 |
| Zero-volume area needing review (ha) | 5519.2 |
| Units with ambiguous model matches | 0 |

### DT coverage below 100%

| Unit | DT coverage | Productive coverage |
|---|---:|---:|
| 08462 | 99.85% | 99.85% |
| 09351 | 99.77% | 80.87% |

### Post-peak monotonicity flags

| Unit | Tables | Max rise (m3/ha over 5 yr) |
|---|---:|---:|
| 02351 | 1 | 0.600 |
| 02251 | 1 | 4.700 |
| 02451 | 2 | 0.800 |
| 02352 | 2 | 1.800 |
| 01171 | 6 | 4.400 |
| 02452 | 3 | 0.900 |
| 03152 | 1 | 0.100 |
| 03153 | 1 | 0.300 |
| 03451 | 2 | 2.400 |
| 03151 | 16 | 7.500 |
| 01272 | 7 | 2.400 |
| 03453 | 5 | 7.600 |
| 03551 | 4 | 7.600 |
| 02551 | 10 | 8.500 |
| 03351 | 3 | 6.700 |
| 04151 | 1 | 1.700 |
| 05151 | 4 | 1.000 |
| 04352 | 2 | 3.700 |
| 04351 | 14 | 4.400 |
| 04251 | 4 | 0.483 |
| 06152 | 4 | 1.000 |
| 06151 | 9 | 1.400 |
| 06251 | 7 | 9.200 |
| 06252 | 4 | 7.100 |
| 06452 | 8 | 4.800 |
| 07151 | 5 | 3.700 |
| 07251 | 7 | 5.200 |
| 07352 | 5 | 1.900 |
| 07152 | 7 | 5.100 |
| 07351 | 12 | 3.200 |

### Zero-volume area needing review

| Unit | Area ha | Example theme codes |
|---|---:|---|
| 08462 | 429.1 | `gs0074|forestier|1|sra002|fc0057|nat|n|exin|na|na|na|na|na|na|forp|na|sip|na|utr5|na; gs0074|forestier|1|sra002|fc0057|nat|o|exin|na|na|na|na|na|na|forp|na|sip|na|utr5|gh4` |
| 09351 | 5090.1 | `gs0001|forestier|1|sr0093|fc0022|nat|o|exex|zt0001|na|m1|au31|na|cos102|forp|na|na|na|utr56|na; gs0001|forestier|1|sr0093|fc0022|nat|o|exex|zt0001|na|m1|au31|na|cos102|forp|na|na|na|utr59|na` |

## 4. Units

| Unit | Region | Bundles | Polygons | Inventory DT | Tables | Inventory ha | DT cover | Runtime s | Notice |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 01171 | R01 | Modeles_compresses | 153,248 | 20,224 | 95 | 363,571.8 | 100.0% | 72.8 |  |
| 01272 | R01 | Modeles_compresses | 277,281 | 26,983 | 155 | 744,185.7 | 100.0% | 120.4 |  |
| 02251 | R02 | Modeles_compresses | 153,175 | 6,741 | 34 | 442,692.1 | 100.0% | 35.1 |  |
| 02351 | R02 | Modeles_compresses | 98,109 | 5,577 | 41 | 305,114.7 | 100.0% | 25.3 |  |
| 02352 | R02 | Modeles_compresses | 264,442 | 12,550 | 46 | 1,075,965.9 | 100.0% | 59.3 |  |
| 02451 | R02 | Modeles_compresses | 207,864 | 14,799 | 43 | 1,177,512.6 | 100.0% | 48.6 |  |
| 02452 | R02 | Modeles_compresses | 244,701 | 21,271 | 40 | 1,244,824.4 | 100.0% | 56.6 |  |
| 02551 | R02 | Modeles_compresses | 545,330 | 32,658 | 32 | 2,720,884.1 | 100.0% | 116.4 |  |
| 02651 | R04 | Modeles_compresses | 39,390 | 3,069 | 20 | 221,555.2 | 100.0% | 21.0 |  |
| 02751 | R02 | Modeles_compresses | 262,794 | 16,163 | 32 | 1,255,007.4 | 100.0% | 68.3 |  |
| 03151 | R03 | Modeles_compresses | 85,416 | 15,096 | 72 | 170,864.8 | 100.0% | 44.5 |  |
| 03152 | R03 | Modeles_compresses | 76,958 | 9,325 | 57 | 169,386.9 | 100.0% | 28.8 |  |
| 03153 | R03 | Modeles_compresses | 83,599 | 7,065 | 25 | 211,769.7 | 100.0% | 24.0 |  |
| 03351 | R03 | Modeles_compresses | 229,003 | 27,114 | 49 | 511,841.4 | 100.0% | 72.0 |  |
| 03451 | R12 | Modeles_compresses | 13,325 | 304 | 16 | 24,064.7 | 100.0% | 7.2 |  |
| 03453 | R12 | Modeles_compresses | 9,576 | 1,382 | 51 | 27,491.5 | 100.0% | 25.3 |  |
| 03551 | R12 | Modeles_compresses | 45,105 | 4,280 | 71 | 114,187.6 | 100.0% | 31.0 |  |
| 04151 | R04 | Modeles_compresses | 170,538 | 45,356 | 41 | 633,104.6 | 100.0% | 56.2 |  |
| 04251 | R04 | Modeles_compresses | 281,335 | 16,282 | 60 | 1,044,650.4 | 100.0% | 74.5 |  |
| 04351 | R04 | Modeles_compresses | 249,662 | 7,768 | 37 | 1,342,671.5 | 100.0% | 52.4 |  |
| 04352 | R04 | Modeles_compresses | 170,301 | 9,883 | 39 | 775,269.6 | 100.0% | 44.0 |  |
| 05151 | R05 | Modeles_compresses | 32,817 | 1,804 | 47 | 80,667.6 | 100.0% | 27.8 |  |
| 06151 | R15 | Modeles_compresses | 33,436 | 3,768 | 55 | 88,260.0 | 100.0% | 38.9 | modification |
| 06152 | R15 | Modeles_compresses | 52,420 | 4,426 | 43 | 159,059.3 | 100.0% | 31.0 |  |
| 06251 | R14 | Modeles_compresses | 138,290 | 9,982 | 40 | 399,305.6 | 100.0% | 53.7 | modification |
| 06252 | R14 | Modeles_compresses | 72,666 | 5,628 | 42 | 290,931.8 | 100.0% | 50.2 | modification |
| 06451 | R15 | Modeles_compresses | 354,400 | 34,678 | 77 | 1,042,574.6 | 100.0% | 183.0 | confidential |
| 06452 | R15 | Modeles_compresses | 114,282 | 11,503 | 40 | 200,680.3 | 100.0% | 55.8 |  |
| 07151 | R07 | Modeles_compresses | 100,099 | 8,347 | 62 | 302,662.4 | 100.0% | 81.4 |  |
| 07152 | R07 | Modeles_compresses | 166,746 | 16,798 | 73 | 575,097.9 | 100.0% | 128.5 |  |
| 07251 | R07 | Modeles_compresses | 100,634 | 9,632 | 56 | 176,656.7 | 100.0% | 65.7 |  |
| 07351 | R07 | Modeles_compresses | 157,815 | 17,735 | 79 | 485,292.9 | 100.0% | 128.2 |  |
| 07352 | R07 | Modeles_compresses | 104,012 | 7,618 | 53 | 464,695.2 | 100.0% | 67.9 |  |
| 07451 | R07 | Modeles_compresses | 226,236 | 20,548 | 69 | 1,026,415.3 | 100.0% | 108.9 |  |
| 08151 | R08 | Modeles_compresses | 136,971 | 14,333 | 63 | 412,251.5 | 100.0% | 95.5 |  |
| 08152 | R08 | Modeles_compresses | 285,122 | 25,008 | 72 | 830,457.3 | 100.0% | 141.5 |  |
| 08251 | R08 | Modeles_compresses | 177,901 | 22,403 | 40 | 767,356.5 | 100.0% | 48.9 | modification |
| 08351 | R08 | Modeles_compresses | 334,442 | 24,346 | 60 | 1,335,435.9 | 100.0% | 75.9 |  |
| 08451 | R08 | Modeles_compresses | 254,207 | 35,193 | 44 | 859,511.4 | 100.0% | 70.4 | modification |
| 08462 | R08 | unit_root_zip | 83,145 | 5,164 | 29 | 286,312.0 | 99.8% | 20.6 | confidential |
| 08551 | R10 | Modeles_compresses | 222,447 | 12,657 | 44 | 1,351,459.5 | 100.0% | 47.0 |  |
| 08562 | R10 | unit_root_zip | 17,309 | 562 | 14 | 180,079.6 | 100.0% | 5.2 | confidential |
| 08651 | R08 | Modeles_compresses | 140,729 | 19,527 | 49 | 673,197.3 | 100.0% | 40.8 |  |
| 08652 | R10 | Modeles_compresses | 66,809 | 5,557 | 26 | 378,700.9 | 100.0% | 18.9 |  |
| 08751 | R10 | Modeles_compresses | 116,241 | 8,710 | 26 | 529,685.2 | 100.0% | 29.9 | modification |
| 09351 | R09 | Modeles_compresses | 586,531 | 50,549 | 35 | 2,262,898.2 | 99.8% | 135.8 |  |
| 09352 | R09 | Modeles_compresses | 187,502 | 11,190 | 20 | 1,577,504.8 | 100.0% | 39.1 |  |
| 09451 | R09 | Modeles_compresses | 471,105 | 39,580 | 33 | 1,813,659.7 | 100.0% | 99.9 |  |
| 09452 | R09 | Modeles_compresses | 218,351 | 19,180 | 27 | 1,154,788.3 | 100.0% | 48.1 |  |
| 09551 | R09 | Modeles_compresses | 76,242 | 4,611 | 22 | 329,163.9 | 100.0% | 16.8 |  |
| 09751 | R09 | Modeles_compresses | 445,831 | 43,715 | 42 | 1,562,238.2 | 100.0% | 110.1 |  |
| 11161 | R11 | Modeles_compresses | 325,866 | 38,640 | 72 | 719,683.1 | 100.0% | 95.0 |  |
| 11262 | R11 | Modeles_compresses | 193,246 | 21,825 | 62 | 471,864.0 | 100.0% | 57.5 |  |
| 11263 | R11 | Modeles_compresses | 202,463 | 20,989 | 50 | 486,867.2 | 100.0% | 52.8 |  |

## 5. Failures and notices

No unit failed.

Notice-bearing units:

| Unit | Notice kind | Restricted | Sources |
|---|---|---|---|
| 06151 | modification | no | Modif06151_Lisez_moi.txt |
| 06251 | modification | no | Modif06251_Lisez_moi.txt |
| 06252 | modification | no | Modif06252_Lisez_moi.txt |
| 06451 | confidentiality | yes | Modif06451_Lisez_moi.txt |
| 08251 | modification | no | Modif08251_Lisez_moi.txt |
| 08451 | modification | no | Modif08451_Lisez_moi.txt |
| 08462 | confidentiality | yes | Modif08462_Lisez_moi.txt; unit page: https://diffusion.mffp.gouv.qc.ca/diffusion/fec/diffusion/R08/08462/Lisez_moi.txt |
| 08562 | confidentiality | yes | Modif08562_Lisez_moi.txt; unit page: https://diffusion.mffp.gouv.qc.ca/diffusion/fec/diffusion/R10/08562/Lisez_moi.txt |
| 08751 | modification | no | Modif08751_Lisez_moi.txt |

The 13 confidential cartography-only units were not downloaded: 02661, 02662, 02663, 02664, 02665, 02666, 08663, 08664, 08665, 08666, 08762, 08763, 08764.

One additional `Modeles_compresses` unit, `06451`, carries a confidentiality
notice inside its model bundle (Indigenous community information). It was
parsed and its curves are published with provenance, but its development-type
aggregates and inventory crosswalk are held in `restricted/`. The other six
notice-bearing units (`06151`, `06251`, `06252`, `08251`, `08451`, `08751`)
carry scenario-modification notes only and are not restricted.

## 6. Artifacts

| File | Rows | Bytes | SHA-256 |
|---|---:|---:|---|
| `qc_bfec_curves.csv` | 107,502 | 5,903,103 | `ae036854ce4e64b578363ff9935f4ddaf85ea398f3e12810b32efb0667841248` |
| `qc_bfec_development_types.csv` | 898,661 | 274,972,191 | `6ad7f58e2ee9387e080a111f931517b30c485c5fa21ceb4565071403b2175eda` |
| `qc_bfec_inventory_crosswalk.csv` | 9,402,611 | 1,468,973,001 | `239c317724a627be6db06747aaff85ce08d1b4610c24499e207341132f66b9ab` |
| `restricted/qc_bfec_development_types_restricted.csv` (not published) | 41,059 | 13,710,014 | `cc5bfe95ea76e967fecccbebb2c598727d2691a5120a31439fc2dc36a4b08993` |
| `restricted/qc_bfec_inventory_crosswalk_restricted.csv` (not published) | 454,854 | 71,789,646 | `3e55c79198a02ac37573c7a9def8cb4fc05128b822975775cdbc6885ee44ae17` |

Raw bundles and extracted model directories remain in external scratch and are not committed. The restricted crosswalk is not committed or pushed.

## 7. Next

- Cluster and compress the unique yield tables for integration with the CLEWs forest-supply chain.
- Join development-type mappings to inventory polygons through the restricted crosswalk inside the secure data environment.
- Validate the curve tables against the earlier five-unit sample and against provincial yield-curve sources before calibration.
