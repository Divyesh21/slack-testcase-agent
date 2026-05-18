#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy/deploy.sh
#
# Full deployment pipeline: build image → push to ECR → sam deploy.
# Run this from the repo root.
#
# Usage:
#   ./deploy/deploy.sh [--region ap-south-1] [--env production] [--tag latest]
#
# Prerequisites (one-time):
#   1. AWS CLI configured with sufficient permissions
#   2. SAM CLI installed  (brew install aws-sam-cli)
#   3. Docker running
#   4. SSM secrets populated  (run deploy/setup_ssm_secrets.sh first)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-south-1}"
ENVIRONMENT="${ENVIRONMENT:-production}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

while [[ $# -gt 0 ]]; do
  case $1 in
    --region)  AWS_REGION="$2";  shift 2 ;;
    --env)     ENVIRONMENT="$2"; shift 2 ;;
    --tag)     IMAGE_TAG="$2";   shift 2 ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Slack Test-Case Bot — full deploy"
echo " Environment : ${ENVIRONMENT}"
echo " Region      : ${AWS_REGION}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Step 1: Build & push container image ──────────────────────────────────────
AWS_REGION="${AWS_REGION}" ENVIRONMENT="${ENVIRONMENT}" IMAGE_TAG="${IMAGE_TAG}" \
  bash "${SCRIPT_DIR}/build_and_push.sh"

# IMAGE_URI is exported by build_and_push.sh
if [[ -z "${IMAGE_URI:-}" ]]; then
  AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
  IMAGE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/slack-testcase-bot:${IMAGE_TAG}"
fi

# ── Step 2: sam deploy ────────────────────────────────────────────────────────
echo ""
echo "→ Deploying CloudFormation stack via SAM (env=${ENVIRONMENT})…"
cd "${REPO_ROOT}"

sam deploy \
  --config-env "${ENVIRONMENT}" \
  --region "${AWS_REGION}" \
  --parameter-overrides \
      "ImageUri=${IMAGE_URI}" \
      "Environment=${ENVIRONMENT}" \
  --no-fail-on-empty-changeset

# ── Step 3: Print the Slack endpoint URL ─────────────────────────────────────
STACK_NAME="slack-testcase-bot"
[[ "${ENVIRONMENT}" != "production" ]] && STACK_NAME="${STACK_NAME}-${ENVIRONMENT}"

ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${AWS_REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='SlackEndpointUrl'].OutputValue" \
  --output text)

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ✅  Deploy complete!"
echo ""
echo " Slack Request URL:"
echo "   ${ENDPOINT}"
echo ""
echo " Paste this URL into your Slack app configuration:"
echo "   Slack API Console → Your App → Slash Commands → /testcases → Request URL"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
