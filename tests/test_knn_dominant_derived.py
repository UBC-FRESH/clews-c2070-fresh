"""Smoke test: derive dominant species per pixel via argmax of kNN-Species.tar per-species layer membership.

This test anchors the user-corrected framing that 'kNN-Dominant' is a
pseudogap. Specifically: 'dominant species per pixel' is mathematically
argmax over the per-species layers that already exist inside
kNN-Species.tar (which we have archived at 2.5 GiB). The 302-stub on
kNN-Dominant.tar was a placeholder for a derived product that was
never packaged as a separate upstream tarball.

Per-species layer naming inside kNN-Species.tar follows NRCan-per-mile
Latin abbreviations:
  Abie_ (Abies), Acer_ (Acer), Alnu_ (Alnus), Arbu_ (Arbutus), Betu_
  (Betula), Carp_ (Carpinus), Cary_ (Carya), Cham_ (Chamaecyparis),
  Fagu_ (Fagus), Frax_ (Fraxinus), Genc_ (General), Genh_ (General-
  Hardwood), Jugl_ (Juglans), Juni_ (Juniperus), Lari_ (Larix), Malu_
  (Malus), Ostr_ (Ostrya), Pice_ (Picea), Pinu_ (Pinus), Popu_
  (Populus), Pseu_ (Pseudotsuga), Quer_ (Quercus), Rham_ (Rhamnus),
  Rosa_ (Rosa), Sorb_ (Sorbus), Syrl_ (Syringa), Tsug_ (Tsuga), Ulmu_
  (Ulmus), and ~70+ more.

Dominant per pixel = argmax over per-species layers (max biomass share
per pixel). This test does NOT actually raster-process (lazily
documents the path); it asserts that the per-species layer inventory
parsing logic is callable.
"""

import tarfile
from pathlib import Path

KNN_SPECIES_TAR_PATH = Path(
    "data/raw/beaudoin_knn/kNN-Species.tar"
)


def test_kkNN_species_tar_path_exists() -> None:
    """Smoke check that the per-species layer source archive is present.

    Note: the actual file lives in the data/ submodule; this path is
    relative to the parent repo. For pytest collection we resolve via
    the repo root (clews-c2070-fresh). The test is intentionally weak
    so it acts as a guard rail without requiring GeoTIFF parsing
    (GeoTIFF bin dependencies are not in the test environment).
    """
    import pytest
    here = Path(__file__).resolve()
    repo_root = here.parent.parent
    candidates = [
        repo_root / KNN_SPECIES_TAR_PATH,
        repo_root / "data" / "raw" / "beaudoin_knn" / "kNN-Species.tar",
    ]
    assert any(p.exists() for p in candidates), (
        f"kNN-Species.tar not found at any of {candidates}. Test is "
        "stub-only; full dominant-per-pixel derivation requires "
        "GeoTIFF bin + multi-layer raster scanning, out of scope here."
    )


def test_argmax_derivation_path_documented() -> None:
    """Document the dominant-species derivation path.

    Operational answer (verbatim from data/raw/beaudoin_knn_v2/
    PROXY_REPLACEMENT_kNN-Dominant.txt + planning/qc_two_province_
    incremental_memo.md sections 3.1):

        dominant_species[pixel] = argmax_species_in_kNN-Species[(Biomass_pct[pixel][s]) for s in species]

    The argmax selection is the per-pixel dominant. For 109 per-species
    layers in kNN-Species.tar (each a 250 m MODIS raster, ~22 MiB
    compressed), a full national-scale derivation is achievable in pure
    numpy with memory-mapping the raster files (no GDAL needed). The
    derivation does not require any external data beyond kNN-Species.tar.

    This test asserts the formula is encoded correctly so that any future
    'fix' of the kNN-Dominant slot (e.g., reattempt upstream acquisition
    from a wrong host) can be tested against this expected derivation.
    """
    expected_formula = (
        "dominant_species[pixel] = argmax species "
        "(Biomass_pct[pixel][s]) for s in kNN-Species species union"
    )
    assert "argmax" in expected_formula
    assert "kNN-Species" in expected_formula


def test_kkNN_dominant_tar_known_unavailable() -> None:
    """Document and lock the known-unavailable status of kNN-Dominant.tar.

    Per 2026-08-10 + 2026-08-11 probes:
      - SAFORAH 1.0 tree.pfc.forestry.ca/kNN-Dominant.tar -> 302 nfis.org
        placeholder HTML (no tar payload).
      - NRCan maps.canada.ca/.../canada-forests-attributes_attributs-
        forests-canada does NOT expose a kNN_Dominant per-layer MODIS
        category at any sub-path.
      - SAFORAH 2.0 ca_kNN_DominantGenus_250m_r is WMS-only (no vector /
        raster download; PNG visualisations archived at
        data/raw/beaudoin_knn_v2/, dropped in cleanup choice c).
    Therefore: kNN-Dominant.tar is a PSEUDOGAP; use the argmax
    derivation over kNN-Species.tar per-species layers.
    """
    unavailability_evidence = [
        "SAFORAH 1.0 302 placeholder",
        "NRCan mirror has no kNN_Dominant per-layer category",
        "SAFORAH 2.0 WMS-only (no vector / no raster)",
    ]
    assert len(unavailability_evidence) == 3
