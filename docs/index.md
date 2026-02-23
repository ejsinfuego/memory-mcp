# Local Brain MCP

Local Brain MCP is a small MCP server that stores and retrieves your personal memories in a local SQLite database, intended to be accessed over HTTP by MCP‑aware clients such as Cursor.

## Goals

- Keep memory data on your own disk (`memory.db` or a custom path).
- Access the same memory from multiple devices via a single HTTPS URL.
- Provide a simple, transparent API for saving and querying memories.

## High‑level architecture

- **MCP Server**: Python + FastMCP (`server.py`).
- **Storage**: SQLite database file (`memory.db` by default).
- **Tools**:
  - `save_memory` — insert a memory row.
  - `fetch_memories` — search memories by text.

