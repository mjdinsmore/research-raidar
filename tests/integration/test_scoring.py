"""
End-to-end scoring integration test — fetches real papers and scores them
via Bedrock so you can see exactly what Claude thinks of each one.

Run with:
    RUN_INTEGRATION_TESTS=1 uv run pytest tests/integration/test_scoring.py -s

Tune the constants below to adjust the lookback window and fetch limit.
Requires AWS credentials with Bedrock access (same as the Lambda uses).
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Skip unless explicitly enabled
pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Integration tests disabled. Set RUN_INTEGRATION_TESTS=1 to enable.",
)

# ── Tune these ────────────────────────────────────────────────────────────────

DAYS_BACK  = 7    # how far back to look (7 = last week)
MAX_FETCH  = 200  # raise this to cast a wider net
BATCH_SIZE = 20   # keep at 20 to match production

# ── Helpers ───────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parents[2] / "config" / "config.json"


def _load_local_config() -> dict:
    """Load and normalise config from the local config/config.json (bypasses S3)."""
    raw = json.loads(CONFIG_PATH.read_text())
    if isinstance(raw["research_interests"], list):
        raw["research_interests"] = "\n".join(f"- {r}" for r in raw["research_interests"])
    raw["scoring_system"] = raw["scoring_system"].replace(
        "[RESEARCH_INTERESTS]", raw["research_interests"]
    )
    return raw


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_score_recent_papers():
    """Fetch the last DAYS_BACK days of papers and print a full score table.

    Nothing is asserted — this test exists purely for local diagnosis.
    Read the printed table to see which papers are passing/failing the
    min_score threshold and why.
    """
    import arxiv_client
    import handler

    cfg = _load_local_config()
    min_score  = cfg["min_score"]
    categories = cfg["categories"]

    try:
        papers = arxiv_client.fetch_recent_papers(categories, MAX_FETCH, days_back=DAYS_BACK)
    except arxiv_client.RateLimitError:
        pytest.skip("arXiv returned 429 — rate limited, try again later")

    print(f"\nFetched {len(papers)} papers from {categories} over the last {DAYS_BACK} days")
    print(f"Scoring in batches of {BATCH_SIZE} via Bedrock ({cfg['claude_model']})…\n")

    all_scored = []
    for i in range(0, len(papers), BATCH_SIZE):
        batch = papers[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(papers) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"  Scoring batch {batch_num}/{total_batches} ({len(batch)} papers)…", flush=True)
        all_scored.extend(handler.score_papers(batch, cfg["scoring_system"], cfg["claude_model"]))

    all_scored.sort(key=lambda p: p["score"], reverse=True)

    # ── Print full score table ────────────────────────────────────────────────
    col_w = 68
    print(f"\n{'Score':<7} {'Pass':<6} {'Date':<12} {'ID':<20} {'Title'}")
    print("─" * (7 + 6 + 12 + 20 + col_w + 4))
    for p in all_scored:
        passes  = "YES" if p["score"] >= min_score else "no"
        title   = p["title"][:col_w] + ("…" if len(p["title"]) > col_w else "")
        print(f"{p['score']:<7} {passes:<6} {p['published']:<12} {p['id']:<20} {title}")

    # ── Summary ───────────────────────────────────────────────────────────────
    passing = [p for p in all_scored if p["score"] >= min_score]
    print(f"\n{'─' * 60}")
    print(f"Threshold : {min_score}")
    print(f"Fetched   : {len(papers)}")
    print(f"Passing   : {len(passing)}  ({len(passing)/len(papers)*100:.0f}% of fetched)" if papers else "Passing   : 0")
    print(f"Would send: top {min(len(passing), cfg['top_n'])} of {len(passing)}")

    if passing:
        print(f"\nTop {min(5, len(passing))} that would be emailed:")
        for p in passing[:5]:
            print(f"  [{p['score']:>3}] {p['title'][:70]}")
            print(f"        {p['reason']}")

    # Warn if nothing passes — that's the bug
    if not passing:
        print(f"\n*** PROBLEM: 0 papers passed min_score={min_score} — no email would be sent ***")
        print(f"    Highest score was {all_scored[0]['score']} for: {all_scored[0]['title'][:60]}")


def test_score_specific_paper():
    """Score a specific paper by arXiv ID to see exactly why it did or didn't pass.

    Fetches the paper directly from the arXiv API and scores it in isolation.
    Edit PAPER_ID below to test any paper you're curious about.
    """
    import arxiv
    import handler

    PAPER_ID = "2604.05774"  # GenomeQA — the paper that should have been emailed

    cfg = _load_local_config()

    client = arxiv.Client()
    search = arxiv.Search(id_list=[PAPER_ID])
    results = list(client.results(search))
    assert results, f"Paper {PAPER_ID} not found on arXiv"

    r = results[0]
    paper = {
        "id":         r.entry_id.split("/")[-1],
        "title":      r.title.replace("\n", " "),
        "abstract":   r.summary.replace("\n", " "),
        "authors":    ", ".join(a.name for a in r.authors[:3])
                      + (" et al." if len(r.authors) > 3 else ""),
        "url":        r.entry_id,
        "published":  str(r.published.date()),
        "categories": list(r.categories),
    }

    print(f"\nScoring paper: {paper['id']}")
    print(f"Title        : {paper['title']}")
    print(f"Published    : {paper['published']}")
    print(f"Categories   : {', '.join(paper['categories'])}")
    print(f"Abstract     : {paper['abstract'][:300]}…\n")

    scored = handler.score_papers([paper], cfg["scoring_system"], cfg["claude_model"])
    result = scored[0]

    min_score = cfg["min_score"]
    verdict   = "PASS — would be included" if result["score"] >= min_score else f"FAIL — below min_score={min_score}"

    print(f"Score   : {result['score']} / 100")
    print(f"Reason  : {result['reason']}")
    print(f"Verdict : {verdict}")

    # Informational — not a hard assertion, since the goal is diagnosis
    if result["score"] < min_score:
        print(f"\n*** This paper scored {result['score']}, below the threshold of {min_score}. ***")
        print("    Consider lowering min_score in config/config.json or broadening research_interests.")
