# Client configuration

## Cursor (HTTP MCP)

To use this server from Cursor (or any MCP client that supports HTTP transport), point the MCP configuration at the local HTTP endpoint:

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
