"""RAG pipeline: question -> embed -> find similar chunks -> ask LLM -> answer + sources."""

import json
import sqlite3

from app.models.query_models import QueryRequest, QueryResponse, SourceSnippet
from app.services import embedding_service, llm_service
from app.utils.logger import log_info


async def answer_question(db: sqlite3.Connection, payload: QueryRequest) -> QueryResponse:
    """Answer a question using only the user's saved content."""
    rows = db.execute(
        """
        SELECT chunks.id AS chunk_id,
               chunks.item_id AS item_id,
               chunks.text AS text,
               chunks.embedding AS embedding,
               items.title AS title
        FROM chunks
        JOIN items ON items.id = chunks.item_id
        """
    ).fetchall()

    if not rows:
        return QueryResponse(
            answer="Your inbox is empty. Save a note or URL first, then ask again.",
            sources=[],
        )

    question_vector = (await embedding_service.embed_texts([payload.question]))[0]
    scored: list[SourceSnippet] = []
    for row in rows:
        try:
            vector = json.loads(row["embedding"] or "[]")
        except json.JSONDecodeError:
            vector = []
        score = embedding_service.cosine_similarity(question_vector, vector)
        scored.append(
            SourceSnippet(
                item_id=row["item_id"],
                chunk_id=row["chunk_id"],
                title=row["title"],
                text=row["text"],
                score=round(float(score), 4),
            )
        )

    top = sorted(scored, key=lambda snippet: snippet.score, reverse=True)[: payload.top_k]
    context = "\n\n".join(
        f"[{index}] {snippet.title or 'Untitled'} (item {snippet.item_id})\n{snippet.text}"
        for index, snippet in enumerate(top, start=1)
    )
    answer = await llm_service.generate_answer(payload.question, context)
    log_info(
        "query.answer_question.success",
        "Retrieved chunks",
        candidates=len(scored),
        returned=len(top),
    )
    return QueryResponse(answer=answer, sources=top)
