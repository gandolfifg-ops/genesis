from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf as fitz


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_pdf_text(path: Path) -> str:
    document = fitz.open(path)
    try:
        pages = [page.get_text("text") for page in document]
    finally:
        document.close()
    return "\n".join(pages).strip()


def _wrap(text: str, width: int = 92) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if len(trial) > width and current:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [""]


def write_pdf(path: Path, title: str, paragraphs: list[str]) -> Path:
    """Write extractable text with insert_text (textbox streams often garbled on get_text)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    y = 72
    for line in _wrap(title, 70):
        page.insert_text((54, y), line, fontsize=14, fontname="helv")
        y += 18
    y += 12
    for paragraph in paragraphs:
        for line in _wrap(paragraph):
            if y > 740:
                page = document.new_page(width=612, height=792)
                y = 72
            page.insert_text((54, y), line, fontsize=11, fontname="helv")
            y += 15
        y += 10
    document.save(path)
    document.close()
    return path
