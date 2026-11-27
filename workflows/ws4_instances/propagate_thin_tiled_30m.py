#!/usr/bin/env python3
"""Propagate the WS4 THIN state transition to the full tiled 30-m coverage.

The 3,345 tiled 30-m datasets were built before the shared pilot builder
emitted THIN tracks. This module patches their metadata tables only:

* append one ``SILV_STATE(thinned)`` track per natural baseline track;
* add its strata row, THIN/CC treatments, feature, products, and accounts;
* preserve every existing track id, block, fragment, curve, group, and
  message row.

Fragments, blocks, curves, groups, and messages are never rewritten. The
patch is deterministic and idempotent: a complete tile is skipped, a partial
tile is repaired by removing only THIN-derived rows and rebuilding them.

Run the full coverage patch, validation sweep, and smoke transition with::

    .venv-ws4/bin/python -m workflows.ws4_instances.propagate_thin_tiled_30m

The module deliberately does not commit, push, copy annex content, or claim
adoption. Those are release steps outside this metadata patch.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data"
TILED_DIR = DATA_ROOT / "derived/ws4/tiled-30m"
NATIONAL_MANIFEST_PATH = TILED_DIR / "national_tiled_manifest.json"
PROPAGATION_PATH = TILED_DIR / "thin_propagation.json"
TILE_PATCH_FILENAME = "thin_patch.json"
SMOKE_DIR = DATA_ROOT / "derived/ws4/thin-transition-tiled-30m-smoke"
WS4_BIN = REPO_ROOT / ".venv-ws4/bin/ws4"

TABLES = (
    "fragments",
    "blocks",
    "tracks",
    "strata",
    "treatments",
    "curves",
    "features",
    "products",
    "protoaccounts",
    "accounts",
    "groups",
    "messages",
)
PATCHED_TABLES = (
    "tracks",
    "strata",
    "treatments",
    "features",
    "products",
    "protoaccounts",
    "accounts",
)
UNCHANGED_TABLES = ("fragments", "blocks", "curves", "groups", "messages")

AREA_CURVE_ID = 1
THIN_TREATMENT = "THIN"
CC_TREATMENT = "CC"
THIN_BEGIN = 20
THIN_END = 40
THIN_OFFSET = -1
THIN_RETAIN = 0
CC_BEGIN = 40
CC_END = 1000
CC_OFFSET = 0
CC_RETAIN = 0

TRACK_PATTERN = re.compile(
    r"^AU\((?P<au>[^)]+)\)"
    r"\+IFM\((?P<ifm>[^)]+)\)"
    r"\+ORIGIN\((?P<origin>[^)]+)\)"
    r"\+SILV_STATE\((?P<silv_state>[^)]+)\)"
    r"\+CURVE\((?P<curve_key>[^)]+)\)"
    r"\+treatment\(\)$"
)

TRACK_COLUMNS = ["track_id", "name"]
STRATA_COLUMNS = ["track_id", "offset", "next_age", "cycle"]
TREATMENT_COLUMNS = [
    "track_id",
    "begin",
    "end",
    "treatment",
    "new_track",
    "offset",
    "retain",
]
FEATURE_COLUMNS = ["track_id", "label", "curve_id"]
PRODUCT_COLUMNS = ["track_id", "treatment", "label", "curve_id"]
ACCOUNT_COLUMNS = ["group", "attribute", "account", "sum"]

TABLE_DTYPES: dict[str, dict[str, str]] = {
    "tracks": {"track_id": "int64", "name": "string"},
    "strata": {
        "track_id": "int64",
        "offset": "int64",
        "next_age": "int64",
        "cycle": "int64",
    },
    "treatments": {
        "track_id": "int64",
        "begin": "int64",
        "end": "int64",
        "treatment": "string",
        "new_track": "int64",
        "offset": "int64",
        "retain": "int64",
    },
    "features": {"track_id": "int64", "label": "string", "curve_id": "int64"},
    "products": {
        "track_id": "int64",
        "treatment": "string",
        "label": "string",
        "curve_id": "int64",
    },
    "accounts": {
        "group": "string",
        "attribute": "string",
        "account": "string",
        "sum": "float64",
    },
    "protoaccounts": {
        "group": "string",
        "attribute": "string",
        "account": "string",
        "sum": "float64",
    },
}


class ThinPropagationError(RuntimeError):
    """Raised when a tile or the national patch cannot be proven correct."""


@dataclass(frozen=True, slots=True)
class TrackIdentity:
    track_id: int
    name: str
    au: str
    ifm: str
    origin: str
    silv_state: str
    curve_key: str


@dataclass(frozen=True, slots=True)
class TrackPair:
    natural: TrackIdentity
    managed: TrackIdentity
    thinned_id: int
    thinned_name: str


@dataclass(slots=True)
class PatchPlan:
    status: str
    frames: dict[str, pd.DataFrame]
    pairs: list[TrackPair]
    deltas: dict[str, int]
    before_counts: dict[str, int]
    after_counts: dict[str, int]
    cc_contract_before: str


# ---------------------------------------------------------------------------
# Small deterministic helpers.
# ---------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            text = json.dumps(
                payload, indent=2, sort_keys=True, allow_nan=False
            ) + "\n"
            handle.write(text)
            digest.update(text.encode("utf-8"))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return digest.hexdigest()


def write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    """Write a frame over a path, replacing an annex symlink rather than its target."""
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(fd)
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def table_fingerprint(path: Path) -> dict[str, Any]:
    """Return a cheap content identity for an unchanged or patched table.

    Annexed files store the content hash in the symlink target, so large
    fragments/blocks need no read. Regular files are hashed.
    """
    stat = path.stat()
    if path.is_symlink():
        target = os.readlink(path)
        return {
            "kind": "annex",
            "key": Path(target).name,
            "bytes": int(stat.st_size),
        }
    return {
        "kind": "sha256",
        "sha256": sha256_file(path),
        "bytes": int(stat.st_size),
    }


def table_sha256(path: Path) -> str:
    return sha256_file(path)


def row_count(path: Path) -> int:
    return int(pq.ParquetFile(path).metadata.num_rows)


def relative_to_repo(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def git_status(repository: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ThinPropagationError(
            f"cannot inspect git status in {repository}: {result.stderr.strip()}"
        )
    return result.stdout


def assert_no_unexpected_deletions(
    repositories: Sequence[Path] = (REPO_ROOT, DATA_ROOT),
) -> None:
    """Halt on any ``D`` in either porcelain status column."""
    incidents: list[str] = []
    for repository in repositories:
        for line in git_status(repository).splitlines():
            if len(line) < 2:
                continue
            if line[0] == "D" or line[1] == "D":
                incidents.append(f"{repository}: {line}")
    if incidents:
        raise ThinPropagationError(
            "unexpected deletion status detected; halting: "
            + " | ".join(incidents)
        )


def parse_track_name(name: str) -> TrackIdentity | None:
    match = TRACK_PATTERN.match(str(name))
    if match is None:
        return None
    values = match.groupdict()
    return TrackIdentity(
        track_id=-1,
        name=str(name),
        au=values["au"],
        ifm=values["ifm"],
        origin=values["origin"],
        silv_state=values["silv_state"],
        curve_key=values["curve_key"],
    )


def track_name(
    *, au: str, curve_key: str, silv_state: str, ifm: str, origin: str
) -> str:
    return (
        f"AU({au})+IFM({ifm})+ORIGIN({origin})"
        f"+SILV_STATE({silv_state})+CURVE({curve_key})+treatment()"
    )


def coerce_frame(table: str, frame: pd.DataFrame) -> pd.DataFrame:
    columns = {
        "tracks": TRACK_COLUMNS,
        "strata": STRATA_COLUMNS,
        "treatments": TREATMENT_COLUMNS,
        "features": FEATURE_COLUMNS,
        "products": PRODUCT_COLUMNS,
        "accounts": ACCOUNT_COLUMNS,
        "protoaccounts": ACCOUNT_COLUMNS,
    }[table]
    if list(frame.columns) != columns:
        raise ThinPropagationError(
            f"{table}: columns {list(frame.columns)} != {columns}"
        )
    return frame.astype(TABLE_DTYPES[table]).reset_index(drop=True)


def frames_equal(table: str, left: pd.DataFrame, right: pd.DataFrame) -> bool:
    return coerce_frame(table, left).equals(coerce_frame(table, right))


def assert_no_nan(table: str, frame: pd.DataFrame) -> None:
    if frame.isna().any().any():
        raise ThinPropagationError(f"{table}: NaN/NA values are not allowed")


def assert_unique(
    table: str, frame: pd.DataFrame, columns: Sequence[str]
) -> None:
    duplicated = frame.duplicated(list(columns), keep=False)
    if duplicated.any():
        sample = frame.loc[duplicated, list(columns)].head(5).to_dict("records")
        raise ThinPropagationError(
            f"{table}: duplicate keys on {list(columns)}: {sample}"
        )


# ---------------------------------------------------------------------------
# Track graph and THIN planning.
# ---------------------------------------------------------------------------


def identify_tracks(tracks: pd.DataFrame) -> list[TrackIdentity]:
    if tracks.empty:
        raise ThinPropagationError("tracks table is empty")
    identities: list[TrackIdentity] = []
    for row in tracks.itertuples(index=False):
        identity = parse_track_name(str(row.name))
        if identity is None:
            raise ThinPropagationError(f"unparseable track name: {row.name!r}")
        identities.append(
            TrackIdentity(
                track_id=int(row.track_id),
                name=str(row.name),
                au=identity.au,
                ifm=identity.ifm,
                origin=identity.origin,
                silv_state=identity.silv_state,
                curve_key=identity.curve_key,
            )
        )
    assert_unique("tracks", tracks, ["track_id"])
    assert_unique("tracks", tracks, ["name"])
    return identities


def pair_natural_and_managed(
    identities: Sequence[TrackIdentity],
) -> tuple[list[TrackPair], list[TrackIdentity]]:
    """Pair each natural baseline track with its managed counterpart."""
    grouped: dict[tuple[str, str], dict[str, list[TrackIdentity]]] = {}
    thinned: list[TrackIdentity] = []
    for identity in identities:
        if (
            identity.ifm == "unmanaged"
            and identity.origin == "natural"
            and identity.silv_state == "baseline"
        ):
            state = "natural"
        elif (
            identity.ifm == "managed"
            and identity.origin == "planted"
            and identity.silv_state == "cc_pl"
        ):
            state = "managed"
        elif (
            identity.ifm == "unmanaged"
            and identity.origin == "natural"
            and identity.silv_state == "thinned"
        ):
            thinned.append(identity)
            continue
        else:
            raise ThinPropagationError(
                "unexpected track state, expected natural/managed/thinned: "
                f"{identity.name!r}"
            )
        grouped.setdefault((identity.au, identity.curve_key), {}).setdefault(
            state, []
        ).append(identity)

    pairs: list[TrackPair] = []
    used_thinned_ids: set[int] = set()
    for key in sorted(grouped):
        states = grouped[key]
        naturals = states.get("natural", [])
        manageds = states.get("managed", [])
        if len(naturals) != 1 or len(manageds) != 1:
            raise ThinPropagationError(
                f"AU/curve {key} has {len(naturals)} natural and "
                f"{len(manageds)} managed tracks; expected one each"
            )
        pairs.append(
            TrackPair(
                natural=naturals[0],
                managed=manageds[0],
                thinned_id=-1,
                thinned_name="",
            )
        )
    if not pairs:
        raise ThinPropagationError("no natural/managed track pairs found")

    natural_ids = {pair.natural.track_id for pair in pairs}
    managed_ids = {pair.managed.track_id for pair in pairs}
    if natural_ids & managed_ids:
        raise ThinPropagationError("a track id is both natural and managed")
    if thinned:
        by_name = {item.name: item for item in thinned}
        if len(by_name) != len(thinned):
            raise ThinPropagationError("duplicate thinned track names")
    return pairs, thinned


def _feature_by_track(features: pd.DataFrame) -> dict[tuple[int, str], int]:
    lookup: dict[tuple[int, str], int] = {}
    for row in features.itertuples(index=False):
        key = (int(row.track_id), str(row.label))
        if key in lookup:
            raise ThinPropagationError(f"features: duplicate row {key}")
        lookup[key] = int(row.curve_id)
    return lookup


def _product_by_track(
    products: pd.DataFrame,
) -> dict[tuple[int, str, str], int]:
    lookup: dict[tuple[int, str, str], int] = {}
    for row in products.itertuples(index=False):
        key = (int(row.track_id), str(row.treatment), str(row.label))
        if key in lookup:
            raise ThinPropagationError(f"products: duplicate row {key}")
        lookup[key] = int(row.curve_id)
    return lookup


def _natural_yield_curves(
    pairs: Sequence[TrackPair], features: pd.DataFrame
) -> dict[int, int]:
    lookup = _feature_by_track(features)
    curves: dict[int, int] = {}
    for pair in pairs:
        key = (pair.natural.track_id, "feature.Yield.unmanaged.Total")
        if key not in lookup:
            raise ThinPropagationError(
                f"natural track {pair.natural.track_id} lacks {key[1]!r}"
            )
        curves[pair.natural.track_id] = lookup[key]
    return curves


def _validate_area_curve(curves: pd.DataFrame) -> None:
    area = curves[curves["curve_id"].eq(AREA_CURVE_ID)]
    if area.empty:
        raise ThinPropagationError("curves: area curve 1 is missing")
    if not (area["y"] == 1.0).all():
        raise ThinPropagationError("curves: area curve 1 must have y == 1.0")


def _expected_thin_track_rows(
    pairs: Sequence[TrackPair], *, first_track_id: int
) -> tuple[list[TrackPair], list[dict[str, Any]], list[dict[str, Any]]]:
    assigned: list[TrackPair] = []
    track_rows: list[dict[str, Any]] = []
    strata_rows: list[dict[str, Any]] = []
    next_id = first_track_id
    for pair in sorted(pairs, key=lambda item: item.natural.track_id):
        name = track_name(
            au=pair.natural.au,
            curve_key=pair.natural.curve_key,
            silv_state="thinned",
            ifm="unmanaged",
            origin="natural",
        )
        assigned.append(
            TrackPair(
                natural=pair.natural,
                managed=pair.managed,
                thinned_id=next_id,
                thinned_name=name,
            )
        )
        track_rows.append({"track_id": next_id, "name": name})
        strata_rows.append(
            {"track_id": next_id, "offset": 0, "next_age": 300, "cycle": 0}
        )
        next_id += 1
    return assigned, track_rows, strata_rows


def _append_missing_rows(
    table: str,
    frame: pd.DataFrame,
    rows: Sequence[Mapping[str, Any]],
    key_columns: Sequence[str],
) -> pd.DataFrame:
    if not rows:
        return frame
    existing = set(
        frame[list(key_columns)]
        .itertuples(index=False, name=None)
    )
    additions = [
        dict(row)
        for row in rows
        if tuple(row[column] for column in key_columns) not in existing
    ]
    if not additions:
        return frame
    return coerce_frame(table, pd.concat([frame, pd.DataFrame(additions)], ignore_index=True))


def _strip_thin_rows(
    frames: dict[str, pd.DataFrame], thinned_ids: set[int]
) -> dict[str, pd.DataFrame]:
    """Remove only THIN-derived rows, preserving natural/managed CC rows."""
    stripped = {name: frame.copy() for name, frame in frames.items()}
    stripped["tracks"] = stripped["tracks"][
        ~stripped["tracks"]["track_id"].isin(thinned_ids)
    ]
    stripped["strata"] = stripped["strata"][
        ~stripped["strata"]["track_id"].isin(thinned_ids)
    ]
    stripped["treatments"] = stripped["treatments"][
        ~(
            stripped["treatments"]["track_id"].isin(thinned_ids)
            | stripped["treatments"]["new_track"].isin(thinned_ids)
            | stripped["treatments"]["treatment"].eq(THIN_TREATMENT)
        )
    ]
    stripped["features"] = stripped["features"][
        ~stripped["features"]["track_id"].isin(thinned_ids)
    ]
    stripped["products"] = stripped["products"][
        ~(
            stripped["products"]["track_id"].isin(thinned_ids)
            | stripped["products"]["treatment"].eq(THIN_TREATMENT)
        )
    ]
    for table in (
        "tracks",
        "strata",
        "treatments",
        "features",
        "products",
        "accounts",
        "protoaccounts",
    ):
        stripped[table] = coerce_frame(table, stripped[table])
    return stripped


def _complete_thin_tile(
    frames: Mapping[str, pd.DataFrame],
    pairs: Sequence[TrackPair],
    thinned: Sequence[TrackIdentity],
    natural_curves: Mapping[int, int],
) -> bool:
    """Return True only when the tile already has the full expected THIN rows."""
    if len(thinned) != len(pairs):
        return False
    thinned_by_key: dict[tuple[str, str], TrackIdentity] = {}
    for identity in thinned:
        thinned_by_key[(identity.au, identity.curve_key)] = identity
    if len(thinned_by_key) != len(thinned):
        return False

    track_ids = set(frames["tracks"]["track_id"])
    strata_by_track = {
        int(row.track_id): (
            int(row.offset),
            int(row.next_age),
            int(row.cycle),
        )
        for row in frames["strata"].itertuples(index=False)
    }
    features = _feature_by_track(frames["features"])
    products = _product_by_track(frames["products"])
    treatment_by_key = {
        (int(row.track_id), str(row.treatment)): (
            int(row.begin),
            int(row.end),
            int(row.new_track),
            int(row.offset),
            int(row.retain),
        )
        for row in frames["treatments"].itertuples(index=False)
    }
    if len(treatment_by_key) != len(frames["treatments"]):
        return False

    for pair in pairs:
        identity = thinned_by_key.get((pair.natural.au, pair.natural.curve_key))
        if identity is None:
            return False
        expected_name = track_name(
            au=pair.natural.au,
            curve_key=pair.natural.curve_key,
            silv_state="thinned",
            ifm="unmanaged",
            origin="natural",
        )
        if identity.name != expected_name or identity.track_id not in track_ids:
            return False
        thin_id = identity.track_id
        if strata_by_track.get(thin_id) != (0, 300, 0):
            return False
        expected_treatments = {
            (pair.natural.track_id, THIN_TREATMENT): (
                THIN_BEGIN,
                THIN_END,
                thin_id,
                THIN_OFFSET,
                THIN_RETAIN,
            ),
            (thin_id, THIN_TREATMENT): (
                THIN_BEGIN,
                THIN_END,
                thin_id,
                THIN_OFFSET,
                THIN_RETAIN,
            ),
            (thin_id, CC_TREATMENT): (
                CC_BEGIN,
                CC_END,
                pair.managed.track_id,
                CC_OFFSET,
                CC_RETAIN,
            ),
            (pair.managed.track_id, THIN_TREATMENT): (
                THIN_BEGIN,
                THIN_END,
                pair.managed.track_id,
                THIN_OFFSET,
                THIN_RETAIN,
            ),
        }
        for key, values in expected_treatments.items():
            if treatment_by_key.get(key) != values:
                return False
        curve = natural_curves[pair.natural.track_id]
        if features.get((thin_id, "feature.Yield.thinned.Total")) != curve:
            return False
        expected_products = {
            (pair.natural.track_id, THIN_TREATMENT, "product.HarvestedVolume.unmanaged.THIN"): curve,
            (pair.natural.track_id, THIN_TREATMENT, "product.Treated.unmanaged.THIN"): AREA_CURVE_ID,
            (thin_id, THIN_TREATMENT, "product.HarvestedVolume.thinned.THIN"): curve,
            (thin_id, THIN_TREATMENT, "product.Treated.thinned.THIN"): AREA_CURVE_ID,
            (thin_id, CC_TREATMENT, "product.HarvestedVolume.thinned.CC"): curve,
            (thin_id, CC_TREATMENT, "product.Treated.thinned.CC"): AREA_CURVE_ID,
            (pair.managed.track_id, THIN_TREATMENT, "product.HarvestedVolume.managed.THIN"): curve,
            (pair.managed.track_id, THIN_TREATMENT, "product.Treated.managed.THIN"): AREA_CURVE_ID,
        }
        for key, expected_curve in expected_products.items():
            if products.get(key) != expected_curve:
                return False
    return True


def rebuild_accounts(
    features: pd.DataFrame, products: pd.DataFrame
) -> pd.DataFrame:
    labels = sorted(
        set(features["label"].astype(str)) | set(products["label"].astype(str))
    )
    return pd.DataFrame(
        {
            "group": ["_ALL_"] * len(labels),
            "attribute": labels,
            "account": labels,
            "sum": [1.0] * len(labels),
        }
    ).astype(TABLE_DTYPES["accounts"])


def _cc_contract(
    frames: Mapping[str, pd.DataFrame],
    *,
    table_fingerprints: Mapping[str, Mapping[str, Any]],
) -> str:
    """Hash the natural/managed CC baseline contract, excluding THIN rows."""
    tracks = frames["tracks"]
    natural_managed_ids = set(
        tracks.loc[
            tracks["name"].str.contains(
                r"\+SILV_STATE\((?:baseline|cc_pl)\)", regex=True
            ),
            "track_id",
        ]
    )
    cc_treatments = frames["treatments"][
        frames["treatments"]["treatment"].eq(CC_TREATMENT)
        & frames["treatments"]["track_id"].isin(natural_managed_ids)
    ].sort_values(list(TREATMENT_COLUMNS))
    strata = frames["strata"][
        frames["strata"]["track_id"].isin(natural_managed_ids)
    ].sort_values(list(STRATA_COLUMNS))
    features = frames["features"][
        frames["features"]["track_id"].isin(natural_managed_ids)
    ].sort_values(list(FEATURE_COLUMNS))
    products = frames["products"][
        frames["products"]["track_id"].isin(natural_managed_ids)
        & frames["products"]["treatment"].eq(CC_TREATMENT)
    ].sort_values(list(PRODUCT_COLUMNS))
    payload = {
        "tracks": tracks[tracks["track_id"].isin(natural_managed_ids)]
        .sort_values(list(TRACK_COLUMNS))
        .to_dict("records"),
        "strata": strata.to_dict("records"),
        "treatments": cc_treatments.to_dict("records"),
        "features": features.to_dict("records"),
        "products": products.to_dict("records"),
        "unchanged_tables": {
            name: dict(fingerprint)
            for name, fingerprint in sorted(table_fingerprints.items())
            if name in UNCHANGED_TABLES
        },
    }
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def plan_thin_patch(
    frames: dict[str, pd.DataFrame],
    *,
    table_fingerprints: Mapping[str, Mapping[str, Any]],
) -> PatchPlan:
    """Plan one tile's metadata patch without touching disk."""
    for table in PATCHED_TABLES:
        frames[table] = coerce_frame(table, frames[table])
    for table in PATCHED_TABLES:
        assert_no_nan(table, frames[table])
    _validate_area_curve(frames["curves"])
    if not frames["curves"]["curve_id"].eq(AREA_CURVE_ID).any():
        raise ThinPropagationError("curves: area curve is missing")

    before_counts = {name: len(frames[name]) for name in PATCHED_TABLES}
    identities = identify_tracks(frames["tracks"])
    pairs, thinned = pair_natural_and_managed(identities)
    natural_curves = _natural_yield_curves(pairs, frames["features"])
    cc_contract_before = _cc_contract(
        frames, table_fingerprints=table_fingerprints
    )

    if thinned and _complete_thin_tile(
        frames, pairs, thinned, natural_curves
    ):
        return PatchPlan(
            status="skipped",
            frames=frames,
            pairs=[],
            deltas={name: 0 for name in PATCHED_TABLES},
            before_counts=before_counts,
            after_counts=before_counts,
            cc_contract_before=cc_contract_before,
        )

    working = frames
    if thinned:
        working = _strip_thin_rows(
            frames, {identity.track_id for identity in thinned}
        )
        working_identities = identify_tracks(working["tracks"])
        pairs, remaining_thinned = pair_natural_and_managed(
            working_identities
        )
        if remaining_thinned:
            raise ThinPropagationError(
                "repair left thinned tracks behind; refusing to continue"
            )
        natural_curves = _natural_yield_curves(pairs, working["features"])

    first_track_id = int(working["tracks"]["track_id"].max()) + 1
    assigned, thin_tracks, thin_strata = _expected_thin_track_rows(
        pairs, first_track_id=first_track_id
    )

    treatment_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    product_rows: list[dict[str, Any]] = []
    for pair in assigned:
        thin_id = pair.thinned_id
        curve = natural_curves[pair.natural.track_id]
        treatment_rows.extend(
            [
                {
                    "track_id": pair.natural.track_id,
                    "begin": THIN_BEGIN,
                    "end": THIN_END,
                    "treatment": THIN_TREATMENT,
                    "new_track": thin_id,
                    "offset": THIN_OFFSET,
                    "retain": THIN_RETAIN,
                },
                {
                    "track_id": thin_id,
                    "begin": THIN_BEGIN,
                    "end": THIN_END,
                    "treatment": THIN_TREATMENT,
                    "new_track": thin_id,
                    "offset": THIN_OFFSET,
                    "retain": THIN_RETAIN,
                },
                {
                    "track_id": thin_id,
                    "begin": CC_BEGIN,
                    "end": CC_END,
                    "treatment": CC_TREATMENT,
                    "new_track": pair.managed.track_id,
                    "offset": CC_OFFSET,
                    "retain": CC_RETAIN,
                },
                {
                    "track_id": pair.managed.track_id,
                    "begin": THIN_BEGIN,
                    "end": THIN_END,
                    "treatment": THIN_TREATMENT,
                    "new_track": pair.managed.track_id,
                    "offset": THIN_OFFSET,
                    "retain": THIN_RETAIN,
                },
            ]
        )
        feature_rows.append(
            {
                "track_id": thin_id,
                "label": "feature.Yield.thinned.Total",
                "curve_id": curve,
            }
        )
        product_rows.extend(
            [
                {
                    "track_id": pair.natural.track_id,
                    "treatment": THIN_TREATMENT,
                    "label": "product.HarvestedVolume.unmanaged.THIN",
                    "curve_id": curve,
                },
                {
                    "track_id": pair.natural.track_id,
                    "treatment": THIN_TREATMENT,
                    "label": "product.Treated.unmanaged.THIN",
                    "curve_id": AREA_CURVE_ID,
                },
                {
                    "track_id": thin_id,
                    "treatment": THIN_TREATMENT,
                    "label": "product.HarvestedVolume.thinned.THIN",
                    "curve_id": curve,
                },
                {
                    "track_id": thin_id,
                    "treatment": THIN_TREATMENT,
                    "label": "product.Treated.thinned.THIN",
                    "curve_id": AREA_CURVE_ID,
                },
                {
                    "track_id": thin_id,
                    "treatment": CC_TREATMENT,
                    "label": "product.HarvestedVolume.thinned.CC",
                    "curve_id": curve,
                },
                {
                    "track_id": thin_id,
                    "treatment": CC_TREATMENT,
                    "label": "product.Treated.thinned.CC",
                    "curve_id": AREA_CURVE_ID,
                },
                {
                    "track_id": pair.managed.track_id,
                    "treatment": THIN_TREATMENT,
                    "label": "product.HarvestedVolume.managed.THIN",
                    "curve_id": curve,
                },
                {
                    "track_id": pair.managed.track_id,
                    "treatment": THIN_TREATMENT,
                    "label": "product.Treated.managed.THIN",
                    "curve_id": AREA_CURVE_ID,
                },
            ]
        )

    result = {name: frame.copy() for name, frame in working.items()}
    result["tracks"] = _append_missing_rows(
        "tracks", result["tracks"], thin_tracks, ["track_id"]
    )
    result["strata"] = _append_missing_rows(
        "strata", result["strata"], thin_strata, ["track_id"]
    )
    result["treatments"] = _append_missing_rows(
        "treatments",
        result["treatments"],
        treatment_rows,
        ["track_id", "treatment"],
    )
    result["features"] = _append_missing_rows(
        "features",
        result["features"],
        feature_rows,
        ["track_id", "label"],
    )
    result["products"] = _append_missing_rows(
        "products",
        result["products"],
        product_rows,
        ["track_id", "treatment", "label"],
    )
    result["accounts"] = rebuild_accounts(result["features"], result["products"])
    result["protoaccounts"] = result["accounts"].copy()
    for table in PATCHED_TABLES:
        result[table] = coerce_frame(table, result[table])

    _assert_tile_integrity(result)
    after_counts = {name: len(result[name]) for name in PATCHED_TABLES}
    deltas = {
        name: after_counts[name] - before_counts[name] for name in PATCHED_TABLES
    }
    return PatchPlan(
        status="repaired" if thinned else "patched",
        frames=result,
        pairs=assigned,
        deltas=deltas,
        before_counts=before_counts,
        after_counts=after_counts,
        cc_contract_before=cc_contract_before,
    )


def _assert_tile_integrity(frames: Mapping[str, pd.DataFrame]) -> None:
    """Fail closed on a broken reference graph or incomplete THIN state."""
    for table in PATCHED_TABLES:
        assert_no_nan(table, frames[table])
    track_ids = set(frames["tracks"]["track_id"])
    if 0 in track_ids:
        raise ThinPropagationError("tracks: track id 0 is reserved")
    assert_unique("tracks", frames["tracks"], ["track_id"])
    assert_unique("tracks", frames["tracks"], ["name"])
    assert_unique("strata", frames["strata"], ["track_id"])
    assert_unique("features", frames["features"], ["track_id", "label"])
    assert_unique(
        "products", frames["products"], ["track_id", "treatment", "label"]
    )
    assert_unique(
        "treatments",
        frames["treatments"],
        ["track_id", "treatment"],
    )
    for table in ("strata", "treatments", "features", "products"):
        unknown = set(frames[table]["track_id"]) - track_ids
        if unknown:
            raise ThinPropagationError(
                f"{table}: unknown track_id values {sorted(unknown)[:5]}"
            )
    unknown_new = set(frames["treatments"]["new_track"]) - track_ids
    if unknown_new:
        raise ThinPropagationError(
            f"treatments: unknown new_track values {sorted(unknown_new)[:5]}"
        )
    curve_ids = set(frames["curves"]["curve_id"])
    for table in ("features", "products"):
        unknown = set(frames[table]["curve_id"]) - curve_ids
        if unknown:
            raise ThinPropagationError(
                f"{table}: unknown curve_id values {sorted(unknown)[:5]}"
            )
    if set(frames["strata"]["track_id"]) != track_ids:
        raise ThinPropagationError("strata: one row per track is required")
    accounts = frames["accounts"]
    labels = set(frames["features"]["label"].astype(str)) | set(
        frames["products"]["label"].astype(str)
    )
    if set(accounts["account"].astype(str)) != labels:
        raise ThinPropagationError(
            "accounts: account labels do not match feature/product labels"
        )
    if not (accounts["sum"] == 1.0).all():
        raise ThinPropagationError("accounts: sum must be 1.0")
    if not (accounts["group"] == "_ALL_").all():
        raise ThinPropagationError("accounts: group must be _ALL_")
    if not accounts["attribute"].astype(str).equals(
        accounts["account"].astype(str)
    ):
        raise ThinPropagationError(
            "accounts: attribute must equal account"
        )
    if not frames["protoaccounts"].equals(frames["accounts"]):
        raise ThinPropagationError(
            "protoaccounts: must equal accounts"
        )
    identities = identify_tracks(frames["tracks"])
    pairs, thinned = pair_natural_and_managed(identities)
    natural_curves = _natural_yield_curves(pairs, frames["features"])
    if not _complete_thin_tile(frames, pairs, thinned, natural_curves):
        raise ThinPropagationError(
            "tile: THIN tracks/treatments/features/products are incomplete"
        )


# ---------------------------------------------------------------------------
# Per-tile disk patch.
# ---------------------------------------------------------------------------


def load_patch_frames(dataset_dir: Path) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for table in PATCHED_TABLES:
        frames[table] = pd.read_parquet(dataset_dir / f"{table}.parquet")
    for table in ("curves",):
        frames[table] = pd.read_parquet(dataset_dir / f"{table}.parquet")
    return frames


def patch_tile(
    *,
    slug: str,
    node_id: str,
    dataset_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Patch one tile dataset and write its per-tile patch record."""
    started = time.perf_counter()
    required = [dataset_dir / f"{name}.parquet" for name in TABLES]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise ThinPropagationError(
            f"{slug}: missing dataset tables {missing}"
        )
    before_counts = {
        name: row_count(dataset_dir / f"{name}.parquet") for name in TABLES
    }
    before_fingerprints = {
        name: table_fingerprint(dataset_dir / f"{name}.parquet")
        for name in TABLES
    }
    before_hashes = {
        name: table_sha256(dataset_dir / f"{name}.parquet")
        for name in PATCHED_TABLES
    }
    frames = load_patch_frames(dataset_dir)
    plan = plan_thin_patch(
        frames,
        table_fingerprints={
            name: before_fingerprints[name] for name in UNCHANGED_TABLES
        },
    )

    changed_tables: list[str] = []
    if not dry_run:
        for table in PATCHED_TABLES:
            if frames_equal(table, plan.frames[table], frames[table]):
                continue
            write_parquet_atomic(
                plan.frames[table], dataset_dir / f"{table}.parquet"
            )
            changed_tables.append(table)

    after_fingerprints = {
        name: table_fingerprint(dataset_dir / f"{name}.parquet")
        for name in TABLES
    }
    unchanged_ok = all(
        before_fingerprints[name] == after_fingerprints[name]
        for name in UNCHANGED_TABLES
    )
    if not unchanged_ok:
        raise ThinPropagationError(
            f"{slug}: an unchanged table changed on disk"
        )

    after_hashes = {
        name: table_sha256(dataset_dir / f"{name}.parquet")
        for name in PATCHED_TABLES
    }
    after_counts = {
        name: row_count(dataset_dir / f"{name}.parquet") for name in TABLES
    }
    if not dry_run and any(
        after_counts[name] != plan.after_counts[name]
        for name in PATCHED_TABLES
    ):
        raise ThinPropagationError(
            f"{slug}: written row counts do not match the plan"
        )

    cc_contract_after = _cc_contract(
        plan.frames,
        table_fingerprints={
            name: after_fingerprints[name] for name in UNCHANGED_TABLES
        },
    )
    if plan.cc_contract_before != cc_contract_after:
        raise ThinPropagationError(
            f"{slug}: natural/managed CC baseline contract changed"
        )

    patch_payload = {
        "tile_slug": slug,
        "tables": {name: after_hashes[name] for name in PATCHED_TABLES},
    }
    patch_sha = sha256_bytes(canonical_json(patch_payload).encode("utf-8"))
    deltas = {
        name: after_counts[name] - before_counts[name]
        for name in PATCHED_TABLES
    }
    record = {
        "artifact": "ws4_tiled_30m_thin_patch_v1",
        "tile_slug": slug,
        "node_id": node_id,
        "status": plan.status if not dry_run else "dry_run",
        "mode": "metadata_only",
        "dataset_path": relative_to_repo(dataset_dir),
        "patch_sha256": patch_sha,
        "counts_before": before_counts,
        "counts_after": after_counts,
        "deltas": deltas,
        "table_sha256": after_hashes,
        "table_fingerprints": after_fingerprints,
        "unchanged_tables": list(UNCHANGED_TABLES),
        "natural_fragments_blocks_unchanged": unchanged_ok,
        "cc_contract_sha256_before": plan.cc_contract_before,
        "cc_contract_sha256_after": cc_contract_after,
        "changed_tables": changed_tables,
        "validation": {"status": "pending"},
        "labels": {
            "evidence_label": "model_estimate",
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "official_osecan_changed": False,
            "chain_v51_unchanged": True,
            "adoption_claimed": False,
        },
        "notes": [
            "metadata-only THIN patch; fragments, blocks, curves, groups, and messages are unchanged",
            "natural/managed track ids and CC rows are preserved; thinned tracks are appended",
        ],
        "patched_utc": utc_now(),
    }
    if not dry_run:
        write_json_atomic(dataset_dir.parent / TILE_PATCH_FILENAME, record)
    record["wall_seconds"] = round(time.perf_counter() - started, 3)
    return record


def update_tile_validation(
    *, slug: str, validation: Mapping[str, Any]
) -> None:
    path = TILED_DIR / slug / TILE_PATCH_FILENAME
    record = json.loads(path.read_text(encoding="utf-8"))
    record["validation"] = dict(validation)
    write_json_atomic(path, record)


# ---------------------------------------------------------------------------
# Validation sweep.
# ---------------------------------------------------------------------------


def validate_tile(slug: str, dataset_dir: Path, *, timeout: int = 900) -> dict[str, Any]:
    started = time.perf_counter()
    command = [
        str(WS4_BIN),
        "validate",
        "--dataset",
        str(dataset_dir),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "fail",
            "exit_code": None,
            "seconds": round(time.perf_counter() - started, 3),
            "error": f"ws4 validate timed out after {timeout}s",
        }
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if result.returncode != 0:
        return {
            "status": "fail",
            "exit_code": int(result.returncode),
            "seconds": round(time.perf_counter() - started, 3),
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
        }
    return {
        "status": "pass",
        "exit_code": int(result.returncode),
        "seconds": round(time.perf_counter() - started, 3),
    }


def run_validation_sweep(
    tiles: Sequence[Mapping[str, Any]], *, workers: int
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    total = len(tiles)
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {
            pool.submit(
                validate_tile, str(tile["tile_slug"]), REPO_ROOT / str(tile["dataset_path"])
            ): str(tile["tile_slug"])
            for tile in tiles
        }
        for done, future in enumerate(futures.as_completed(pending), start=1):
            slug = pending[future]
            try:
                results[slug] = future.result()
            except Exception as error:  # pragma: no cover - defensive
                results[slug] = {
                    "status": "fail",
                    "exit_code": None,
                    "seconds": 0.0,
                    "error": f"{type(error).__name__}: {error}",
                }
            if done % 100 == 0 or done == total:
                failures = sum(
                    1 for item in results.values() if item["status"] != "pass"
                )
                print(
                    f"validate {done}/{total}: failures={failures}",
                    flush=True,
                )
    return results


# ---------------------------------------------------------------------------
# Smoke transition on one patched tile.
# ---------------------------------------------------------------------------


def choose_smoke_tile(
    tiles: Sequence[Mapping[str, Any]],
) -> tuple[str, Path, int]:
    """Return the first tile with blocks in the THIN age window."""
    preferred = {"A24-ON-S11"}
    ordered = sorted(
        tiles,
        key=lambda tile: (
            str(tile["tile_slug"]) not in preferred,
            str(tile["tile_slug"]),
        ),
    )
    for tile in ordered:
        dataset_dir = REPO_ROOT / str(tile["dataset_path"])
        offsets = pd.read_parquet(
            dataset_dir / "blocks.parquet", columns=["offset"]
        )["offset"]
        eligible = int(offsets.between(THIN_BEGIN, THIN_END).sum())
        if eligible > 0:
            return str(tile["tile_slug"]), dataset_dir, eligible
    raise ThinPropagationError(
        "no tiled dataset has a block in the THIN age window"
    )


def run_thin_smoke(
    *, slug: str, dataset_dir: Path, max_blocks: int, years: int
) -> dict[str, Any]:
    command = [
        str(REPO_ROOT / ".venv-ws4/bin/python"),
        "-m",
        "workflows.ws4_instances.run_ws4_thin_transition",
        "--dataset",
        str(dataset_dir),
        "--out",
        str(SMOKE_DIR),
        "--max-blocks",
        str(max_blocks),
        "--years",
        str(years),
    ]
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ThinPropagationError(
            "THIN smoke failed:\n"
            f"stdout:\n{result.stdout[-4000:]}\n"
            f"stderr:\n{result.stderr[-4000:]}"
        )
    summary = json.loads((SMOKE_DIR / "summary.json").read_text(encoding="utf-8"))
    if summary.get("status") != "pass":
        raise ThinPropagationError(
            f"THIN smoke summary status is {summary.get('status')!r}"
        )
    evidence = summary["evidence"]
    if evidence["thin_harvested_volume_m3"] <= 0.0:
        raise ThinPropagationError("THIN smoke harvested no volume")
    if not (
        abs(
            evidence["retained_thinned_value_at_offset_0"]
            - evidence["expected_thin_volume_m3"]
        )
        <= max(1e-6, 1e-9 * evidence["expected_thin_volume_m3"])
    ):
        raise ThinPropagationError(
            "THIN smoke did not retain the pre-treatment volume"
        )
    return {
        "status": "pass",
        "tile_slug": slug,
        "dataset_path": relative_to_repo(dataset_dir),
        "out_dir": relative_to_repo(SMOKE_DIR),
        "command": command,
        "summary_path": relative_to_repo(SMOKE_DIR / "summary.json"),
        "evidence": evidence,
        "simulation": summary.get("simulation", {}),
        "labels": summary.get("labels", {}),
    }


# ---------------------------------------------------------------------------
# National manifest and cumulative propagation report.
# ---------------------------------------------------------------------------


def completed_tiles(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = manifest.get("completed_tiles")
    if not isinstance(rows, list) or not rows:
        raise ThinPropagationError(
            "national manifest has no completed_tiles"
        )
    seen: set[str] = set()
    for row in rows:
        slug = str(row["tile_slug"])
        if slug in seen:
            raise ThinPropagationError(
                f"national manifest repeats tile {slug}"
            )
        seen.add(slug)
    return rows


def update_national_manifest(
    manifest: dict[str, Any],
    records: Mapping[str, Mapping[str, Any]],
    *,
    validation: Mapping[str, Any],
    smoke: Mapping[str, Any],
    counts: Mapping[str, int],
) -> dict[str, Any]:
    rows = completed_tiles(manifest)
    missing = [
        str(row["tile_slug"])
        for row in rows
        if str(row["tile_slug"]) not in records
    ]
    if missing:
        raise ThinPropagationError(
            f"national manifest tiles without patch records: {missing[:5]}"
        )
    patch_hashes: dict[str, str] = {}
    thin_totals = {
        "tracks_added": 0,
        "strata_added": 0,
        "treatments_added": 0,
        "features_added": 0,
        "products_added": 0,
        "account_rows_added": 0,
    }
    for row in rows:
        slug = str(row["tile_slug"])
        record = records[slug]
        patch_hash = str(record["patch_sha256"])
        patch_hashes[slug] = patch_hash
        deltas = record["deltas"]
        row["tracks"] = int(record["counts_after"]["tracks"])
        row["strata"] = int(record["counts_after"]["strata"])
        row["treatments"] = int(record["counts_after"]["treatments"])
        row["features"] = int(record["counts_after"]["features"])
        row["products"] = int(record["counts_after"]["products"])
        row["accounts"] = int(record["counts_after"]["accounts"])
        row["thin_patch_sha256"] = patch_hash
        row["thin_patch_record"] = (
            f"{record['dataset_path'].rsplit('/dataset', 1)[0]}/{TILE_PATCH_FILENAME}"
        )
        row["thin_tracks_added"] = int(deltas["tracks"])
        row["thin_strata_added"] = int(deltas["strata"])
        row["thin_treatments_added"] = int(deltas["treatments"])
        row["thin_features_added"] = int(deltas["features"])
        row["thin_products_added"] = int(deltas["products"])
        row["thin_account_rows_added"] = int(deltas["accounts"])
        row["thin_validation_status"] = validation["tiles"][slug]["status"]
        row["thin_validation_exit_code"] = validation["tiles"][slug]["exit_code"]
        row["thin_validation_seconds"] = validation["tiles"][slug]["seconds"]
        thin_totals["tracks_added"] += int(deltas["tracks"])
        thin_totals["strata_added"] += int(deltas["strata"])
        thin_totals["treatments_added"] += int(deltas["treatments"])
        thin_totals["features_added"] += int(deltas["features"])
        thin_totals["products_added"] += int(deltas["products"])
        thin_totals["account_rows_added"] += int(deltas["accounts"])

    patch_hashes_sha = sha256_bytes(
        canonical_json(patch_hashes).encode("utf-8")
    )
    manifest["thin_propagated"] = True
    manifest["thin_tiles"] = len(rows)
    manifest["thin_skipped_tiles"] = int(counts["skipped"])
    manifest["thin_failed_tiles"] = int(counts["failed"])
    manifest["thin_totals"] = thin_totals
    manifest["thin_patch_hashes"] = patch_hashes
    manifest["thin_patch_hashes_sha256"] = patch_hashes_sha
    manifest["thin_validation"] = {
        "command": ".venv-ws4/bin/ws4 validate --dataset <tile>/dataset",
        "workers": int(validation["workers"]),
        "pass": int(validation["pass"]),
        "fail": int(validation["fail"]),
        "tiles": int(validation["tiles_count"]),
    }
    manifest["thin_smoke"] = dict(smoke)
    manifest["thin_propagated_utc"] = utc_now()
    manifest["thin_note"] = (
        "Metadata-only THIN propagation: one thinned track/strata row, "
        "THIN/CC treatments, feature/products, and accounts were appended "
        "to each built tile. Natural fragments, blocks, curves, groups, and "
        "messages are unchanged; natural/managed track ids and CC rows are "
        "unchanged. No full-coverage CC result was rerun or replaced."
    )
    manifest.setdefault("labels", {})["thin_propagated"] = True
    manifest.setdefault("full_run", {})["status"] = "complete"
    manifest["full_run"]["thin_propagated"] = True
    manifest["full_run"]["thin_tiles"] = len(rows)
    return manifest


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


def run(
    *,
    workers: int = 32,
    tile_slugs: Sequence[str] | None = None,
    do_validate: bool = True,
    do_smoke: bool = True,
    do_update_national: bool = True,
    max_blocks: int = 20,
    years: int = 20,
) -> dict[str, Any]:
    started = time.perf_counter()
    assert_no_unexpected_deletions()
    if not NATIONAL_MANIFEST_PATH.is_file():
        raise ThinPropagationError(
            f"national manifest is missing: {NATIONAL_MANIFEST_PATH}"
        )
    manifest = json.loads(
        NATIONAL_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    rows = completed_tiles(manifest)
    by_slug = {str(row["tile_slug"]): row for row in rows}
    selected_slugs = (
        [str(slug) for slug in tile_slugs]
        if tile_slugs
        else sorted(by_slug)
    )
    unknown = [slug for slug in selected_slugs if slug not in by_slug]
    if unknown:
        raise ThinPropagationError(
            f"requested tiles are not in the national manifest: {unknown[:5]}"
        )
    if len(set(selected_slugs)) != len(selected_slugs):
        raise ThinPropagationError("requested tile slugs contain duplicates")

    print(
        f"patching {len(selected_slugs)} tile datasets with {workers} workers",
        flush=True,
    )
    records: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    with futures.ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {
            pool.submit(
                patch_tile,
                slug=slug,
                node_id=str(by_slug[slug]["node_id"]),
                dataset_dir=REPO_ROOT / str(by_slug[slug]["dataset_path"]),
            ): slug
            for slug in selected_slugs
        }
        for done, future in enumerate(futures.as_completed(pending), start=1):
            slug = pending[future]
            try:
                records[slug] = future.result()
            except Exception as error:
                failures[slug] = f"{type(error).__name__}: {error}"
                print(f"FAILED {slug}: {failures[slug]}", file=sys.stderr, flush=True)
            if done % 100 == 0 or done == len(selected_slugs):
                print(
                    f"patch {done}/{len(selected_slugs)}: "
                    f"failures={len(failures)}",
                    flush=True,
                )
    if failures:
        raise ThinPropagationError(
            f"{len(failures)} tile patches failed: "
            + " | ".join(f"{key}: {value}" for key, value in sorted(failures.items())[:10])
        )

    counts = Counter(record["status"] for record in records.values())
    patch_counts = {
        "patched": int(counts.get("patched", 0) + counts.get("repaired", 0)),
        "skipped": int(counts.get("skipped", 0)),
        "failed": len(failures),
    }
    if patch_counts["patched"] + patch_counts["skipped"] != len(selected_slugs):
        raise ThinPropagationError(
            f"unexpected patch status counts: {dict(counts)}"
        )
    selected_rows = [by_slug[slug] for slug in selected_slugs]

    validation: dict[str, Any] | None = None
    if do_validate:
        print(
            f"validating {len(selected_slugs)} tiles with {workers} workers",
            flush=True,
        )
        validation_tiles = run_validation_sweep(
            selected_rows, workers=workers
        )
        selected_validation = {
            slug: validation_tiles[slug] for slug in selected_slugs
        }
        passed = sum(
            1 for item in selected_validation.values() if item["status"] == "pass"
        )
        failed = len(selected_validation) - passed
        validation = {
            "workers": workers,
            "pass": passed,
            "fail": failed,
            "tiles_count": len(selected_validation),
            "tiles": selected_validation,
        }
        for slug in selected_slugs:
            update_tile_validation(
                slug=slug, validation=selected_validation[slug]
            )
        if failed:
            failing = [
                slug
                for slug, item in selected_validation.items()
                if item["status"] != "pass"
            ]
            raise ThinPropagationError(
                f"{failed} tile validations failed: {failing[:10]}"
            )

    smoke: dict[str, Any] | None = None
    if do_smoke:
        smoke_slug, smoke_dir, eligible = choose_smoke_tile(selected_rows)
        print(
            f"THIN smoke on {smoke_slug} ({eligible} eligible blocks)",
            flush=True,
        )
        smoke = run_thin_smoke(
            slug=smoke_slug,
            dataset_dir=smoke_dir,
            max_blocks=max_blocks,
            years=years,
        )

    manifest_sha = None
    if do_update_national:
        if validation is None or smoke is None:
            raise ThinPropagationError(
                "national update requires validation and smoke evidence"
            )
        if set(selected_slugs) != set(by_slug):
            raise ThinPropagationError(
                "national manifest update requires every completed tile"
            )
        assert_no_unexpected_deletions()
        manifest = update_national_manifest(
            manifest,
            records,
            validation=validation,
            smoke=smoke,
            counts=patch_counts,
        )
        manifest_sha = write_json_atomic(NATIONAL_MANIFEST_PATH, manifest)

    tile_records = {
        slug: {
            "status": record["status"],
            "patch_sha256": record["patch_sha256"],
            "counts_after": record["counts_after"],
            "deltas": record["deltas"],
            "natural_fragments_blocks_unchanged": record[
                "natural_fragments_blocks_unchanged"
            ],
            "cc_contract_sha256": record["cc_contract_sha256_after"],
            "validation": record.get("validation", {"status": "pending"}),
        }
        for slug, record in sorted(records.items())
    }
    propagation = {
        "artifact": "ws4_tiled_30m_thin_propagation_v1",
        "status": "pass",
        "generated_utc": utc_now(),
        "scope": {
            "coverage": "full national tiled 30-m WS4 coverage",
            "tiles_expected": len(by_slug),
            "tiles_processed": len(selected_slugs),
            "tile_resolution_m": 30000,
            "inventory_resolution_m": 30,
        },
        "mode": "metadata_only",
        "counts": {
            "patched": patch_counts["patched"],
            "skipped": patch_counts["skipped"],
            "failed": patch_counts["failed"],
            "tiles": len(selected_slugs),
        },
        "deltas": {
            key: sum(int(record["deltas"][key]) for record in records.values())
            for key in (
                "tracks",
                "strata",
                "treatments",
                "features",
                "products",
                "accounts",
                "protoaccounts",
            )
        },
        "tracks_added": sum(
            int(record["deltas"]["tracks"]) for record in records.values()
        ),
        "treatments_added": sum(
            int(record["deltas"]["treatments"]) for record in records.values()
        ),
        "patch_hashes": {
            slug: record["patch_sha256"]
            for slug, record in sorted(records.items())
        },
        "patch_hashes_sha256": sha256_bytes(
            canonical_json(
                {
                    slug: record["patch_sha256"]
                    for slug, record in sorted(records.items())
                }
            ).encode("utf-8")
        ),
        "table_sha256": {
            slug: record["table_sha256"]
            for slug, record in sorted(records.items())
        },
        "validation": (
            None
            if validation is None
            else {
                "command": ".venv-ws4/bin/ws4 validate --dataset <tile>/dataset",
                "workers": validation["workers"],
                "pass": validation["pass"],
                "fail": validation["fail"],
                "tiles": validation["tiles_count"],
            }
        ),
        "smoke": smoke,
        "manifest": {
            "path": relative_to_repo(NATIONAL_MANIFEST_PATH),
            "sha256": manifest_sha,
        },
        "tiles": tile_records,
        "labels": {
            "evidence_label": "model_estimate",
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "official_osecan_changed": False,
            "chain_v51_unchanged": True,
            "adoption_claimed": False,
        },
        "notes": [
            "Metadata-only patch; the 3,345 datasets were not rebuilt.",
            "Natural fragments, blocks, curves, groups, and messages are unchanged.",
            "Natural/managed track ids and CC treatment rows are preserved.",
            "No full-coverage CC result was rerun or replaced.",
            "THIN is a state transition with the same RSYC curve; no thinning response is claimed.",
            "This is not an adoption or promotion decision.",
        ],
        "wall_seconds": round(time.perf_counter() - started, 3),
    }
    if do_update_national:
        write_json_atomic(PROPAGATION_PATH, propagation)
    print(
        json.dumps(
            {
                "counts": propagation["counts"],
                "deltas": propagation["deltas"],
                "validation": propagation["validation"],
                "smoke": {
                    key: propagation["smoke"]["evidence"][key]
                    for key in (
                        "thin_harvested_volume_m3",
                        "retained_thinned_value_at_offset_0",
                        "expected_thin_volume_m3",
                        "thinned_cc_harvested_volume_m3",
                        "scheduled_area_ha",
                    )
                }
                if propagation["smoke"]
                else None,
                "manifest_sha256": manifest_sha,
                "wall_seconds": propagation["wall_seconds"],
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return propagation


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument(
        "--tiles",
        nargs="*",
        default=None,
        help="Patch only these tile slugs (development aid).",
    )
    parser.add_argument(
        "--no-validate", action="store_false", dest="validate", default=True
    )
    parser.add_argument(
        "--no-smoke", action="store_false", dest="smoke", default=True
    )
    parser.add_argument(
        "--no-update-national",
        action="store_false",
        dest="update_national",
        default=True,
    )
    parser.add_argument("--max-blocks", type=int, default=20)
    parser.add_argument("--years", type=int, default=20)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        run(
            workers=args.workers,
            tile_slugs=args.tiles,
            do_validate=args.validate,
            do_smoke=args.smoke,
            do_update_national=args.update_national,
            max_blocks=args.max_blocks,
            years=args.years,
        )
    except ThinPropagationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
