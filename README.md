Local Brain MCP
===============

Local Brain MCP is a small MCP server that lets AI assistants store and retrieve personal memories on your own disk, and optionally use **semantic (embedding) search** for RAG‑style workflows.

It is designed to be run locally (or behind something like Cloudflare Tunnel) and accessed by MCP‑aware clients such as Cursor.

## Features

- **Local, transparent storage**
  - SQLite database file in the project directory (`memory.db` by default, configurable via env or `dbUrl`).
  - Simple schema: `memories` table with `title`, `content`, `tags`, `source`, timestamps, and IDs.

- **MCP tools**
  - `save_memory` — insert a memory row (optionally generating an embedding).
  - `fetch_memories` — retrieve relevant memories via:
    - keyword search (`LIKE`) over `content` and `title`, or
    - semantic vector search using embeddings (RAG‑style).

- **Embeddings / vector search**
  - Optional embeddings are stored in a separate `memory_embeddings` table.
  - Supports multiple providers via environment variables:
    - **OpenAI** embeddings (default).
    - **OpenRouter** embeddings (great for low‑cost / free models).

## Quick start

### 1. Install dependencies

Create and activate a virtual environment, then install Python deps:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure embeddings (optional but recommended)

By default, the server will try to use OpenAI embeddings if `OPENAI_API_KEY` is set.

#### Option A: OpenAI

```bash
export EMBEDDING_PROVIDER=openai        # default, can be omitted
export OPENAI_API_KEY=sk-...
export EMBEDDING_MODEL=text-embedding-3-small  # optional override
```

#### Option B: OpenRouter

```bash
export EMBEDDING_PROVIDER=openrouter
export OPENROUTER_API_KEY=sk-or-...
# Any OpenRouter embedding model (example shown):
export EMBEDDING_MODEL=openai/text-embedding-3-small

# Optional but recommended for OpenRouter analytics:
export OPENROUTER_SITE_URL="https://your-site-or-localhost"
export OPENROUTER_APP_NAME="Local Brain MCP"
```

If no embedding provider is configured, the tools still work, but:

- `save_memory(generate_embedding=True)` silently skips embedding storage.
- `fetch_memories(use_vector_search=True)` falls back to keyword search.

### 3. Run the MCP server

From the project root:

```bash
source .venv/bin/activate
python server.py
```

You should see FastMCP start up and log something like:

- `Local Brain MCP` listening on `http://0.0.0.0:3000`
- MCP endpoint: `http://localhost:3000/mcp`

You can then point Cursor (or another MCP client) at `http://localhost:3000/mcp` as a remote MCP server.

## Database layout

By default, the SQLite file is `memory.db` in the project root. This can be overridden with:

- Env vars: `dbUrl`, `DB_URL`, or `MEMORY_DB_URL` (first non‑empty wins).
- Or the `dbUrl` parameter passed to tools (file path or `file:` URL).

Tables:

- `memories`
  - `id` (INTEGER, primary key)
  - `created_at` (TEXT, ISO datetime, default `datetime('now')`)
  - `title` (TEXT, nullable)
  - `content` (TEXT, required)
  - `tags` (TEXT, JSON‑encoded list of strings)
  - `source` (TEXT, optional source identifier)

- `memory_embeddings`
  - `memory_id` (INTEGER, primary key, FK → `memories(id)` with `ON DELETE CASCADE`)
  - `model` (TEXT, embedding model identifier)
  - `embedding` (TEXT, JSON‑encoded list of floats)

The embeddings table is created automatically on startup; you do not need to run migrations manually.

## MCP tools

### `save_memory`

**Purpose**: Insert a new memory row (and optionally its embedding).

Parameters:

- `content` (str, required): main text content of the memory.
- `title` (str, optional): short title.
- `tags` (List[str], optional): arbitrary tags, stored as JSON.
- `source` (str, optional): where the memory came from (e.g. `"cursor"`, `"cli"`, `"web"`).
- `dbUrl` (str, optional): override database path/URL for this call.
- `generate_embedding` (bool, optional, default `True`):
  - If `True`, and an embedding provider is configured, an embedding is generated and stored.

Returns:

- A dict with `id`, `title`, `content`, `tags`, `source`.

### `fetch_memories`

**Purpose**: Retrieve memories relevant to a query.

Parameters:

- `query` (str, required): text query.
- `limit` (int, optional, default `10`, max `50`): max number of results.
- `dbUrl` (str, optional): override database path/URL.
- `use_vector_search` (bool, optional, default `False`):
  - `False`: keyword mode — SQL `LIKE` on `content` and `title`, ordered by recency.
  - `True`: vector mode — embed the query and return memories ranked by cosine similarity (if embeddings exist), otherwise fall back to keyword search.

Returns:

- A list of dicts with `id`, `created_at`, `title`, `content`, `tags`, `source`.

## Using this as a RAG memory

Typical pattern for an AI assistant:

1. **On new long‑term information**  
   Call `save_memory` with:
   - `content`: the text snippet you want to remember.
   - `title` / `tags`: short descriptors.
   - `generate_embedding=True` (default) so it’s indexed semantically.

2. **Before answering a user query**  
   Call `fetch_memories` with:
   - `query`: the user’s question or a brief summary of it.
   - `use_vector_search=True` for semantic retrieval.
   - Use the top few results as RAG context when generating the answer.

3. **Fallback behavior**  
   If embeddings are not configured or not yet stored, vector search cleanly degrades to keyword search, so the tools remain usable.

## Development notes

- The server is implemented in `server.py` using **FastMCP**.
- All schema creation is performed on connection; deleting `memory.db` will recreate an empty database on next start.
- Embedding provider configuration is controlled entirely by environment variables; you can experiment with providers without changing tool contracts.

