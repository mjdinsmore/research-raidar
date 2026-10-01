import os

import pytest

os.environ.setdefault("SES_SENDER", "sender@example.com")
os.environ.setdefault("SES_RECIPIENT", "recipient@example.com")

import email_client

CATEGORIES = ["cs.LG", "q-bio.GN"]

PAPER = {
    "id": "2405.00001v1",
    "title": "A Test Paper on Genomics",
    "abstract": "This paper explores large-scale genomic data.",
    "authors": "Alice Smith, Bob Jones",
    "url": "https://arxiv.org/abs/2405.00001v1",
    "published": "2024-05-01",
    "categories": ["cs.LG", "q-bio.GN"],
}


def test_format_digest_subject_contains_counts():
    paper = {**PAPER, "score": 8.5, "reason": "Relevant to interests."}
    subject, _ = email_client.format_digest([paper], total_fetched=42, categories=CATEGORIES)
    assert "1" in subject
    assert "42" in subject


def test_format_digest_html_contains_paper_details():
    paper = {**PAPER, "score": 85, "reason": "Strong genomics match."}
    _, html = email_client.format_digest([paper], total_fetched=10, categories=CATEGORIES)
    assert paper["title"] in html
    assert paper["url"] in html
    assert "85" in html
    assert paper["reason"] in html
    assert paper["authors"] in html


def test_format_digest_multiple_papers_are_numbered():
    papers = [
        {**PAPER, "id": f"id{i}", "score": float(9 - i), "reason": "reason"}
        for i in range(3)
    ]
    _, html = email_client.format_digest(papers, total_fetched=10, categories=CATEGORIES)
    assert ">1<" in html
    assert ">2<" in html
    assert ">3<" in html


def test_format_digest_html_contains_categories():
    paper = {**PAPER, "score": 7.0, "reason": "ok"}
    _, html = email_client.format_digest([paper], total_fetched=10, categories=CATEGORIES)
    assert "cs.LG" in html
    assert "q-bio.GN" in html
