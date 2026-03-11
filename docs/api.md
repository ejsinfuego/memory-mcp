# API Reference

This project exposes two MCP tools: `save_memory` and `fetch_memories`.

## `save_memory`

Save a memory snippet into the local SQLite database.

### Signature (Python)

```python
@mcp.tool
def save_memory(
    content: str,
    title: Optional[str] = None,
    tags: Optional[List[str]] = None,
    source: Optional[str] = None,
    dbUrl: Optional[str] = None,
) -> dict:
    ...
```

### Parameters

- `content` (string, required): main text content of the memory.
- `title` (string, optional): short title or label.
- `tags` (list of strings, optional): tags for later filtering/search.
- `source` (string, optional): where this memory came from (e.g. project name).
- `dbUrl` (string, optional): database path or `file:` URL; overrides env/default.

### Storage schema

Rows are written into the `memories` table with columns:

- `id` — `INTEGER PRIMARY KEY AUTOINCREMENT`
- `created_at` — `TEXT`, default `datetime('now')`
- `title` — `TEXT`
- `content` — `TEXT NOT NULL`
- `tags` — `TEXT` (JSON‑encoded list)
- `source` — `TEXT`

### Return

JSON object with:

- `id`, `title`, `content`, `tags`, `source`.

---

## `fetch_memories`

RAG-style retrieval: search memories by semantic similarity (default) or by keyword. Results are ordered by relevance (vector mode) or recency (keyword mode).

### Signature (Python)

```python
@mcp.tool
def fetch_memories(
    query: str,
    limit: int = 10,
    dbUrl: Optional[str] = None,
    use_vector_search: bool = True,
) -> List[dict]:
    ...
```

### Parameters

- `query` (string, required): text to search for (semantic match when RAG/vector search is used).
- `limit` (int, optional): max results (default `10`, max `50`).
- `dbUrl` (string, optional): database path or `file:` URL; overrides env/default.
- `use_vector_search` (bool, optional, default `True`): if `True`, use RAG (embedding-based similarity); falls back to keyword search if embeddings unavailable. If `False`, keyword-only (`LIKE` on `content`/`title`).

### Behavior

- **Default (RAG)**: Embeds the query, compares to stored embeddings, returns memories ranked by cosine similarity; falls back to keyword search if no embeddings.
- **Keyword-only** (`use_vector_search=False`): `LIKE` on `content` and `title`, ordered by recency.
- Returns a list of objects: `id`, `created_at`, `title`, `content`, `tags` (parsed list), `source`.

