"""Business logic for reading and deleting saved items."""

import sqlite3

from app.models.item_models import ItemListResponse, ItemResponse
from app.utils.errors import NotFoundError
from app.utils.logger import log_info


def list_items(db: sqlite3.Connection) -> ItemListResponse:
    """Return all saved items, newest first."""
    rows = db.execute("SELECT * FROM items ORDER BY created_at DESC, id DESC").fetchall()
    items = [ItemResponse(**dict(row)) for row in rows]
    return ItemListResponse(items=items, count=len(items))


def delete_item(db: sqlite3.Connection, item_id: int) -> None:
    """Delete a saved item and its chunks (cascade). Raises NotFoundError if not found."""
    row = db.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise NotFoundError(f"Item {item_id} not found")
    db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    db.commit()
    log_info("item.delete_item.success", "Item deleted", item_id=item_id)
