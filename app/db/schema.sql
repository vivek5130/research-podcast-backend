-- items: one row per saved note or URL (the raw content + metadata)
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT    NOT NULL CHECK (source_type IN ('note', 'url')),
    source_url  TEXT,                                   -- only for source_type = 'url'
    title       TEXT,
    content     TEXT    NOT NULL,                       -- raw text (fetched page text for URLs)
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- chunks: small pieces of an item's content + their embedding vector (for RAG search)
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,                       -- position of chunk inside the item
    text        TEXT    NOT NULL,
    embedding   TEXT,                                   -- JSON list of floats, e.g. "[0.12, -0.03, ...]"
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chunks_item_id ON chunks(item_id);
