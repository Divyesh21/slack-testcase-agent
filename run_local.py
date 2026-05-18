"""
Local development test server — Slack Socket Mode.

Runs the bot on your Mac without any AWS deployment or ngrok.
Slack sends events over a WebSocket to this process directly.

Usage:
    .dev-venv/bin/python run_local.py

How it works:
    - Reads credentials from .env
    - Connects to Slack via Socket Mode WebSocket (uses SLACK_APP_TOKEN)
    - You type  /testcases <text>  in Slack and results appear there
    - Lazy listeners run in background threads (same logic as Lambda, different concurrency)
"""

import os
import logging

# Load .env BEFORE importing app.py so os.environ is populated
from dotenv import load_dotenv
load_dotenv(".env")

# Validate required vars before doing anything heavy
REQUIRED = [
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
    "SLACK_APP_TOKEN",
    "OPENAI_API_KEY",
]
missing = [v for v in REQUIRED if not os.environ.get(v)]
if missing:
    raise SystemExit(
        f"\n❌  Missing environment variables: {', '.join(missing)}\n"
        "    Make sure .env is populated (copy .env.example and fill in real values).\n"
    )

if not os.environ["SLACK_APP_TOKEN"].startswith("xapp-"):
    raise SystemExit(
        "\n❌  SLACK_APP_TOKEN must start with 'xapp-'.\n"
        "    Get it from api.slack.com → Your App → Basic Information → App-Level Tokens.\n"
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger("run_local")

# Import the bolt app (ChromaDB init is lazy — happens on first /testcases call)
from app import bolt_app  # noqa: E402
from slack_bolt.adapter.socket_mode import SocketModeHandler  # noqa: E402



if __name__ == "__main__":
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log.info("  Slack Test-Case Bot — local Socket Mode server  ")
    log.info("  Connecting to Slack…")
    log.info("  Type  /testcases <text>  in any Slack channel.")
    log.info("  Press Ctrl-C to stop.")
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    handler = SocketModeHandler(bolt_app, os.environ["SLACK_APP_TOKEN"])
    handler.start()  # blocks until Ctrl-C
