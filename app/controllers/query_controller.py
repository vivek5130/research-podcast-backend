"""Controller for RAG question answering."""

import sqlite3

from app.models.query_models import QueryRequest, QueryResponse
from app.services import query_service
from app.utils.logger import log_info


async def handle_query(db: sqlite3.Connection, payload: QueryRequest) -> QueryResponse:
    """Handle POST /query."""
    log_info("query.handle_query.start", "Query received", top_k=payload.top_k)
    result = await query_service.answer_question(db, payload)
    log_info("query.handle_query.success", "Query answered", sources=len(result.sources))
    return result
