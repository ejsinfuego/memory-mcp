"""Local graph of memories. Nodes change when server.py records a call."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import parse_qs, urlparse

import server

HOST = "127.0.0.1"
PORT = 8765
_GRAPH: dict = {"nodes": [], "links": []}
_GRAPH_TOKEN = -1


def build_graph() -> dict:
	"""Each memory is a node. Each link is one of its two nearest embeddings."""
	conn = server._get_connection(None)
	try:
		memories = conn.execute(
			"SELECT id, title, content FROM memories ORDER BY id"
		).fetchall()
		nodes = []
		for row in memories:
			title = (row["title"] or "").strip() or (row["content"] or "").strip()
			nodes.append({"id": int(row["id"]), "title": title[:80]})

		links: Dict[Tuple[int, int], float] = {}
		if server._load_sqlite_vec(conn):
			server._sync_vec_index(conn)
			embedded = conn.execute(
				"""
				SELECT memory_id, embedding
				FROM memory_embeddings
				WHERE model = ?
				""",
				(server.EMBEDDING_MODEL,),
			).fetchall()
			for row in embedded:
				try:
					vec = json.loads(row["embedding"])
				except json.JSONDecodeError:
					continue
				if not isinstance(vec, list) or len(vec) != server.VEC_DIMS:
					continue
				hits = conn.execute(
					"""
					SELECT rowid, distance
					FROM memory_vec
					WHERE embedding MATCH ?
					  AND k = 3
					""",
					(server._serialize_vec(vec),),
				).fetchall()
				source_id = int(row["memory_id"])
				for other_id, distance in hits:
					other_id = int(other_id)
					if other_id == source_id:
						continue
					pair = (source_id, other_id) if source_id < other_id else (other_id, source_id)
					weight = server._similarity_score(float(distance), from_distance=True)
					links[pair] = max(links.get(pair, 0.0), weight)
		return {
			"nodes": nodes,
			"links": [
				{"source": source, "target": target, "weight": round(weight, 4)}
				for (source, target), weight in links.items()
			],
		}
	finally:
		conn.close()


def current_graph() -> dict:
	"""Rebuild after a recorded server call so a reload shows new and deleted memories."""
	global _GRAPH, _GRAPH_TOKEN
	conn = server._get_connection(None)
	try:
		token = conn.execute("SELECT COALESCE(MAX(id), 0) FROM retrieval_events").fetchone()[0]
	finally:
		conn.close()
	if token != _GRAPH_TOKEN:
		_GRAPH = build_graph()
		_GRAPH_TOKEN = token
	return _GRAPH


def links_for(memory_id: int) -> List[dict]:
	"""Up to two nearest memories for one row, using the same similarity index as the graph."""
	conn = server._get_connection(None)
	try:
		if not server._load_sqlite_vec(conn):
			return []
		row = conn.execute(
			"""
			SELECT embedding
			FROM memory_embeddings
			WHERE memory_id = ? AND model = ?
			""",
			(memory_id, server.EMBEDDING_MODEL),
		).fetchone()
		if row is None:
			return []
		try:
			vec = json.loads(row["embedding"])
		except json.JSONDecodeError:
			return []
		if not isinstance(vec, list) or len(vec) != server.VEC_DIMS:
			return []
		hits = conn.execute(
			"""
			SELECT rowid, distance
			FROM memory_vec
			WHERE embedding MATCH ?
			  AND k = 3
			""",
			(server._serialize_vec(vec),),
		).fetchall()
		links = []
		for other_id, distance in hits:
			other_id = int(other_id)
			if other_id == memory_id:
				continue
			links.append(
				{
					"source": memory_id,
					"target": other_id,
					"weight": round(server._similarity_score(float(distance), from_distance=True), 4),
				}
			)
		return links
	finally:
		conn.close()


def _events_after(after_id: int) -> List[dict]:
	conn = server._get_connection(None)
	try:
		rows = conn.execute(
			"""
			SELECT id, created_at, query, retrieval, memory_ids, scores, action
			FROM retrieval_events
			WHERE id > ?
			ORDER BY id
			LIMIT 20
			""",
			(after_id,),
		).fetchall()
	finally:
		conn.close()
	events = []
	for row in rows:
		try:
			memory_ids = json.loads(row["memory_ids"]) if row["memory_ids"] else []
		except json.JSONDecodeError:
			memory_ids = []
		try:
			scores = json.loads(row["scores"]) if row["scores"] else {}
		except json.JSONDecodeError:
			scores = {}
		events.append(
			{
				"id": int(row["id"]),
				"created_at": row["created_at"],
				"query": row["query"] or "",
				"retrieval": row["retrieval"],
				"memory_ids": memory_ids,
				"scores": scores,
				"action": row["action"] or row["retrieval"],
			}
		)
	return events


def _memory_detail(memory_id: int):
	conn = server._get_connection(None)
	try:
		row = conn.execute(
			"""
			SELECT id, created_at, title, content, tags, source
			FROM memories
			WHERE id = ?
			""",
			(memory_id,),
		).fetchone()
		if row is None:
			return None
		try:
			tags = json.loads(row["tags"]) if row["tags"] else []
		except json.JSONDecodeError:
			tags = []
		neighbor_ids = []
		for link in links_for(memory_id):
			neighbor_ids.append(link["target"])
		return {
			"id": int(row["id"]),
			"created_at": row["created_at"],
			"title": row["title"] or "",
			"content": row["content"] or "",
			"tags": tags,
			"source": row["source"] or "",
			"neighbors": neighbor_ids[:8],
		}
	finally:
		conn.close()


class _Handler(BaseHTTPRequestHandler):
	def log_message(self, fmt: str, *args) -> None:
		return

	def _send(self, status: int, body: bytes, content_type: str) -> None:
		self.send_response(status)
		self.send_header("Content-Type", content_type)
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def do_GET(self) -> None:
		parsed = urlparse(self.path)
		if parsed.path == "/":
			html = (Path(__file__).with_name("dashboard.html")).read_bytes()
			self._send(200, html, "text/html; charset=utf-8")
			return
		if parsed.path == "/graph":
			self._send(200, json.dumps(current_graph()).encode(), "application/json")
			return
		if parsed.path.startswith("/neighbors/"):
			raw_id = parsed.path.removeprefix("/neighbors/")
			if not raw_id.isdigit():
				self._send(404, b"not found", "text/plain")
				return
			memory_id = int(raw_id)
			detail = _memory_detail(memory_id)
			if detail is None:
				self._send(404, b"not found", "text/plain")
				return
			body = {
				"id": memory_id,
				"title": detail["title"],
				"links": links_for(memory_id),
			}
			self._send(200, json.dumps(body).encode(), "application/json")
			return
		if parsed.path == "/events":
			raw = parse_qs(parsed.query).get("after", ["0"])[0]
			try:
				after_id = int(raw)
			except ValueError:
				after_id = 0
			self._send(200, json.dumps({"events": _events_after(after_id)}).encode(), "application/json")
			return
		if parsed.path.startswith("/memories/"):
			raw_id = parsed.path.removeprefix("/memories/")
			if not raw_id.isdigit():
				self._send(404, b"not found", "text/plain")
				return
			detail = _memory_detail(int(raw_id))
			if detail is None:
				self._send(404, b"not found", "text/plain")
				return
			self._send(200, json.dumps(detail).encode(), "application/json")
			return
		self._send(404, b"not found", "text/plain")

	def do_POST(self) -> None:
		if urlparse(self.path).path != "/search":
			self._send(404, b"not found", "text/plain")
			return
		length = int(self.headers.get("Content-Length", "0"))
		try:
			payload = json.loads(self.rfile.read(length) or b"{}")
		except json.JSONDecodeError:
			self._send(400, b"invalid json", "text/plain")
			return
		query = str(payload.get("query") or "")
		found = server.retrieve_memories(query=query, limit=8, use_vector_search=True)
		body = json.dumps(
			{
				"retrieval": found["retrieval"],
				"ids": [row.get("id") for row in found["results"]],
			}
		).encode()
		self._send(200, body, "application/json")


def main() -> None:
	global _GRAPH, _GRAPH_TOKEN
	print("Building memory graph...", flush=True)
	_GRAPH = build_graph()
	conn = server._get_connection(None)
	try:
		_GRAPH_TOKEN = conn.execute("SELECT COALESCE(MAX(id), 0) FROM retrieval_events").fetchone()[0]
	finally:
		conn.close()
	print(
		f"Graph: {len(_GRAPH['nodes'])} memories, {len(_GRAPH['links'])} similarity links",
		flush=True,
	)
	print(f"Dashboard http://{HOST}:{PORT}", flush=True)
	ThreadingHTTPServer((HOST, PORT), _Handler).serve_forever()


if __name__ == "__main__":
	main()
