"""Parse BC Timber Sales public auction-result pages.

The public result page is a legacy ASP form.  A timber-sale-licence (TSL)
lookup returns the sale's upset rate and every bid received, including the
winning bid.  This module reads that page and never invents a bid where the
page has none.

The page reports a *bonus* bid: the amount offered above the upset rate.  The
winning bid used here is therefore ``upset + bonus``.  For innovative lump-sum
sales the page reports an upset value and a bonus offer in dollars; both are
converted to per-cubic-metre rates with the sale volume printed on the same
page.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

INDEX_URL = "https://www.for.gov.bc.ca/mof/wapr/bcts02/"
RESULTS_URL = "https://www.for.gov.bc.ca/pscripts/mof/wapr/bcts02/results.asp"
RESULT_QUERY_URL = RESULTS_URL + "?timbersale={tsl}"
SOURCE_ID = "prov_bcts_auction_results"
SOURCE_NAME = "BC Timber Sales public sale results"
LICENCE_URL = (
    "https://www2.gov.bc.ca/gov/content/data/open-data/open-government-licence-bc"
)

USER_AGENT = "clews-c2070-fresh/0.1 (BCTS bid-results audit)"
_RATE_QUANTUM = Decimal("0.0001")
_INELIGIBLE_STATUSES = frozenset({"INELIGIBLE", "DISQUALIFIED"})

_AUCTION_DATE_RE = re.compile(
    r"Auction Date\.\s*([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})"
)
_LOCATION_RE = re.compile(r"Location:\.+\s*(.*?)\s+Volume:")
_VOLUME_RE = re.compile(r"Volume:\.+\s*([\d,]+)\s*/m3", re.IGNORECASE)
_UPSET_RATE_RE = re.compile(
    r"Upset Rate:\.+\s*\$([\d,]+(?:\.\d+)?)\s*/m3", re.IGNORECASE
)
_UPSET_VALUE_RE = re.compile(r"Upset Value:\.?\s*\$([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_NOT_FOUND_RE = re.compile(r"Timber Sale\s+(\S+)\s+not found", re.IGNORECASE)
_STATUS_ECHO_RE = re.compile(r"Timber Sale Status for\s+(\S+)")
_BID_RE = re.compile(
    r"(?:Status:\.+\s*(?P<status>[A-Z][A-Z ]*?)\s+)?"
    r"Client Number:\s*\d+\s+"
    r"Client Name:\s*.*?\s+"
    r"Bonus (?:Bid|Offer):[.\s]*\$?(?P<bonus>[\d,]+(?:\.\d+)?)"
    r"(?P<per_m3>\s*/m3)?"
)
_BONUS_LABEL_RE = re.compile(r"Bonus (?:Bid|Offer):", re.IGNORECASE)


class BctsBidDataError(ValueError):
    """The result page is not the shape this parser was written for."""


@dataclass(frozen=True, slots=True)
class Bid:
    """One bid entry; the client identity is deliberately not retained."""

    status: str
    bonus: Decimal
    unit: str

    @property
    def is_eligible(self) -> bool:
        return self.status not in _INELIGIBLE_STATUSES


@dataclass(frozen=True, slots=True)
class ParsedSale:
    tsl: str
    found: bool
    auction_date: date | None
    location: str
    volume_m3: Decimal | None
    auction_type: str | None
    upset_cad_m3: Decimal | None
    upset_total_cad: Decimal | None
    results_final: bool
    no_bids: bool
    bids: tuple[Bid, ...]
    source_sha256: str | None
    source_url: str

    @property
    def winner(self) -> Bid | None:
        awarded = [bid for bid in self.bids if bid.status == "AWARDED"]
        pending = [bid for bid in self.bids if bid.status == "PENDING APPROVAL"]
        if len(awarded) > 1:
            raise BctsBidDataError(
                f"{self.tsl} reports {len(awarded)} AWARDED bids; expected at most one"
            )
        if awarded:
            return awarded[0]
        if pending:
            return pending[0]
        return None

    @property
    def status(self) -> str:
        if not self.found:
            return "NOT_FOUND"
        if self.bids:
            if any(bid.status == "AWARDED" for bid in self.bids):
                return "AWARDED"
            if any(bid.status == "PENDING APPROVAL" for bid in self.bids):
                return "PENDING_APPROVAL"
            return "UNVERIFIED_BIDS"
        if self.no_bids:
            return "NO_BIDS" if self.results_final else "NO_BIDS_PENDING"
        return "NO_BIDS" if self.results_final else "NO_RESULT"

    @property
    def bidders(self) -> int | None:
        if not self.found:
            return None
        if self.no_bids:
            return 0
        return sum(1 for bid in self.bids if bid.is_eligible)

    @property
    def upset_rate_cad_m3(self) -> Decimal | None:
        if self.upset_cad_m3 is not None:
            return _quantize_rate(self.upset_cad_m3)
        if self.upset_total_cad is None or not self.volume_m3:
            return None
        return _quantize_rate(self.upset_total_cad / self.volume_m3)

    @property
    def bonus_rate_cad_m3(self) -> Decimal | None:
        winner = self.winner
        if winner is None:
            return None
        if winner.unit == "per_m3":
            return _quantize_rate(winner.bonus)
        if not self.volume_m3:
            return None
        return _quantize_rate(winner.bonus / self.volume_m3)

    @property
    def winning_rate_cad_m3(self) -> Decimal | None:
        upset = self.upset_rate_cad_m3
        bonus = self.bonus_rate_cad_m3
        if upset is None or bonus is None:
            return None
        return _quantize_rate(upset + bonus)

    @property
    def confidence(self) -> str:
        if not self.found:
            return "low"
        if self.status == "AWARDED":
            return "medium" if self.auction_type == "lump_sum" else "high"
        if self.status == "PENDING_APPROVAL":
            return "medium"
        if self.status in {"NO_BIDS", "NO_RESULT"}:
            return "high"
        return "medium"


def _quantize_rate(value: Decimal) -> Decimal:
    return value.quantize(_RATE_QUANTUM, rounding=ROUND_HALF_UP)


def _decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation as exc:
        raise BctsBidDataError(f"not a decimal number: {raw!r}") from exc


def _page_text(html: str) -> str:
    """Flatten the legacy page into one space-separated line."""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</(?:p|li|tr|div|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return " ".join(line for line in lines if line)


def _parse_auction_date(raw: str, tsl: str) -> date:
    for fmt in ("%A, %B %d, %Y", "%A %B %d, %Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    raise BctsBidDataError(f"{tsl}: unparseable auction date {raw!r}")


def parse_bid_results_html(
    html: str, tsl: str, *, page_sha256: str | None = None
) -> ParsedSale:
    """Parse one result page.  Missing headers fail loudly for found sales."""
    if not html.strip():
        raise BctsBidDataError(f"{tsl}: empty result page")

    text = _page_text(html)
    source_url = RESULT_QUERY_URL.format(tsl=urllib.parse.quote(tsl))

    if _NOT_FOUND_RE.search(text):
        return ParsedSale(
            tsl=tsl,
            found=False,
            auction_date=None,
            location="",
            volume_m3=None,
            auction_type=None,
            upset_cad_m3=None,
            upset_total_cad=None,
            results_final=False,
            no_bids=False,
            bids=(),
            source_sha256=page_sha256,
            source_url=source_url,
        )

    echo = _STATUS_ECHO_RE.search(text)
    if echo is None or echo.group(1) != tsl:
        raise BctsBidDataError(f"{tsl}: page does not echo a matching sale id")

    date_match = _AUCTION_DATE_RE.search(text)
    volume_match = _VOLUME_RE.search(text)
    if date_match is None or volume_match is None:
        raise BctsBidDataError(f"{tsl}: missing auction date or sale volume")

    auction_date = _parse_auction_date(date_match.group(1), tsl)
    volume_m3 = _decimal(volume_match.group(1))
    if volume_m3 <= 0:
        raise BctsBidDataError(f"{tsl}: sale volume must be positive")

    upset_rate_match = _UPSET_RATE_RE.search(text)
    upset_value_match = _UPSET_VALUE_RE.search(text)
    is_lump_sum_page = "Lump Sum" in text
    if upset_rate_match and upset_value_match:
        raise BctsBidDataError(f"{tsl}: page has both a rate and a lump-sum upset")
    if upset_rate_match:
        auction_type = "per_m3"
        upset_cad_m3 = _decimal(upset_rate_match.group(1))
        upset_total_cad = None
    elif upset_value_match:
        auction_type = "lump_sum"
        upset_cad_m3 = None
        upset_total_cad = _decimal(upset_value_match.group(1))
    elif is_lump_sum_page:
        auction_type = "lump_sum"
        upset_cad_m3 = None
        upset_total_cad = None
    else:
        raise BctsBidDataError(f"{tsl}: missing upset rate or upset value")

    bids = tuple(
        Bid(
            status=(match.group("status") or "").strip(),
            bonus=_decimal(match.group("bonus")),
            unit="per_m3"
            if match.group("per_m3")
            else ("lump_sum" if auction_type == "lump_sum" else "per_m3"),
        )
        for match in _BID_RE.finditer(text)
    )
    bonus_labels = len(_BONUS_LABEL_RE.findall(text))
    if bonus_labels != len(bids):
        raise BctsBidDataError(
            f"{tsl}: found {bonus_labels} bonus labels but parsed {len(bids)} bids"
        )

    no_bids = "No Bids Received" in text
    if no_bids and bids:
        raise BctsBidDataError(f"{tsl}: page says no bids but also lists bids")

    location_match = _LOCATION_RE.search(text)
    return ParsedSale(
        tsl=tsl,
        found=True,
        auction_date=auction_date,
        location=location_match.group(1).strip() if location_match else "",
        volume_m3=volume_m3,
        auction_type=auction_type,
        upset_cad_m3=upset_cad_m3,
        upset_total_cad=upset_total_cad,
        results_final="Auction results have been approved" in text,
        no_bids=no_bids,
        bids=bids,
        source_sha256=page_sha256,
        source_url=source_url,
    )


def fetch_result_bytes(
    tsl: str,
    *,
    timeout: int = 30,
    attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """Fetch one result page with bounded retries and a descriptive failure."""
    url = RESULT_QUERY_URL.format(tsl=urllib.parse.quote(tsl))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < attempts:
                sleep(1.5 * attempt)
    raise BctsBidDataError(f"{tsl}: could not fetch {url}: {last_error}")


def fetch_and_parse(tsl: str, **kwargs: Any) -> ParsedSale:
    raw = fetch_result_bytes(tsl, **kwargs)
    digest = hashlib.sha256(raw).hexdigest()
    return parse_bid_results_html(raw.decode("latin-1"), tsl, page_sha256=digest)


def collect_bid_results(
    tsls: Sequence[str],
    *,
    fetcher: Callable[[str], ParsedSale] = fetch_and_parse,
    delay_seconds: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[ParsedSale, ...]:
    """Fetch results in input order with a polite fixed delay."""
    seen: set[str] = set()
    results: list[ParsedSale] = []
    for tsl in tsls:
        if tsl in seen:
            raise BctsBidDataError(f"duplicate TSL in request list: {tsl}")
        seen.add(tsl)
        results.append(fetcher(tsl))
        if delay_seconds > 0:
            sleep(delay_seconds)
    return tuple(results)
