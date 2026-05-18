# Slack Test-Case Bot 🤖

> AI-powered QA test case generator — `/generate-tests` slash command for Slack.  
> Built by the QA Architect team. Deployed on AWS Lambda + API Gateway.

---

## What it does

Type `/generate-tests <description or JIRA ticket>` in any Slack channel and the bot returns structured, edge-case-focused test cases within 30 seconds — covering boundary values, concurrency, security, locale, session timing, and Editage-specific business rules pulled from the internal knowledge base.

```
/generate-tests login flow for the editor dashboard
/generate-tests PROJ-123
/generate-tests PROJ-123 focus on the OTP flow
```

**Output example:**
```
### 1. Happy Path
TC-1 | Successful login for internal SSO user ...

### 9. KB-Informed Scenarios
TC-11 | Rate limit: >3 OTP sends/min returns 429 ...

📚 KB files used: sample_api.txt, sample_overview.md, Eddie_US.txt
```

---

## Architecture

```
Slack User  →  /generate-tests
                    │
                    ▼
           API Gateway (HTTP API)
                    │
          ┌─────────┴──────────┐
          │  Lambda invocation #1│  ← acks Slack within 3 s
          │  (ack only)         │
          └─────────┬──────────┘
                    │ self-invokes via AWS SDK
          ┌─────────┴──────────┐
          │  Lambda invocation #2│  ← heavy processing (15–30 s)
          │  OpenAI + ChromaDB  │
          │  + Jira REST API    │
          └─────────┬──────────┘
                    │ POST to response_url
                    ▼
             Slack channel
```

**Why two Lambda invocations?**  
Slack enforces a hard 3-second HTTP response deadline on slash commands. All AI processing runs in a deferred second invocation so Slack never times out. This is slack_bolt's standard lazy-listener pattern.

**Stack:**
| Layer | Technology |
|-------|-----------|
| Runtime | AWS Lambda — Python 3.12 container image |
| API | AWS HTTP API Gateway |
| IaC | AWS SAM / CloudFormation (`template.yaml`) |
| AI | OpenAI `gpt-4o-mini` + `text-embedding-3-small` |
| Vector store | ChromaDB (ephemeral `/tmp`, re-indexes on cold start) |
| Secrets | AWS SSM Parameter Store (SecureString) |
| Logs | CloudWatch Logs (30-day retention) |

---

## Quick Start for DevOps

### Prerequisites
```bash
brew install awscli aws-sam-cli   # or equivalent for your OS
# Docker must be running
# AWS credentials must be configured with sufficient permissions
```

### Step 1 — Populate secrets in AWS SSM (one-time)
```bash
cp .env.example .env
# Fill in .env with real values (get from the QA team via secure channel)

./deploy/setup_ssm_secrets.sh --region ap-south-1
```

### Step 2 — Deploy
```bash
./deploy/deploy.sh --region ap-south-1 --env production
```

This single command:
1. Builds the Docker image (`linux/amd64`, Python 3.12 Lambda base)
2. Creates the ECR repository if it doesn't exist
3. Pushes the image to ECR
4. Runs `sam deploy` — creates Lambda, API Gateway, IAM role, CloudWatch log group
5. Prints the **Slack Request URL** to configure in the Slack app

### Step 3 — Configure Slack
Paste the printed URL into:  
**api.slack.com → Your App → Slash Commands → `/generate-tests` → Request URL**

---

## Secrets Reference

All secrets live in AWS SSM Parameter Store under `/slack-testcase-bot/`.  
`deploy/setup_ssm_secrets.sh` creates them from your `.env` file.

| SSM Parameter | Description | Where to get it |
|---------------|-------------|-----------------|
| `/slack-testcase-bot/SLACK_BOT_TOKEN` | Slack bot OAuth token | api.slack.com → OAuth & Permissions |
| `/slack-testcase-bot/SLACK_SIGNING_SECRET` | Request signature verification | api.slack.com → Basic Information |
| `/slack-testcase-bot/OPENAI_API_KEY` | OpenAI API key | platform.openai.com/api-keys |
| `/slack-testcase-bot/JIRA_BASE_URL` | Jira instance URL | e.g. `https://cactustech.atlassian.net` |
| `/slack-testcase-bot/JIRA_EMAIL` | Jira service account email | — |
| `/slack-testcase-bot/JIRA_API_TOKEN` | Jira API token | id.atlassian.com → Security → API tokens |

---

## Repository Structure

```
slack-testcase-bot/
│
├── app.py                     # Core bot logic
│   ├── Jira ADF parser        #   — extracts plain text from Jira API v3 descriptions
│   ├── ChromaDB RAG           #   — retrieves relevant KB context per query
│   ├── Prompt engine          #   — 9-category edge-case focused prompt
│   └── Slack handlers         #   — lazy listener (ack + deferred processing)
│
├── lambda_function.py         # Lambda entry point — wires lazy listener self-invoke
├── Dockerfile                 # Container image (Python 3.12, linux/amd64)
├── template.yaml              # SAM/CloudFormation — Lambda + API GW + IAM
├── samconfig.toml             # SAM CLI config (region, stack name, environments)
├── requirements.lambda.txt    # Python dependencies (installed in Docker image)
│
├── editage_kb/                # Knowledge base — bundled into container at build time
│   ├── Eddie_US.txt           #   Editage US services & business rules
│   ├── Editage_Korea_MCP.txt  #   Korea-specific plans
│   ├── Japan_MCP1.txt         #   Japan-specific plans
│   ├── MCP2.txt               #   Additional MCP rules
│   ├── ROW MCP.txt            #   Rest-of-world plans
│   ├── chinaNew.txt           #   China-specific plans
│   ├── sample_api.txt         #   API contracts (OTP limits, error codes)
│   └── sample_overview.md     #   Auth & OTP business rules
│
├── deploy/
│   ├── setup_ssm_secrets.sh   # One-time: push .env values to SSM
│   ├── build_and_push.sh      # Build Docker image + push to ECR
│   └── deploy.sh              # Full pipeline (build → push → sam deploy)
│
├── .env.example               # Safe credentials template — copy to .env locally
├── run_local.py               # Local Socket Mode server (dev/testing only)
└── setup_local.sh             # Local dev environment setup (dev/testing only)
```

---

## Updating the Knowledge Base

The KB files in `editage_kb/` are bundled into the Docker image at build time.

To add or update KB content:
1. Edit / add `.txt` or `.md` files in `editage_kb/`
2. Redeploy: `./deploy/deploy.sh` (builds a new image with the updated KB)

No code changes required.

---

## Lambda Configuration

| Setting | Value |
|---------|-------|
| Runtime | Container image — Python 3.12 |
| Handler | `lambda_function.handler` |
| Timeout | 300 s (5 min) |
| Memory | 1024 MB |
| Architecture | x86\_64 |
| IAM | Self-invocation (`lambda:InvokeFunction`) for lazy listeners |

---

## Monitoring & Troubleshooting

```bash
# Tail live logs
aws logs tail /aws/lambda/slack-testcase-bot-production --follow --region ap-south-1

# Common issues
# "This request took too long"  → Check SLACK_BOLT_LAZY_LAMBDA_FUNCTION_NAME is set
#                                  and IAM self-invocation policy is attached
# "SSM parameter not found"     → Run deploy/setup_ssm_secrets.sh first
# "No KB docs found"            → editage_kb/ is empty — add files and redeploy
# Cold start >60s               → Normal on first invocation (ChromaDB re-indexes)
#                                  Subsequent calls are fast (container reuse)
```

Full troubleshooting guide: [DEPLOYMENT.md](DEPLOYMENT.md)

---

## Local Testing (QA / Dev only)

```bash
./setup_local.sh                          # one-time setup
.dev-venv/bin/python run_local.py         # start Socket Mode server
# Then use /generate-tests in Slack
```

---

*Built by the QA Architect team · Questions: ping @divyesh on Slack*
