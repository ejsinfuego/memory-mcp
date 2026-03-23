import json
import math
import os
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

import httpx
from fastmcp import FastMCP
from openai import OpenAI


def _load_cursor_mcp_env() -> dict:
	"""
	Load environment overrides from the local Cursor MCP config (mcp.json), if present.

	This lets a locally-run MCP server pick up the same env configuration that
	Cursor uses, without requiring you to manually export variables each time.
	"""
	try:
		config_path = Path.home() / ".cursor" / "mcp.json"
		if not config_path.is_file():
			return {}

		with config_path.open("r", encoding="utf-8") as f:
			raw = json.load(f)
	except Exception:
		return {}

	try:
		servers = raw.get("mcpServers", {})
		local_brain = servers.get("local-brain-mcp", {})
		env_cfg = local_brain.get("env", {}) or {}
		# Only allow simple string key/values.
		return {str(k): str(v) for k, v in env_cfg.items()}
	except Exception:
		return {}


_CURSOR_ENV = _load_cursor_mcp_env()


def _get_env(key: str, default: Optional[str] = None) -> Optional[str]:
	"""
	Helper to read environment variables with a fallback to the local
	Cursor MCP config (mcp.json) when running locally.
	"""
	if key in os.environ:
		return os.environ[key]
	if key in _CURSOR_ENV:
		return _CURSOR_ENV[key]
	return default


DEFAULT_DB_URL = (
	_get_env("dbUrl")
	or _get_env("DB_URL")
	or _get_env("MEMORY_DB_URL")
	or "memory.db"
)

EMBEDDING_PROVIDER = _get_env("EMBEDDING_PROVIDER", "openai")
EMBEDDING_MODEL = _get_env("EMBEDDING_MODEL", "text-embedding-3-small")

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


def _resolve_db_path(_db_url: Optional[str]) -> Path:
	"""
	Resolve the database path.

	Note: The effective DB path is always derived from environment variables
	(via DEFAULT_DB_URL). The dbUrl tool argument is intentionally ignored so
	that all clients share the same configured database.
	"""
	raw = DEFAULT_DB_URL

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
	conn.execute("PRAGMA foreign_keys = ON")
	conn.execute(CREATE_TABLE_SQL)
	conn.execute(CREATE_EMBEDDINGS_SQL)
	return conn


def _get_memory_row(conn: sqlite3.Connection, memory_id: int) -> Optional[sqlite3.Row]:
	cur = conn.execute(
		"SELECT id, created_at, title, content, tags, source FROM memories WHERE id = ?",
		(memory_id,),
	)
	return cur.fetchone()


def _embed_text(text: str) -> Optional[List[float]]:
	"""
	Create an embedding vector for the given text using the configured provider/model.

	Supported providers (via EMBEDDING_PROVIDER env):
	- "openai" (default): uses OPENAI_API_KEY and OpenAI SDK
	- "openrouter": uses OPENROUTER_API_KEY and OpenRouter HTTP API
	"""
	if EMBEDDING_PROVIDER == "openrouter":
		api_key = _get_env("OPENROUTER_API_KEY")
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

		try:
			with httpx.Client(timeout=20.0) as client:
				resp = client.post(
					"https://openrouter.ai/api/v1/embeddings",
					headers=headers,
					json={"model": model, "input": text},
				)
				resp.raise_for_status()
				data = resp.json()
				return data["data"][0]["embedding"]
		except Exception:
			# Any networking/SSL or API error disables embeddings for this call,
			# and the caller will transparently fall back to keyword search.
			return None

	# Default: OpenAI embeddings
	api_key = _get_env("OPENAI_API_KEY")
	if not api_key:
		return None

	try:
		client = OpenAI(api_key=api_key)
		resp = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
		return resp.data[0].embedding
	except Exception:
		# Any networking/SSL or API error disables embeddings for this call,
		# and the caller will transparently fall back to keyword search.
		return None


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


def _fetch_latest_memories(conn: sqlite3.Connection, limit: int) -> List[sqlite3.Row]:
	"""
	Return the most recently created memories, newest first.
	"""
	cursor = conn.execute(
		"""
    SELECT id, created_at, title, content, tags, source
    FROM memories
    ORDER BY datetime(created_at) DESC, id DESC
    LIMIT ?
    """,
		(limit,),
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
		# If we have no stored embeddings yet, fall back to keyword search
		# rather than returning an empty result set.
		return _search_memories_keyword(conn, query, limit)

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


_FETCH_MEMORY_FIELD_NAMES = frozenset(
	{"id", "created_at", "title", "content", "tags", "source"}
)


def _normalize_fetch_fields(fields: Optional[List[str]]) -> Optional[frozenset]:
	if fields is None:
		return None
	names = {str(f).strip() for f in fields if str(f).strip()}
	if not names:
		raise ValueError("fields, if provided, must include at least one field name")
	unknown = names - _FETCH_MEMORY_FIELD_NAMES
	if unknown:
		raise ValueError(
			f"unknown field(s): {sorted(unknown)}; allowed: {sorted(_FETCH_MEMORY_FIELD_NAMES)}"
		)
	return frozenset(names)


def _memory_row_to_result_dict(
	row: sqlite3.Row, parsed_tags: List[str], field_set: Optional[frozenset]
) -> dict:
	full = {
		"id": row["id"],
		"created_at": row["created_at"],
		"title": row["title"],
		"content": row["content"],
		"tags": parsed_tags,
		"source": row["source"],
	}
	if field_set is None:
		return full
	return {k: full[k] for k in full if k in field_set}


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
def update_memory(
	memory_id: int,
	title: Optional[str] = None,
	content: Optional[str] = None,
	tags: Optional[List[str]] = None,
	source: Optional[str] = None,
	dbUrl: Optional[str] = None,
	generate_embedding: bool = True,
) -> dict:
	"""
	Update an existing memory row. Pass only fields to change; omitted fields stay as stored.

	- memory_id: row id to update.
	- title, content, tags, source: optional replacements (at least one must be provided).
	- generate_embedding: when `content` is updated, if True (default) recompute and store the
	  embedding for the new text; if False, remove any stored embedding so vector search cannot
	  return stale vectors for old text.
	"""
	if (
		title is None
		and content is None
		and tags is None
		and source is None
	):
		raise ValueError(
			"at least one of title, content, tags, source must be provided to update_memory"
		)
	if content is not None and not str(content).strip():
		raise ValueError("content, if provided, must be non-empty")

	conn = _get_connection(dbUrl)
	try:
		row = _get_memory_row(conn, memory_id)
		if row is None:
			raise ValueError(f"memory not found: id={memory_id}")

		prev_content = row["content"]
		new_title = row["title"] if title is None else title
		new_content = prev_content if content is None else content
		new_tags_json = row["tags"] if tags is None else json.dumps(tags)
		new_source = row["source"] if source is None else source

		parsed_tags: List[str]
		if tags is None:
			try:
				parsed_tags = json.loads(row["tags"]) if row["tags"] else []
			except json.JSONDecodeError:
				parsed_tags = []
		else:
			parsed_tags = tags

		content_changed = new_content != prev_content

		conn.execute(
			"""
			UPDATE memories
			SET title = ?, content = ?, tags = ?, source = ?
			WHERE id = ?
			""",
			(new_title, new_content, new_tags_json, new_source, memory_id),
		)

		if content_changed:
			conn.execute(
				"DELETE FROM memory_embeddings WHERE memory_id = ?",
				(memory_id,),
			)
			if generate_embedding:
				try:
					vec = _embed_text(new_content)
					if vec is not None:
						conn.execute(
							"INSERT INTO memory_embeddings (memory_id, model, embedding) VALUES (?, ?, ?)",
							(memory_id, EMBEDDING_MODEL, json.dumps(vec)),
						)
				except Exception:
					pass

		conn.commit()
	finally:
		conn.close()

	return {
		"id": memory_id,
		"title": new_title,
		"content": new_content,
		"tags": parsed_tags,
		"source": new_source,
	}


@mcp.tool
def delete_memory(memory_id: int, dbUrl: Optional[str] = None) -> dict:
	"""
	Delete a memory row by id. Removes any stored embedding for that row (FK cascade).
	Returns whether a row was deleted.
	"""
	conn = _get_connection(dbUrl)
	try:
		cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
		conn.commit()
		deleted = cur.rowcount > 0
	finally:
		conn.close()

	return {"id": memory_id, "deleted": deleted}


@mcp.tool
def fetch_memories(
	query: Optional[str] = None,
	limit: int = 5,
	dbUrl: Optional[str] = None,
	use_vector_search: bool = True,
	fields: Optional[List[str]] = None,
) -> List[dict]:
	"""
	Search memories by text query (RAG-style retrieval). By default uses
	embedding-based semantic similarity; falls back to keyword search if
	embeddings are not configured or unavailable.

	- query: text to search for (semantic match when use_vector_search is True).
	  If omitted, null, or blank/whitespace only, returns recent memories (latest first)—same as a “list recent” view, not semantic search.
	- limit: maximum number of results to return (default 5, max 50).
	- dbUrl: optional database URL or path (file: URL or filesystem path).
	- use_vector_search: if True (default), use RAG/semantic retrieval; if False, keyword-only.
	- fields: optional subset of keys to return per row: id, created_at, title, content, tags, source.
	  Omit or pass null for all fields. Requesting fewer fields (e.g. without content) reduces tool payload size.
	"""
	if limit <= 0:
		raise ValueError("limit must be positive")
	if limit > 50:
		limit = 50

	field_set = _normalize_fetch_fields(fields)

	conn = _get_connection(dbUrl)
	try:
		# If no query is provided, return the latest memories instead of erroring.
		if query is None or not str(query).strip():
			rows = _fetch_latest_memories(conn, limit)
		elif use_vector_search:
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

		results.append(_memory_row_to_result_dict(row, parsed_tags, field_set))

	return results


def backfill_embeddings(db_url: Optional[str] = None) -> dict:
	"""
	Generate and store embeddings for all memories that don't have one.
	Requires an embedding API to be configured (e.g. OPENAI_API_KEY).
	Returns a dict with backfilled, failed, and total_without counts.
	"""
	conn = _get_connection(db_url)
	try:
		cursor = conn.execute(
			"""
			SELECT m.id, m.content
			FROM memories m
			LEFT JOIN memory_embeddings e ON m.id = e.memory_id
			WHERE e.memory_id IS NULL
			ORDER BY m.id
			"""
		)
		rows = cursor.fetchall()
		created = 0
		failed = 0
		for row in rows:
			memory_id, content = row["id"], row["content"]
			try:
				vec = _embed_text(content)
				if vec is None:
					failed += 1
					continue
				conn.execute(
					"INSERT OR REPLACE INTO memory_embeddings (memory_id, model, embedding) VALUES (?, ?, ?)",
					(memory_id, EMBEDDING_MODEL, json.dumps(vec)),
				)
				created += 1
			except Exception:
				failed += 1
		conn.commit()
		return {
			"backfilled": created,
			"failed": failed,
			"total_without": len(rows),
		}
	finally:
		conn.close()


@mcp.tool
def backfill_all_embeddings(dbUrl: Optional[str] = None) -> dict:
	"""
	Generate and store embeddings for all memories that don't have one.

	Use this after configuring an embedding provider/API key so that older
	memories become available for semantic (RAG-style) retrieval.
	"""
	return backfill_embeddings(dbUrl)


if __name__ == "__main__":
	# Expose the MCP server over HTTP so it can be used via Cloudflare Tunnel.
	# The MCP endpoint will be available at http://localhost:3000/mcp
	mcp.run(transport="http", host="0.0.0.0", port=3000)

