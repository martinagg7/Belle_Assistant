"""Extracts text from a PDF and cleans it generically (works for any PDF).
Input: a PDF path. Output: clean text (str).
"""
import re
from collections import Counter

from pypdf import PdfReader


def extract_pages(pdf_path):
    """Return a list with the text of each PDF page."""
    reader = PdfReader(str(pdf_path))
    return [(page.extract_text() or "") for page in reader.pages]


def _repeated_lines(pages, fraction=0.5, minimum=2):
    """Detect headers and footers WITHOUT knowing their content: the lines that
    repeat across many pages. Returns the set of lines to drop. Generic.
    """
    count = Counter()
    for page in pages:
        # each line counts once per page
        for line in {l.strip() for l in page.splitlines() if l.strip()}:
            count[line] += 1

    threshold = max(minimum, int(len(pages) * fraction))
    # Only short lines, the body is untouched
    return {line for line, times in count.items() if times >= threshold and len(line) < 120}


def clean(pages):
    """Drop repeated headers/footers and page numbers, normalize whitespace."""
    drop = _repeated_lines(pages) if len(pages) > 1 else set()

    lines = []
    for page in pages:
        for line in page.splitlines():
            l = line.strip()
            if not l:
                continue
            if l in drop:
                continue
            # stray page number: "3", "Pag. 3", "Pag 4"
            if re.fullmatch(r"(p[aá]g\.?\s*)?\d{1,4}", l.lower()):
                continue
            lines.append(l)

    texto = "\n".join(lines)
    texto = re.sub(r"[ \t]+", " ", texto)      # collapse spaces
    texto = re.sub(r"\n{3,}", "\n\n", texto)   # collapse blank lines
    return texto.strip()


def load(pdf_path):
    """Shortcut: extract the pages and clean them in one step."""
    return clean(extract_pages(pdf_path))
