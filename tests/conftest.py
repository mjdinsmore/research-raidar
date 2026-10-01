import os
from unittest.mock import patch

import pytest

# Set env vars before any module imports
os.environ.setdefault("SES_SENDER",          "test@example.com")
os.environ.setdefault("SES_RECIPIENT",       "test@example.com")
os.environ.setdefault("SCHEDULER_ROLE_ARN",  "arn:aws:iam::123456789012:role/test-scheduler-role")
os.environ.setdefault("CONFIG_ENV",          "test")

# ── Shared test config (mirrors the shape of lambda/config.json) ──────────────

TEST_CONFIG = {
    "categories":             ["cs.LG", "q-bio.GN"],
    "max_fetch":              80,
    "top_n":                  5,
    "min_score":              6.0,
    "claude_model":           "anthropic.claude-haiku-4-5-20251001-v1:0",
    "retry_interval_minutes": 13,
    "max_retry_attempts":     18,
    "research_interests":     ["AI for biomedical research"],
    "scoring_system":         "Score papers on relevance.\n\n[RESEARCH_INTERESTS]\n\nRespond with JSON.",
}


def _resolved_test_config() -> dict:
    """Return TEST_CONFIG with [RESEARCH_INTERESTS] substituted, as _load_config() would."""
    cfg = dict(TEST_CONFIG)
    if isinstance(cfg["research_interests"], list):
        cfg["research_interests"] = "\n".join(f"- {r}" for r in cfg["research_interests"])
    cfg["scoring_system"] = cfg["scoring_system"].replace(
        "[RESEARCH_INTERESTS]", cfg["research_interests"]
    )
    return cfg


# Save reference to the real _load_config before any test patches it
import handler as _handler_module
_REAL_LOAD_CONFIG = _handler_module._load_config


@pytest.fixture(autouse=True)
def mock_s3_config():
    """Patch handler._load_config for every test; yields the resolved config dict."""
    import handler
    handler._config = None                          # reset warm-cache between tests
    resolved = _resolved_test_config()
    with patch("handler._load_config", return_value=resolved):
        yield resolved
