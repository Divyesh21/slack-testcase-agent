#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy/build_and_push.sh
#
# Builds the Lambda container image and pushes it to ECR.
# This is step 1 of the deployment — run it before `sam deploy`.
#
# Prerequisites:
#   - Docker running locally
#   - AWS CLI configured with credentials that can push to ECR
#   - The ECR repository already exists (created once via AWS Console or CLI)
#
# Usage:
#   ./deploy/build_and_push.sh [--region ap-south-1] [--env production]
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Defaults (override via flags or environment variables) ────────────────────
AWS_REGION="${AWS_REGION:-ap-south-1}"
ENVIRONMENT="${ENVIRONMENT:-production}"
ECR_REPO_NAME="slack-testcase-bot"
IMAGE_TAG="${IMAGE_TAG:-latest}"

# ── Parse flags ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --region)  AWS_REGION="$2";  shift 2 ;;
    --env)     ENVIRONMENT="$2"; shift 2 ;;
    --tag)     IMAGE_TAG="$2";   shift 2 ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

# ── Derived values ────────────────────────────────────────────────────────────
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
FULL_IMAGE_URI="${ECR_REGISTRY}/${ECR_REPO_NAME}:${IMAGE_TAG}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Slack Test-Case Bot — build & push"
echo " Region   : ${AWS_REGION}"
echo " Account  : ${AWS_ACCOUNT_ID}"
echo " Image    : ${FULL_IMAGE_URI}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Step 1: Create ECR repo if it doesn't exist ───────────────────────────────
echo "→ Ensuring ECR repository '${ECR_REPO_NAME}' exists…"
aws ecr describe-repositories \
    --repository-names "${ECR_REPO_NAME}" \
    --region "${AWS_REGION}" > /dev/null 2>&1 \
  || aws ecr create-repository \
    --repository-name "${ECR_REPO_NAME}" \
    --region "${AWS_REGION}" \
    --image-scanning-configuration scanOnPush=true \
    --encryption-configuration encryptionType=AES256 \
    > /dev/null
echo "   ✓ ECR repository ready."

# ── Step 2: Authenticate Docker with ECR ─────────────────────────────────────
echo "→ Logging Docker into ECR…"
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_REGISTRY}"
echo "   ✓ Docker authenticated."

# ── Step 3: Build the image ───────────────────────────────────────────────────
# Run from the repo root so COPY instructions in the Dockerfile resolve correctly.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "→ Building Docker image (platform linux/amd64)…"
docker build \
  --platform linux/amd64 \
  --tag "${FULL_IMAGE_URI}" \
  "${REPO_ROOT}"
echo "   ✓ Image built."

# ── Step 4: Push to ECR ───────────────────────────────────────────────────────
echo "→ Pushing image to ECR…"
docker push "${FULL_IMAGE_URI}"
echo "   ✓ Image pushed: ${FULL_IMAGE_URI}"

# ── Export for deploy.sh ──────────────────────────────────────────────────────
export IMAGE_URI="${FULL_IMAGE_URI}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Build complete. IMAGE_URI=${IMAGE_URI}"
echo " Next step: run  ./deploy/deploy.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
