"""Retrieval mode, embedding flag, absolute database path, and vector ranking."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def _unit_vector(index: int) -> list:
	vec = [0.0] * server.VEC_DIMS
	vec[index] = 1.0
	return vec


class TestRetrievalImprovements(unittest.TestCase):
	def setUp(self) -> None:
		fd, path = tempfile.mkstemp(suffix=".db")
		os.close(fd)
		self._db_path = path
		self._prev_default = server.DEFAULT_DB_URL
		server.DEFAULT_DB_URL = path
		self._prev_embed = server._embed_text

	def tearDown(self) -> None:
		server.DEFAULT_DB_URL = self._prev_default
		server._embed_text = self._prev_embed
		Path(self._db_path).unlink(missing_ok=True)

	def test_relative_database_path_is_rejected(self) -> None:
		server.DEFAULT_DB_URL = "memory.db"
		with self.assertRaises(ValueError) as ctx:
			server.save_memory(content="nope", generate_embedding=False)
		self.assertIn("absolute", str(ctx.exception).lower())

	def test_save_reports_when_embedding_is_skipped(self) -> None:
		out = server.save_memory(content="plain note", generate_embedding=False)
		self.assertFalse(out["embedded"])

	def test_recent_retrieval_mode(self) -> None:
		server.save_memory(content="hello", title="hi", generate_embedding=False)
		found = server.retrieve_memories(query=None, use_vector_search=False)
		self.assertEqual(found["retrieval"], "recent")
		self.assertEqual(found["results"][0]["retrieval"], "recent")
		self.assertIsNone(found["results"][0]["score"])

	def test_vector_search_ranks_the_closer_memory(self) -> None:
		alpha = server.save_memory(content="alpha", title="alpha", generate_embedding=False)
		beta = server.save_memory(content="beta", title="beta", generate_embedding=False)
		conn = server._get_connection(None)
		try:
			server._store_embedding(conn, alpha["id"], _unit_vector(0))
			server._store_embedding(conn, beta["id"], _unit_vector(1))
			conn.commit()
		finally:
			conn.close()

		server._embed_text = lambda text: _unit_vector(0)
		found = server.retrieve_memories(query="closest to alpha", limit=2)
		self.assertEqual(found["retrieval"], "vector")
		self.assertEqual(found["results"][0]["title"], "alpha")
		self.assertGreater(found["results"][0]["score"], found["results"][1]["score"])

	def test_keyword_mode_is_explicit(self) -> None:
		server.save_memory(content="unique-keyword-phrase", title="kw", generate_embedding=False)
		found = server.retrieve_memories(
			query="unique-keyword-phrase",
			use_vector_search=False,
		)
		self.assertEqual(found["retrieval"], "keyword")
		self.assertEqual(found["results"][0]["title"], "kw")
		self.assertIsNone(found["results"][0]["score"])

	def test_save_update_and_delete_record_graph_events(self) -> None:
		import sqlite3

		saved = server.save_memory(content="graph node", title="graph node", generate_embedding=False)
		server.update_memory(saved["id"], title="graph node renamed")
		server.delete_memory(saved["id"])
		server.delete_memory(saved["id"])
		conn = sqlite3.connect(self._db_path)
		try:
			actions = [row[0] for row in conn.execute("SELECT action FROM retrieval_events ORDER BY id")]
		finally:
			conn.close()
		self.assertEqual(actions, ["save_memory", "update_memory", "delete_memory"])

	def test_retrieve_records_an_event(self) -> None:
		import sqlite3

		server.save_memory(content="logged note", title="logged", generate_embedding=False)
		server.retrieve_memories(query=None)
		conn = sqlite3.connect(self._db_path)
		try:
			row = conn.execute(
				"SELECT retrieval, memory_ids, action FROM retrieval_events ORDER BY id DESC LIMIT 1"
			).fetchone()
		finally:
			conn.close()
		self.assertIsNotNone(row)
		self.assertEqual(row[0], "recent")
		self.assertIn("[", row[1])
		self.assertEqual(row[2], "retrieve_memories")

	def test_doctor_reports_missing_embeddings_without_ping(self) -> None:
		server.save_memory(content="bare", generate_embedding=False)
		report = server.doctor(ping=False)
		self.assertEqual(report["db_path"], self._db_path)
		self.assertEqual(report["missing_embeddings"], 1)
		self.assertEqual(report["embedding_api"], "skipped")
		self.assertTrue(report["warnings"])


if __name__ == "__main__":
	unittest.main()
