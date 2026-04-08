# Client configuration

## Cursor (HTTP MCP)

Because the server runs **locally** on your machine, Cursor connects to it at `http://localhost:3000/mcp`. Point your MCP configuration at that endpoint:

```json
"local-brain-mcp": {
  "url": "http://localhost:3000/mcp",
  "headers": {},
  "env": {
    "DB_URL": "memory.db"
  }
}
```

The `env.DB_URL` value is optional; it sets the database file the **server** will use (the server checks `dbUrl`, `DB_URL`, then `MEMORY_DB_URL`, then defaults to `memory.db`).

Notes:

- The `dbUrl` *tool argument* is intentionally ignored for DB path resolution (so all clients share the same DB).
- When you run `server.py` locally, it will also try to read `~/.cursor/mcp.json` and apply the `mcpServers.local-brain-mcp.env` block as environment overrides.
