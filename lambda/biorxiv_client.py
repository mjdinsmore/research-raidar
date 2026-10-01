"""bioRxiv / medRxiv paper fetching via the CSHL API (api.biorxiv.org).

Both servers share the same API.  Results are paginated at 100 articles
per call; this module pages through automatically up to max_fetch.

Returned dicts match the shape produced by arxiv_client so the rest of
the pipeline (scoring, email) requires no changes.
"""

import json
import urllib.error
import urllib.request
from datetime import date, timedelta

BASE_URL   = "https://api.biorxiv.org/details"
PAGE_SIZE  = 100


class RateLimitError(Exception):
    """Raised when the CSHL API responds with HTTP 429."""


def _get(url: str) -> dict:
    """Fetch a URL and return parsed JSON.  Raises RateLimitError on 429."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimitError(f"CSHL API rate limit (429): {url}") from e
        raise


def _format_authors(raw: str) -> str:
    """Format a semicolon-separated author string to match arxiv_client style.

    'Smith, J; Jones, B; Lee, C; Park, D'  →  'Smith, J, Jones, B, Lee, C et al.'
    """
    parts = [a.strip() for a in raw.split(";") if a.strip()]
    if len(parts) <= 3:
        return ", ".join(parts)
    return ", ".join(parts[:3]) + " et al."


def fetch_recent_papers(
    servers: list[str], max_fetch: int, days_back: int = 1
) -> list[dict]:
    """Fetch papers submitted in the last `days_back` days from each server.

    Queries each server independently with an equal share of max_fetch,
    then merges and deduplicates by DOI.

    Args:
        servers:   List of CSHL server names, e.g. ["biorxiv", "medrxiv"].
        max_fetch: Total paper budget across all servers.
        days_back: How many days back to look (1 = last 24 h).

    Returns:
        List of paper dicts with keys: id, title, abstract, authors, url,
        published, categories.

    Raises:
        RateLimitError: if the API responds with HTTP 429.
    """
    cutoff   = date.today() - timedelta(days=days_back)
    end_date = date.today()
    interval = f"{cutoff}/{end_date}"
    per_srv  = max(1, max_fetch // len(servers))

    seen_ids = set()
    papers   = []

    for server in servers:
        count  = 0
        cursor = 0

        while count < per_srv:
            url  = f"{BASE_URL}/{server}/{interval}/{cursor}"
            data = _get(url)

            collection = data.get("collection") or []
            if not collection:
                break   # no more results

            for item in collection:
                if count >= per_srv:
                    break
                paper_date = item.get("date", "")
                try:
                    if date.fromisoformat(paper_date) < cutoff:
                        continue
                except ValueError:
                    continue

                doi = item.get("doi", "").strip()
                if not doi or doi in seen_ids:
                    continue
                seen_ids.add(doi)

                papers.append({
                    "id":         doi,
                    "title":      item.get("title", "").replace("\n", " ").strip(),
                    "abstract":   item.get("abstract", "").replace("\n", " ").strip(),
                    "authors":    _format_authors(item.get("authors", "")),
                    "url":        f"https://www.biorxiv.org/content/{doi}",
                    "published":  paper_date,
                    "categories": [server, item.get("category", "").strip()],
                })
                count += 1

            # Fewer results than a full page means no more pages exist
            if len(collection) < PAGE_SIZE:
                break
            cursor += PAGE_SIZE

    return papers
