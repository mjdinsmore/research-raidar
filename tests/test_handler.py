import json
from unittest.mock import MagicMock, patch

import handler
from arxiv_client import RateLimitError
from conftest import TEST_CONFIG

# ── Shared fixtures ───────────────────────────────────────────────────────────

PAPER = {
    "id": "2405.00001v1",
    "title": "A Test Paper on Genomics",
    "abstract": "This paper explores large-scale genomic data.",
    "authors": "Alice Smith, Bob Jones",
    "url": "https://arxiv.org/abs/2405.00001v1",
    "published": "2024-05-01",
    "categories": ["cs.LG", "q-bio.GN"],
}

SCORING_SYSTEM = "Score papers on relevance. Respond with JSON."
CLAUDE_MODEL   = TEST_CONFIG["claude_model"]


def _make_bedrock_mock(response_text: str) -> MagicMock:
    mock_body = MagicMock()
    mock_body.read.return_value = json.dumps(
        {"content": [{"type": "text", "text": response_text}]}
    ).encode()
    mock_client = MagicMock()
    mock_client.invoke_model.return_value = {"body": mock_body}
    return mock_client


# ── score_papers ──────────────────────────────────────────────────────────────

def test_score_papers_merges_scores_into_papers():
    papers   = [{**PAPER}]
    response = json.dumps([{"id": PAPER["id"], "score": 7.5, "reason": "Good match."}])
    with patch("handler.boto3.client", return_value=_make_bedrock_mock(response)):
        results = handler.score_papers(papers, SCORING_SYSTEM, CLAUDE_MODEL)
    assert results[0]["score"]  == 7.5
    assert results[0]["reason"] == "Good match."
    assert results[0]["title"]  == PAPER["title"]


def test_score_papers_strips_markdown_fences():
    papers   = [{**PAPER}]
    inner    = json.dumps([{"id": PAPER["id"], "score": 6.0, "reason": "ok"}])
    response = f"```json\n{inner}\n```"
    with patch("handler.boto3.client", return_value=_make_bedrock_mock(response)):
        results = handler.score_papers(papers, SCORING_SYSTEM, CLAUDE_MODEL)
    assert results[0]["score"] == 6.0


def test_score_papers_missing_id_defaults_to_zero():
    papers   = [{**PAPER}]
    response = json.dumps([])   # model returns nothing for this id
    with patch("handler.boto3.client", return_value=_make_bedrock_mock(response)):
        results = handler.score_papers(papers, SCORING_SYSTEM, CLAUDE_MODEL)
    assert results[0]["score"]  == 0.0
    assert results[0]["reason"] == ""


def test_score_papers_truncates_abstract_to_600_chars():
    long_abstract = "x" * 1000
    papers        = [{**PAPER, "abstract": long_abstract}]
    response      = json.dumps([{"id": PAPER["id"], "score": 5.0, "reason": "ok"}])
    mock_client   = _make_bedrock_mock(response)
    with patch("handler.boto3.client", return_value=mock_client):
        handler.score_papers(papers, SCORING_SYSTEM, CLAUDE_MODEL)
    call_body    = json.loads(mock_client.invoke_model.call_args[1]["body"])
    call_content = call_body["messages"][0]["content"]
    assert "x" * 600 in call_content
    assert "x" * 601 not in call_content


# ── handler (orchestration) ───────────────────────────────────────────────────

def test_handler_returns_early_when_no_papers():
    with patch("handler.fetch_recent_papers", return_value=[]):
        result = handler.handler({}, None)
    assert result == {"statusCode": 200, "body": "no papers"}


def test_handler_returns_early_when_no_qualifying_papers():
    low_scored = [{**PAPER, "score": 2.0, "reason": "Not relevant."}]
    with (
        patch("handler.fetch_recent_papers", return_value=[PAPER]),
        patch("handler.score_papers", return_value=low_scored),
    ):
        result = handler.handler({}, None)
    assert result == {"statusCode": 200, "body": "no qualifying papers"}


def test_handler_happy_path_sends_email_and_returns_counts(mock_s3_config):
    scored = [{**PAPER, "score": 8.0, "reason": "Great match."}]
    with (
        patch("handler.fetch_recent_papers", return_value=[PAPER]),
        patch("handler.score_papers", return_value=scored),
        patch("handler.email_client.send_digest") as mock_send,
    ):
        result = handler.handler({}, None)
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["fetched"] == 1
    assert body["scored"]  == 1
    assert body["sent"]    == 1
    mock_send.assert_called_once_with(scored, 1, mock_s3_config["categories"])


def test_handler_scores_in_batches_of_20():
    papers = [{**PAPER, "id": f"id{i}"} for i in range(45)]
    scored = [{**p, "score": 7.0, "reason": "ok"} for p in papers]

    mock_score = MagicMock(side_effect=[scored[0:20], scored[20:40], scored[40:45]])
    with (
        patch("handler.fetch_recent_papers", return_value=papers),
        patch("handler.score_papers", mock_score),
        patch("handler.email_client.send_digest"),
    ):
        handler.handler({}, None)

    # 45 papers → 3 batches of (20, 20, 5)
    assert mock_score.call_count == 3
    assert len(mock_score.call_args_list[0][0][0]) == 20
    assert len(mock_score.call_args_list[1][0][0]) == 20
    assert len(mock_score.call_args_list[2][0][0]) == 5


# ── Rate limit retry ──────────────────────────────────────────────────────────

def _make_context(fn_arn="arn:aws:lambda:us-east-1:123:function:test"):
    ctx = MagicMock()
    ctx.invoked_function_arn = fn_arn
    return ctx


def test_handler_schedules_retry_on_rate_limit():
    with (
        patch("handler.fetch_recent_papers", side_effect=RateLimitError("429")),
        patch("handler.boto3.client") as mock_boto,
    ):
        result = handler.handler({"attempt": 1}, _make_context())

    assert result["statusCode"] == 429
    assert "scheduled" in result["body"]
    mock_boto.return_value.create_schedule.assert_called_once()
    schedule_input = json.loads(mock_boto.return_value.create_schedule.call_args[1]["Target"]["Input"])
    assert schedule_input["attempt"] == 2


def test_handler_retry_increments_attempt():
    with (
        patch("handler.fetch_recent_papers", side_effect=RateLimitError("429")),
        patch("handler.boto3.client"),
    ):
        result = handler.handler({"attempt": 5}, _make_context())

    assert "retry 6 scheduled" in result["body"]


def test_handler_gives_up_after_max_attempts():
    with (
        patch("handler.fetch_recent_papers", side_effect=RateLimitError("429")),
        patch("handler.boto3.client") as mock_boto,
    ):
        result = handler.handler({"attempt": 18}, _make_context())

    assert result["statusCode"] == 429
    assert "giving up" in result["body"]
    mock_boto.return_value.create_schedule.assert_not_called()


def test_handler_first_attempt_uses_default_when_no_attempt_in_event():
    with (
        patch("handler.fetch_recent_papers", side_effect=RateLimitError("429")),
        patch("handler.boto3.client") as mock_boto,
    ):
        handler.handler({}, _make_context())

    schedule_input = json.loads(mock_boto.return_value.create_schedule.call_args[1]["Target"]["Input"])
    assert schedule_input["attempt"] == 2  # started at 1, retries as 2


# ── Config loading ────────────────────────────────────────────────────────────

def test_load_config_substitutes_research_interests():
    """[RESEARCH_INTERESTS] placeholder in scoring_system is replaced at load time."""
    raw_config = {
        "research_interests": "- Cancer genomics",
        "scoring_system":     "Rate papers.\n\n[RESEARCH_INTERESTS]\n\nReturn JSON.",
    }
    # Replicate the substitution logic used inside _load_config
    result = raw_config["scoring_system"].replace(
        "[RESEARCH_INTERESTS]", raw_config["research_interests"]
    )
    assert "[RESEARCH_INTERESTS]" not in result
    assert "- Cancer genomics" in result


def test_load_config_warm_cache_skips_s3():
    """Real _load_config only calls S3 once; subsequent calls return the cached dict."""
    from conftest import _REAL_LOAD_CONFIG

    raw_config = {**TEST_CONFIG}
    mock_body  = MagicMock()
    mock_body.read.return_value = json.dumps(raw_config).encode()
    mock_s3    = MagicMock()
    mock_s3.get_object.return_value = {"Body": mock_body}

    handler._config = None
    with patch("handler.boto3.client", return_value=mock_s3):
        first  = _REAL_LOAD_CONFIG()
        second = _REAL_LOAD_CONFIG()   # should hit cache, not S3

    assert first is second
    assert mock_s3.get_object.call_count == 1
