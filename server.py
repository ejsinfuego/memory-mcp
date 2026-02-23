import json
import math
import os
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

import httpx
from fastmcp import FastMCP
from openai import OpenAI


DEFAULT_DB_URL = (
	os.environ.get("dbUrl")
	or os.environ.get("DB_URL")
	or os.environ.get("MEMORY_DB_URL")
	or "memory.db"
)

EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "openai")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")

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

CREATE_EMBEDDINGS_SQL = """
CREATE TABLE IF NOT EXISTS memory_embeddings (
  memory_id INTEGER PRIMARY KEY,
  model TEXT NOT NULL,
  embedding TEXT NOT NULL,
  FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
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
	conn.execute(CREATE_EMBEDDINGS_SQL)
	return conn


def _embed_text(text: str) -> Optional[List[float]]:
	"""
	Create an embedding vector for the given text using the configured provider/model.

	Supported providers (via EMBEDDING_PROVIDER env):
	- "openai" (default): uses OPENAI_API_KEY and OpenAI SDK
	- "openrouter": uses OPENROUTER_API_KEY and OpenRouter HTTP API
	"""
	if EMBEDDING_PROVIDER == "openrouter":
		api_key = os.environ.get("OPENROUTER_API_KEY")
		if not api_key:
			return None

		model = EMBEDDING_MODEL or "openai/text-embedding-3-small"
		headers = {
			"Authorization": f"Bearer {api_key}",
			"Content-Type": "application/json",
		}

		# Optional but recommended headers for OpenRouter
		site = os.environ.get("OPENROUTER_SITE_URL")
		if site:
			headers["HTTP-Referer"] = site
		app_name = os.environ.get("OPENROUTER_APP_NAME")
		if app_name:
			headers["X-Title"] = app_name

		with httpx.Client(timeout=20.0) as client:
			resp = client.post(
				"https://openrouter.ai/api/v1/embeddings",
				headers=headers,
				json={"model": model, "input": text},
			)
			resp.raise_for_status()
			data = resp.json()
			return data["data"][0]["embedding"]

	# Default: OpenAI embeddings
	api_key = os.environ.get("OPENAI_API_KEY")
	if not api_key:
		return None

	client = OpenAI(api_key=api_key)
	resp = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
	return resp.data[0].embedding


def _cosine_similarity(a: List[float], b: List[float]) -> float:
	if not a or not b or len(a) != len(b):
		return 0.0
	dot = sum(x * y for x, y in zip(a, b))
	norm_a = math.sqrt(sum(x * x for x in a))
	norm_b = math.sqrt(sum(y * y for y in b))
	if norm_a == 0.0 or norm_b == 0.0:
		return 0.0
	return dot / (norm_a * norm_b)


def _search_memories_keyword(
	conn: sqlite3.Connection, query: str, limit: int
) -> List[sqlite3.Row]:
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
	return cursor.fetchall()


def _search_memories_vector(
	conn: sqlite3.Connection, query: str, limit: int
) -> List[sqlite3.Row]:
	query_embedding = _embed_text(query)
	if query_embedding is None:
		# Fallback to keyword search if embeddings are not configured.
		return _search_memories_keyword(conn, query, limit)

	# Load all embeddings for this model.
	cursor = conn.execute(
		"""
    SELECT memory_id, embedding
    FROM memory_embeddings
    WHERE model = ?
    """,
		(EMBEDDING_MODEL,),
	)
	rows = cursor.fetchall()

	if not rows:
		return []

	# Compute cosine similarity in Python.
	scores: List[Tuple[int, float]] = []
	for row in rows:
		try:
			vec = json.loads(row["embedding"])
			score = _cosine_similarity(query_embedding, vec)
		except Exception:
			continue
		scores.append((row["memory_id"], score))

	# Sort by similarity descending and take top N ids.
	scores.sort(key=lambda item: item[1], reverse=True)
	top_ids = [memory_id for memory_id, _ in scores[:limit] if memory_id is not None]

	if not top_ids:
		return []

	placeholders = ",".join("?" for _ in top_ids)
	cursor = conn.execute(
		f"""
    SELECT id, created_at, title, content, tags, source
    FROM memories
    WHERE id IN ({placeholders})
    """,
		top_ids,
	)
	memory_rows = cursor.fetchall()

	# Preserve the ranking order from top_ids.
	order = {memory_id: idx for idx, memory_id in enumerate(top_ids)}
	memory_rows.sort(key=lambda r: order.get(r["id"], len(order)))
	return memory_rows


mcp = FastMCP("Local Brain MCP")


@mcp.tool
def save_memory(
	content: str,
	title: Optional[str] = None,
	tags: Optional[List[str]] = None,
	source: Optional[str] = None,
	dbUrl: Optional[str] = None,
	generate_embedding: bool = True,
) -> dict:
	"""
	Save a memory snippet into a local SQLite database.

	- content: main text content of the memory
	- title: optional short title
	- tags: optional list of tag strings
	- source: optional identifier for where this memory came from
	- dbUrl: optional database URL or path (file: URL or filesystem path).
	- generate_embedding: whether to generate and store an embedding (requires OPENAI_API_KEY).
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
		memory_id = cursor.lastrowid

		if generate_embedding:
			try:
				vec = _embed_text(content)
				if vec is None:
					raise RuntimeError("Embedding backend not configured")

				conn.execute(
					"INSERT OR REPLACE INTO memory_embeddings (memory_id, model, embedding) VALUES (?, ?, ?)",
					(memory_id, EMBEDDING_MODEL, json.dumps(vec)),
				)
			except Exception:
				# If embedding fails, still keep the textual memory.
				pass

		conn.commit()
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
	use_vector_search: bool = False,
) -> List[dict]:
	"""
	Search memories by text query. Results are ordered by recency (keyword mode)
	or semantic similarity (vector mode).

	- query: text to search for
	- limit: maximum number of results to return (default 10, max 50)
	- dbUrl: optional database URL or path (file: URL or filesystem path).
	- use_vector_search: if True, use embedding-based similarity (requires OPENAI_API_KEY).
	"""
	if limit <= 0:
		raise ValueError("limit must be positive")
	if limit > 50:
		limit = 50

	conn = _get_connection(dbUrl)
	try:
		if use_vector_search:
			rows = _search_memories_vector(conn, query, limit)
		else:
			rows = _search_memories_keyword(conn, query, limit)
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

