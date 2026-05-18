# Slack Test-Case Bot — AWS Deployment Guide

## Overview

This bot exposes a `/testcases` Slack slash command that generates QA test cases using OpenAI GPT and an internal knowledge base (ChromaDB + RAG).

**Runtime target:** AWS Lambda (container image) + HTTP API Gateway  
**Language:** Python 3.12  
**IaC:** AWS SAM (`template.yaml`)

---

## Architecture

```
Slack user types /testcases <text or JIRA-KEY>
        │
        ▼
  Slack Platform
        │  HTTPS POST (3-second SLA)
        ▼
  API Gateway (HTTP API)
        │
        ▼
  Lambda invocation #1  ──→  calls ack() → returns HTTP 200 to Slack immediately
        │
        │  slack_bolt lazy listener: Lambda invokes itself via AWS SDK
        ▼
  Lambda invocation #2  ──→  OpenAI + ChromaDB → POST result to Slack response_url
```

**Why two Lambda invocations?**  
Slack requires an HTTP 200 acknowledgement within 3 seconds. OpenAI + ChromaDB processing easily takes 15–30 s. `slack_bolt`'s lazy listener pattern solves this by splitting the work: the first call acks immediately, the second call does the heavy lifting and posts back via Slack's `response_url`.

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| AWS CLI | v2 | `brew install awscli` |
| SAM CLI | latest | `brew install aws-sam-cli` |
| Docker | any recent | docker.com |
| Python | 3.12+ | (local dev only) |

AWS credentials must have permissions for: ECR, Lambda, IAM, CloudFormation, API Gateway, SSM, CloudWatch Logs.

---

## One-Time Setup (run once per AWS account / region)

### 1. Clone the repo

```bash
git clone <your-repo-url>
cd slack-testcase-bot
```

### 2. Put secrets into SSM Parameter Store

The bot reads all credentials from SSM — **never hardcode secrets**.

```bash
# Copy the template and fill in real values
cp .env.example .env
# Edit .env with your actual Slack tokens, OpenAI key, Jira credentials

# Push to SSM (requires AWS credentials)
./deploy/setup_ssm_secrets.sh --region ap-south-1
```

This creates the following SSM SecureString parameters:

| SSM Path | Description |
|----------|-------------|
| `/slack-testcase-bot/SLACK_BOT_TOKEN` | Slack bot OAuth token |
| `/slack-testcase-bot/SLACK_SIGNING_SECRET` | Slack signing secret |
| `/slack-testcase-bot/OPENAI_API_KEY` | OpenAI API key |
| `/slack-testcase-bot/JIRA_BASE_URL` | Jira base URL |
| `/slack-testcase-bot/JIRA_EMAIL` | Jira user email |
| `/slack-testcase-bot/JIRA_API_TOKEN` | Jira API token |

---

## Deployment

### Full deploy (build + push + CloudFormation)

```bash
./deploy/deploy.sh --region ap-south-1 --env production
```

This script:
1. Builds the Docker image (`linux/amd64`, Python 3.12 Lambda base image)
2. Creates the ECR repository if it doesn't exist
3. Pushes the image to ECR
4. Runs `sam deploy` which creates/updates the CloudFormation stack:
   - Lambda function (`slack-testcase-bot-production`)
   - HTTP API Gateway with `/slack/events` POST route
   - IAM execution role with self-invocation permission
   - CloudWatch log group (30-day retention)
5. Prints the **Slack Request URL** to copy into the Slack app config

### Subsequent deploys (code change)

```bash
./deploy/deploy.sh
```

### Deploy staging environment

```bash
./deploy/deploy.sh --env staging
```

---

## Post-Deploy: Configure the Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → select your app
2. Navigate to **Slash Commands** → find `/testcases` → click **Edit**
3. Set **Request URL** to the URL printed at the end of `deploy.sh`:
   ```
   https://<api-id>.execute-api.ap-south-1.amazonaws.com/slack/events
   ```
4. Save changes
5. Reinstall the app to your workspace if prompted

---

## Lambda Configuration Reference

| Setting | Value |
|---------|-------|
| Runtime | Container image (Python 3.12) |
| Handler | `lambda_function.handler` |
| Timeout | 300 seconds (5 minutes) |
| Memory | 1024 MB |
| Architecture | `x86_64` |

**Environment variables set automatically by `template.yaml`:**

| Variable | Source |
|----------|--------|
| `SLACK_BOT_TOKEN` | SSM |
| `SLACK_SIGNING_SECRET` | SSM |
| `OPENAI_API_KEY` | SSM |
| `JIRA_BASE_URL` | SSM |
| `JIRA_EMAIL` | SSM |
| `JIRA_API_TOKEN` | SSM |
| `PERSIST_DIR` | Hard-coded to `/tmp/chroma_store` |

**IAM permissions granted to the Lambda role:**

- `AWSLambdaBasicExecutionRole` (CloudWatch Logs)
- `lambda:InvokeFunction` on itself (required for lazy listeners)

---

## Knowledge Base (ChromaDB / RAG)

The bot uses files in `editage_kb/` (`.txt` and `.md`) to provide domain-specific context to the test case generator.

- KB files are **bundled into the container image** at build time.
- On each Lambda cold start, if `/tmp/chroma_store` is empty the bot re-indexes from the bundled files (takes ~20 s on first cold start).
- Subsequent requests within the same container reuse the in-memory index.

**To update the knowledge base:**
1. Add/edit files in `editage_kb/`
2. Redeploy: `./deploy/deploy.sh` (this rebuilds and pushes a new image)

> **Note:** `/tmp/chroma_store` is ephemeral — it's reset whenever a new Lambda container starts (cold start). This is acceptable for small KBs. For large KBs or to eliminate re-indexing latency, mount an EFS volume and set `PERSIST_DIR` to the EFS mount path.

---

## Validation

After deploying, verify the bot works end-to-end:

```bash
# In Slack, type:
/testcases login flow for the editor dashboard

# Or with a Jira ticket:
/testcases PROJ-123
```

You should see:
1. An immediate "⏳ Generating…" message (within 3 seconds)
2. The full test cases posted as a follow-up message (within 30 seconds)

**Check CloudWatch Logs:**

```bash
aws logs tail /aws/lambda/slack-testcase-bot-production --follow --region ap-south-1
```

---

## Rollback

To roll back to a previous container image:

```bash
# List recent images in ECR
aws ecr describe-images \
  --repository-name slack-testcase-bot \
  --region ap-south-1 \
  --query 'sort_by(imageDetails, &imagePushedAt)[-5:].imageTags' \
  --output table

# Deploy a specific tag
IMAGE_TAG=<previous-tag> ./deploy/deploy.sh
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Slash command shows "This request took too long" | Lambda not returning ack within 3 s | Ensure `SLACK_BOLT_LAZY_LAMBDA_FUNCTION_NAME` env var is set and IAM self-invocation policy is in place |
| `AccessDeniedException` in logs | Lambda role missing `lambda:InvokeFunction` | Redeploy — `template.yaml` provisions this policy |
| "No KB docs found" in logs | `editage_kb/` is empty | Add `.txt`/`.md` files to `editage_kb/` and redeploy |
| Cold start takes 60+ seconds | KB re-indexing + OpenAI embeddings on every cold start | Mount EFS for persistent ChromaDB, or pre-build the Chroma index and bundle it in the image |
| `chromadb` import errors | Wrong platform binaries in image | Always build with `--platform linux/amd64` (the deploy script does this) |
| SSM parameter not found | Secrets not set up | Run `./deploy/setup_ssm_secrets.sh` |

---

## Files Reference

```
slack-testcase-bot/
├── app.py                    # Slack bot logic (lazy listeners, ChromaDB, OpenAI, Jira)
├── lambda_function.py        # Lambda entry point (wires up lazy listener self-invoke)
├── Dockerfile                # Lambda container image definition
├── template.yaml             # SAM/CloudFormation IaC
├── samconfig.toml            # SAM CLI config (region, stack name)
├── requirements.lambda.txt   # Python deps installed in the Docker image
├── .env.example              # Safe template — copy to .env for local dev
├── editage_kb/               # Knowledge base files (.txt, .md) — bundled into image
└── deploy/
    ├── setup_ssm_secrets.sh  # One-time: push .env values to SSM
    ├── build_and_push.sh     # Build Docker image and push to ECR
    └── deploy.sh             # Full deployment pipeline (build + sam deploy)
```
