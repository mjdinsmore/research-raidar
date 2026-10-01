"""Generic arXiv paper fetching utilities."""

from datetime import date, timedelta

import arxiv


class RateLimitError(Exception):
    """Raised when arXiv returns HTTP 429 Too Many Requests."""


def fetch_recent_papers(
    categories: list[str], max_fetch: int, days_back: int = 1
) -> list[dict]:
    """Fetch papers submitted in the last `days_back` days across the given categories.

    Queries each category independently, allocating max_fetch // len(categories)
    results per category, then merges and deduplicates. This ensures no single
    high-volume category (e.g. cs.CL) crowds out lower-volume ones (e.g. q-bio.*).

    Returns a list of dicts with keys: id, title, abstract, authors, url,
    published, categories.

    Raises:
        RateLimitError: if arXiv responds with HTTP 429.
    """
    cutoff  = date.today() - timedelta(days=days_back)
    per_cat = max(1, max_fetch // len(categories))

    client   = arxiv.Client()
    seen_ids = set()
    papers   = []

    for cat in categories:
        search = arxiv.Search(
            query=f"cat:{cat}",
            max_results=per_cat,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        try:
            for result in client.results(search):
                if result.published.date() < cutoff:
                    break
                paper_id = result.entry_id.split("/")[-1]
                if paper_id in seen_ids:
                    continue
                seen_ids.add(paper_id)
                papers.append({
                    "id":         paper_id,
                    "title":      result.title.replace("\n", " "),
                    "abstract":   result.summary.replace("\n", " "),
                    "authors":    ", ".join(a.name for a in result.authors[:3])
                                  + (" et al." if len(result.authors) > 3 else ""),
                    "url":        result.entry_id,
                    "published":  str(result.published.date()),
                    "categories": list(result.categories),
                })
        except arxiv.HTTPError as e:
            if e.status == 429:
                raise RateLimitError(f"arXiv rate limit (429) on {cat}") from e
            raise

    return papers
