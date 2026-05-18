# JIRA TICKET — Copy-paste this into Jira

---

## Summary
Deploy AI-Powered Slack Test-Case Bot to AWS Lambda (Container Image) + API Gateway — Move from Local to Production

---

## Fields

| Field | Value |
|-------|-------|
| **Issue Type** | Task |
| **Priority** | High |
| **Component** | Infrastructure / DevOps |
| **Reporter** | Divyesh Gavade (QA Architect) |
| **Labels** | `aws`, `lambda`, `slack-bot`, `qa-tooling`, `deployment` |
| **Attachment** | `slack-testcase-bot-devops-handoff.zip` ← shared separately |

---

## Description

### 🧠 Background & Business Context

The QA Architect team has built an internal AI-powered bot that generates structured, edge-case-focused QA test cases directly inside Slack. A QA engineer or developer types a slash command with a feature description or a Jira ticket ID, and the bot returns a complete set of test cases — including boundary values, concurrency issues, security gaps, locale edge cases, and scenarios derived from Editage's internal business rules — within 30 seconds.

Currently this bot runs on the QA Architect's local machine. This means:

- It only works when that machine is on and the local server is running
- No other team member can use it
- It cannot be reliably shared across QA, dev, or product teams

**The goal of this ticket is to move the bot off the local machine and onto AWS so that every team member can use `/generate-tests` in Slack at any time, without depending on anyone's laptop.**

---

### 🤖 What the Bot Does (for context)

Any team member in Slack types:

```
/generate-tests login flow for the editor dashboard
/generate-tests PROJ-123
/generate-tests PROJ-123 focus on OTP flow
```

The bot:
1. Fetches the Jira ticket description (if a ticket ID is provided) via Jira REST API
2. Searches an internal knowledge base (Editage-specific business rules, service limits, API contracts) using vector similarity search
3. Sends both to OpenAI GPT with a carefully engineered prompt
4. Returns structured test cases in 9 categories: Happy Path, Boundary & Edge Values, Negative Validation, State & Concurrency, Permission & Security, External Dependency Failures, Locale & Internationalisation, Session & Timing, and KB-Informed Scenarios

The bot currently works and has been validated end-to-end locally.

---

### 🏗️ Architecture — What Needs to Be Deployed

```
Slack User  →  /generate-tests
                    │
                    ▼
         AWS HTTP API Gateway  (HTTPS endpoint)
                    │
         ┌──────────┴──────────┐
         │  Lambda Invocation #1│  ← returns HTTP 200 to Slack within 3 seconds
         │  (acknowledgement)   │    (Slack has a hard 3-second deadline)
         └──────────┬──────────┘
                    │  Lambda invokes itself (AWS SDK)
         ┌──────────┴──────────┐
         │  Lambda Invocation #2│  ← does the heavy work (15–30 seconds)
         │  OpenAI + ChromaDB  │    posts result back to Slack via response_url
         │  + Jira REST API    │
         └─────────────────────┘
```

**AWS Resources that need to be created (all automated via the deploy script):**

| Resource | Detail |
|----------|--------|
| ECR Repository | Stores the Docker container image |
| Lambda Function | `slack-testcase-bot-production` — Python 3.12, 1024 MB, 300s timeout |
| HTTP API Gateway | Exposes one POST endpoint: `/slack/events` |
| IAM Role | Allows Lambda to write CloudWatch logs + invoke itself (required for the 2-invocation pattern) |
| CloudWatch Log Group | `/aws/lambda/slack-testcase-bot-production` — 30 day retention |
| SSM Parameters | 6 SecureString parameters storing all credentials |

---

### 📦 Why a Docker Container Image — Not a Traditional Lambda Zip

> This is important context so you understand why the deployment works differently from a standard Lambda.

The bot uses **ChromaDB** (a vector database with Rust/C++ native binaries) for its internal knowledge base search. ChromaDB's compiled libraries are approximately **150 MB**.

AWS Lambda has a hard **50 MB limit on zip deployment packages**. This means the traditional approach — zip your code + pip packages, upload to Lambda — is not possible here.

The solution is a **Lambda container image**, which AWS supports natively and allows up to 10 GB. This is the standard AWS-recommended approach for AI/ML workloads on Lambda.

Additionally, the local development environment is macOS, which produces platform-specific binaries that would fail on Lambda's Linux runtime. The container image is built from `public.ecr.aws/lambda/python:3.12` (an official AWS base image), which guarantees Linux-compatible binaries.

**All of this is handled automatically by the deploy script — you do not need to manage it manually.**

---

### ✅ What You (DevOps) Need to Do

#### Prerequisites — Install these once

```bash
# AWS CLI (must be configured with credentials for the target account)
brew install awscli

# SAM CLI (AWS Serverless Application Model — used for CloudFormation deployment)
brew install aws-sam-cli

# Docker (must be running during deployment)
# Install from https://docker.com if not already installed
```

Confirm AWS credentials have permissions for:
`ECR`, `Lambda`, `IAM`, `CloudFormation`, `API Gateway`, `SSM Parameter Store`, `CloudWatch Logs`

---

#### Step 1 — Unzip the handoff package

```bash
unzip slack-testcase-bot-devops-handoff.zip
cd slack-testcase-bot
```

---

#### Step 2 — Populate secrets (one-time only)

The bot requires 6 credentials. These are stored in **AWS SSM Parameter Store as SecureString** — they never appear in code or config files.

I (Divyesh) will share the real credential values with you securely (via 1Password / secure Slack DM). Once you have them:

```bash
cp .env.example .env
# Fill in the 6 values in .env with the real credentials I share with you
```

Then push them to SSM:

```bash
./deploy/setup_ssm_secrets.sh --region ap-south-1
```

This creates the following SSM parameters:

| SSM Parameter Path | What It Is |
|-------------------|------------|
| `/slack-testcase-bot/SLACK_BOT_TOKEN` | Slack bot OAuth token (starts with `xoxb-`) |
| `/slack-testcase-bot/SLACK_SIGNING_SECRET` | Slack request verification secret |
| `/slack-testcase-bot/OPENAI_API_KEY` | OpenAI API key for GPT + embeddings |
| `/slack-testcase-bot/JIRA_BASE_URL` | `https://cactustech.atlassian.net` |
| `/slack-testcase-bot/JIRA_EMAIL` | Service account email for Jira API |
| `/slack-testcase-bot/JIRA_API_TOKEN` | Jira API token |

After this step, **delete the `.env` file** — secrets should live only in SSM.

---

#### Step 3 — Deploy everything

```bash
./deploy/deploy.sh --region ap-south-1 --env production
```

This single command does the following automatically:
1. Builds the Docker image (`linux/amd64`, Python 3.12 Lambda base)
2. Creates the ECR repository `slack-testcase-bot` if it doesn't exist
3. Authenticates Docker with ECR
4. Pushes the image to ECR
5. Runs `sam deploy` which creates/updates the CloudFormation stack with all AWS resources
6. Prints the **API Gateway HTTPS URL** at the end

The output will look like:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ✅  Deploy complete!

 Slack Request URL:
   https://abc123def.execute-api.ap-south-1.amazonaws.com/slack/events

 Paste this URL into your Slack app configuration:
   Slack API Console → Your App → Slash Commands → /generate-tests → Request URL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

#### Step 4 — Share the URL with me

Once deployment completes, share the printed API Gateway URL back with me (Divyesh). I will paste it into the Slack app configuration. You do not need access to the Slack API console.

---

### 📁 What's in the Handoff Zip

```
slack-testcase-bot/
├── README.md                     ← Start here — full reference doc
├── DEPLOYMENT.md                 ← Detailed deployment guide with troubleshooting
├── app.py                        ← Core bot logic (Slack handlers, OpenAI, ChromaDB, Jira)
├── lambda_function.py            ← AWS Lambda entry point
├── Dockerfile                    ← Container image definition
├── template.yaml                 ← CloudFormation/SAM — defines ALL AWS resources
├── samconfig.toml                ← SAM CLI config (region, stack name, environments)
├── requirements.lambda.txt       ← Python dependencies (installed inside Docker image)
├── .env.example                  ← Credential template — fill in and run setup script
├── .gitignore                    ← Excludes secrets and build artefacts from git
├── deploy/
│   ├── setup_ssm_secrets.sh      ← Step 2: push credentials to AWS SSM
│   ├── build_and_push.sh         ← Builds Docker image and pushes to ECR
│   └── deploy.sh                 ← Step 3: full deployment pipeline
└── editage_kb/                   ← Internal knowledge base (bundled into container)
    ├── Eddie_US.txt
    ├── Editage_Korea_MCP.txt
    ├── Japan_MCP1.txt
    ├── MCP2.txt
    ├── ROW MCP.txt
    ├── chinaNew.txt
    ├── sample_api.txt
    └── sample_overview.md
```

---

### 🔍 How to Verify the Deployment Worked

After deploying, verify end-to-end:

**1. Check Lambda is running:**
```bash
aws lambda get-function \
  --function-name slack-testcase-bot-production \
  --region ap-south-1 \
  --query 'Configuration.[FunctionName,State,LastUpdateStatus]'
```
Expected: `["slack-testcase-bot-production", "Active", "Successful"]`

**2. Check API Gateway URL is reachable:**
```bash
curl -X POST https://<your-api-id>.execute-api.ap-south-1.amazonaws.com/slack/events
# Expected: 403 or 400 (not 404 or 502) — means Lambda is running, Slack verification is working
```

**3. Check CloudWatch logs:**
```bash
aws logs tail /aws/lambda/slack-testcase-bot-production \
  --follow \
  --region ap-south-1
```

**4. End-to-end test in Slack** (I'll do this once you share the URL):
```
/generate-tests login flow for the editor dashboard
```
Expected: Immediate "⏳ Generating…" message, followed by full test cases within 30 seconds.

---

### ⚠️ Common Issues & Fixes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `AccessDeniedException` in logs | Lambda role missing self-invocation permission | Redeploy — `template.yaml` provisions this automatically |
| Slack shows "This request took too long" | Lambda not invoking itself for deferred processing | Check `SLACK_BOLT_LAZY_LAMBDA_FUNCTION_NAME` is set in Lambda env |
| `Parameter not found` during deploy | SSM secrets not created | Run `./deploy/setup_ssm_secrets.sh` first |
| Docker build fails | Docker not running or wrong platform | Ensure Docker is running; script uses `--platform linux/amd64` |
| `No space left` during ECR push | Large image (~1 GB) | Normal — ChromaDB + Python deps are large; ensure >2 GB disk free |

---

### 🔁 Future Deployments (when code or KB changes)

Any time the bot code or knowledge base files are updated:

```bash
./deploy/deploy.sh --region ap-south-1 --env production
```

That's the only command needed. No manual steps. The script handles building a new image and updating the Lambda.

---

### 📞 Contact

| Question | Contact |
|----------|---------|
| Bot behaviour, test case output, Slack config | Divyesh Gavade (QA Architect) |
| AWS permissions, account access, infra | DevOps team |
| OpenAI / Jira API issues | Divyesh Gavade |

Ping **@divyesh** on Slack for anything related to this ticket.

---

### ✅ Acceptance Criteria

- [ ] ECR repository `slack-testcase-bot` exists in `ap-south-1`
- [ ] CloudFormation stack `slack-testcase-bot` is in `CREATE_COMPLETE` or `UPDATE_COMPLETE` state
- [ ] Lambda function `slack-testcase-bot-production` is in `Active` state
- [ ] API Gateway endpoint returns non-502 response on POST
- [ ] CloudWatch log group `/aws/lambda/slack-testcase-bot-production` exists
- [ ] All 6 SSM parameters exist under `/slack-testcase-bot/`
- [ ] API Gateway URL shared back with Divyesh Gavade for Slack app configuration
