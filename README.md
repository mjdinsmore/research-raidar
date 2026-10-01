# Research rAIdar

Daily digest of arXiv, bioXiv and other papers scored for relevance by Claude (via Bedrock), delivered via SES.

## Project layout

```
research-raidar/
├── lambda/       # Lambda handler and library code
├── config/       # config.json — uploaded to S3 on deploy
├── infra/        # CDK stack
└── tests/
    └── integration/
```

## Prerequisites

1. SES sender address verified in your AWS account
2. Bedrock model access enabled for `anthropic.claude-haiku-4-5-20251001-v1:0` in your region
3. CDK bootstrapped in target account/region

## Deploy

CDK picks up your account and region automatically from your AWS credentials/profile.

```bash
cd infra && ./deploy.sh
```

## Test locally

```bash
cd lambda
uv pip install -r requirements.txt

export SES_SENDER=test@example.com
export SES_RECIPIENT=test@example.com
export CONFIG_ENV=dev           # reads s3://research-raidar-dev/config.json

python -c "import handler; print(handler.handler({}, None))"
```

Bedrock and S3 auth use your local AWS credentials — no API key needed.

## Run unit tests

```bash
uv run pytest
```

## Run integration test (live arXiv fetch)

```bash
RUN_INTEGRATION_TESTS=1 uv run pytest tests/integration -s
```

## Tuning

All tuneable settings live in **`config/config.json`**. Edit the file and re-run
`./infra/deploy.sh` — it uploads the config to S3 automatically after every deploy.
No Lambda redeployment is needed for config-only changes; just run:

```bash
aws s3 cp config/config.json s3://research-raidar-dev/config.json
```

Key fields:

| Field | Default | Description |
|-------|---------|-------------|
| `categories` | `["q-bio.GN", ...]` | arXiv category codes. Full list: https://arxiv.org/category_taxonomy |
| `min_score` | `6.0` | Papers below this score are excluded from the digest |
| `top_n` | `5` | Max papers included regardless of score |
| `research_interests` | *(your interests)* | Most important tuning lever — be specific |
| `scoring_system` | *(prompt template)* | Full Claude prompt; use `[RESEARCH_INTERESTS]` as placeholder |

Useful category codes for biotech/ML:
- `cs.AI`    artificial intelligence
- `q-bio.GN` genomics
- `q-bio.QM` quantitative methods
- `q-bio.TO` tissues and organs, tumor growth

### Schedule
EventBridge cron is `30 6 * * ? *` (06:30 UTC). Edit in `infra/app.py` to match
your timezone. arXiv posts new submissions around 00:00 UTC, so 06:00–08:00 UTC
is a good window.

### arXiv rate limit retries
If arXiv returns HTTP 429, the Lambda schedules a retry via EventBridge Scheduler
13 minutes later, passing the attempt count in the event payload. It retries up to
18 times (~4 hours total) before giving up. The 13-minute interval is intentionally
odd to avoid re-syncing with other clients polling on regular intervals.

## Cost estimate (rough)

| Resource | Est. monthly |
|----------|-------------|
| Lambda (5 min/day, 512 MB) | ~$0.01 |
| Claude Haiku via Bedrock (80 abstracts/day, ~600 tokens each) | ~$0.40 |
| SES (1 email/day) | ~$0.00 |
| S3 (1 tiny config file, ~30 GETs/month) | ~$0.00 |
| **Total** | **~$0.40/month** |
