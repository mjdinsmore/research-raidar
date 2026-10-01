#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"   # always run from infra/ regardless of where script is called from

SENDER="${RAIDAR_SENDER:?Set RAIDAR_SENDER in your shell profile}"
RECIPIENT="${RAIDAR_RECIPIENT:?Set RAIDAR_RECIPIENT in your shell profile}"
SLACK_SECRET_NAME=research-raidar-dev/slack-webhook
CONFIG_ENV=dev

uv venv --allow-existing
source .venv/bin/activate
uv pip install -r requirements.txt

cdk deploy \
  --method=direct \
  --parameters SesSender=$SENDER \
  --parameters SesRecipient=$RECIPIENT \
  --parameters SlackWebhookSecretName=$SLACK_SECRET_NAME

echo ""
echo "Uploading assets to s3://research-raidar-${CONFIG_ENV} ..."
aws s3 cp ../config/config.json "s3://research-raidar-${CONFIG_ENV}/config.json"
aws s3 cp ../resources/research-raidar-logo.png "s3://research-raidar-${CONFIG_ENV}/research-raidar-logo.png"
echo "Assets uploaded."
