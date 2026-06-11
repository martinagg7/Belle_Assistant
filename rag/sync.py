"""Downloads to the Pi the medical documents the family uploads from the web.

Reads the 'medical_documents' Firestore collection and downloads the PDFs to
rag/corpus/informes/, mirroring the web: if a document is deleted on the web, it
disappears here on sync too.

Only PDFs are downloaded. Images (x-rays, etc.) cannot be embedded as text, so
they are ignored.

Run from the project root:
    python -m rag.sync
"""
from pathlib import Path

from .config import CORPUS_REPORTS


def _is_pdf(data):
    """Decide whether a Firestore document is a PDF (by type or extension)."""
    name = (data.get("name") or "").lower()
    return data.get("type") == "application/pdf" or name.endswith(".pdf")


def sync_documents():
    """Leave rag/corpus/informes/ with the current web PDFs (mirror)."""
    from firebase_client import get_db
    from firebase_admin import storage as fb_storage

    db     = get_db()                 # initializes firebase admin (with storageBucket)
    bucket = fb_storage.bucket()

    CORPUS_REPORTS.mkdir(parents=True, exist_ok=True)

    # 1 Delete the old PDFs to mirror the web
    for old in CORPUS_REPORTS.glob("*.pdf"):
        old.unlink()

    # 2 Download the current PDFs from medical_documents
    total = 0
    for doc in db.collection("medical_documents").stream():
        data = doc.to_dict()
        path = data.get("path", "")
        if not path or not _is_pdf(data):
            if path:
                print(f"[sync] ignored (not a PDF): {data.get('name')}")
            continue
        dest = CORPUS_REPORTS / Path(path).name
        bucket.blob(path).download_to_filename(str(dest))
        print(f"[sync] downloaded: {dest.name}")
        total += 1

    print(f"[sync] {total} PDF documents synced to {CORPUS_REPORTS}")


if __name__ == "__main__":
    sync_documents()
