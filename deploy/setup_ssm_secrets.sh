#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy/setup_ssm_secrets.sh
#
# One-time script: writes all bot secrets into AWS SSM Parameter Store
# as SecureString parameters.  Run this ONCE before the first deployment.
#
# The values are sourced from your local .env file — NEVER commit .env to git.
#
# Usage:
#   cp .env.example .env          # fill in real values
#   ./deploy/setup_ssm_secrets.sh [--region ap-south-1]
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-south-1}"

while [[ $# -gt 0 ]]; do
  case $1 in
    --region) AWS_REGION="$2"; shift 2 ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: .env file not found at ${ENV_FILE}"
  echo "Copy .env.example to .env and fill in the real values first."
  exit 1
fi

# Load the .env file
set -o allexport
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +o allexport

put_param() {
  local name="$1"
  local value="$2"
  echo "  → Putting ${name}"
  aws ssm put-parameter \
    --name "${name}" \
    --value "${value}" \
    --type SecureString \
    --overwrite \
    --region "${AWS_REGION}" \
    > /dev/null
}

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Writing secrets to SSM Parameter Store"
echo " Region: ${AWS_REGION}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

put_param "/slack-testcase-bot/SLACK_BOT_TOKEN"     "${SLACK_BOT_TOKEN}"
put_param "/slack-testcase-bot/SLACK_SIGNING_SECRET" "${SLACK_SIGNING_SECRET}"
put_param "/slack-testcase-bot/OPENAI_API_KEY"       "${OPENAI_API_KEY}"
put_param "/slack-testcase-bot/JIRA_BASE_URL"        "${JIRA_BASE_URL}"
put_param "/slack-testcase-bot/JIRA_EMAIL"           "${JIRA_EMAIL}"
put_param "/slack-testcase-bot/JIRA_API_TOKEN"       "${JIRA_API_TOKEN}"

echo ""
echo "✅  All secrets written to SSM."
echo "   You can now run  ./deploy/deploy.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
