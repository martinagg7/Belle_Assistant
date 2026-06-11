"""Loads the vector store index into memory and searches the most relevant
chunks for a question. Used by tools/salud.py in real time.

Manual test from the project root:
    python -m rag.store "me duelen las rodillas"
"""
import json

import faiss

from .config import INDEX_FAISS, INDEX_CHUNKS, TOP_K
from .embedder import Embedder


class VectorStore:
    def __init__(self):
        # Load the index and chunk map once at startup
        self._index = faiss.read_index(str(INDEX_FAISS))
        with open(INDEX_CHUNKS, "r", encoding="utf-8") as f:
            self._chunks = json.load(f)
        self._embedder = Embedder()

    def search(self, pregunta, k=TOP_K):
        """Return the k chunks most similar to the question.
        Each result: {texto, fuente, tipo, score} (kept schema).
        """
        vector = self._embedder.embed([pregunta])
        scores, positions = self._index.search(vector, k)

        results = []
        for position, score in zip(positions[0], scores[0]):
            if position < 0:
                continue
            chunk = self._chunks[position]
            results.append({
                "texto":  chunk["texto"],
                "fuente": chunk["fuente"],
                "tipo":   chunk["tipo"],
                "score":  float(score),
            })
        return results

    def search_balanced(self, pregunta, n_informe=3, n_manual=3):
        """Return a balanced mix: the best n_informe report chunks + the best
        n_manual manual chunks, so the person's history always gets in (not only
        what wins on score). Each chunk carries its 'tipo' label.
        """
        candidates = self.search(pregunta, k=20)
        informe = [c for c in candidates if c["tipo"] == "informe"][:n_informe]
        manual  = [c for c in candidates if c["tipo"] == "manual"][:n_manual]
        return informe + manual


if __name__ == "__main__":
    import sys

    pregunta = " ".join(sys.argv[1:]) or "me duelen las rodillas"
    store = VectorStore()
    print(f"Pregunta: {pregunta}\n")
    for r in store.search_balanced(pregunta):
        print(f"--- [{r['tipo']} | {r['fuente']} | score {r['score']:.3f}]")
        print(r["texto"][:300])
        print()
