# Client configuration

## Cursor (HTTP MCP)

Because the server runs **locally** on your machine, Cursor connects to it at `http://localhost:3000/mcp`. Point your MCP configuration at that endpoint:

```json
"local-brain-mcp": {
  "url": "http://localhost:3000/mcp",
  "headers": {},
  "env": {
    "dbUrl": "memory.db"
  }
}
```

The `env.dbUrl` value is optional; it sets the default database file for this client. Individual tool calls can still override it with a `dbUrl` argument.
