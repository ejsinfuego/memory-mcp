# Running the server

The server is meant to be **run locally** on your machine. There is no cloud or hosted option — you start it yourself and clients connect to `localhost`.

## Local HTTP MCP server

Start the MCP server on `http://localhost:3000/mcp`:

```bash
python server.py
```

FastMCP will run an HTTP server that exposes the MCP endpoint at `/mcp`.

## Database location

The server stores memories in a SQLite database file.

- **Default**: `~/.local/share/local-brain/memory.db` (outside any git checkout).
- **Override via environment** (checked in this order). The value must be an absolute path:
  - `dbUrl`
  - `DB_URL`
  - `MEMORY_DB_URL`

The `dbUrl` *tool argument* is intentionally ignored for path resolution so all clients share the same configured DB.

A relative path is rejected so a second checkout cannot create a separate database.
