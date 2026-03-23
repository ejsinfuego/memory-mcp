# API Reference

MCP tools: `save_memory`, `update_memory`, `delete_memory`, `fetch_memories`, and `backfill_all_embeddings`.

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
    generate_embedding: bool = True,
) -> dict:
    ...
```

### Parameters

- `content` (string, required): main text content of the memory.
- `title` (string, optional): short title or label.
- `tags` (list of strings, optional): tags for later filtering/search.
- `source` (string, optional): where this memory came from (e.g. project name).
- `dbUrl` (string, optional): ignored for path resolution; DB comes from env (see server rules).
- `generate_embedding` (bool, optional, default `True`): store an embedding when an API key is configured.

### Return

JSON object with `id`, `title`, `content`, `tags`, `source`.

---

## `update_memory`

Patch an existing memory. Omitted parameters leave the stored value unchanged. At least one of `title`, `content`, `tags`, `source` must be provided.

### Signature (Python)

```python
@mcp.tool
def update_memory(
    memory_id: int,
    title: Optional[str] = None,
    content: Optional[str] = None,
    tags: Optional[List[str]] = None,
    source: Optional[str] = None,
    dbUrl: Optional[str] = None,
    generate_embedding: bool = True,
) -> dict:
    ...
```

### Parameters

- `memory_id` (int, required): row id.
- `title`, `content`, `tags`, `source` (optional): replacements; `content` must be non-empty if provided.
- `generate_embedding` (bool, default `True`): when `content` changes, recompute embedding if `True`; if `False`, remove stored embedding so vector search cannot match stale text.

### Errors

- `ValueError` if no fields to update, if `content` is empty when provided, or if `memory_id` does not exist.

### Return

JSON object with `id`, `title`, `content`, `tags`, `source`.

---

## `delete_memory`

Delete a memory row by id. Associated `memory_embeddings` rows are removed (foreign key `ON DELETE CASCADE` with `PRAGMA foreign_keys = ON`).

### Signature (Python)

```python
@mcp.tool
def delete_memory(
    memory_id: int,
    dbUrl: Optional[str] = None,
) -> dict:
    ...
```

### Return

`{ "id": <int>, "deleted": <bool> }` — `deleted` is `False` if no row existed.

---

## `fetch_memories`

RAG-style retrieval: search memories by semantic similarity (default) or by keyword. Results are ordered by relevance (vector mode) or recency (keyword mode).

### Signature (Python)

```python
@mcp.tool
def fetch_memories(
    query: Optional[str] = None,
    limit: int = 5,
    dbUrl: Optional[str] = None,
    use_vector_search: bool = True,
    fields: Optional[List[str]] = None,
) -> List[dict]:
    ...
```

### Parameters

- `query` (string, optional): search text. If omitted, null, or whitespace-only, returns the most recent memories (latest first)—a “list recent” view, not search.
- `limit` (int, optional): max results (default `5`, max `50`).
- `dbUrl` (string, optional): ignored for path resolution; DB comes from env.
- `use_vector_search` (bool, optional, default `True`): if `True`, use RAG (embedding-based similarity); falls back to keyword search if embeddings unavailable. If `False`, keyword-only (`LIKE` on `content`/`title`).
- `fields` (list of strings, optional): if set, each result includes only these keys: `id`, `created_at`, `title`, `content`, `tags`, `source`. Omit for all fields.

### Behavior

- **Default (RAG)**: Embeds the query, compares to stored embeddings, returns memories ranked by cosine similarity; falls back to keyword search if no embeddings.
- **Keyword-only** (`use_vector_search=False`): `LIKE` on `content` and `title`, ordered by recency.
- Returns a list of objects; keys depend on `fields` (default: `id`, `created_at`, `title`, `content`, `tags` as a parsed list, `source`).
