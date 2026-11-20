#!/usr/bin/env python3
"""Fetch BC Timber Sales auction results for the staged BCTS sale schedule.

Run from the repository root::

    .venv/bin/python scripts/national/fetch_bcts_bid_results.py

The script queries the public Timber Sale Status page once per unique TSL,
parses the winning bid, upset rate, bonus, and bidder count, and writes:

* ``data/derived/national/harvest_basis/bcts_bc/bcts_bid_results.csv``
* ``data/derived/national/harvest_basis/bcts_bc/bcts_bid_ledger.csv``
* ``data/derived/national/harvest_basis/bcts_bc/bcts_bid_results_provenance.json``

It does not infer a bid where the page has none and it does not treat a bid as
a delivered log price.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(REPO_ROOT))

from workflows.national.harvest import bcts_bc, bcts_bids
from workflows.national.harvest.cost_revenue import load_ledger

DERIVED = REPO_ROOT / "data/derived/national/harvest_basis/bcts_bc"
SCHEDULE_PATH = DERIVED / "bcts_sales.csv"
RESULTS_PATH = DERIVED / "bcts_bid_results.csv"
LEDGER_PATH = DERIVED / "bcts_bid_ledger.csv"
PROVENANCE_PATH = DERIVED / "bcts_bid_results_provenance.json"

RESULT_FIELDS: tuple[str, ...] = (
    "licence_id",
    "tsl",
    "status",
    "auction_date",
    "winning_bid_cad_m3",
    "upset_cad_m3",
    "bonus_cad_m3",
    "bidders",
    "source_url",
    "confidence",
    "results_final",
    "auction_type",
    "site_status",
    "location",
    "site_volume_m3",
    "bid_entries",
    "schedule_auction_date",
    "schedule_status",
    "schedule_block_ids",
    "schedule_block_count",
    "schedule_volume_m3",
    "schedule_date_shift_days",
    "source_sha256",
    "fetched_utc",
    "notes",
)


def read_schedule(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def unique_licences(schedule: Sequence[Mapping[str, str]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for row in schedule:
        licence = row["LICENCE_ID"]
        if licence not in seen:
            seen.add(licence)
            ordered.append(licence)
    return ordered


def group_blocks(
    schedule: Sequence[Mapping[str, str]],
) -> dict[str, tuple[Mapping[str, str], ...]]:
    grouped: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in schedule:
        grouped[row["LICENCE_ID"]].append(row)
    return {licence: tuple(rows) for licence, rows in grouped.items()}


def _schedule_date(blocks: Sequence[Mapping[str, str]]) -> date:
    return min(date.fromisoformat(row["AUCTION_DATE"]) for row in blocks)


def _rate_text(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.4f}"


def _result_notes(
    sale: bcts_bids.ParsedSale,
    status: str,
    date_shift_days: int | None,
) -> str:
    tokens = [
        "source=bcts_public_sale_results",
        "winning_bid=upset_plus_bonus",
        "bonus_is_premium=1",
        "bid_is_not_delivered_revenue=1",
        "volume_is_sale_weight_not_harvest=1",
        "currency_basis=CAD_nominal",
    ]
    if sale.auction_type == "lump_sum":
        tokens.append("conversion=lump_sum_to_per_m3")
        tokens.append("conversion_source=sale_page_volume")
    if sale.upset_total_cad is None and sale.auction_type == "lump_sum":
        tokens.append("upset_value_missing_on_page=1")
    if date_shift_days not in (None, 0):
        tokens.append(f"schedule_date_shift_days={date_shift_days}")
    if status == "STALE_RESULT":
        tokens.append("stale_schedule_link=1")
    return "; ".join(tokens)


def build_result_rows(
    sales: Sequence[bcts_bids.ParsedSale],
    blocks_by_licence: Mapping[str, Sequence[Mapping[str, str]]],
    *,
    fetched_utc: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sale in sales:
        blocks = blocks_by_licence.get(sale.tsl)
        if not blocks:
            raise bcts_bids.BctsBidDataError(
                f"{sale.tsl}: result has no matching sale-schedule block"
            )

        schedule_date = _schedule_date(blocks)
        date_shift = (
            (sale.auction_date - schedule_date).days if sale.auction_date else None
        )
        status = sale.status
        confidence = sale.confidence
        if date_shift is not None and date_shift < -180:
            status = "STALE_RESULT"
            confidence = "low"

        rows.append(
            {
                "licence_id": sale.tsl,
                "tsl": sale.tsl,
                "status": status,
                "auction_date": (
                    sale.auction_date.isoformat() if sale.auction_date else ""
                ),
                "winning_bid_cad_m3": _rate_text(sale.winning_rate_cad_m3),
                "upset_cad_m3": _rate_text(sale.upset_rate_cad_m3),
                "bonus_cad_m3": _rate_text(sale.bonus_rate_cad_m3),
                "bidders": "" if sale.bidders is None else sale.bidders,
                "source_url": sale.source_url,
                "confidence": confidence,
                "results_final": "1" if sale.results_final else "0",
                "auction_type": sale.auction_type or "",
                "site_status": (
                    "AWARDED"
                    if sale.status == "AWARDED"
                    else "PENDING APPROVAL"
                    if sale.status == "PENDING_APPROVAL"
                    else "NO BIDS"
                    if "NO_BIDS" in sale.status
                    else "NOT FOUND"
                    if sale.status == "NOT_FOUND"
                    else "NO RESULT"
                ),
                "location": sale.location,
                "site_volume_m3": (
                    "" if sale.volume_m3 is None else f"{sale.volume_m3:f}"
                ),
                "bid_entries": len(sale.bids),
                "schedule_auction_date": schedule_date.isoformat(),
                "schedule_status": "|".join(
                    sorted({row["AUCTION_STATUS"] for row in blocks})
                ),
                "schedule_block_ids": "|".join(
                    sorted(row["BLOCK_ID"] for row in blocks)
                ),
                "schedule_block_count": len(blocks),
                "schedule_volume_m3": f"{sum(Decimal(row['BLOCK_VOLUME']) for row in blocks):.6f}",
                "schedule_date_shift_days": ("" if date_shift is None else date_shift),
                "source_sha256": sale.source_sha256 or "",
                "fetched_utc": fetched_utc,
                "notes": _result_notes(sale, status, date_shift),
            }
        )
    return rows


def _ledger_notes(sale: bcts_bids.ParsedSale, *, mirror: bool, has_base: bool) -> str:
    auction_date = sale.auction_date
    assert auction_date is not None
    tokens = [
        "currency_basis=CAD_nominal",
        "observed_year=" + str(auction_date.year),
        "auction_date=" + auction_date.isoformat(),
        "coverage=sample",
        "coverage_denominator=BC_BCTS_auctioned_sale_results",
        "volume_is_sale_weight_not_harvest=1",
        "bid_is_not_delivered_revenue=1",
        "premium=bonus_bid_direct",
    ]
    if has_base:
        tokens.append("base=upset")
    else:
        tokens.append("base=missing_upset_value")
    if sale.auction_type == "lump_sum":
        tokens.append("conversion=lump_sum_to_per_m3")
        tokens.append("conversion_source=sale_page_volume")
    if mirror:
        tokens.append("mirror=1")
    return "; ".join(tokens)


def build_ledger_rows(
    sales: Sequence[bcts_bids.ParsedSale],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sale in sales:
        if sale.status != "AWARDED" or not sale.results_final:
            continue
        if sale.auction_date is None or sale.volume_m3 is None:
            continue
        if sale.auction_date.year not in {2026, 2027}:
            continue

        upset = sale.upset_rate_cad_m3
        premium = sale.bonus_rate_cad_m3
        if premium is None:
            continue

        has_base = upset is not None
        components: list[tuple[str, Decimal]] = []
        if upset is not None:
            components.append(("stumpage_base", upset))
        components.append(("stumpage_bid_premium", premium))

        for component, value in components:
            for perspective, sign, mirror in (
                ("crown", 1, False),
                ("forest_sector", -1, True),
            ):
                rows.append(
                    {
                        "jurisdiction": "BC",
                        "source": bcts_bids.SOURCE_ID,
                        "year": sale.auction_date.year,
                        "spatial_unit": f"BC-BCTS_{sale.tsl}",
                        "species": "all",
                        "product": "all",
                        "volume_m3": f"{sale.volume_m3:f}",
                        "unit": "CAD_per_m3_log",
                        "value_cad": f"{value:f}",
                        "value_basis": "observed_sale:standing",
                        "perspective": perspective,
                        "component": component,
                        "sign": sign,
                        "confidence": "medium"
                        if bcts_bids.SOURCE_ID.startswith("prov_")
                        else "high",
                        "licence": sale.tsl,
                        "notes": _ledger_notes(sale, mirror=mirror, has_base=has_base),
                    }
                )
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_with_cache(
    tsl: str,
    *,
    cache_dir: Path,
    delay_seconds: float,
    sleep_fn,
) -> bcts_bids.ParsedSale:
    cache_path = cache_dir / f"{tsl}.html"
    if cache_path.exists():
        raw = cache_path.read_bytes()
    else:
        raw = bcts_bids.fetch_result_bytes(tsl)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(raw)
        if delay_seconds > 0:
            sleep_fn(delay_seconds)
    digest = hashlib.sha256(raw).hexdigest()
    return bcts_bids.parse_bid_results_html(
        raw.decode("latin-1"), tsl, page_sha256=digest
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=REPO_ROOT / "tmp/bcts_bid_html",
        help="Raw result-page cache (not committed)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds between live requests",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore the raw-page cache and refetch every TSL",
    )
    args = parser.parse_args()

    schedule = read_schedule(SCHEDULE_PATH)
    tsls = unique_licences(schedule)
    blocks_by_licence = group_blocks(schedule)
    if len(tsls) != 99:
        raise bcts_bids.BctsBidDataError(
            f"expected 99 unique TSLs in the schedule, got {len(tsls)}"
        )

    fetched_utc = bcts_bc.utc_now()
    results: list[bcts_bids.ParsedSale] = []
    for tsl in tsls:
        if args.refresh:
            cache_path = args.cache_dir / f"{tsl}.html"
            if cache_path.exists():
                cache_path.unlink()
        results.append(
            _fetch_with_cache(
                tsl,
                cache_dir=args.cache_dir,
                delay_seconds=args.delay,
                sleep_fn=time.sleep,
            )
        )

    result_rows = build_result_rows(results, blocks_by_licence, fetched_utc=fetched_utc)
    bcts_bc.write_csv(RESULTS_PATH, RESULT_FIELDS, result_rows)

    ledger_rows = build_ledger_rows(results)
    if not ledger_rows:
        raise bcts_bids.BctsBidDataError("no awarded results to place in the ledger")
    bcts_bc.write_csv(LEDGER_PATH, tuple(ledger_rows[0]), ledger_rows)
    ledger = load_ledger(LEDGER_PATH)

    status_counts = Counter(row["status"] for row in result_rows)
    awarded_rows = [row for row in result_rows if row["status"] == "AWARDED"]
    block_count = sum(int(row["schedule_block_count"]) for row in result_rows)
    provenance = {
        "artifact_version": "0.1.0",
        "generated_utc": fetched_utc,
        "source": {
            "id": bcts_bids.SOURCE_ID,
            "name": bcts_bids.SOURCE_NAME,
            "index_url": bcts_bids.INDEX_URL,
            "results_url": bcts_bids.RESULTS_URL,
            "query_url_template": bcts_bids.RESULT_QUERY_URL,
            "access": (
                "HTTP GET with ?timbersale=<TSL>; the public form posts the same "
                "field to results.asp"
            ),
            "licence": "Open Government Licence - British Columbia",
            "licence_url": bcts_bids.LICENCE_URL,
        },
        "access_policy": {
            "delay_seconds": args.delay,
            "user_agent": bcts_bids.USER_AGENT,
            "personal_names_stored": False,
            "client_numbers_stored": False,
        },
        "counts": {
            "schedule_blocks": len(schedule),
            "unique_tsls_queried": len(tsls),
            "result_rows": len(result_rows),
            "schedule_blocks_covered": block_count,
            "awarded_sales": len(awarded_rows),
            "awarded_block_count": sum(
                int(row["schedule_block_count"]) for row in awarded_rows
            ),
            "bid_entries": sum(int(row["bid_entries"]) for row in result_rows),
            "eligible_bidders": sum(
                int(row["bidders"]) for row in result_rows if row["bidders"] != ""
            ),
            "status": dict(sorted(status_counts.items())),
        },
        "outputs": [
            {
                "path": str(path.relative_to(REPO_ROOT)),
                "rows": rows,
                "sha256": _sha256(path),
            }
            for path, rows in (
                (RESULTS_PATH, len(result_rows)),
                (LEDGER_PATH, len(ledger)),
            )
        ],
        "ledger": {
            "components": ["stumpage_base", "stumpage_bid_premium"],
            "perspectives": ["crown", "forest_sector"],
            "value_basis": "observed_sale:standing",
            "confidence": "medium",
            "note": (
                "Premium is the bonus bid; base is the upset. Mirrors are "
                "genuine Crown-to-sector transfers. A bid is not delivered "
                "revenue and the sale volume is a weight, not harvest."
            ),
        },
        "gaps": [
            "The public result page has no bidder identity requirement; personal names and client numbers are deliberately not stored.",
            "The result page is a legacy ASP service with no documented API or bulk export; this is a rate-limited page-by-page snapshot.",
            "Sales not yet approved return 'not found' or 'results not yet final'; absence is not a zero bid.",
            "Two awarded lump-sum sales (TA1945, TA1946) publish a bonus offer but no upset value, so only the premium is ledger-eligible.",
            "The schedule's planned auction date can differ from the approved result date; the result date is authoritative for the bid.",
            "BCTS is a selected auction sample, not the provincial harvest population.",
        ],
    }
    bcts_bc.write_json(PROVENANCE_PATH, provenance)

    print(f"Result rows: {len(result_rows)}")
    print(f"Status: {dict(sorted(status_counts.items()))}")
    print(f"Awarded sales: {len(awarded_rows)}")
    print(f"Ledger rows: {len(ledger)} (validated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
