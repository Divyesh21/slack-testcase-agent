import os
import re
from typing import List, Dict, Any

from dotenv import load_dotenv
from flask import Flask, request
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler

# ────────────────────────────────────────────────────────────────────────────────
# Load environment BEFORE any os.getenv calls
# ────────────────────────────────────────────────────────────────────────────────
load_dotenv(".env")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")

# JIRA
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")

# KB / RAG
KB_DIR = os.getenv("KB_DIR", "./editage_kb")
PERSIST_DIR = os.getenv("PERSIST_DIR", "./chroma_store")
EMB_MODEL = os.getenv("EMB_MODEL", "text-embedding-3-large")
GEN_MODEL = os.getenv("GEN_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# OpenAI (v1) client
from openai import OpenAI
openai_client = OpenAI()  # uses OPENAI_API_KEY

# Boot prints (masked)
def _mask(s: str, keep: int = 6) -> str:
    return (s[:keep] + "…" + s[-2:]) if s and len(s) > keep + 2 else s

print("[BOOT] Using port:", os.getenv("FLASK_RUN_PORT"))
print("[BOOT] SLACK_BOT_TOKEN:", _mask(SLACK_BOT_TOKEN))
print("[BOOT] SLACK_SIGNING_SECRET length:", len(SLACK_SIGNING_SECRET or ""))
print("[BOOT] OPENAI_API_KEY:", _mask(OPENAI_API_KEY))

# ────────────────────────────────────────────────────────────────────────────────
# ChromaDB — no embedding_function here (we pass vectors manually)
# ────────────────────────────────────────────────────────────────────────────────
collection = None
try:
    import chromadb
    chroma_client = chromadb.PersistentClient(path=PERSIST_DIR)
    collection = chroma_client.get_or_create_collection(name="kb_collection")
    print("[BOOT] Chroma collection ready: kb_collection")
except Exception as e:
    collection = None
    print(f"[BOOT] Chroma disabled (will run without KB): {e}")

# ────────────────────────────────────────────────────────────────────────────────
# Helpers: embeddings + retrieval
# ────────────────────────────────────────────────────────────────────────────────
def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts using OpenAI v1 embeddings API."""
    resp = openai_client.embeddings.create(model=EMB_MODEL, input=texts)
    return [d.embedding for d in resp.data]

JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")

def get_context(query: str, k: int = 6) -> List[str]:
    """Retrieve top-k context strings from Chroma using query embeddings."""
    if not collection or not query:
        return []
    try:
        qvec = _embed_texts([query])[0]
        results = collection.query(query_embeddings=[qvec], n_results=k)
        docs = results.get("documents", [[]])[0]
        return [d for d in docs if d]
    except Exception as e:
        print("[CTX] Retrieval error:", e)
        return []

# ────────────────────────────────────────────────────────────────────────────────
# JIRA fetch + normalization
# ────────────────────────────────────────────────────────────────────────────────
import requests
from requests.auth import HTTPBasicAuth

def _flatten_adf(node: Any) -> str:
    """Very small ADF-to-text flattener for JIRA Cloud."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        t = node.get("text", "")
        parts = []
        if t:
            parts.append(t)
        for c in node.get("content", []) or []:
            parts.append(_flatten_adf(c))
        return " ".join([p for p in parts if p])
    if isinstance(node, list):
        return "\n".join(filter(None, (_flatten_adf(x) for x in node)))
    return ""

def fetch_jira_issue(key: str) -> Dict[str, Any]:
    if not (JIRA_BASE_URL and JIRA_EMAIL and JIRA_API_TOKEN):
        raise RuntimeError("JIRA credentials missing in .env")

    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{key}?expand=renderedFields"
    resp = requests.get(url, auth=HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN))
    if resp.status_code == 404:
        raise ValueError(f"JIRA issue not found: {key}")
    resp.raise_for_status()
    data = resp.json()

    fields = data.get("fields", {})
    summary = fields.get("summary") or ""
    desc = fields.get("description")
    description_text = desc if isinstance(desc, str) else _flatten_adf(desc)

    acceptance_criteria = ""
    if description_text:
        m = re.search(r"(?is)acceptance\s*criteria[:\-]*\s*(.+)$", description_text)
        if m:
            acceptance_criteria = m.group(1).strip()

    return {
        "key": key,
        "summary": (summary or "").strip(),
        "description": (description_text or "").strip(),
        "acceptance_criteria": acceptance_criteria,
    }

# ────────────────────────────────────────────────────────────────────────────────
# Prompting
# ────────────────────────────────────────────────────────────────────────────────
def build_prompt(user_text: str, jira: Dict[str, str], context_blocks: List[str]) -> str:
    parts = [
        "You are a senior QA test designer for Editage (web + mobile). Generate thorough, crisp, non-redundant test cases in Markdown.",
        "Each test case must have: ID, Title, Preconditions, Steps, Expected Result.",
        "Also include Negative, Boundary, Security, Accessibility, i18n, and Performance scenarios where appropriate.",
    ]
    if jira:
        parts.append(f"\n[JIRA] Key: {jira.get('key','')}")
        if jira.get("summary"):
            parts.append(f"Summary: {jira['summary']}")
        if jira.get("description"):
            parts.append(f"Description:\n{jira['description']}")
        if jira.get("acceptance_criteria"):
            parts.append(f"Acceptance Criteria:\n{jira['acceptance_criteria']}")
    if context_blocks:
        parts.append("\n[Product Context from KB] (most relevant first)")
        for i, block in enumerate(context_blocks, 1):
            parts.append(f"[CTX-{i}]\n{block}\n")
    parts.append("\nUser addendum (if any):\n" + (user_text or ""))
    parts.append("\nNow output a prioritized list of test cases grouped by: Functional, Negative/Boundary, Security, Accessibility, Performance.")
    return "\n\n".join(parts)

def generate_testcases(user_text: str, jira_key: str | None) -> str:
    jira = None
    query_for_kb = user_text
    if jira_key:
        jira = fetch_jira_issue(jira_key)
        query_for_kb = " ".join(filter(None, [jira.get("summary", ""), jira.get("description", "")]))
    context = get_context(query_for_kb, k=6)
    prompt = build_prompt(user_text, jira or {}, context)

    completion = openai_client.chat.completions.create(
        model=GEN_MODEL,
        temperature=0.2,
        messages=[
            {"role": "system", "content": "You generate high-quality QA test suites in Markdown."},
            {"role": "user", "content": prompt},
        ],
    )
    return completion.choices[0].message.content.strip()

# ────────────────────────────────────────────────────────────────────────────────
# Flask + Slack setup
# ────────────────────────────────────────────────────────────────────────────────
flask_app = Flask(__name__)
bolt_app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
handler = SlackRequestHandler(bolt_app)

@flask_app.route("/", methods=["GET"])
def health():
    return {"ok": True, "app": "slack-testcase-bot", "model": GEN_MODEL}, 200

@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

# Support whichever slash command you configured
@bolt_app.command("/generate")
@bolt_app.command("/generate-tests")
@bolt_app.command("/testcases")
def slash_testcases(ack, respond, command, logger):
    ack()
    user_text = (command.get("text") or "").strip()
    jira_key = None

    m = JIRA_KEY_RE.search(user_text)
    if m:
        jira_key = m.group(1)
        user_text = (JIRA_KEY_RE.sub("", user_text) or "").strip()

    if not jira_key and not user_text:
        respond(
            response_type="ephemeral",
            text=(
                "Usage: */generate <JIRA-KEY> [notes]*  (or */testcases*, */generate-tests*)\n\n"
                "Examples:\n"
                "• /generate EDIT-123\n"
                "• /generate EDIT-456 Include mobile OTP edge cases\n"
                "• /generate Chinese localization workflow for login\n"
            ),
        )
        return

    try:
        md = generate_testcases(user_text=user_text, jira_key=jira_key)
        if len(md) > 28000:
            md = md[:27950] + "\n\n_…truncated due to Slack limits…_"
        respond(response_type="ephemeral", text=md)
    except Exception as e:
        logger.exception("Generation failed")
        respond(response_type="ephemeral", text=f"Generation failed: `{e}`")

# Optional: quick KB debug command
@bolt_app.command("/kbsearch")
def kbsearch(ack, respond, command, logger):
    ack()
    q = (command.get("text") or "").strip()
    if not q:
        respond(response_type="ephemeral", text="Usage: /kbsearch <query>")
        return
    if not collection:
        respond(response_type="ephemeral", text="KB is not available.")
        return
    try:
        qvec = _embed_texts([q])[0]
        res = collection.query(query_embeddings=[qvec], n_results=3)
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        lines = []
        for i, (d, m) in enumerate(zip(docs or [], metas or []), 1):
            src = (m or {}).get("filename") or (m or {}).get("source_path") or "unknown"
            snippet = (d[:500] + "…") if d and len(d) > 500 else (d or "")
            lines.append(f"*{i}.* _{src}_\n```{snippet}```")
        respond(response_type="ephemeral", text="\n\n".join(lines) or "No results.")
    except Exception as e:
        logger.exception("kbsearch failed")
        respond(response_type="ephemeral", text=f"kbsearch failed: `{e}`")

# ────────────────────────────────────────────────────────────────────────────────
# Socket Mode + optional Flask health endpoint
# ────────────────────────────────────────────────────────────────────────────────
import logging
import threading
from slack_bolt.adapter.socket_mode import SocketModeHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("runner")

# If you want to keep a local health endpoint (not required for Socket Mode),
# we’ll run Flask in a background thread so it doesn’t block the socket loop.
def start_flask():
    port = int(os.getenv("FLASK_RUN_PORT", "5050"))
    log.info("[HTTP] Starting Flask health server on port %s", port)
    # Keep ONLY non-Slack routes in Flask when using Socket Mode
    # (Slack events/commands will arrive over the WebSocket, not HTTP)
    flask_app.run(host="127.0.0.1", port=port, debug=True)

def start_socket_mode():
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token:
        raise RuntimeError("SLACK_APP_TOKEN is required for Socket Mode (set it in .env).")
    log.info("[SOCKET] Starting Slack Socket Mode…")
    handler = SocketModeHandler(bolt_app, app_token)
    handler.start()  # blocking

if __name__ == "__main__":
    # Print one consolidated boot line
    print(f"[BOOT] GEN_MODEL={GEN_MODEL} | PERSIST_DIR={PERSIST_DIR} | SOCKET_MODE=True")

    # Optional: run Flask (health/debug) in background; comment out if you don’t need it.
    threading.Thread(target=start_flask, daemon=True).start()

    # Always run Socket Mode in the main thread (blocking)
    start_socket_mode()
