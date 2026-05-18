# ─────────────────────────────────────────────────────────────────────────────
# Slack Test-Case Bot — Lambda container image
#
# Why container instead of zip?
#   ChromaDB ships Rust/C++ native binaries that easily exceed Lambda's 50 MB
#   zip limit.  Container images support up to 10 GB and guarantee Linux-
#   compatible binaries (unlike the macOS-built lambda-package/ directory).
#
# IMPORTANT — always build with --platform linux/amd64.
#   Lambda runs on Linux x86_64. Building on a Mac (arm64) without this flag
#   produces binaries that silently fail on Lambda.
#
# Build & push (see deploy/build_and_push.sh for the full automated version):
#   docker build --platform linux/amd64 -t slack-testcase-bot .
#   docker tag  slack-testcase-bot <account_id>.dkr.ecr.<region>.amazonaws.com/slack-testcase-bot:latest
#   docker push <account_id>.dkr.ecr.<region>.amazonaws.com/slack-testcase-bot:latest
# ─────────────────────────────────────────────────────────────────────────────

FROM public.ecr.aws/lambda/python:3.12

# ── System dependencies ───────────────────────────────────────────────────────
# sqlite3 is required by ChromaDB's embedded mode.
RUN dnf install -y sqlite-devel gcc && dnf clean all

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.lambda.txt .
RUN pip install --no-cache-dir -r requirements.lambda.txt -t "${LAMBDA_TASK_ROOT}"

# ── Application code ──────────────────────────────────────────────────────────
COPY app.py lambda_function.py "${LAMBDA_TASK_ROOT}/"

# Knowledge-base files (bundled into the image at build time).
# On Lambda cold start, if /tmp/chroma_store is empty the app re-indexes from
# these files.  Rebuild and redeploy the image whenever the KB content changes.
COPY editage_kb/ "${LAMBDA_TASK_ROOT}/editage_kb/"

# ── Lambda handler ────────────────────────────────────────────────────────────
CMD ["lambda_function.handler"]
