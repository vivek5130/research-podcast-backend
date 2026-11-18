"""Business logic for saving notes / URLs."""

import json
import sqlite3

from app.models.item_models import IngestRequest, ItemResponse
from app.services import chunking_service, embedding_service, scraper_service
from app.utils.errors import BadRequestError
from app.utils.logger import log_info


async def ingest_content(db: sqlite3.Connection, payload: IngestRequest) -> ItemResponse:
    """Save a note or URL, then chunk + embed it so it becomes searchable."""
    source_url = None
    title = payload.title.strip() if payload.title else None

    if payload.source_type == "url":
        source_url = str(payload.url)
        page_title, content = await scraper_service.fetch_page_text(source_url)
        title = title or page_title
    else:
        content = (payload.content or "").strip()

    if not content:
        raise BadRequestError("There is no text to save")

    if not title:
        first_line = content.splitlines()[0].strip()
        title = first_line[:80] or None

    cursor = db.execute(
        "INSERT INTO items (source_type, source_url, title, content) VALUES (?, ?, ?, ?)",
        (payload.source_type, source_url, title, content),
    )
    item_id = cursor.lastrowid

    chunks = chunking_service.split_into_chunks(content)
    if not chunks:
        raise BadRequestError("Content is empty after cleaning")

    vectors = await embedding_service.embed_texts(chunks)
    db.executemany(
        "INSERT INTO chunks (item_id, chunk_index, text, embedding) VALUES (?, ?, ?, ?)",
        [
            (item_id, index, chunk, json.dumps(vector))
            for index, (chunk, vector) in enumerate(zip(chunks, vectors))
        ],
    )
    db.commit()

    row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    log_info(
        "ingest.ingest_content.success",
        "Item stored with chunks",
        item_id=item_id,
        chunks=len(chunks),
        source_type=payload.source_type,
    )
    return ItemResponse(**dict(row))
