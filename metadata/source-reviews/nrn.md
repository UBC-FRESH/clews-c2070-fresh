# National Road Network Source Review

Review date: 2026-06-25.

## Source

- Dataset: National Road Network - NRN - GeoBase Series.
- DOI: `https://doi.org/10.23687/3d282116-e556-400c-9306-ca1a3cada77f`.
- Publisher: Statistics Canada / GeoBase.
- Open Canada licence: Open Government Licence - Canada.
- Open Canada landing page:
  `https://open.canada.ca/data/en/dataset/3d282116-e556-400c-9306-ca1a3cada77f`.
- Documentation:
  `https://geo.statcan.gc.ca/nrn_rrn/doc/nrn-rrn_documentation.zip`.
- Web services:
  - WMS:
    `https://geo.statcan.gc.ca/geo_wa/services/NRN-RRN/nrn_rrn/MapServer/WMSServer?request=GetCapabilities&service=WMS`
  - ESRI REST:
    `https://geo.statcan.gc.ca/geo_wa/rest/services/NRN-RRN/nrn_rrn/MapServer`

## Package Inventory

The Open Canada metadata lists one documentation ZIP, WMS/REST services, and
province/territory ZIP packages in both GeoPackage and shapefile distributions.
Package byte sizes were recorded from HTTP `Content-Length` headers on
2026-06-25; payloads were not downloaded.

Manifest:

- `metadata/source-file-manifests/nrn_files.csv`

Observed package totals:

- Documentation ZIP: 1 file, about 2.3 MB.
- GeoPackage ZIPs: 13 jurisdiction packages, about 1.628 GB total.
- Shapefile ZIPs: 13 jurisdiction packages, about 1.539 GB total.

Current release notes in the documentation give jurisdiction-specific validity
dates and versions. Several large forest jurisdictions are not equally current:
for example British Columbia is listed as 2017-03 / v14.0, Quebec as
2016-02 / v9.0, and Manitoba as 2012-04 / v6.0, while New Brunswick, Northwest
Territories, Nova Scotia, Nunavut, and Yukon are listed as 2025 releases.

## Schema Notes

The downloadable NRN segmented view includes these entities:

- `ADDRANGE`: Address Range table.
- `ALTNAMLINK`: Alternate Name Link table.
- `BLKPASSAGE`: Blocked Passage point layer.
- `FERRYSEG`: Ferry Connection Segment line layer.
- `JUNCTION`: Junction point layer.
- `ROADSEG`: Road Segment line layer.
- `STRPLANAME`: Street and Place Names table.
- `TOLLPOINT`: Toll Point layer.

Important common metadata fields include acquisition technique, coverage,
creation date, dataset name, planimetric accuracy, provider, revision date, and
standard version.

Important `ROADSEG` fields for transport screening include:

- `ROADCLASS`: functional road class, including Freeway,
  Expressway / Highway, Arterial, Collector, Local / Street,
  Local / Strata, Local / Unknown, Alleyway / Lane, Ramp,
  Resource / Recreation, Rapid Transit, Service Lane, and Winter.
- `NBRLANES`: number of lanes.
- `PAVSTATUS`: paved or unpaved.
- `PAVSURF` and `UNPAVSURF`: paved and unpaved surface type.
- `ROADJURIS`: road jurisdiction.
- `SPEED`: speed restrictions.
- `TRAFFICDIR`: traffic direction.
- route-name and route-number fields.
- `CLOSING`: closing period.

The GeoPackage distribution packages all entities for one jurisdiction in a
single file. The shapefile distribution splits entities by geometry/table and is
useful as an alternate distribution, but GeoPackage should be preferred for
model ingestion and provenance.

## Modelling Assessment

NRN is accepted as the national coarse road-network baseline for first-pass
haul-distance and accessibility screening. It is public, national, structured by
jurisdiction, and includes the road class, surface, lane, closure, jurisdiction,
ferry, and junction information needed to build a routable screening layer.

NRN should not be treated as sufficient by itself for operational forest access
or delivered-cost modelling. The dataset can identify broad road access and
some `Resource / Recreation` roads, but the release recency varies substantially
by jurisdiction and national products are likely to miss or lag local forest
service roads, resource roads, seasonal roads, private industrial roads, and
active/inactive status details that matter for biomass haul modelling.

For the first national baseline:

- archive exact GeoPackage ZIP releases in the DataLad repository if NRN is used
  in model outputs;
- keep shapefile URLs in the manifest as alternate distributions, but do not
  mirror both formats unless a workflow requires them;
- build first-pass routing from `ROADSEG`, `FERRYSEG`, and `JUNCTION`, with
  functional class, pavement/surface, speed, closure, and traffic-direction
  attributes retained; and
- continue separate jurisdiction-specific forest/resource road triage before
  using haul distances for operational siting or cost claims.

## Recommendation

Set `nrn` to `accepted_baseline`. It is appropriate for national screening and
for anchoring the first transport layer, but the required-datasets registry
should keep an explicit note that jurisdiction-specific forest/resource road
layers remain mandatory follow-up work.
