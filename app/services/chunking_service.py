"""Splits long text into overlapping chunks for embedding.

Strategy: pack whole sentences into windows of about `chunk_size` characters,
and carry the last `overlap` characters into the next window so a fact that
sits on a boundary still appears in both neighbors. A hard slide is used only
when a single sentence is longer than the window.
"""

import re


def split_into_chunks(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    """Return overlapping text chunks, preserving sentence boundaries when possible."""
    cleaned = re.sub(r"[ \t]+", " ", text).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [cleaned]

    sentences = _sentences(cleaned)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if len(sentence) > chunk_size:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_hard_slide(sentence, chunk_size, overlap))
            continue

        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= chunk_size:
            current = candidate
            continue

        if current:
            chunks.append(current.strip())
        current = _with_overlap(chunks, sentence, overlap)

    if current.strip():
        chunks.append(current.strip())

    return [chunk for chunk in chunks if chunk]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [part.strip() for part in parts if part.strip()]


def _hard_slide(text: str, chunk_size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    step = max(chunk_size - overlap, 1)
    while start < len(text):
        piece = text[start : start + chunk_size].strip()
        if piece:
            chunks.append(piece)
        start += step
    return chunks


def _with_overlap(chunks: list[str], sentence: str, overlap: int) -> str:
    if not chunks or overlap <= 0:
        return sentence
    tail = chunks[-1][-overlap:].strip()
    if not tail:
        return sentence
    return f"{tail} {sentence}".strip()
