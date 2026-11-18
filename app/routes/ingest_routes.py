"""Routes only declare URL, method, status code and shapes; logic lives in controllers/services."""

import sqlite3

from fastapi import APIRouter, Depends, status

from app.controllers.ingest_controller import handle_ingest
from app.db.database import get_db
from app.models.item_models import IngestRequest, ItemResponse

router = APIRouter(tags=["ingest"])


@router.post("/ingest", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
async def ingest(payload: IngestRequest, db: sqlite3.Connection = Depends(get_db)):
    """Save a note or URL."""
    return await handle_ingest(db, payload)
