# [Local Brain MCP](https://github.com/ejsinfuego/memory-mcp)

Local Brain MCP is a small MCP server that you **run locally** on your machine. It stores and retrieves your personal memories in a local SQLite database and is intended to be accessed over HTTP by MCP‑aware clients such as Cursor (e.g. at `http://localhost:3000/mcp`).

## Goals

- **Run locally** — start the server on your own machine; no cloud deployment required.
- Keep memory data on your own disk (`memory.db` or a custom path).
- Optionally expose the same memory from multiple devices via a tunnel (e.g. Cloudflare Tunnel) if you choose.
- Provide a simple, transparent API for saving and querying memories.

## High‑level architecture

- **MCP Server**: Python + FastMCP (`server.py`).
- **Storage**: SQLite database file (`memory.db` by default).
- **Tools**:
  - `save_memory` — insert a memory row.
  - `fetch_memories` — RAG-style search (semantic by default, keyword fallback).

