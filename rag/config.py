"""Central configuration for the vector store (health RAG).

Gathers the paths, the embedding model and the chunking/search parameters in one
place. Paths are computed relative to the project, so it works the same on the
Mac and on the Pi without changes.
"""
from pathlib import Path

# rag/ folder and project root
RAG_DIR      = Path(__file__).resolve().parent
PROJECT_ROOT = RAG_DIR.parent

# Input corpus, the PDFs
CORPUS_MANUALS = RAG_DIR / "corpus" / "manuales"
CORPUS_REPORTS = RAG_DIR / "corpus" / "informes"

# Generated index
INDEX_DIR    = RAG_DIR / "index"
INDEX_FAISS  = INDEX_DIR / "index.faiss"
INDEX_CHUNKS = INDEX_DIR / "chunks.json"

# Embedding model
MODEL_NAME  = "jinaai/jina-embeddings-v2-base-es"
MODEL_DIM   = 768
MODEL_CACHE = PROJECT_ROOT / "models" / "embeddings"

# Chunking
CHUNK_WORDS   = 250
CHUNK_OVERLAP = 40

# Search
TOP_K = 4

# Anonymization: names are read from the SQLite profile
DB_PATH = PROJECT_ROOT / "data" / "belle.db"

# Tokens that replace the names in the chunks
ANON_TOKEN_PATIENT  = "la persona mayor"
ANON_TOKEN_RELATIVE = "un familiar"
