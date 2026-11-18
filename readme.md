# AI Knowledge Inbox — Backend (FastAPI + SQLite)

## Run

```bash
cd backend
venv\Scripts\activate          # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload  # http://localhost:8000/docs
```

The SQLite file `data/inbox.db` and its tables are created automatically on startup.

## Folder structure

```
backend/
├── app/
│   ├── main.py            # creates the app, CORS, error handlers, includes routers, runs init_db on startup
│   ├── config/settings.py # all env vars in one place
│   ├── db/
│   │   ├── database.py    # SQLite connection, get_db dependency, init_db
│   │   └── schema.sql     # CREATE TABLE statements (items, chunks)
│   ├── models/            # Pydantic request/response shapes (validation)
│   ├── routes/            # URL + method only → calls controller
│   ├── controllers/       # logs start/success → calls service
│   ├── services/          # real logic (DB, chunking, embeddings, LLM, scraping)  ← you implement
│   └── utils/
│       ├── logger.py      # structured JSON logs: log_info / log_error
│       └── errors.py      # AppError classes + global error handlers
└── data/inbox.db          # created at runtime (git-ignored)
```

Request flow: `route → controller → service → db / external APIs`.
Errors: raise `BadRequestError`, `NotFoundError`, `ExternalServiceError`… anywhere; the global handler returns:

```json
{ "error": { "code": "NOT_FOUND", "message": "Item not found", "details": null } }
```

Logs (one JSON line each), event = `feature.function.success|error`:

```json
{"level": "INFO", "event": "ingest.handle_ingest.success", "message": "Item saved", "params": {"item_id": 1}}
```

## API

| Method | Path      | Body                                                      | Status |
|--------|-----------|-----------------------------------------------------------|--------|
| GET    | /health   | –                                                         | works  |
| POST   | /ingest   | `{"source_type":"note","content":"..."}` or `{"source_type":"url","url":"https://..."}` | works |
| GET    | /items    | –                                                         | works  |
| POST   | /query    | `{"question":"...","top_k":5}`                            | works  |

## Tradeoffs

**Chunking.** Sentences are packed into ~800 character windows with a 100 character overlap. Whole sentences stay together so a fact is less likely to be cut in half. Overlap keeps a sentence that sits on a boundary in both neighboring chunks. This is character-based, so it ignores tokens and document structure (headings, tables). At scale, switch to token windows and structure-aware splits.

**Vector store.** Embeddings are JSON text in SQLite. One file, no extra service, and cosine similarity is computed in Python over every chunk. That is fine for hundreds of notes. It breaks when the inbox grows: every query loads every vector into memory, there is no ANN index, and writes are single-writer. Production would move vectors to pgvector, sqlite-vec, or a dedicated index (Qdrant/Chroma) and keep SQLite or Postgres for the raw items.

**Embeddings and answers.** With `OPENAI_API_KEY` set, ingest and query call `text-embedding-3-small` and `gpt-4o-mini`. With no key, a local hashed bag-of-words vector is stored and the answer is the retrieved passages themselves. Do not mix the two: vectors from different models are not comparable. Delete `data/inbox.db` if you switch.

**What else changes in production.** Background jobs for URL fetch and embedding (the request should not wait on OpenAI), retries and rate limits, a chunk delete when an item is removed, auth, request size limits, and observability (trace each query’s retrieved ids and model latency). Logging is already one JSON line per event.

## Implementation plan (suggested order)

1. **`item_service.list_items`** — easiest SQLite read; makes GET /items work.
2. **`ingest_service.ingest_content` for notes** — INSERT into `items`. Now you can add + list.
3. **`scraper_service.fetch_page_text`** — URL support (`pip install beautifulsoup4`).
4. **`chunking_service.split_into_chunks`** — sliding window (~800 chars, 100 overlap).
5. **`embedding_service.embed_texts` + `cosine_similarity`** — store vectors in `chunks.embedding` as JSON.
6. **`llm_service.generate_answer` + `query_service.answer_question`** — RAG: top-k chunks → prompt → answer + sources.
7. Write the tradeoffs section (chunking choice, why SQLite, what breaks at scale, prod changes).

## SQLite in 2 minutes

SQLite = a database stored in **one file**, built into Python (`import sqlite3`). No server, no install.

```python
from app.db.database import get_connection

db = get_connection()

# WRITE: always use ? placeholders, then commit
cur = db.execute(
    "INSERT INTO items (source_type, content) VALUES (?, ?)",
    ("note", "hello world"),
)
db.commit()
new_id = cur.lastrowid

# READ one / many — rows act like dicts
row  = db.execute("SELECT * FROM items WHERE id = ?", (new_id,)).fetchone()
rows = db.execute("SELECT * FROM items ORDER BY created_at DESC").fetchall()
print(row["content"], dict(row))

# MANY inserts at once
db.executemany(
    "INSERT INTO chunks (item_id, chunk_index, text, embedding) VALUES (?, ?, ?, ?)",
    [(new_id, 0, "chunk text", json.dumps([0.1, 0.2]))],
)
db.commit()
```

In routes you never open connections yourself — `db: sqlite3.Connection = Depends(get_db)` hands you one and closes it after the request.

Tips:
- `(value,)` — a single param still needs a tuple (note the comma).
- Forgot `commit()` → data silently not saved.
- Inspect the DB: install the VS Code extension "SQLite Viewer", or run `python -c "import sqlite3;print(sqlite3.connect('data/inbox.db').execute('select * from items').fetchall())"`.
- Reset everything: stop the server and delete `data/inbox.db`.
- Changed `schema.sql`? `CREATE TABLE IF NOT EXISTS` won't alter existing tables — delete the db file to recreate.
