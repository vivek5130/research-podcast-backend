"""Controller = glue between route and service: logs start/success, delegates the real work."""

import sqlite3

from app.models.item_models import IngestRequest, ItemResponse
from app.services import ingest_service
from app.utils.logger import log_info


async def handle_ingest(db: sqlite3.Connection, payload: IngestRequest) -> ItemResponse:
    """Handle POST /ingest. Errors bubble up to the global handler (already logged there)."""
    log_info("ingest.handle_ingest.start", "Ingest requested", source_type=payload.source_type)
    item = await ingest_service.ingest_content(db, payload)
    log_info("ingest.handle_ingest.success", "Item saved", item_id=item.id)
    return item
