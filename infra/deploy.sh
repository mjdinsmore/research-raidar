#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"   # always run from infra/ regardless of where script is called from

SENDER=sender@example.com
RECIPIENT=test@example.com
CONFIG_ENV=dev

uv venv --allow-existing
source .venv/bin/activate
uv pip install -r requirements.txt

cdk deploy \
  --method=direct \
  --parameters SesSender=$SENDER \
  --parameters SesRecipient=$RECIPIENT

echo ""
echo "Uploading assets to s3://research-raidar-${CONFIG_ENV} ..."
aws s3 cp ../config/config.json "s3://research-raidar-${CONFIG_ENV}/config.json"
aws s3 cp ../resources/research-raidar-logo.png "s3://research-raidar-${CONFIG_ENV}/research-raidar-logo.png"
echo "Assets uploaded."
