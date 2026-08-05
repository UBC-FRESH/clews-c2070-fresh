# Map of Forest Management in Canada - Source Review

**Review date**: 2026-08-05
**Source ID**: `forest_management_canada`
**Status**: `accepted`

## Source Overview

- **Dataset**: Map of Forest Management in Canada
- **Publisher**: Natural Resources Canada
- **License**: Open Government Licence - Canada
- **Access**: Direct download via ca.nfis.org; also WMS/WFS
- **URL**: https://open.canada.ca/data/en/dataset/d8fa9a38-c4df-442a-8319-9bbcbdc29060
- **Temporal**: 2017 version and 2020 version

## Description

Generalized national classification of forest management in Canada, including:
- Protected areas
- Treaty/Settlement Lands (including Treaty Lands identified in Final Agreements)
- General forest management zones

**Note**: This is a generalized classification, not precise tenure boundaries. For
supply-node spatial unit definition, use `databasin_canada_forest_tenures`. This
layer serves as a management constraint overlay.

## Available Resources

| Format | Version | Download URL | Size |
|--------|---------|--------------|------|
| TIFF raster | 2020 | `https://ca.nfis.org/fss/fss?command=retrieveById&fss_id=Anh-adILivU0opBCuud2oA` | TBD |
| TIFF raster | 2017 | `https://ca.nfis.org/fss/fss?command=retrieveById&fss_id=xVtr6R4NLbwZtA7Q-Xt4Tg` | TBD |
| FGDB vector | 2017 | `https://ca.nfis.org/fss/fss?command=retrieveById&fss_id=BXDz1azGcURq5f3p75S8Pg` | TBD |
| WMS | 2020 | `https://ca.nfis.org/mapserver/cgi-bin/ccfm_managed_forests_eng.cgi` | N/A |
| WFS | 2020 | `https://ca.nfis.org/mapserver/cgi-bin/ccfm_managed_forests_eng.cgi` | N/A |
| XLSX | 2020 | `https://ca.nfis.org/fss/fss?command=retrieveById&fss_id=ohJbKGlhJEnh4lOz7ItArw` | TBD |

## Fit to FRESH

- **Supply-node spatial units**: No — too generalized. Use databasin tenures.
- **Management constraints**: Yes — protected areas and Treaty Lands are key
  constraint flags for harvest eligibility screening.
- **National coverage**: Yes — entire Canada.

## Recommended Next Steps

1. Download the 2020 TIFF and/or 2017 FGDB vector.
2. Inspect attribute table for classification categories.
3. Assess resolution and currency against SCANFI landcover.
4. Archive accepted payload in `data/raw/nrcan/forest_management/`.
