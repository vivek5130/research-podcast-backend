import sqlite3

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.controllers.item_controller import handle_delete_item, handle_list_items
from app.db.database import get_db
from app.models.item_models import ItemListResponse

router = APIRouter(tags=["items"])


@router.get("/items", response_model=ItemListResponse)
def list_items(db: sqlite3.Connection = Depends(get_db)):
    """List all saved items."""
    return handle_list_items(db)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: sqlite3.Connection = Depends(get_db)):
    """Delete a saved item and all its chunks."""
    handle_delete_item(db, item_id)
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)
