import json
import math
import os
import sqlite3
from pathlib import Path
from typing import List, Optional, Set, Tuple

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


def _default_db_path() -> Path:
	"""Single database location outside any git checkout."""
	return Path.home() / ".local" / "share" / "local-brain" / "memory.db"


DEFAULT_DB_URL = (
	_get_env("dbUrl")
	or _get_env("DB_URL")
	or _get_env("MEMORY_DB_URL")
	or str(_default_db_path())
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

# text-embedding-3-small. The sqlite-vec table is created at this width.
VEC_DIMS = 1536


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
		raise ValueError(
			"Database path must be absolute so every checkout shares one file. "
			f"Got {raw!r}. Set dbUrl to an absolute path, or unset it to use {_default_db_path()}."
		)
	return p


def _get_connection(db_url: Optional[str]) -> sqlite3.Connection:
	path = _resolve_db_path(db_url)
	path.parent.mkdir(parents=True, exist_ok=True)
	conn = sqlite3.connect(path)
	conn.row_factory = sqlite3.Row
	conn.execute("PRAGMA foreign_keys = ON")
	conn.execute(CREATE_TABLE_SQL)
	conn.execute(CREATE_EMBEDDINGS_SQL)
	conn.execute(
		"""
		CREATE TABLE IF NOT EXISTS retrieval_events (
		  id INTEGER PRIMARY KEY AUTOINCREMENT,
		  created_at TEXT NOT NULL DEFAULT (datetime('now')),
		  query TEXT,
		  retrieval TEXT NOT NULL,
		  memory_ids TEXT NOT NULL,
		  scores TEXT
		)
		"""
	)
	columns = {row[1] for row in conn.execute("PRAGMA table_info(retrieval_events)")}
	if "action" not in columns:
		conn.execute("ALTER TABLE retrieval_events ADD COLUMN action TEXT")
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


def _similarity_score(distance_or_cosine: float, *, from_distance: bool) -> float:
	"""Higher is a closer match. sqlite-vec cosine distance is 1 - cosine similarity."""
	value = (1.0 - distance_or_cosine) if from_distance else distance_or_cosine
	if value < 0.0:
		return 0.0
	if value > 1.0:
		return 1.0
	return round(value, 4)


def _load_sqlite_vec(conn: sqlite3.Connection) -> bool:
	"""Enable the sqlite-vec index. Returns False when the extension cannot load."""
	try:
		import sqlite_vec

		conn.enable_load_extension(True)
		sqlite_vec.load(conn)
		conn.enable_load_extension(False)
		conn.execute(
			f"""
			CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(
				embedding float[{VEC_DIMS}] distance_metric=cosine
			)
			"""
		)
		return True
	except Exception:
		return False


def _serialize_vec(vec: List[float]):
	import sqlite_vec

	return sqlite_vec.serialize_float32(vec)


def _store_embedding(conn: sqlite3.Connection, memory_id: int, vec: List[float]) -> None:
	conn.execute(
		"INSERT OR REPLACE INTO memory_embeddings (memory_id, model, embedding) VALUES (?, ?, ?)",
		(memory_id, EMBEDDING_MODEL, json.dumps(vec)),
	)
	if len(vec) != VEC_DIMS or not _load_sqlite_vec(conn):
		return
	conn.execute("DELETE FROM memory_vec WHERE rowid = ?", (memory_id,))
	conn.execute(
		"INSERT INTO memory_vec(rowid, embedding) VALUES (?, ?)",
		(memory_id, _serialize_vec(vec)),
	)


def _clear_embedding(conn: sqlite3.Connection, memory_id: int) -> None:
	conn.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,))
	if not _load_sqlite_vec(conn):
		return
	conn.execute("DELETE FROM memory_vec WHERE rowid = ?", (memory_id,))


def _sync_vec_index(conn: sqlite3.Connection) -> None:
	"""Copy any JSON embeddings for the current model into the vec index."""
	if not _load_sqlite_vec(conn):
		return
	json_ids = {
		row[0]
		for row in conn.execute(
			"SELECT memory_id FROM memory_embeddings WHERE model = ?",
			(EMBEDDING_MODEL,),
		)
	}
	vec_ids = {row[0] for row in conn.execute("SELECT rowid FROM memory_vec")}
	missing = json_ids - vec_ids
	if missing:
		placeholders = ",".join("?" for _ in missing)
		rows = conn.execute(
			f"SELECT memory_id, embedding FROM memory_embeddings WHERE memory_id IN ({placeholders})",
			tuple(missing),
		).fetchall()
		for memory_id, raw in rows:
			try:
				vec = json.loads(raw)
			except json.JSONDecodeError:
				continue
			if not isinstance(vec, list) or len(vec) != VEC_DIMS:
				continue
			conn.execute(
				"INSERT INTO memory_vec(rowid, embedding) VALUES (?, ?)",
				(memory_id, _serialize_vec(vec)),
			)
	for memory_id in vec_ids - json_ids:
		conn.execute("DELETE FROM memory_vec WHERE rowid = ?", (memory_id,))
	if missing or (vec_ids - json_ids):
		conn.commit()


def _embedding_stored(conn: sqlite3.Connection, memory_id: int) -> bool:
	row = conn.execute(
		"SELECT 1 FROM memory_embeddings WHERE memory_id = ?",
		(memory_id,),
	).fetchone()
	return row is not None


def _search_memories_keyword(
	conn: sqlite3.Connection,
	query: str,
	limit: int,
	tags_any: Optional[Set[str]] = None,
	source_prefix: Optional[str] = None,
) -> List[sqlite3.Row]:
	cursor = conn.execute(
		"""
    SELECT id, created_at, title, content, tags, source
    FROM memories
    WHERE content LIKE ? OR IFNULL(title, '') LIKE ?
    ORDER BY datetime(created_at) DESC, id DESC
    """,
		(f"%{query}%", f"%{query}%"),
	)
	rows = cursor.fetchall()
	return _apply_memory_filters(rows, limit, tags_any, source_prefix)


def _fetch_latest_memories(
	conn: sqlite3.Connection,
	limit: int,
	tags_any: Optional[Set[str]] = None,
	source_prefix: Optional[str] = None,
) -> List[sqlite3.Row]:
	"""
	Return the most recently created memories, newest first.
	"""
	cursor = conn.execute(
		"""
    SELECT id, created_at, title, content, tags, source
    FROM memories
    ORDER BY datetime(created_at) DESC, id DESC
    """,
		(),
	)
	rows = cursor.fetchall()
	return _apply_memory_filters(rows, limit, tags_any, source_prefix)


def _rows_for_ids(conn: sqlite3.Connection, memory_ids: List[int]) -> dict[int, sqlite3.Row]:
	if not memory_ids:
		return {}
	placeholders = ",".join("?" for _ in memory_ids)
	rows = conn.execute(
		f"""
		SELECT id, created_at, title, content, tags, source
		FROM memories
		WHERE id IN ({placeholders})
		""",
		tuple(memory_ids),
	).fetchall()
	return {row["id"]: row for row in rows}


def _search_with_sqlite_vec(
	conn: sqlite3.Connection,
	query_embedding: List[float],
	limit: int,
	tags_any: Optional[Set[str]],
	source_prefix: Optional[str],
) -> Optional[Tuple[List[sqlite3.Row], dict]]:
	"""Nearest neighbors via sqlite-vec. None means the extension is unavailable."""
	if len(query_embedding) != VEC_DIMS or not _load_sqlite_vec(conn):
		return None
	_sync_vec_index(conn)
	indexed = conn.execute("SELECT COUNT(*) FROM memory_vec").fetchone()[0]
	if indexed == 0:
		return None

	k = indexed if (tags_any or source_prefix) else limit
	k = max(1, min(int(indexed), max(k, limit)))
	hits = conn.execute(
		"""
		SELECT rowid, distance
		FROM memory_vec
		WHERE embedding MATCH ?
		  AND k = ?
		""",
		(_serialize_vec(query_embedding), k),
	).fetchall()
	ordered_ids = [row[0] for row in hits]
	id_to_row = _rows_for_ids(conn, ordered_ids)
	scores: dict[int, float] = {}
	matched: List[sqlite3.Row] = []
	for rowid, distance in hits:
		row = id_to_row.get(rowid)
		if row is None or not _row_matches_filters(row, tags_any, source_prefix):
			continue
		scores[rowid] = _similarity_score(float(distance), from_distance=True)
		matched.append(row)
		if len(matched) >= limit:
			break
	return matched, scores


def _search_memories_vector(
	conn: sqlite3.Connection,
	query: str,
	limit: int,
	tags_any: Optional[Set[str]] = None,
	source_prefix: Optional[str] = None,
) -> Tuple[str, List[sqlite3.Row], dict]:
	query_embedding = _embed_text(query)
	if query_embedding is None:
		return (
			"keyword",
			_search_memories_keyword(conn, query, limit, tags_any, source_prefix),
			{},
		)

	vec_hit = _search_with_sqlite_vec(
		conn, query_embedding, limit, tags_any, source_prefix
	)
	if vec_hit is not None:
		return "vector", vec_hit[0], vec_hit[1]

	cursor = conn.execute(
		"""
    SELECT
      e.memory_id,
      e.embedding,
      m.id,
      m.created_at,
      m.title,
      m.content,
      m.tags,
      m.source
    FROM memory_embeddings e
    JOIN memories m ON m.id = e.memory_id
    WHERE e.model = ?
    """,
		(EMBEDDING_MODEL,),
	)
	rows = cursor.fetchall()
	if not rows:
		return (
			"keyword",
			_search_memories_keyword(conn, query, limit, tags_any, source_prefix),
			{},
		)

	scores_list: List[Tuple[int, float]] = []
	id_to_row: dict[int, sqlite3.Row] = {}
	for row in rows:
		if not _row_matches_filters(row, tags_any, source_prefix):
			continue
		try:
			vec = json.loads(row["embedding"])
			score = _cosine_similarity(query_embedding, vec)
		except Exception:
			continue
		scores_list.append((row["memory_id"], score))
		id_to_row[row["memory_id"]] = row

	scores_list.sort(key=lambda item: item[1], reverse=True)
	top = scores_list[:limit]
	score_map = {
		memory_id: _similarity_score(score, from_distance=False)
		for memory_id, score in top
		if memory_id is not None
	}
	ordered = [id_to_row[memory_id] for memory_id in score_map if memory_id in id_to_row]
	return "vector", ordered, score_map


def _normalize_tag_filter(tags_any: Optional[List[str]]) -> Optional[Set[str]]:
	if tags_any is None:
		return None
	names = {str(t).strip().lower() for t in tags_any if str(t).strip()}
	if not names:
		raise ValueError("tags_any, if provided, must include at least one non-empty tag")
	return names


def _normalize_source_prefix(source_prefix: Optional[str]) -> Optional[str]:
	if source_prefix is None:
		return None
	value = str(source_prefix).strip()
	if not value:
		raise ValueError(
			"source_prefix, if provided, must contain at least one non-whitespace character"
		)
	return value


def _row_matches_filters(
	row: sqlite3.Row, tags_any: Optional[Set[str]], source_prefix: Optional[str]
) -> bool:
	if source_prefix is not None:
		source = row["source"] or ""
		if not str(source).startswith(source_prefix):
			return False

	if tags_any is not None:
		raw_tags = row["tags"]
		try:
			stored_tags = json.loads(raw_tags) if raw_tags else []
		except json.JSONDecodeError:
			stored_tags = []
		stored_tag_set = {
			str(tag).strip().lower()
			for tag in stored_tags
			if isinstance(tag, str) and str(tag).strip()
		}
		if not (stored_tag_set & tags_any):
			return False

	return True


def _apply_memory_filters(
	rows: List[sqlite3.Row],
	limit: int,
	tags_any: Optional[Set[str]],
	source_prefix: Optional[str],
) -> List[sqlite3.Row]:
	if tags_any is None and source_prefix is None:
		return rows[:limit]
	matched: List[sqlite3.Row] = []
	for row in rows:
		if _row_matches_filters(row, tags_any, source_prefix):
			matched.append(row)
			if len(matched) >= limit:
				break
	return matched


_FETCH_MEMORY_FIELD_NAMES = frozenset(
	{"id", "created_at", "title", "content", "tags", "source", "retrieval", "score"}
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
	row: sqlite3.Row,
	parsed_tags: List[str],
	field_set: Optional[frozenset],
	retrieval: str,
	score: Optional[float],
) -> dict:
	full = {
		"id": row["id"],
		"created_at": row["created_at"],
		"title": row["title"],
		"content": row["content"],
		"tags": parsed_tags,
		"source": row["source"],
		"retrieval": retrieval,
		"score": score,
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
		embedded = False

		if generate_embedding:
			try:
				vec = _embed_text(content)
				if vec is not None:
					_store_embedding(conn, memory_id, vec)
					embedded = True
			except Exception:
				# Keep the textual memory even when the embedding API fails.
				embedded = False

		_log_activity(conn, "save_memory", [int(memory_id)])
		conn.commit()
	finally:
		conn.close()

	return {
		"id": memory_id,
		"title": title,
		"content": content,
		"tags": tags or [],
		"source": source,
		"embedded": embedded,
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

		embedded = _embedding_stored(conn, memory_id)
		if content_changed:
			_clear_embedding(conn, memory_id)
			embedded = False
			if generate_embedding:
				try:
					vec = _embed_text(new_content)
					if vec is not None:
						_store_embedding(conn, memory_id, vec)
						embedded = True
				except Exception:
					embedded = False

		_log_activity(conn, "update_memory", [int(memory_id)])
		conn.commit()
	finally:
		conn.close()

	return {
		"id": memory_id,
		"title": new_title,
		"content": new_content,
		"tags": parsed_tags,
		"source": new_source,
		"embedded": embedded,
	}


@mcp.tool
def delete_memory(memory_id: int, dbUrl: Optional[str] = None) -> dict:
	"""
	Delete a memory row by id. Removes any stored embedding for that row (FK cascade).
	Returns whether a row was deleted.
	"""
	conn = _get_connection(dbUrl)
	try:
		_clear_embedding(conn, memory_id)
		cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
		deleted = cur.rowcount > 0
		if deleted:
			_log_activity(conn, "delete_memory", [int(memory_id)])
		conn.commit()
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
	tags_any: Optional[List[str]] = None,
	source_prefix: Optional[str] = None,
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
	- fields: optional subset of keys to return per row: id, created_at, title, content, tags, source, retrieval, score.
	  Omit or pass null for all fields. Requesting fewer fields (e.g. without content) reduces tool payload size.
	  `retrieval` is `vector`, `keyword`, or `recent`. `score` is cosine similarity for vector hits, otherwise null.
	- tags_any: optional list of tags; when provided, return only rows that contain at least one of these tags.
	- source_prefix: optional source prefix; when provided, return only rows where source starts with this prefix.
	"""
	found = retrieve_memories(
		query=query,
		limit=limit,
		dbUrl=dbUrl,
		use_vector_search=use_vector_search,
		fields=fields,
		tags_any=tags_any,
		source_prefix=source_prefix,
	)
	return found["results"]


def retrieve_memories(
	query: Optional[str] = None,
	limit: int = 5,
	dbUrl: Optional[str] = None,
	use_vector_search: bool = True,
	fields: Optional[List[str]] = None,
	tags_any: Optional[List[str]] = None,
	source_prefix: Optional[str] = None,
) -> dict:
	"""Same lookup as fetch_memories, plus the retrieval mode when the result list is empty."""
	if limit <= 0:
		raise ValueError("limit must be positive")
	if limit > 50:
		limit = 50

	field_set = _normalize_fetch_fields(fields)
	tag_filter = _normalize_tag_filter(tags_any)
	source_filter = _normalize_source_prefix(source_prefix)

	conn = _get_connection(dbUrl)
	try:
		if query is None or not str(query).strip():
			retrieval = "recent"
			rows = _fetch_latest_memories(conn, limit, tag_filter, source_filter)
			scores: dict = {}
		elif use_vector_search:
			retrieval, rows, scores = _search_memories_vector(
				conn, query, limit, tag_filter, source_filter
			)
		else:
			retrieval = "keyword"
			rows = _search_memories_keyword(conn, query, limit, tag_filter, source_filter)
			scores = {}
		_log_activity(
			conn,
			"retrieve_memories",
			[int(row["id"]) for row in rows],
			query="" if query is None else str(query).strip(),
			retrieval=retrieval,
			scores=scores,
		)
		conn.commit()
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
			_memory_row_to_result_dict(
				row,
				parsed_tags,
				field_set,
				retrieval,
				scores.get(row["id"]),
			)
		)

	return {"retrieval": retrieval, "results": results}


def _log_activity(
	conn: sqlite3.Connection,
	action: str,
	memory_ids: List[int],
	query: str = "",
	retrieval: str = "",
	scores: Optional[dict] = None,
) -> None:
	"""Record a server call so the live graph can add, move, or remove memories."""
	score_payload = {str(memory_id): value for memory_id, value in (scores or {}).items()}
	conn.execute(
		"""
		INSERT INTO retrieval_events (query, retrieval, memory_ids, scores, action)
		VALUES (?, ?, ?, ?, ?)
		""",
		(
			query,
			retrieval or action,
			json.dumps(memory_ids),
			json.dumps(score_payload),
			action,
		),
	)
	cutoff = conn.execute(
		"SELECT COALESCE(MAX(id), 0) - 200 FROM retrieval_events"
	).fetchone()[0]
	if cutoff and cutoff > 0:
		conn.execute("DELETE FROM retrieval_events WHERE id < ?", (cutoff,))


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
		created_ids: List[int] = []
		for row in rows:
			memory_id, content = row["id"], row["content"]
			try:
				vec = _embed_text(content)
				if vec is None:
					failed += 1
					continue
				_store_embedding(conn, memory_id, vec)
				created += 1
				created_ids.append(int(memory_id))
			except Exception:
				failed += 1
		_log_activity(conn, "backfill_embeddings", created_ids)
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


def reembed_embeddings(db_url: Optional[str] = None) -> dict:
	"""Re-embed rows with no vector or a vector from a different model."""
	conn = _get_connection(db_url)
	try:
		rows = conn.execute(
			"""
			SELECT m.id, m.content
			FROM memories m
			LEFT JOIN memory_embeddings e ON m.id = e.memory_id
			WHERE e.memory_id IS NULL OR e.model != ?
			ORDER BY m.id
			""",
			(EMBEDDING_MODEL,),
		).fetchall()
		reembedded = 0
		failed = 0
		reembedded_ids: List[int] = []
		for row in rows:
			try:
				vec = _embed_text(row["content"])
				if vec is None:
					failed += 1
					continue
				_clear_embedding(conn, row["id"])
				_store_embedding(conn, row["id"], vec)
				reembedded += 1
				reembedded_ids.append(int(row["id"]))
			except Exception:
				failed += 1
		_log_activity(conn, "reembed_embeddings", reembedded_ids)
		conn.commit()
		return {
			"reembedded": reembedded,
			"failed": failed,
			"total": len(rows),
			"model": EMBEDDING_MODEL,
		}
	finally:
		conn.close()


@mcp.tool
def doctor(ping: bool = True) -> dict:
	"""
	Report whether the memory database and embedding API are healthy.

	- ping: when True (default), send a short embedding request and record success or failure.
	"""
	path = _resolve_db_path(None)
	warnings: List[str] = []
	report = {
		"db_path": str(path),
		"db_exists": path.is_file(),
		"configured_model": EMBEDDING_MODEL,
		"embedding_provider": EMBEDDING_PROVIDER,
		"memories": 0,
		"embeddings": 0,
		"missing_embeddings": 0,
		"model_mismatch_count": 0,
		"stored_models": {},
		"vector_index": "python",
		"embedding_api": "skipped",
		"warnings": warnings,
	}
	if not path.is_file():
		warnings.append("Database file does not exist yet. It will be created on the next save.")
		return report

	conn = _get_connection(None)
	try:
		report["memories"] = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
		report["embeddings"] = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
		report["missing_embeddings"] = conn.execute(
			"""
			SELECT COUNT(*) FROM memories m
			LEFT JOIN memory_embeddings e ON m.id = e.memory_id
			WHERE e.memory_id IS NULL
			"""
		).fetchone()[0]
		report["model_mismatch_count"] = conn.execute(
			"SELECT COUNT(*) FROM memory_embeddings WHERE model != ?",
			(EMBEDDING_MODEL,),
		).fetchone()[0]
		report["stored_models"] = {
			row[0]: row[1]
			for row in conn.execute(
				"SELECT model, COUNT(*) FROM memory_embeddings GROUP BY model"
			)
		}
		if _load_sqlite_vec(conn):
			report["vector_index"] = "sqlite-vec"
			_sync_vec_index(conn)
			indexed = conn.execute("SELECT COUNT(*) FROM memory_vec").fetchone()[0]
			report["vector_index_rows"] = indexed
			matching_model = conn.execute(
				"SELECT COUNT(*) FROM memory_embeddings WHERE model = ?",
				(EMBEDDING_MODEL,),
			).fetchone()[0]
			if indexed < matching_model:
				warnings.append(
					"Some embeddings for the configured model are not in the sqlite-vec index. "
					"They may have a different dimension than "
					f"{VEC_DIMS}."
				)
		else:
			warnings.append(
				"sqlite-vec is not available. Vector search is computing cosine similarity in Python."
			)
	finally:
		conn.close()

	if report["missing_embeddings"]:
		warnings.append(
			f"{report['missing_embeddings']} memories have no embedding and are skipped by vector search."
		)
	if report["model_mismatch_count"]:
		warnings.append(
			f"{report['model_mismatch_count']} embeddings were built with a different model than "
			f"{EMBEDDING_MODEL}. Run reembed to include them in vector search."
		)
	if ping:
		try:
			vec = _embed_text("embedding health check")
			report["embedding_api"] = "ok" if vec else "failed"
		except Exception:
			report["embedding_api"] = "failed"
		if report["embedding_api"] != "ok":
			warnings.append(
				"Embedding API call failed. Semantic search will use keyword matching."
			)
	return report


if __name__ == "__main__":
	# Expose the MCP server over HTTP so it can be used via Cloudflare Tunnel.
	# The MCP endpoint will be available at http://localhost:3000/mcp
	mcp.run(transport="http", host="0.0.0.0", port=3000)

