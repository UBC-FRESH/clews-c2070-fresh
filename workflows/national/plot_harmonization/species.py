"""Source species codes to the coarse conifer/deciduous/unknown groups.

The crosswalk is intentionally coarse.  It exists so the calibration can
stratify conifer and deciduous growth without pretending that a BC species
code, a Québec French code, a Newfoundland code, and a MAGPlot Latin code are
the same taxon.  The ``fvs_jsp_code_todo`` column is a deliberate placeholder
for a later FVS JSP mapping.
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

#: BC FAIB species codes.  Only confident assignments are made; rare or
#: ambiguous codes stay ``unknown``.
_FAIB: dict[str, str] = {
    # conifers
    "FD": "conifer", "FDC": "conifer", "FDI": "conifer",
    "PL": "conifer", "SB": "conifer", "SE": "conifer", "SW": "conifer",
    "SS": "conifer", "EP": "conifer", "BA": "conifer", "BL": "conifer",
    "CW": "conifer", "HW": "conifer", "HM": "conifer", "YC": "conifer",
    "BG": "conifer", "LW": "conifer", "PY": "conifer", "PW": "conifer",
    "PA": "conifer", "PJ": "conifer", "GP": "conifer", "TW": "conifer",
    # deciduous
    "AT": "deciduous", "AC": "deciduous", "DR": "deciduous",
    "MB": "deciduous", "W": "deciduous", "WB": "deciduous",
    "WP": "deciduous", "M": "deciduous",
}

#: Québec PEP French species codes.  Confident assignments only.
_QC: dict[str, str] = {
    # conifers
    "SAB": "conifer", "EPN": "conifer", "EPB": "conifer",
    "EPR": "conifer", "EPO": "conifer", "PIG": "conifer",
    "PIB": "conifer", "PIS": "conifer", "PIR": "conifer",
    "PRU": "conifer", "THO": "conifer", "MEL": "conifer",
    "PED": "conifer", "PEB": "conifer", "PID": "conifer",
    "RES": "conifer", "CET": "conifer", "CEO": "conifer",
    # deciduous
    "BOP": "deciduous", "BOJ": "deciduous", "ERS": "deciduous",
    "ERR": "deciduous", "ERE": "deciduous", "ERA": "deciduous",
    "ERG": "deciduous", "ERN": "deciduous", "ERP": "deciduous",
    "PET": "deciduous", "PEG": "deciduous", "PEH": "deciduous",
    "SAL": "deciduous", "SOA": "deciduous", "SOD": "deciduous",
    "OSV": "deciduous", "HEG": "deciduous", "PRP": "deciduous",
    "PRV": "deciduous", "CHB": "deciduous", "CHE": "deciduous",
    "CHG": "deciduous", "CAC": "deciduous", "CAR": "deciduous",
    "COA": "deciduous", "CRA": "deciduous", "FRA": "deciduous",
    "FRN": "deciduous", "FRP": "deciduous", "AME": "deciduous",
    "AUC": "deciduous", "AUR": "deciduous", "BOG": "deciduous",
    "FEU": "deciduous", "MAS": "deciduous", "MEH": "deciduous",
    "NOC": "deciduous", "ORA": "deciduous", "ORR": "deciduous",
    "ORT": "deciduous", "RHS": "deciduous", "RHT": "deciduous",
    "TIL": "deciduous",
}

#: Newfoundland FAL simplified codes.
_NL: dict[str, str] = {
    "BF": "conifer", "BS": "conifer", "WS": "conifer", "TL": "conifer",
    "JP": "conifer", "NS": "conifer", "WP": "conifer", "OP": "conifer",
    "WB": "deciduous", "TA": "deciduous", "RM": "deciduous",
    "YB": "deciduous", "OH": "deciduous",
}

#: MAGPlot genus-level assignments.
_MAGP_GENUS: dict[str, str] = {
    # conifers
    "ABIE": "conifer", "CHAM": "conifer", "JUNI": "conifer",
    "LARI": "conifer", "PICE": "conifer", "PINU": "conifer",
    "PSEU": "conifer", "TAXU": "conifer", "THUJ": "conifer",
    "TSUG": "conifer",
    # deciduous
    "ACER": "deciduous", "ALNU": "deciduous", "AMEL": "deciduous",
    "BETU": "deciduous", "CARP": "deciduous", "CARY": "deciduous",
    "CELT": "deciduous", "CORN": "deciduous", "CORY": "deciduous",
    "CRAT": "deciduous", "DIRC": "deciduous", "FAGU": "deciduous",
    "FRAX": "deciduous", "GLED": "deciduous", "HAMA": "deciduous",
    "ILEX": "deciduous", "JUGL": "deciduous", "LONI": "deciduous",
    "MALU": "deciduous", "OSTR": "deciduous", "POPU": "deciduous",
    "PRUN": "deciduous", "QUER": "deciduous", "RHAM": "deciduous",
    "RHUS": "deciduous", "ROSA": "deciduous", "RUBU": "deciduous",
    "SALI": "deciduous", "SAMB": "deciduous", "SHEP": "deciduous",
    "SORB": "deciduous", "TILI": "deciduous", "ULMU": "deciduous",
    "VIBU": "deciduous",
}

_SOURCE_MAPS: dict[str, dict[str, str]] = {
    "faib_psp": _FAIB,
    "faib_non_psp": _FAIB,
    "qc_pep": _QC,
    "nl_fal": _NL,
}


def _normalize_code(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "<na>", ""}:
        return ""
    return text


def species_group(source: str, code: object) -> str:
    """Return ``conifer``, ``deciduous`` or ``unknown`` for a source code."""
    normalized = _normalize_code(code)
    if not normalized:
        return "unknown"
    if source in {"magplot", "magplot_wfs"}:
        genus = normalized.split(".")[0].strip().upper()
        return _MAGP_GENUS.get(genus, "unknown")
    lookup = _SOURCE_MAPS.get(source, {})
    return lookup.get(normalized.upper(), "unknown")


def crosswalk_note(source: str, code: object) -> str:
    """Return a short provenance note for a crosswalk row."""
    normalized = _normalize_code(code)
    if not normalized:
        return "missing source code"
    if source in {"magplot", "magplot_wfs"}:
        genus = normalized.split(".")[0].strip().upper()
        if genus in _MAGP_GENUS:
            return f"mapped from genus {genus}"
        return f"genus {genus} not in coarse map"
    if source in _SOURCE_MAPS:
        if normalized.upper() in _SOURCE_MAPS[source]:
            return "mapped from source code"
        return "source code not in coarse map"
    return "source has no species-code map"


def build_crosswalk(
    observations: pd.DataFrame,
) -> pd.DataFrame:
    """Build the species crosswalk from ``source``/``species_code`` counts.

    Parameters
    ----------
    observations : pandas.DataFrame
        Must contain ``source``, ``species_code`` and ``n`` columns.

    Returns
    -------
    pandas.DataFrame
        Columns ``source, species_code, species_group, notes,
        fvs_jsp_code_todo``.
    """
    if observations.empty:
        return pd.DataFrame(
            columns=[
                "source",
                "species_code",
                "species_group",
                "notes",
                "fvs_jsp_code_todo",
            ]
        )
    frame = observations.copy()
    frame["source"] = frame["source"].astype("string")
    frame["species_code"] = frame["species_code"].astype("string")
    frame = (
        frame.groupby(["source", "species_code"], dropna=False, as_index=False)["n"]
        .sum()
        .sort_values(["source", "n"], ascending=[True, False])
    )
    frame["species_group"] = [
        species_group(source, code)
        for source, code in zip(frame["source"], frame["species_code"])
    ]
    frame["notes"] = [
        crosswalk_note(source, code)
        for source, code in zip(frame["source"], frame["species_code"])
    ]
    frame["fvs_jsp_code_todo"] = pd.NA
    return frame[
        ["source", "species_code", "species_group", "notes", "fvs_jsp_code_todo", "n"]
    ]


def species_group_series(source: str, codes: Iterable[object]) -> pd.Series:
    """Vectorized helper for adapter use.

    Only distinct codes are classified; the result is aligned to the input.
    """
    series = codes if isinstance(codes, pd.Series) else pd.Series(codes)
    mapping = {
        code: species_group(source, code)
        for code in series.dropna().unique()
    }
    return series.map(mapping).fillna("unknown").astype("string")
