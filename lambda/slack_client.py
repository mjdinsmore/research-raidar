"""Slack webhook delivery for Research rAIdar digests."""

import json
import os

import boto3
import requests

_SECRET_NAME = os.environ.get("RAIDAR_SLACK_WEBHOOK_SECRET_NAME", "")

# ── Webhook URL cache (warm Lambda reuse) ────────────────────────────────────

_webhook_url: str | None = None


def _get_webhook_url() -> str | None:
    """Fetch webhook URL from Secrets Manager; cached for warm containers."""
    global _webhook_url
    if _webhook_url is None:
        if not _SECRET_NAME:
            return None
        sm = boto3.client("secretsmanager")
        secret = sm.get_secret_value(SecretId=_SECRET_NAME)
        _webhook_url = secret["SecretString"]
    return _webhook_url


# ── Public interface ─────────────────────────────────────────────────────────

def send_digest(top_papers: list[dict], total_fetched: int, categories: list[str]) -> None:
    """Format and send the digest to Slack. No-op if webhook is not configured."""
    url = _get_webhook_url()
    if not url:
        return
    payload = _format_message(top_papers, total_fetched, categories)
    _send(url, payload)


def _format_message(top_papers: list[dict], total_fetched: int, categories: list[str]) -> dict:
    """Build a Slack Block Kit message payload."""
    from datetime import date

    today = date.today().strftime("%a %b %-d")
    header = f"Research rAIdar {today} — top {len(top_papers)} of {total_fetched} papers"

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "divider"},
    ]

    for i, p in enumerate(top_papers, 1):
        cats = " · ".join(p["categories"][:3])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{i}. <{p['url']}|{p['title']}>*\n"
                    f"_{p['authors']}_\n"
                    f"{p['reason']}\n"
                    f"`Score {int(p['score'])}` · {cats}"
                ),
            },
        })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"Categories: {', '.join(categories)}"}],
    })

    return {"blocks": blocks}


# ── Webhook delivery ─────────────────────────────────────────────────────────

def _send(url: str, payload: dict) -> None:
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
