# Running the server

## Local HTTP MCP server

Start the MCP server on `http://localhost:3000/mcp`:

```bash
python server.py
```

FastMCP will run an HTTP server that exposes the MCP endpoint at `/mcp`.

## Database location

The server stores memories in a SQLite database file.

- **Default**: `./memory.db` in the current working directory.
- **Override via environment** (checked in this order):
  - `dbUrl`
  - `DB_URL`
  - `MEMORY_DB_URL`
- **Override per call**:
  - Pass a `dbUrl` argument (filesystem path or `file:` URL) to the tools.

All paths are resolved relative to the current working directory if they are not absolute.***
