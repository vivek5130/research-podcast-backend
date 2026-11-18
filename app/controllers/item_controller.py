"""Controller for listing and deleting saved items."""

import sqlite3

from app.models.item_models import ItemListResponse
from app.services import item_service
from app.utils.logger import log_info


def handle_list_items(db: sqlite3.Connection) -> ItemListResponse:
    """Handle GET /items."""
    result = item_service.list_items(db)
    log_info("items.handle_list_items.success", "Items listed", count=result.count)
    return result


def handle_delete_item(db: sqlite3.Connection, item_id: int) -> None:
    """Handle DELETE /items/{item_id}."""
    log_info("items.handle_delete_item.start", "Delete requested", item_id=item_id)
    item_service.delete_item(db, item_id)
    log_info("items.handle_delete_item.success", "Item deleted", item_id=item_id)
