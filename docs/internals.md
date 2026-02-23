# Internals

The main server implementation lives in `server.py`.

## Database helpers

### `_resolve_db_path(db_url: Optional[str]) -> Path`

Resolves a `dbUrl` argument or environment setting into an absolute `Path`.

- Supports `file:` URLs (e.g. `file:///home/user/memory.db`).
- Treats relative paths as relative to the current working directory.

### `_get_connection(db_url: Optional[str]) -> sqlite3.Connection`

Opens a SQLite connection to the resolved path, sets `row_factory` to `sqlite3.Row`, and ensures the `memories` table exists by running `CREATE TABLE IF NOT EXISTS`.

## MCP server

```python
from fastmcp import FastMCP

mcp = FastMCP("Local Brain MCP")

# tools: save_memory, fetch_memories

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=3000)
```

FastMCP runs an HTTP MCP server listening on `0.0.0.0:3000` and serving the MCP endpoint at `/mcp`. This endpoint is what you point Cloudflare Tunnel or other HTTP MCP clients at.

