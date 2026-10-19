# API Reference

MCP tools: `save_memory`, `update_memory`, `delete_memory`, `fetch_memories`, `backfill_all_embeddings`, and `doctor`.

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

JSON object with `id`, `title`, `content`, `tags`, `source`, `embedded`. `embedded` is `true` when a vector was stored.

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

JSON object with `id`, `title`, `content`, `tags`, `source`, `embedded`. `embedded` is whether a vector exists after the update.

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
    tags_any: Optional[List[str]] = None,
    source_prefix: Optional[str] = None,
) -> List[dict]:
    ...
```

### Parameters

- `query` (string, optional): search text. If omitted, null, or whitespace-only, returns the most recent memories (latest first)—a “list recent” view, not search.
- `limit` (int, optional): max results (default `5`, max `50`).
- `dbUrl` (string, optional): ignored for path resolution; DB comes from env.
- `use_vector_search` (bool, optional, default `True`): if `True`, use RAG (embedding-based similarity); falls back to keyword search if embeddings unavailable. If `False`, keyword-only (`LIKE` on `content`/`title`).
- `fields` (list of strings, optional): if set, each result includes only these keys: `id`, `created_at`, `title`, `content`, `tags`, `source`, `mode`, `retrieval`, `score`. Omit for all fields.
- `tags_any` (list of strings, optional): return only rows that contain at least one of these tags (case-insensitive match).
- `source_prefix` (string, optional): return only rows whose `source` starts with this prefix.

### Behavior

- **Default (RAG)**: Embeds the query and returns the nearest memories. sqlite-vec does the nearest-neighbor lookup when it is installed; otherwise cosine similarity is computed in Python. If the embedding API fails or no vectors exist, the same call uses keyword search.
- **Keyword-only** (`use_vector_search=False`): `LIKE` on `content` and `title`, ordered by recency.
- **Filters**: `tags_any` and `source_prefix` apply to recent, keyword, and vector retrieval paths.
- Returns a list of objects. Default keys: `id`, `created_at`, `title`, `content`, `tags` (parsed list), `source`, `mode` (`vector`, `keyword`, or `recent`), `retrieval` (the same value as `mode`), and `score` (cosine similarity for vector hits, otherwise `null`).
- `fields` limits which of those keys are returned.

## `doctor`

Report the database path, memory and embedding counts, model mismatches, whether sqlite-vec is active, and (when `ping` is true) whether a live embedding call succeeds.
