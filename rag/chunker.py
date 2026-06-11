"""Splits clean text into controlled-size chunks, with overlap.

Each chunk keeps which file it came from and whether it is a manual or a report.
"""
from .config import CHUNK_WORDS, CHUNK_OVERLAP


def chunk(texto, fuente, tipo):
    """Split text into ~CHUNK_WORDS-word chunks, overlapping CHUNK_OVERLAP words
    between consecutive chunks. Returns a list of dicts: {texto, fuente, tipo}.

    The dict keys (texto/fuente/tipo) are kept: they are the persisted schema in
    chunks.json that tools/salud.py reads.
    """
    words = texto.split()
    if not words:
        return []

    step = max(1, CHUNK_WORDS - CHUNK_OVERLAP)
    chunks = []
    for start in range(0, len(words), step):
        piece = " ".join(words[start:start + CHUNK_WORDS]).strip()
        if piece:
            chunks.append({"texto": piece, "fuente": fuente, "tipo": tipo})
        # if this chunk already reaches the end, no need for another
        if start + CHUNK_WORDS >= len(words):
            break
    return chunks
