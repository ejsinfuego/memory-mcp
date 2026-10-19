# Internals

The main server implementation lives in `server.py`.

## Database helpers

### `_resolve_db_path(db_url: Optional[str]) -> Path`

Resolves the configured database path into an absolute `Path`. The `dbUrl` tool argument is ignored.

- Supports `file:` URLs (e.g. `file:///home/user/memory.db`).
- Rejects relative paths. The default is `~/.local/share/local-brain/memory.db`.

### `_get_connection(db_url: Optional[str]) -> sqlite3.Connection`

Opens a SQLite connection to the resolved path, sets `row_factory` to `sqlite3.Row`, runs `PRAGMA foreign_keys = ON` (so `memory_embeddings` rows cascade on memory delete), and ensures the `memories` and `memory_embeddings` tables exist via `CREATE TABLE IF NOT EXISTS`.

## MCP server

```python
from fastmcp import FastMCP

mcp = FastMCP("Local Brain MCP")

# tools: save_memory, update_memory, delete_memory, fetch_memories, backfill_all_embeddings, doctor

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=3000)
```

FastMCP runs an HTTP MCP server listening on `0.0.0.0:3000` and serving the MCP endpoint at `/mcp`. This endpoint is what you point Cloudflare Tunnel or other HTTP MCP clients at.

## Cursor env auto-load (local dev convenience)

When running locally, the server attempts to load environment overrides from `~/.cursor/mcp.json`:

- Looks up `mcpServers.local-brain-mcp.env`
- Merges those key/value strings as a fallback behind real `os.environ`

This makes it easy to keep DB and embedding configuration in one place for Cursor + the local server process.
