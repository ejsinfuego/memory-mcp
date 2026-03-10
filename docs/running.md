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

- **Default**: `./memory.db` in the current working directory.
- **Override via environment** (checked in this order):
  - `dbUrl`
  - `DB_URL`
  - `MEMORY_DB_URL`
- **Override per call**:
  - Pass a `dbUrl` argument (filesystem path or `file:` URL) to the tools.

All paths are resolved relative to the current working directory if they are not absolute.
