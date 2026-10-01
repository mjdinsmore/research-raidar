import slack_client

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


def test_format_message_has_header_and_blocks():
    paper = {**PAPER, "score": 85, "reason": "Strong genomics match."}
    payload = slack_client._format_message([paper], total_fetched=42, categories=CATEGORIES)
    assert "blocks" in payload
    blocks = payload["blocks"]
    assert blocks[0]["type"] == "header"
    assert "42" in blocks[0]["text"]["text"]


def test_format_message_contains_paper_details():
    paper = {**PAPER, "score": 85, "reason": "Strong genomics match."}
    payload = slack_client._format_message([paper], total_fetched=10, categories=CATEGORIES)
    section_blocks = [b for b in payload["blocks"] if b["type"] == "section"]
    assert len(section_blocks) == 1
    text = section_blocks[0]["text"]["text"]
    assert paper["title"] in text
    assert paper["url"] in text
    assert "85" in text
    assert paper["reason"] in text
    assert paper["authors"] in text


def test_format_message_multiple_papers():
    papers = [
        {**PAPER, "id": f"id{i}", "score": float(90 - i), "reason": "reason"}
        for i in range(3)
    ]
    payload = slack_client._format_message(papers, total_fetched=10, categories=CATEGORIES)
    section_blocks = [b for b in payload["blocks"] if b["type"] == "section"]
    assert len(section_blocks) == 3


def test_format_message_has_category_context():
    paper = {**PAPER, "score": 70, "reason": "ok"}
    payload = slack_client._format_message([paper], total_fetched=10, categories=CATEGORIES)
    context_blocks = [b for b in payload["blocks"] if b["type"] == "context"]
    assert len(context_blocks) == 1
    assert "cs.LG" in context_blocks[0]["elements"][0]["text"]
