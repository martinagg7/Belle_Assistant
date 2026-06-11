"""fastembed wrapper. Loads the model once (from models/embeddings) and turns
text into normalized vectors, ready for FAISS.
"""
import numpy as np
from fastembed import TextEmbedding

from .config import MODEL_NAME, MODEL_CACHE


class Embedder:
    def __init__(self):
        # Load the model from the local cache
        self._model = TextEmbedding(model_name=MODEL_NAME, cache_dir=str(MODEL_CACHE))

    def embed(self, textos):
        """Turn a list of texts into a matrix of normalized vectors (norm 1), so
        the inner product in FAISS equals the cosine similarity.
        """
        vectors = np.array(list(self._model.embed(textos)), dtype="float32")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vectors / norms
