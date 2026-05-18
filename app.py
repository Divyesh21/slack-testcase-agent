"""
Slack Test-Case Bot — application core.

Architecture note (AWS Lambda + slack_bolt):
  Slack requires an HTTP 200 acknowledgement within 3 seconds of a slash command.
  OpenAI + ChromaDB processing easily takes 10-30 s, so we use slack_bolt's
  *lazy listener* pattern:

    ack function   → runs in the first Lambda invocation, returns 200 to Slack immediately.
    lazy function  → slack_bolt invokes the same Lambda a *second* time (via the
                     AWS SDK) to run the heavy work and post back via response_url.

  For this to work, the Lambda execution role must have lambda:InvokeFunction on
  itself and the env var SLACK_BOLT_LAZY_LAMBDA_FUNCTION_NAME must be set
  (lambda_function.py does this automatically from AWS_LAMBDA_FUNCTION_NAME).
"""

import os
import re
import glob
import logging
from typing import List, Dict, Any, Optional

from slack_bolt import App

# ─────────────────────────────────────────────────────────────────────────────
# Configuration — read from environment variables.
# In Lambda these are set in the function config (or via SSM / Secrets Manager).
# Locally, export them in your shell or use `direnv` with a .env file.
# ─────────────────────────────────────────────────────────────────────────────
SLACK_BOT_TOKEN     = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]

JIRA_BASE_URL  = os.environ.get("JIRA_BASE_URL", "")
JIRA_EMAIL     = os.environ.get("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
KB_DIR       = os.environ.get("KB_DIR", os.path.join(BASE_DIR, "editage_kb"))
PERSIST_DIR  = os.environ.get("PERSIST_DIR", "/tmp/chroma_store")

EMB_MODEL = os.environ.get("EMB_MODEL", "text-embedding-3-small")
GEN_MODEL = os.environ.get("GEN_MODEL", "gpt-4o-mini")

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("qa-bot")

# ─────────────────────────────────────────────────────────────────────────────
# OpenAI client
# Reads OPENAI_API_KEY from the environment automatically.
# ─────────────────────────────────────────────────────────────────────────────
from openai import OpenAI
openai_client = OpenAI()

# ─────────────────────────────────────────────────────────────────────────────
# ChromaDB — lazy initialisation.
#
# We intentionally do NOT initialise ChromaDB at module-import time.
# Module-level code runs during Lambda cold start (before the handler is called),
# and triggering a full KB re-index there would push cold-start latency to 30+ s.
# Instead we initialise on the first real request via _get_collection().
# ─────────────────────────────────────────────────────────────────────────────
import chromadb

_chroma_client: Optional[chromadb.PersistentClient] = None
_collection = None
COLLECTION_NAME = "kb_collection"


def _get_collection():
    """Return the ChromaDB collection, initialising (and ingesting) if needed."""
    global _chroma_client, _collection

    if _collection is not None:
        return _collection

    log.info("Cold start: initialising ChromaDB at %s", PERSIST_DIR)
    _chroma_client = chromadb.PersistentClient(path=PERSIST_DIR)

    existing_names = [c.name for c in _chroma_client.list_collections()]
    if COLLECTION_NAME in existing_names:
        _collection = _chroma_client.get_collection(COLLECTION_NAME)
        log.info("Reusing existing collection (%d chunks)", _collection.count())
    else:
        _collection = _chroma_client.create_collection(name=COLLECTION_NAME)
        log.info("Collection not found — ingesting knowledge base")
        _ingest_kb(_collection)

    return _collection


# ─────────────────────────────────────────────────────────────────────────────
# Text chunking
# ─────────────────────────────────────────────────────────────────────────────
def chunk_text(text: str, chunk_size: int = 1000) -> List[str]:
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


# ─────────────────────────────────────────────────────────────────────────────
# Embeddings
#
# text-embedding-3-small has an 8191-token limit (~32 000 chars worst-case).
# We truncate to 6 000 chars per string — well inside the limit for any language —
# so a single oversized Jira description never causes a 400 error.
# ─────────────────────────────────────────────────────────────────────────────
_EMBED_CHAR_LIMIT = 6_000


def embed_texts(texts: List[str]) -> List[List[float]]:
    safe = [t[:_EMBED_CHAR_LIMIT] for t in texts]
    response = openai_client.embeddings.create(model=EMB_MODEL, input=safe)
    return [d.embedding for d in response.data]


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge base ingestion
# ─────────────────────────────────────────────────────────────────────────────
def _ingest_kb(collection) -> None:
    """Read all .txt / .md files from KB_DIR, embed them, and add to collection."""
    files = glob.glob(os.path.join(KB_DIR, "*.txt")) + \
            glob.glob(os.path.join(KB_DIR, "*.md"))

    if not files:
        log.warning("No KB files found in %s — collection will be empty.", KB_DIR)
        return

    docs, metadatas, ids = [], [], []
    idx = 0
    for file_path in files:
        with open(file_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        for chunk in chunk_text(content):
            if chunk.strip():
                docs.append(chunk)
                metadatas.append({"source": os.path.basename(file_path)})
                ids.append(f"doc_{idx}")
                idx += 1

    if not docs:
        log.warning("KB files are all empty.")
        return

    log.info("Embedding %d chunks from %d files…", len(docs), len(files))
    embeddings = embed_texts(docs)
    collection.add(documents=docs, embeddings=embeddings, metadatas=metadatas, ids=ids)
    log.info("Indexed %d KB chunks.", len(docs))


# ─────────────────────────────────────────────────────────────────────────────
# Retrieval
# Returns (chunks, source_filenames) so the prompt and the Slack response can
# both show which KB files actually contributed context.
# ─────────────────────────────────────────────────────────────────────────────
def get_context(query: str, k: int = 6) -> tuple:
    col = _get_collection()
    if col.count() == 0:
        log.info("KB collection is empty — no context retrieved.")
        return [], []

    qvec = embed_texts([query])[0]
    results = col.query(query_embeddings=[qvec], n_results=k)

    raw_docs  = results.get("documents",  [[]])[0] or []
    raw_metas = results.get("metadatas",  [[]])[0] or []

    chunks, sources = [], []
    for doc, meta in zip(raw_docs, raw_metas):
        if not doc:
            continue
        chunks.append(doc)
        # meta can be None, {}, or {"source": "/full/path/..."} or {"source": "basename.txt"}
        raw_src = (meta or {}).get("source") or ""
        # Always store just the filename so the footer is readable
        sources.append(os.path.basename(raw_src) if raw_src else "unknown")

    if chunks:
        log.info("KB retrieved %d chunks from: %s", len(chunks), sorted(set(sources)))
    else:
        log.info("KB query returned no matching chunks.")

    return chunks, sources


# ─────────────────────────────────────────────────────────────────────────────
# Jira
# ─────────────────────────────────────────────────────────────────────────────
import requests
from requests.auth import HTTPBasicAuth


def _extract_adf_text(node: Any, parts: Optional[List[str]] = None) -> str:
    """
    Recursively extract plain text from a Jira Atlassian Document Format (ADF) node.

    Jira REST API v3 returns `description` as a nested ADF JSON object, not a
    plain string.  Passing the raw dict to OpenAI embeddings stringifies to a
    massive JSON blob that easily blows through the 8 192-token limit.

    This walks the node tree and pulls out every 'text' leaf, joining them with
    spaces so the result reads naturally.
    """
    if parts is None:
        parts = []

    if isinstance(node, str):
        parts.append(node)
    elif isinstance(node, dict):
        # Leaf text node
        if node.get("type") == "text" and "text" in node:
            parts.append(node["text"])
        # Recurse into content children
        for child in node.get("content", []):
            _extract_adf_text(child, parts)
    elif isinstance(node, list):
        for item in node:
            _extract_adf_text(item, parts)

    return " ".join(parts)


def fetch_jira_issue(key: str) -> Dict[str, Any]:
    if not all([JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN]):
        raise ValueError("Jira credentials are not configured.")
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{key}"
    resp = requests.get(url, auth=HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN), timeout=10)
    resp.raise_for_status()
    fields = resp.json().get("fields", {})

    # description is ADF (dict) in API v3, or a plain string in older versions
    raw_desc = fields.get("description", "")
    description = _extract_adf_text(raw_desc) if isinstance(raw_desc, dict) else (raw_desc or "")

    return {
        "key": key,
        "summary": fields.get("summary", ""),
        "description": description,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builder
# ─────────────────────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
You are a principal QA engineer with a "break-it-before-prod" mindset.
Your speciality is uncovering high-impact edge cases that:
  • Developers miss because they only test the happy path
  • Standard QA misses because it follows the spec too literally
  • Only surface under specific real-world conditions

You have deep knowledge of Editage's products, business rules, and system \
behaviour from the Internal KB Context provided.

OUTPUT FORMAT — generate test cases grouped under these headings \
(omit a heading only if it genuinely doesn't apply):

### 1. Happy Path
Baseline success scenarios — brief, one or two cases only.

### 2. Boundary & Edge Values
Empty, null, min/max lengths, special characters, very long strings, \
Unicode/emoji, whitespace-only inputs.

### 3. Negative & Validation
Invalid formats, wrong data types, missing required fields, \
malformed payloads, unsupported file types / sizes.

### 4. State & Concurrency
Double-submit, race conditions, back-button after submit, \
refreshing mid-flow, multiple browser tabs, session shared between devices.

### 5. Permission & Security
Unauthenticated access, accessing another user's data, \
expired/tampered tokens, role privilege escalation, \
IDOR (Insecure Direct Object Reference) via ID manipulation.

### 6. External Dependency Failures
Slow or timing-out third-party APIs (Jira, payment, email), \
malformed API responses, partial failures, retry storms.

### 7. Locale & Internationalisation
RTL languages, character limits that differ by locale, \
date/currency/number formatting, region-specific plan behaviour \
(Korea, Japan, US, ROW), translated UI strings truncating in narrow viewports.

### 8. Session & Timing
Session expiry mid-flow, OTP expiry before use, \
token refresh race, stale cache after plan upgrade, \
concurrent login from different IPs.

### 9. KB-Informed Scenarios  ← MOST IMPORTANT
Test cases that could only be written with knowledge of Editage's \
internal business rules, system limits, and workflows as found in \
the Internal KB Context below. Reference the exact rule or limit \
(e.g. "OTP expires after 5 min", "rate limit: 3 OTP sends/min", \
"423 Locked after 5 wrong attempts").

For each test case use this structure:
**TC-N** | *Category* | **Test:** <what you're testing> | \
**Steps:** <concise numbered steps> | **Expected:** <exact expected result> | \
⚠️ **Why devs miss this:** <one sentence>
"""


def build_prompt(user_text: str, jira: Dict, context_blocks: List[str], sources: List[str]) -> str:
    parts = []

    if jira:
        parts.append(
            f"[JIRA TICKET]\n"
            f"Key: {jira.get('key')}\n"
            f"Summary: {jira.get('summary')}\n"
            f"Description:\n{jira.get('description', '(no description)')}"
        )

    if context_blocks:
        kb_section = ["[Internal KB Context — use this to write KB-Informed test cases]"]
        for i, (block, src) in enumerate(zip(context_blocks, sources), 1):
            kb_section.append(f"[CTX-{i} | source: {src}]\n{block}")
        parts.append("\n\n".join(kb_section))
    else:
        parts.append("[Internal KB Context]\nNo relevant KB context found for this query.")

    parts.append(f"[User Request]\n{user_text}")

    return "\n\n---\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Test case generation
# ─────────────────────────────────────────────────────────────────────────────
def generate_testcases(user_text: str, jira_key: Optional[str] = None) -> str:
    jira: Dict = {}
    query = user_text

    if jira_key:
        jira = fetch_jira_issue(jira_key)
        query = f"{jira.get('summary', '')} {jira.get('description', '')}"

    context_blocks, sources = get_context(query)
    prompt = build_prompt(user_text, jira, context_blocks, sources)

    log.info("Calling OpenAI: model=%s, prompt_chars=%d", GEN_MODEL, len(prompt))

    completion = openai_client.chat.completions.create(
        model=GEN_MODEL,
        temperature=0.3,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
    )

    result = completion.choices[0].message.content.strip()

    # Append KB source summary so the team can see what informed the output
    if sources:
        unique_sources = sorted(set(sources))
        kb_footer = (
            "\n\n---\n📚 *KB files used to generate KB-Informed scenarios:*\n"
            + "\n".join(f"  • `{s}`" for s in unique_sources)
        )
        result += kb_footer
    else:
        result += "\n\n---\n📚 *No KB context matched this query — KB-Informed section may be limited.*"

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Slack app
#
# process_before_response=True is required for lazy listeners:
# it tells slack_bolt to call ack() before running listeners so the HTTP
# response can be returned to API Gateway/Slack before the heavy work starts.
# ─────────────────────────────────────────────────────────────────────────────
bolt_app = App(
    token=SLACK_BOT_TOKEN,
    signing_secret=SLACK_SIGNING_SECRET,
    process_before_response=True,
)


# ── /generate-tests command ───────────────────────────────────────────────────
# Command name matches what is registered in the Slack API console.
# Usage:  /generate-tests <free text>
#         /generate-tests PROJ-123
#         /generate-tests PROJ-123 additional notes here

def ack_testcases(ack):
    """
    Ack function — MUST complete within Slack's 3-second window.
    slack_bolt invokes this in the first Lambda call and returns the HTTP 200
    to Slack before the lazy processing Lambda is even started.
    """
    ack("⏳ Generating test cases — this usually takes 15–30 s. Results will appear here.")


def process_testcases(respond, command):
    """
    Lazy function — runs in a *second* Lambda invocation, so there is no
    time pressure.  Results are posted back to Slack via the response_url.
    """
    text = (command.get("text") or "").strip()
    jira_match = re.search(r"\b[A-Z]+-\d+\b", text)
    jira_key = jira_match.group(0) if jira_match else None
    user_text = text.replace(jira_key, "").strip() if jira_key else text

    try:
        result = generate_testcases(user_text, jira_key)
        respond(response_type="ephemeral", text=result[:28000])
    except Exception as exc:
        log.exception("Failed to generate test cases")
        respond(text=f"❌ Error generating test cases: {exc}")


# Register the command with separate ack + lazy handlers.
# Both names are registered so either Slack app configuration works.
# /generate-tests — original name used during local development
# /testcases      — name DevOps configured in the production Slack app
bolt_app.command("/generate-tests")(ack=ack_testcases, lazy=[process_testcases])
bolt_app.command("/testcases")(ack=ack_testcases, lazy=[process_testcases])

# lambda_function.py imports bolt_app directly and creates the SlackRequestHandler
# there, so it can set SLACK_BOLT_LAZY_LAMBDA_FUNCTION_NAME before any imports.
