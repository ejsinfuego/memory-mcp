import json
import os
import sqlite3
from pathlib import Path
from typing import List, Optional

from fastmcp import FastMCP


DEFAULT_DB_URL = (
	os.environ.get("dbUrl")
	or os.environ.get("DB_URL")
	or os.environ.get("MEMORY_DB_URL")
	or "memory.db"
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  title TEXT,
  content TEXT NOT NULL,
  tags TEXT,
  source TEXT
);
"""


def _resolve_db_path(db_url: Optional[str]) -> Path:
	raw = db_url or DEFAULT_DB_URL

	if raw.startswith("file:"):
		from urllib.parse import urlparse

		parsed = urlparse(raw)
		return Path(parsed.path)

	p = Path(raw)
	if not p.is_absolute():
		p = Path.cwd() / p
	return p


def _get_connection(db_url: Optional[str]) -> sqlite3.Connection:
	path = _resolve_db_path(db_url)
	conn = sqlite3.connect(path)
	conn.row_factory = sqlite3.Row
	conn.execute(CREATE_TABLE_SQL)
	return conn


mcp = FastMCP("Local Brain MCP")


@mcp.tool
def save_memory(
	content: str,
	title: Optional[str] = None,
	tags: Optional[List[str]] = None,
	source: Optional[str] = None,
	dbUrl: Optional[str] = None,
) -> dict:
	"""
	Save a memory snippet into a local SQLite database.

	- content: main text content of the memory
	- title: optional short title
	- tags: optional list of tag strings
	- source: optional identifier for where this memory came from
	- dbUrl: optional database URL or path (file: URL or filesystem path).
	"""
	conn = _get_connection(dbUrl)
	try:
		cursor = conn.execute(
			"INSERT INTO memories (content, title, tags, source) VALUES (?, ?, ?, ?)",
			(
				content,
				title,
				json.dumps(tags or []),
				source,
			),
		)
		conn.commit()
		memory_id = cursor.lastrowid
	finally:
		conn.close()

	return {
		"id": memory_id,
		"title": title,
		"content": content,
		"tags": tags or [],
		"source": source,
	}


@mcp.tool
def fetch_memories(
	query: str,
	limit: int = 10,
	dbUrl: Optional[str] = None,
) -> List[dict]:
	"""
	Search memories by text query in content or title. Results are ordered by recency.

	- query: text to search for
	- limit: maximum number of results to return (default 10, max 50)
	- dbUrl: optional database URL or path (file: URL or filesystem path).
	"""
	if limit <= 0:
		raise ValueError("limit must be positive")
	if limit > 50:
		limit = 50

	conn = _get_connection(dbUrl)
	try:
		cursor = conn.execute(
			"""
      SELECT id, created_at, title, content, tags, source
      FROM memories
      WHERE content LIKE ? OR IFNULL(title, '') LIKE ?
      ORDER BY datetime(created_at) DESC, id DESC
      LIMIT ?
      """,
			(f"%{query}%", f"%{query}%", limit),
		)
		rows = cursor.fetchall()
	finally:
		conn.close()

	results: List[dict] = []
	for row in rows:
		raw_tags = row["tags"]
		try:
			parsed_tags = json.loads(raw_tags) if raw_tags else []
		except json.JSONDecodeError:
			parsed_tags = []

		results.append(
			{
				"id": row["id"],
				"created_at": row["created_at"],
				"title": row["title"],
				"content": row["content"],
				"tags": parsed_tags,
				"source": row["source"],
			},
		)

	return results


if __name__ == "__main__":
	# Expose the MCP server over HTTP so it can be used via Cloudflare Tunnel.
	# The MCP endpoint will be available at http://localhost:3000/mcp
	mcp.run(transport="http", host="0.0.0.0", port=3000)

