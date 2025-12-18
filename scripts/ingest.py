# scripts/ingest.py
import os, glob, re
from typing import List, Dict, Any

from dotenv import load_dotenv
load_dotenv()

KB_DIR = os.getenv("KB_DIR", "./editage_kb")
PERSIST_DIR = os.getenv("PERSIST_DIR", "./chroma_store")
EMB_MODEL = os.getenv("EMB_MODEL", "text-embedding-3-large")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ---- Readers (txt/md/json/yaml + pdf) ----
def read_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

def read_docx(path: str) -> str:
    try:
        from docx import Document
        doc = Document(path)
        return "\n".join([p.text for p in doc.paragraphs])
    except Exception:
        return ""
def load_doc(path: str) -> str:
    low = path.lower()
    if low.endswith((".md", ".txt", ".rst", ".log", ".cfg", ".json", ".yaml", ".yml")):
        return read_text_file(path)
    if low.endswith(".pdf"):
        return read_pdf(path)
    if low.endswith(".docx"):
        return read_docx(path)
    return ""

# ---- Chunking (simple, overlap to preserve context) ----
def split_into_chunks(text: str, max_chars: int = 1600, overlap: int = 200) -> List[str]:
    text = re.sub(r"\s+\n", "\n", text).strip()
    if not text:
        return []
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        chunks.append(text[start:end])
        start = max(0, end - overlap)
    return chunks

# ---- Chroma + OpenAI embeddings ----
import chromadb
from chromadb.utils import embedding_functions

def get_collection():
    client = chromadb.PersistentClient(path=PERSIST_DIR)
    openai_ef = embedding_functions.OpenAIEmbeddingFunction(
        api_key=OPENAI_API_KEY,
        model_name=EMB_MODEL,
    )
    # Use ONE stable collection name everywhere
    return client.get_or_create_collection(
        name="kb_collection",
        embedding_function=openai_ef
    )

def build_metadata(path: str, chunk_idx: int) -> Dict[str, Any]:
    base = os.path.basename(path)
    return {"source_path": path, "filename": base, "chunk_index": chunk_idx}

def main():
    col = get_collection()

    paths = [
        p for p in glob.glob(os.path.join(KB_DIR, "**/*"), recursive=True)
        if os.path.isfile(p) and p.lower().endswith(
            (".md", ".txt", ".rst", ".log", ".cfg", ".json",".docx", ".yaml", ".yml", ".pdf")
        )
    ]

    if not paths:
        print(f"No ingestable files found in {KB_DIR}")
        return

    BATCH = 64
    ids, docs, metas = [], [], []
    count = 0

    for path in paths:
        raw = load_doc(path)
        if not raw.strip():
            continue
        for i, ch in enumerate(split_into_chunks(raw, max_chars=1600, overlap=200)):
            ids.append(f"{path}::chunk:{i}")
            docs.append(ch)
            metas.append(build_metadata(path, i))
            count += 1
            if len(ids) >= BATCH:
                col.add(ids=ids, documents=docs, metadatas=metas)
                ids, docs, metas = [], [], []

    if ids:
        col.add(ids=ids, documents=docs, metadatas=metas)

    print(f"Ingest complete. Upserted chunks: {count}")

if __name__ == "__main__":
    main()

