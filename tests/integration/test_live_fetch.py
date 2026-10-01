"""
Integration test — calls the real arXiv API to help tune parameters.

Run with:
    RUN_INTEGRATION_TESTS=1 uv run pytest tests/integration -s
"""

import os

import pytest

import arxiv_client

# Skip all tests in this module unless explicitly enabled
pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Integration tests disabled. Set RUN_INTEGRATION_TESTS=1 to enable.",
)

# ── Tune these to experiment ──────────────────────────────────────────────────

# CATEGORIES = ["cs.LG", "q-bio.GN", "q-bio.QM", "stat.ML", "cs.AI"]
CATEGORIES = ["q-bio.GN"]
MAX_FETCH  = 40
DAYS_BACK  = 7   # increase to look further back (1 = last 24h, normal production behaviour)


def test_print_recent_papers():
    try:
        papers = arxiv_client.fetch_recent_papers(CATEGORIES, MAX_FETCH, days_back=DAYS_BACK)
    except arxiv_client.RateLimitError:
        pytest.skip("arXiv returned 429 — rate limited, try again later")


    print(f"\nFetched {len(papers)} papers\n")
    print(f"{'#':<4} {'ID':<20} {'Categories':<30} {'Title'}")
    print("-" * 100)
    for i, p in enumerate(papers, 1):
        cats = ", ".join(p["categories"][:2])
        title = p["title"]
        print(f"{i:<4} {p['id']:<20} {cats:<30} {title}")

    print(f"\nTotal: {len(papers)} papers from {len(CATEGORIES)} categories")
    assert len(papers) >= 0  # always passes — this test is for observation only
