"""Builds the vector store index from the corpus.
Steps: extract -> clean -> chunk -> anonymize (reports) -> embed -> index.

Run from the project root:
    python -m rag.build_index
"""
import json
import sqlite3

import faiss

from .config import (
    CORPUS_MANUALS, CORPUS_REPORTS, INDEX_DIR, INDEX_FAISS, INDEX_CHUNKS,
    MODEL_DIM, DB_PATH, ANON_TOKEN_PATIENT, ANON_TOKEN_RELATIVE,
)
from .loader import load
from .chunker import chunk
from .embedder import Embedder


def _names_to_anonymize():
    """Read the elder's and relatives' names from Belle's SQLite profile to hide
    them in the reports. Generic: works for any patient. Returns (text, token)
    pairs, longest first so full names are replaced first.
    """
    pairs = []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute("SELECT nombre, apellidos FROM perfil LIMIT 1").fetchone()
        if row:
            nombre    = (row[0] or "").strip()
            apellidos = (row[1] or "").strip()
            if nombre and apellidos:
                pairs.append((f"{nombre} {apellidos}", ANON_TOKEN_PATIENT))
            if nombre:
                pairs.append((nombre, ANON_TOKEN_PATIENT))
        for (fam,) in conn.execute("SELECT nombre FROM familia WHERE activo = 1"):
            fam = (fam or "").strip()
            if fam:
                pairs.append((fam, ANON_TOKEN_RELATIVE))
        conn.close()
    except Exception:
        return []

    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    return pairs


def _anonymize(texto, pairs):
    for find, token in pairs:
        texto = texto.replace(find, token)
    return texto


def _collect_chunks(anon_pairs):
    """Walk manuals and reports and return the global list of chunks."""
    chunks = []

    for path in sorted(CORPUS_MANUALS.glob("*.pdf")):
        texto = load(path)
        chunks.extend(chunk(texto, fuente=path.name, tipo="manual"))

    for path in sorted(CORPUS_REPORTS.glob("*.pdf")):
        texto = load(path)
        for c in chunk(texto, fuente=path.name, tipo="informe"):
            c["texto"] = _anonymize(c["texto"], anon_pairs)
            chunks.append(c)

    return chunks


def build(anonymize_names=None):
    """Build and save the index. If no names are passed, read them from the profile."""
    pairs = anonymize_names if anonymize_names is not None else _names_to_anonymize()
    if pairs:
        print(f"[rag] anonymizing {len(pairs)} names read from the profile")
    else:
        print("[rag] no names to anonymize (empty profile or no database)")

    print("[rag] collecting and chunking the corpus...")
    chunks = _collect_chunks(pairs)
    if not chunks:
        print("[rag] no PDFs found in corpus/. Nothing to index.")
        return

    print(f"[rag] {len(chunks)} chunks. Embedding (may take a while)...")
    embedder = Embedder()
    vectors = embedder.embed([c["texto"] for c in chunks])

    print("[rag] building the FAISS index...")
    index = faiss.IndexFlatIP(MODEL_DIM)
    index.add(vectors)

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(INDEX_FAISS))
    with open(INDEX_CHUNKS, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False)

    print(f"[rag] index saved to {INDEX_DIR} ({len(chunks)} chunks)")


if __name__ == "__main__":
    build()
