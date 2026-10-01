"""Tests for biorxiv_client.fetch_recent_papers."""

import json
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

import biorxiv_client

TODAY     = date.today()
YESTERDAY = TODAY - timedelta(days=1)
SERVERS   = ["biorxiv"]


def _api_response(items: list, total: int | None = None) -> bytes:
    """Build a fake CSHL API JSON response."""
    return json.dumps({
        "messages": [{"status": "ok", "total": total if total is not None else len(items), "cursor": "0"}],
        "collection": items,
    }).encode()


def _make_item(
    doi: str = "10.1101/2024.01.01.000001",
    title: str = "Test Paper",
    published: date = YESTERDAY,
    authors: str = "Smith, J; Jones, B",
    category: str = "genomics",
    abstract: str = "An abstract.",
    server: str = "biorxiv",
) -> dict:
    return {
        "doi":      doi,
        "title":    title,
        "date":     str(published),
        "authors":  authors,
        "category": category,
        "abstract": abstract,
        "server":   server,
    }


@contextmanager
def _mock_api(*page_responses: bytes):
    """Patch urllib.request.urlopen; each call returns the next response."""
    responses = list(page_responses)
    call_idx  = [0]

    def fake_urlopen(url, timeout=30):
        idx = call_idx[0]
        call_idx[0] += 1
        resp = responses[idx % len(responses)]
        mock_resp = MagicMock()
        mock_resp.read.return_value = resp
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__  = MagicMock(return_value=False)
        return mock_resp

    with patch("biorxiv_client.urllib.request.urlopen", side_effect=fake_urlopen):
        yield


# ── Field mapping ─────────────────────────────────────────────────────────────

def test_fetch_maps_all_fields():
    item = _make_item(doi="10.1101/2024.01.01.000001", title="My Paper", abstract="Details here.")
    with _mock_api(_api_response([item])):
        papers = biorxiv_client.fetch_recent_papers(SERVERS, max_fetch=10)

    assert len(papers) == 1
    p = papers[0]
    assert p["id"]         == "10.1101/2024.01.01.000001"
    assert p["title"]      == "My Paper"
    assert p["abstract"]   == "Details here."
    assert p["url"]        == "https://www.biorxiv.org/content/10.1101/2024.01.01.000001"
    assert p["published"]  == str(YESTERDAY)
    assert "biorxiv"       in p["categories"]
    assert "genomics"      in p["categories"]


def test_fetch_strips_newlines_from_title_and_abstract():
    item = _make_item(title="Title\nWith Break", abstract="Line1\nLine2")
    with _mock_api(_api_response([item])):
        papers = biorxiv_client.fetch_recent_papers(SERVERS, max_fetch=10)

    assert "\n" not in papers[0]["title"]
    assert "\n" not in papers[0]["abstract"]


# ── Author formatting ─────────────────────────────────────────────────────────

def test_fetch_formats_two_authors():
    item = _make_item(authors="Smith, J; Jones, B")
    with _mock_api(_api_response([item])):
        papers = biorxiv_client.fetch_recent_papers(SERVERS, max_fetch=10)
    assert papers[0]["authors"] == "Smith, J, Jones, B"


def test_fetch_formats_three_authors_no_et_al():
    item = _make_item(authors="A, B; C, D; E, F")
    with _mock_api(_api_response([item])):
        papers = biorxiv_client.fetch_recent_papers(SERVERS, max_fetch=10)
    assert papers[0]["authors"] == "A, B, C, D, E, F"
    assert "et al." not in papers[0]["authors"]


def test_fetch_appends_et_al_for_more_than_three():
    item = _make_item(authors="A; B; C; D")
    with _mock_api(_api_response([item])):
        papers = biorxiv_client.fetch_recent_papers(SERVERS, max_fetch=10)
    assert papers[0]["authors"] == "A, B, C et al."


# ── Date filtering ────────────────────────────────────────────────────────────

def test_fetch_excludes_papers_before_cutoff():
    old    = _make_item(doi="10.1101/old", published=TODAY - timedelta(days=5))
    recent = _make_item(doi="10.1101/new", published=YESTERDAY)
    with _mock_api(_api_response([recent, old])):
        papers = biorxiv_client.fetch_recent_papers(SERVERS, max_fetch=10)
    assert len(papers) == 1
    assert papers[0]["id"] == "10.1101/new"


def test_fetch_includes_papers_from_today():
    item = _make_item(doi="10.1101/today", published=TODAY)
    with _mock_api(_api_response([item])):
        papers = biorxiv_client.fetch_recent_papers(SERVERS, max_fetch=10)
    assert len(papers) == 1


def test_fetch_returns_empty_when_collection_is_empty():
    with _mock_api(_api_response([])):
        papers = biorxiv_client.fetch_recent_papers(SERVERS, max_fetch=10)
    assert papers == []


# ── Deduplication ─────────────────────────────────────────────────────────────

def test_fetch_deduplicates_by_doi_across_servers():
    item = _make_item(doi="10.1101/shared")
    # Same DOI returned by both biorxiv and medrxiv
    with _mock_api(_api_response([item]), _api_response([item])):
        papers = biorxiv_client.fetch_recent_papers(["biorxiv", "medrxiv"], max_fetch=20)
    assert len(papers) == 1


# ── Per-server quota ──────────────────────────────────────────────────────────

def test_fetch_splits_budget_across_servers():
    """Each server gets max_fetch // len(servers) slots."""
    items = [_make_item(doi=f"10.1101/{i}", published=YESTERDAY) for i in range(10)]
    with _mock_api(_api_response(items), _api_response(items)):
        papers = biorxiv_client.fetch_recent_papers(["biorxiv", "medrxiv"], max_fetch=10)
    # 10 // 2 = 5 per server, 10 unique DOIs total
    assert len(papers) == 10


# ── Pagination ────────────────────────────────────────────────────────────────

def test_fetch_paginates_when_first_page_is_full():
    # The API only has more pages when it returns a full PAGE_SIZE batch.
    # Simulate page1 = 100 items, page2 = 5 items.
    ps    = biorxiv_client.PAGE_SIZE
    page1 = [_make_item(doi=f"10.1101/p1-{i}", published=YESTERDAY) for i in range(ps)]
    page2 = [_make_item(doi=f"10.1101/p2-{i}", published=YESTERDAY) for i in range(5)]
    total = ps + 5
    with _mock_api(_api_response(page1, total=total), _api_response(page2, total=total)):
        papers = biorxiv_client.fetch_recent_papers(SERVERS, max_fetch=total + 10)
    assert len(papers) == total


# ── Rate limit handling ───────────────────────────────────────────────────────

def test_fetch_raises_rate_limit_error_on_429():
    http_429 = HTTPError(url="https://api.biorxiv.org/...", code=429, msg="Too Many Requests", hdrs={}, fp=None)
    with patch("biorxiv_client.urllib.request.urlopen", side_effect=http_429):
        with pytest.raises(biorxiv_client.RateLimitError):
            biorxiv_client.fetch_recent_papers(SERVERS, max_fetch=10)


def test_fetch_reraises_non_429_http_errors():
    http_500 = HTTPError(url="https://api.biorxiv.org/...", code=500, msg="Server Error", hdrs={}, fp=None)
    with patch("biorxiv_client.urllib.request.urlopen", side_effect=http_500):
        with pytest.raises(HTTPError):
            biorxiv_client.fetch_recent_papers(SERVERS, max_fetch=10)
