# Research rAIdar

Daily digest of arXiv, bioRxiv and medRxiv papers scored for relevance by Claude (via Bedrock), delivered via SES and Slack.

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

Set `RAIDAR_BASE_BUCKET` to override the default S3 bucket prefix (`research-raidar`).
The deploy script appends `-dev` automatically, so `RAIDAR_BASE_BUCKET=my-papers` creates `my-papers-dev`.

## Test locally

```bash
cd lambda
uv pip install -r requirements.txt

export SES_SENDER=michael@company.com
export SES_RECIPIENT=michael@company.com
export CONFIG_ENV=dev                              # env suffix
export RAIDAR_BASE_BUCKET=research-raidar          # optional, default shown

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

## Slack integration (optional)

1. Create a [Slack incoming webhook](https://api.slack.com/messaging/webhooks) for your target channel
2. Store the webhook URL in Secrets Manager:
   ```bash
   aws secretsmanager create-secret \
     --name research-raidar-dev/slack-webhook \
     --secret-string "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
   ```
3. Deploy — the `deploy.sh` script passes the secret name automatically

The digest is sent to both email and Slack. If the secret doesn't exist or the env var is empty, Slack is silently skipped.

## Tuning

All tuneable settings live in **`config/config.json`**. Edit the file and re-run
`./infra/deploy.sh` — it uploads the config to S3 automatically after every deploy.
No Lambda redeployment is needed for config-only changes; just run:

```bash
aws s3 cp config/config.json s3://${RAIDAR_BASE_BUCKET:-research-raidar}-dev/config.json
```

Key fields:

| Field | Default | Description |
|-------|---------|-------------|
| `categories` | `["q-bio.GN", ...]` | arXiv category codes. Full list: https://arxiv.org/category_taxonomy |
| `biorxiv_servers` | `["biorxiv"]` | CSHL servers to fetch from: `"biorxiv"`, `"medrxiv"`, or both |
| `biorxiv_max_fetch` | `40` | Max bioRxiv/medRxiv papers to fetch per run (0 to disable) |
| `min_score` | `75` | Papers below this score (1–100 scale) are excluded from the digest |
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

### Rate limit retries
If arXiv returns HTTP 429, the Lambda schedules a retry via EventBridge Scheduler
13 minutes later, passing the attempt count in the event payload. It retries up to
18 times (~4 hours total) before giving up. The 13-minute interval is intentionally
odd to avoid re-syncing with other clients polling on regular intervals.

If bioRxiv/medRxiv is rate limited, the Lambda logs a warning and continues with
arXiv results only — it does not block the entire digest.

## Cost estimate (rough)

| Resource | Est. monthly |
|----------|-------------|
| Lambda (5 min/day, 512 MB) | ~$0.01 |
| Claude Haiku via Bedrock (80 abstracts/day, ~600 tokens each) | ~$0.40 |
| SES (1 email/day) | ~$0.00 |
| S3 (1 tiny config file, ~30 GETs/month) | ~$0.00 |
| **Total** | **~$0.40/month** |
