#!/usr/bin/env python3
"""SAFORAH2 CSW harvest — emit per-keyword record manifests.

Walks the NRCan/Canadian Forest Service SAFORAH2 federated CSW 2.0.2 catalogue
and emits a single CSV manifest per invocation, with one row per
(keyword, record) hit. The script is stdlib-only and **lazy on network**:
imports do not perform any HTTP; the network step happens only inside
``main()``.

Endpoint: ``https://saforah2.nfis.org/geonetwork-main/srv/eng/csw`` (live,
OGC CSW 2.0.2, HTTP GET with CQL_TEXT query language for ``GetRecords``).

Method::

    GET /geonetwork-main/srv/eng/csw
        ?service=CSW&version=2.0.2&request=GetRecords
        &typenames=csw:Record&resulttype=results&elementsetname=brief
        &constraint=csw:AnyText%20like%20'%25<keyword>%25'
        &constraintLanguage=CQL_TEXT&constraint_language_version=1.1.0

Keywords (initial set): ``biomass``, ``species composition``, ``yield``,
``disturbance``, ``inventory``, ``land cover``, ``fire``.

Output schema (CSV columns):
``harvest_date,query_keyword,identifier,title,organisation,modified,abstract_snippet``.

Output path: ``metadata/discovery-snapshots/saforah2_<date>.csv``. The output
directory is created if it does not exist.

Usage::

    ./.venv/bin/python scripts/discovery/saforah2_csw_harvest.py [YYYY-MM-DD]

Default date = today (UTC, ISO 8601).

Errors are logged per keyword to stderr but do not abort the harvest
(other keywords still run). On HTTP 5xx a single retry is attempted after a
2-second sleep; on HTTP 4xx the keyword is logged and skipped.
"""

from __future__ import annotations

import csv
import datetime as _dt
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants (module-level for easy discovery)
# ---------------------------------------------------------------------------

# Live CSW endpoint — only neighbour URL out of 8 tried on 2026-08-09.
CSW_ENDPOINT = "https://saforah2.nfis.org/geonetwork-main/srv/eng/csw"

# Output dir relative to the repository root.
_OUTPUT_SUBDIR = Path("metadata") / "discovery-snapshots"

# OGC CSW 2.0.2 / ISO 19139 namespace prefixes commonly seen in
# GetRecords responses from a GeoNetwork 3.x backend.
_NS = {
    "csw": "http://www.opengis.net/cat/csw/2.0.2",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dct": "http://purl.org/dc/terms/",
    "ows": "http://www.opengis.net/ows",
}

# Initial keywords.
_KEYWORDS = (
    "biomass",
    "species composition",
    "yield",
    "disturbance",
    "inventory",
    "land cover",
    "fire",
)

# Runtime knobs.
_USER_AGENT = "clews-c2070-fresh saforah2-csw-harvest/1.0"
_HTTP_TIMEOUT_SEC = 20
_RETRY_SLEEP_SEC = 2


# ---------------------------------------------------------------------------
# Network helpers — explicitly NOT invoked at import
# ---------------------------------------------------------------------------

def _build_getrecords_url(keyword: str) -> str:
    """Build the CQL_TEXT GetRecords URL for a single keyword.

    ``%25`` is the percent-encoded form of ``%`` in URL-encoded form.
    ``urllib.parse.quote`` is used on the *literal* keyword so any
    embedded ``%``, spaces, or other reserved characters are escaped
    correctly before the CQL_STRING_LIKE wildcard characters (``%``)
    are added around it.
    """
    escaped_keyword = urllib.parse.quote(keyword)
    encoded_constraint = (
        "csw:AnyText%20like%20'%25" + escaped_keyword + "%25'"
    )
    params = {
        "service": "CSW",
        "version": "2.0.2",
        "request": "GetRecords",
        "typenames": "csw:Record",
        "resulttype": "results",
        "elementsetname": "brief",
        "constraint": encoded_constraint,
        "constraintLanguage": "CQL_TEXT",
        "constraint_language_version": "1.1.0",
    }
    return CSW_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _fetch_xml(url: str) -> str:
    """Fetch a CSW response body as text with one retry on 5xx.

    Raises urllib.error.HTTPError on 4xx (caller logs and continues).
    Raises urllib.error.URLError on persistent connection failure.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        if 500 <= exc.code < 600:
            time.sleep(_RETRY_SLEEP_SEC)
            req2 = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req2, timeout=_HTTP_TIMEOUT_SEC) as resp2:
                charset = resp2.headers.get_content_charset() or "utf-8"
                return resp2.read().decode(charset, errors="replace")
        raise


# ---------------------------------------------------------------------------
# XML → record-list adapter
# ---------------------------------------------------------------------------

def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _findtext(elem: ET.Element, path_triples):
    """Findtext helper that walks a list of (ns, localname) tuples."""
    for ns, local in path_triples:
        found = elem.find("{%s}%s" % (ns, local))
        if found is not None and found.text:
            return found.text.strip()
    return ""


def _parse_records(xml_payload: str):
    """Yield per-record dicts from a csw:GetRecords response body."""
    root = ET.fromstring(xml_payload)
    # csw:GetRecords - children: csw:SearchResults - children: csw:Record | dc:... proxies
    search_results = None
    for child in root:
        if _strip_ns(child.tag) == "SearchResults":
            search_results = child
            break
    if search_results is None:
        return
    for rec in search_results:
        if _strip_ns(rec.tag) == "Record" or _strip_ns(rec.tag) in (
            "Record", "MD_Metadata",
        ):
            yield _record_to_dict(rec, query_keyword_hint=None)


def _record_to_dict(rec: ET.Element, query_keyword_hint):
    """Pull the brief elementset columns out of one csw:Record."""
    identifier = _findtext(rec, [
        _NAMESPACES("dc", "identifier"),
    ]) or ""
    title = _findtext(rec, [
        _NAMESPACES("dc", "title"),
    ]) or ""
    org = _findtext(rec, [
        _NAMESPACES("dc", "creator"),
        _NAMESPACES("dct", "creator"),
        _NAMESPACES("dc", "publisher"),
    ]) or ""
    modified = _findtext(rec, [
        _NAMESPACES("dct", "modified"),
        _NAMESPACES("dc", "date"),
    ]) or ""
    abstract = _findtext(rec, [
        _NAMESPACES("dct", "abstract"),
        _NAMESPACES("dc", "description"),
    ]) or ""
    snippet = _snippet(abstract)
    return {
        "identifier": identifier,
        "title": _safe_csv(title),
        "organisation": _safe_csv(org),
        "modified": modified,
        "abstract_snippet": _safe_csv(snippet),
    }


def _NAMESPACES(prefix: str, local: str):
    return (_NS[prefix], local)


def _snippet(abstract: str, max_chars: int = 240) -> str:
    abstract = (abstract or "").strip()
    if len(abstract) <= max_chars:
        return abstract
    return abstract[: max_chars - 1].rstrip() + "…"


def _safe_csv(s: str) -> str:
    """Conservative cleanup; csv.writer handles quotation."""
    return (s or "").replace("\r", " ").replace("\n", " ").strip()


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

_OUTPUT_COLUMNS = [
    "harvest_date",
    "query_keyword",
    "identifier",
    "title",
    "organisation",
    "modified",
    "abstract_snippet",
]


def _output_path(harvest_date: str, repo_root: Path) -> Path:
    return repo_root / _OUTPUT_SUBDIR / f"saforah2_{harvest_date}.csv"


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def harvest(harvest_date: str, repo_root: Path):
    """Run one harvest; ``harvest_date`` is YYYY-MM-DD UTC ISO."""
    out_path = _output_path(harvest_date, repo_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for keyword in _KEYWORDS:
        url = _build_getrecords_url(keyword)
        print(
            f"[saforah2] keyword={keyword!r}  get={url[:80]}…",
            file=sys.stderr,
        )
        try:
            xml_payload = _fetch_xml(url)
        except urllib.error.HTTPError as exc:
            print(
                f"[saforah2] HTTP {exc.code} on keyword={keyword!r}; skipping",
                file=sys.stderr,
            )
            continue
        except urllib.error.URLError as exc:
            print(
                f"[saforah2] URLError {exc!r} on keyword={keyword!r}; skipping",
                file=sys.stderr,
            )
            continue
        except (ET.ParseError,) as exc:
            print(
                f"[saforah2] XML parse error on keyword={keyword!r}: {exc!r}; skipping",
                file=sys.stderr,
            )
            continue

        try:
            for rec in _parse_records(xml_payload):
                rows.append({
                    "harvest_date": harvest_date,
                    "query_keyword": keyword,
                    **rec,
                })
        except ET.ParseError as exc:
            print(
                f"[saforah2] XML parse error during record extraction "
                f"on keyword={keyword!r}: {exc!r}; skipping",
                file=sys.stderr,
            )
            continue

    # Write atomically-ish: write then rename. Single CSV per invocation;
    # the team's existing pattern prefers one file per harvest date.
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    tmp_path.replace(out_path)
    print(
        f"[saforah2] wrote {len(rows)} rows -> {out_path}",
        file=sys.stderr,
    )
    return out_path


def _today_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_repo_root(path: Path) -> bool:
    return (path / ".git").exists() or (path / "pyproject.toml").exists()


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    harvest_date = None
    repo_root = None
    explicit_root = False
    for arg in argv:
        if arg.startswith("--repo-root="):
            repo_root = Path(arg.split("=", 1)[1]).resolve()
            explicit_root = True
        elif _DATE_RE.match(arg):
            harvest_date = arg
        else:
            print(f"[saforah2] unrecognised argument: {arg!r}", file=sys.stderr)
            return 2

    if harvest_date is None:
        harvest_date = _today_utc()
    if repo_root is None:
        # Walk up from this script's directory until we find a repo-root
        # marker. We intentionally do NOT make network calls during this
        # walk; the path resolution is file-system-only.
        here = Path(__file__).resolve().parent
        candidate = here
        while candidate != candidate.parent:
            if _is_repo_root(candidate):
                repo_root = candidate
                break
            candidate = candidate.parent
        if repo_root is None:
            repo_root = Path.cwd()

    out_path = harvest(harvest_date, repo_root)
    # Final stdout line for caller (parsers can grep it).
    print(str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
