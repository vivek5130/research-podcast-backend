import sqlite3

from fastapi import APIRouter, Depends

from app.controllers.query_controller import handle_query
from app.db.database import get_db
from app.models.query_models import QueryRequest, QueryResponse

router = APIRouter(tags=["query"])


@router.post("/query", response_model=QueryResponse)
async def query(payload: QueryRequest, db: sqlite3.Connection = Depends(get_db)):
    """Ask a question over saved content."""
    return await handle_query(db, payload)
