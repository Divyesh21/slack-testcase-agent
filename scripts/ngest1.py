import os, sys, re, time
from typing import List

# Load env
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

KB_DIR      = os.getenv("KB_DIR", "./editage_kb")
PERSIST_DIR = os.getenv("PERSIST_DIR", "./chroma_store")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMB_MODEL      = os.getenv("EMB_MODEL", "text-embedding-3-large")

# Chunking settings (safe defaults)
MAX_CHARS = int(os.getenv("INGEST_MAX_CHARS", "1200"))
OVERLAP   = int(os.getenv("INGEST_OVERLAP", "150"))
MAX_CHUNKS_PER_FILE = int(os.getenv("INGEST_MAX_CHUNKS", "200"))

def read_text(p: str) -> str:
    return open(p, "r", encoding="utf-8", errors="ignore").read()

def read_pdf(p: str) -> str:
    from pypdf import PdfReader
    t0 = time.time()
    txt = "\n".join((pg.extract_text() or "") for pg in PdfReader(p).pages)
    print(f"  [PDF] parsed in {time.time()-t0:.1f}s")
    return txt

def read_docx(p: str) -> str:
    from docx import Document
    d = Document(p)
    return "\n".join(par.text for par in d.paragraphs)

def load_doc(p: str) -> str:
    low = p.lower()
    if low.endswith((".md",".txt",".rst",".log",".cfg",".json",".yaml",".yml")):
        return read_text(p)
    if low.endswith(".docx"):
        return read_docx(p)
    if low.endswith(".pdf"):
        # tip: skip huge/scanned PDFs until last
        return read_pdf(p)
    return ""

def chunk(text: str, max_chars=MAX_CHARS, overlap=OVERLAP) -> List[str]:
    text = re.sub(r"\s+\n", "\n", text).strip()
    out, i = [], 0
    while i < len(text) and len(out) < MAX_CHUNKS_PER_FILE:
        j = min(len(text), i + max_chars)
        out.append(text[i:j])
        i = max(0, j - overlap)
    return out

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/ngest1.py <file-in-editage_kb or absolute-path>")
        sys.exit(1)

    src = sys.argv[1]
    if not os.path.isabs(src):
        src = os.path.join(KB_DIR, src)

    if not os.path.isfile(src):
        print(f"[ERR] File not found: {src}")
        sys.exit(1)

    print(f"[ONE] Reading {src}")
    txt = load_doc(src)
    if not txt.strip():
        print("[ONE] Empty or unreadable file")
        sys.exit(0)

    pieces = chunk(txt)
    print(f"[ONE] chunks={len(pieces)} (cap {MAX_CHUNKS_PER_FILE})")

    # Embed with OpenAI
    from openai import OpenAI
    client = OpenAI()
    print("[ONE] embedding…")
    emb = client.embeddings.create(model=EMB_MODEL, input=pieces).data
    vecs = [e.embedding for e in emb]
    print("[ONE] got embeddings")

    # Upsert to Chroma
    import chromadb
    client_db = chromadb.PersistentClient(path=PERSIST_DIR)
    col = client_db.get_or_create_collection(name="kb_collection")
    ids   = [f"{os.path.abspath(src)}::chunk:{i}" for i in range(len(pieces))]
    metas = [{"source_path": src, "filename": os.path.basename(src), "chunk_index": i}
             for i in range(len(pieces))]

    print(f"[ONE] upserting {len(pieces)} chunks …")
    col.add(ids=ids, documents=pieces, metadatas=metas, embeddings=vecs)
    print("[ONE] done.")

if __name__ == "__main__":
    main()
