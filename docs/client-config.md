# Client configuration

## Cursor (HTTP MCP)

Because the server runs **locally** on your machine, Cursor connects to it at `http://localhost:3000/mcp`. Point your MCP configuration at that endpoint:

```json
"local-brain-mcp": {
  "url": "http://localhost:3000/mcp",
  "headers": {},
  "env": {
    "dbUrl": "/Users/you/.local/share/local-brain/memory.db"
  }
}
```

The database path is optional. When it is omitted, the server uses `~/.local/share/local-brain/memory.db`. `dbUrl`, `DB_URL`, and `MEMORY_DB_URL` are checked in that order, and the value must be an absolute path.

Notes:

- The `dbUrl` *tool argument* is intentionally ignored for DB path resolution (so all clients share the same DB).
- When you run `server.py` locally, it will also try to read `~/.cursor/mcp.json` and apply the `mcpServers.local-brain-mcp.env` block as environment overrides.
