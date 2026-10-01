"""
arXiv Daily Radar — Lambda handler
Fetches recent papers from configured categories, scores them for relevance
using Claude via Bedrock, and delivers a digest.

Configuration is loaded from s3://research-raidar-{CONFIG_ENV}/config.json and
cached for the lifetime of a warm Lambda container.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

import boto3
import biorxiv_client
import email_client
import slack_client
from arxiv_client import RateLimitError, fetch_recent_papers

# ── Config environment ─────────────────────────────────────────────────────────

CONFIG_ENV    = os.environ.get("CONFIG_ENV", "dev")
CONFIG_BUCKET = f"research-raidar-{CONFIG_ENV}"

# ── S3-backed config with warm-Lambda caching ──────────────────────────────────

_config: dict | None = None


def _load_config() -> dict:
    """Load config from S3; result is cached for the lifetime of a warm container."""
    global _config
    if _config is None:
        s3  = boto3.client("s3")
        obj = s3.get_object(Bucket=CONFIG_BUCKET, Key="config.json")
        raw = json.loads(obj["Body"].read())
        # Normalise research_interests: accept either a list or a plain string
        if isinstance(raw["research_interests"], list):
            raw["research_interests"] = "\n".join(f"- {r}" for r in raw["research_interests"])
        # Substitute [RESEARCH_INTERESTS] placeholder in scoring_system
        raw["scoring_system"] = raw["scoring_system"].replace(
            "[RESEARCH_INTERESTS]", raw["research_interests"]
        )
        _config = raw
    return _config


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_papers(papers: list[dict], scoring_system: str, claude_model: str) -> list[dict]:
    """Send a batch of abstracts to Claude via Bedrock and return scored results."""
    client = boto3.client("bedrock-runtime")

    lines = []
    for p in papers:
        lines.append(
            f'ID: {p["id"]}\nTitle: {p["title"]}\nAbstract: {p["abstract"][:600]}'
        )
    user_content = "\n\n---\n\n".join(lines)

    response = client.invoke_model(
        modelId=claude_model,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
            "system": scoring_system,
            "messages": [{"role": "user", "content": user_content}],
        }),
    )

    raw = json.loads(response["body"].read())["content"][0]["text"].strip()
    # Strip accidental markdown fences if the model adds them
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    scored    = json.loads(raw)
    score_map = {s["id"]: s for s in scored}
    results   = []
    for p in papers:
        s = score_map.get(p["id"], {})
        results.append({**p, "score": s.get("score", 0.0), "reason": s.get("reason", "")})
    return results


# ── Retry scheduling ──────────────────────────────────────────────────────────

def _schedule_retry(attempt: int, fn_arn: str, retry_interval_minutes: int) -> None:
    run_at = datetime.now(timezone.utc) + timedelta(minutes=retry_interval_minutes)
    name   = f"research-raidar-retry-{attempt}-{run_at.strftime('%Y%m%d')}"
    scheduler = boto3.client("scheduler")
    try:
        scheduler.create_schedule(
            Name=name,
            ScheduleExpression=f"at({run_at.strftime('%Y-%m-%dT%H:%M:%S')})",
            Target={
                "Arn": fn_arn,
                "RoleArn": os.environ["SCHEDULER_ROLE_ARN"],
                "Input": json.dumps({"attempt": attempt}),
            },
            FlexibleTimeWindow={"Mode": "OFF"},
            ActionAfterCompletion="DELETE",
        )
    except scheduler.exceptions.ConflictException:
        print(f"Retry schedule '{name}' already exists — already queued, skipping")


# ── Lambda entrypoint ─────────────────────────────────────────────────────────

def handler(event, context):
    cfg = _load_config()

    categories             = cfg["categories"]
    max_fetch              = cfg["max_fetch"]
    biorxiv_servers        = cfg.get("biorxiv_servers", [])
    biorxiv_max_fetch      = cfg.get("biorxiv_max_fetch", 0)
    top_n                  = cfg["top_n"]
    min_score              = cfg["min_score"]
    claude_model           = cfg["claude_model"]
    scoring_system         = cfg["scoring_system"]
    retry_interval_minutes = cfg["retry_interval_minutes"]
    max_retry_attempts     = cfg["max_retry_attempts"]

    attempt = event.get("attempt", 1)
    print(f"Fetching up to {max_fetch} arXiv papers from {categories} (attempt {attempt})")

    try:
        papers = fetch_recent_papers(categories, max_fetch)
    except RateLimitError:
        if attempt >= max_retry_attempts:
            print(f"Rate limited on attempt {attempt}, max retries reached — giving up")
            return {"statusCode": 429, "body": "rate limit exceeded, giving up"}
        _schedule_retry(attempt + 1, context.invoked_function_arn, retry_interval_minutes)
        print(f"Rate limited on attempt {attempt}, retry {attempt + 1} scheduled in {retry_interval_minutes} min")
        return {"statusCode": 429, "body": f"rate limited, retry {attempt + 1} scheduled"}

    print(f"Fetched {len(papers)} arXiv papers from the last 24h")

    if biorxiv_servers and biorxiv_max_fetch:
        print(f"Fetching up to {biorxiv_max_fetch} bioRxiv/medRxiv papers from {biorxiv_servers}")
        try:
            biorxiv_papers = biorxiv_client.fetch_recent_papers(biorxiv_servers, biorxiv_max_fetch)
            print(f"Fetched {len(biorxiv_papers)} bioRxiv/medRxiv papers from the last 24h")
            papers.extend(biorxiv_papers)
        except biorxiv_client.RateLimitError as e:
            print(f"bioRxiv/medRxiv rate limited — skipping and continuing with arXiv only: {e}")

    print(f"Total papers to score: {len(papers)}")

    if not papers:
        print("No new papers today — skipping digest")
        return {"statusCode": 200, "body": "no papers"}

    # Score in batches of 20 to stay within token limits
    batch_size = 20
    all_scored = []
    for i in range(0, len(papers), batch_size):
        batch = papers[i : i + batch_size]
        print(f"Scoring batch {i//batch_size + 1} ({len(batch)} papers)")
        all_scored.extend(score_papers(batch, scoring_system, claude_model))

    top = sorted(
        (p for p in all_scored if p["score"] >= min_score),
        key=lambda p: p["score"],
        reverse=True,
    )[:top_n]

    if not top:
        print(f"No papers scored above {min_score} today")
        return {"statusCode": 200, "body": "no qualifying papers"}

    email_client.send_digest(top, len(papers), categories)
    print(f"Email digest sent: {len(top)} papers")

    try:
        slack_client.send_digest(top, len(papers), categories)
        print("Slack digest sent")
    except Exception as e:
        print(f"Slack delivery failed (non-fatal): {e}")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "fetched": len(papers),
            "scored":  len(all_scored),
            "sent":    len(top),
            "top_scores": [{"title": p["title"][:60], "score": p["score"]} for p in top],
        }),
    }
