from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import arxiv
import pytest

import arxiv_client

CATEGORIES = ["cs.LG", "q-bio.GN"]
TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


def _make_result(
    arxiv_id: str,
    title: str,
    published: date,
    authors: list[str] | None = None,
    categories: list[str] | None = None,
    abstract: str = "An abstract.",
) -> MagicMock:
    result = MagicMock()
    result.entry_id = f"https://arxiv.org/abs/{arxiv_id}"
    result.title = title
    result.summary = abstract

    result.published = MagicMock()
    result.published.date.return_value = published

    author_mocks = []
    for name in (authors or ["Alice Smith", "Bob Jones"]):
        a = MagicMock()
        a.name = name
        author_mocks.append(a)
    result.authors = author_mocks

    result.categories = list(categories or ["cs.LG"])

    return result


@contextmanager
def _mock_arxiv(results: list):
    """Patch both arxiv.Search and arxiv.Client; yield the Search class mock.

    Uses side_effect so each per-category call to client.results() receives a
    fresh iterator over the same result list (deduplication in the client under
    test ensures duplicates across categories are dropped).
    """
    mock_client = MagicMock()
    mock_client.results.side_effect = lambda _: iter(results)
    with (
        patch("arxiv_client.arxiv.Client", return_value=mock_client),
        patch("arxiv_client.arxiv.Search") as mock_search_cls,
    ):
        yield mock_search_cls


# ── Field mapping ─────────────────────────────────────────────────────────────

def test_fetch_maps_fields_correctly():
    result = _make_result("2405.00001v1", "Test Paper", YESTERDAY)
    with _mock_arxiv([result]):
        papers = arxiv_client.fetch_recent_papers(CATEGORIES, max_fetch=10)

    assert len(papers) == 1
    p = papers[0]
    assert p["id"] == "2405.00001v1"
    assert p["title"] == "Test Paper"
    assert p["url"] == "https://arxiv.org/abs/2405.00001v1"
    assert p["published"] == str(YESTERDAY)
    assert p["abstract"] == "An abstract."


def test_fetch_normalises_newlines_in_title_and_abstract():
    result = _make_result("id1", "Title\nWith Newline", YESTERDAY, abstract="Line1\nLine2")
    with _mock_arxiv([result]):
        papers = arxiv_client.fetch_recent_papers(CATEGORIES, max_fetch=10)

    assert "\n" not in papers[0]["title"]
    assert "\n" not in papers[0]["abstract"]


# ── Author formatting ─────────────────────────────────────────────────────────

def test_fetch_formats_two_authors():
    result = _make_result("id1", "T", YESTERDAY, authors=["Alice", "Bob"])
    with _mock_arxiv([result]):
        papers = arxiv_client.fetch_recent_papers(CATEGORIES, max_fetch=10)
    assert papers[0]["authors"] == "Alice, Bob"


def test_fetch_formats_three_authors_no_et_al():
    result = _make_result("id1", "T", YESTERDAY, authors=["Alice", "Bob", "Carol"])
    with _mock_arxiv([result]):
        papers = arxiv_client.fetch_recent_papers(CATEGORIES, max_fetch=10)
    assert papers[0]["authors"] == "Alice, Bob, Carol"
    assert "et al." not in papers[0]["authors"]


def test_fetch_appends_et_al_for_more_than_three_authors():
    result = _make_result("id1", "T", YESTERDAY, authors=["A", "B", "C", "D"])
    with _mock_arxiv([result]):
        papers = arxiv_client.fetch_recent_papers(CATEGORIES, max_fetch=10)
    assert papers[0]["authors"] == "A, B, C et al."


# ── Date filtering ────────────────────────────────────────────────────────────

def test_fetch_excludes_papers_older_than_yesterday():
    old = _make_result("old1", "Old Paper", TODAY - timedelta(days=2))
    recent = _make_result("new1", "New Paper", YESTERDAY)
    with _mock_arxiv([recent, old]):
        papers = arxiv_client.fetch_recent_papers(CATEGORIES, max_fetch=10)
    assert len(papers) == 1
    assert papers[0]["id"] == "new1"


def test_fetch_includes_papers_from_today():
    result = _make_result("id1", "Today Paper", TODAY)
    with _mock_arxiv([result]):
        papers = arxiv_client.fetch_recent_papers(CATEGORIES, max_fetch=10)
    assert len(papers) == 1


def test_fetch_returns_empty_when_no_recent_papers():
    old = _make_result("old1", "Old", TODAY - timedelta(days=5))
    with _mock_arxiv([old]):
        papers = arxiv_client.fetch_recent_papers(CATEGORIES, max_fetch=10)
    assert papers == []


# ── Query construction ────────────────────────────────────────────────────────

def test_fetch_builds_per_category_queries():
    with _mock_arxiv([]) as mock_search_cls:
        arxiv_client.fetch_recent_papers(["cs.LG", "q-bio.GN"], max_fetch=50)

    # One Search call per category, each with its own query and equal quota
    assert mock_search_cls.call_count == 2
    calls = [c[1] for c in mock_search_cls.call_args_list]
    assert calls[0]["query"] == "cat:cs.LG"
    assert calls[1]["query"] == "cat:q-bio.GN"
    assert calls[0]["max_results"] == 25   # 50 // 2
    assert calls[1]["max_results"] == 25


# ── Rate limit handling ───────────────────────────────────────────────────────

def test_fetch_raises_rate_limit_error_on_429():
    http_429 = arxiv.HTTPError(url="https://export.arxiv.org/...", retry=3, status=429)
    mock_client = MagicMock()
    mock_client.results.return_value = _raise(http_429)
    with (
        patch("arxiv_client.arxiv.Client", return_value=mock_client),
        patch("arxiv_client.arxiv.Search"),
    ):
        with pytest.raises(arxiv_client.RateLimitError):
            arxiv_client.fetch_recent_papers(CATEGORIES, max_fetch=10)


def test_fetch_reraises_non_429_http_errors():
    http_500 = arxiv.HTTPError(url="https://export.arxiv.org/...", retry=3, status=500)
    mock_client = MagicMock()
    mock_client.results.return_value = _raise(http_500)
    with (
        patch("arxiv_client.arxiv.Client", return_value=mock_client),
        patch("arxiv_client.arxiv.Search"),
    ):
        with pytest.raises(arxiv.HTTPError):
            arxiv_client.fetch_recent_papers(CATEGORIES, max_fetch=10)


def _raise(exc):
    """Generator that immediately raises the given exception."""
    raise exc
    yield  # make it a generator
