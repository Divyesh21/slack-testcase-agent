import os
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

import chromadb
from chromadb.utils import embedding_functions

# Load env values
openai_key = os.getenv("OPENAI_API_KEY")
persist_dir = os.getenv("PERSIST_DIR", "./chroma_store")
emb_model = os.getenv("EMB_MODEL", "text-embedding-3-large")

# Build embedding function + collection
ef = embedding_functions.OpenAIEmbeddingFunction(
    api_key=openai_key,
    model_name=emb_model,
)
col = chromadb.PersistentClient(path=persist_dir).get_or_create_collection(
    name="kb_collection",
    embedding_function=ef
)

# Run a sample query
query = "Chinese localization workflow and translation"
print(f"[QUERY] {query}")
res = col.query(query_texts=[query], n_results=3)

# Print top matches
docs = res.get("documents", [[]])[0]
metas = res.get("metadatas", [[]])[0]
print("\nTop matching chunks:\n--------------------")
for i, (m, d) in enumerate(zip(metas, docs), 1):
    print(f"{i}. {m.get('filename')} (chunk {m.get('chunk_index')})")
    print(f"   {d[:200].strip()}...\n")
