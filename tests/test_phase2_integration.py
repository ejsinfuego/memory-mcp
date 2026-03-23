"""
Phase 2: delete_memory, update_memory, and FK cascade for embeddings.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class TestPhase2Hygiene(unittest.TestCase):
	def setUp(self) -> None:
		fd, path = tempfile.mkstemp(suffix=".db")
		os.close(fd)
		self._db_path = path
		self._prev_default = server.DEFAULT_DB_URL
		server.DEFAULT_DB_URL = path

	def tearDown(self) -> None:
		server.DEFAULT_DB_URL = self._prev_default
		Path(self._db_path).unlink(missing_ok=True)

	def test_delete_memory(self) -> None:
		out = server.save_memory(
			content="to delete",
			title="td",
			tags=["x"],
			source="t",
			generate_embedding=False,
		)
		mid = out["id"]
		d = server.delete_memory(mid)
		self.assertTrue(d["deleted"])
		self.assertEqual(d["id"], mid)
		d2 = server.delete_memory(mid)
		self.assertFalse(d2["deleted"])

	def test_delete_cascades_embedding(self) -> None:
		out = server.save_memory(
			content="with vec",
			generate_embedding=False,
		)
		mid = out["id"]
		conn = sqlite3.connect(self._db_path)
		try:
			conn.execute("PRAGMA foreign_keys = ON")
			conn.execute(
				"INSERT INTO memory_embeddings (memory_id, model, embedding) VALUES (?, ?, ?)",
				(mid, server.EMBEDDING_MODEL, json.dumps([0.0, 1.0])),
			)
			conn.commit()
		finally:
			conn.close()

		server.delete_memory(mid)
		conn = sqlite3.connect(self._db_path)
		try:
			n = conn.execute(
				"SELECT COUNT(*) FROM memory_embeddings WHERE memory_id = ?",
				(mid,),
			).fetchone()[0]
		finally:
			conn.close()
		self.assertEqual(n, 0)

	def test_update_memory_requires_field(self) -> None:
		out = server.save_memory(content="a", generate_embedding=False)
		with self.assertRaises(ValueError):
			server.update_memory(out["id"])

	def test_update_memory_not_found(self) -> None:
		with self.assertRaises(ValueError) as ctx:
			server.update_memory(999_999, title="nope")
		self.assertIn("not found", str(ctx.exception).lower())

	def test_update_memory_title_only(self) -> None:
		out = server.save_memory(
			content="body",
			title="old",
			generate_embedding=False,
		)
		mid = out["id"]
		updated = server.update_memory(mid, title="new")
		self.assertEqual(updated["title"], "new")
		self.assertEqual(updated["content"], "body")

	def test_update_memory_content_clears_embedding_when_no_regen(self) -> None:
		out = server.save_memory(content="original", generate_embedding=False)
		mid = out["id"]
		conn = sqlite3.connect(self._db_path)
		try:
			conn.execute("PRAGMA foreign_keys = ON")
			conn.execute(
				"INSERT INTO memory_embeddings (memory_id, model, embedding) VALUES (?, ?, ?)",
				(mid, server.EMBEDDING_MODEL, json.dumps([1.0, 0.0])),
			)
			conn.commit()
		finally:
			conn.close()

		server.update_memory(
			mid,
			content="replaced text",
			generate_embedding=False,
		)
		conn = sqlite3.connect(self._db_path)
		try:
			n = conn.execute(
				"SELECT COUNT(*) FROM memory_embeddings WHERE memory_id = ?",
				(mid,),
			).fetchone()[0]
			row = conn.execute(
				"SELECT content FROM memories WHERE id = ?",
				(mid,),
			).fetchone()
		finally:
			conn.close()
		self.assertEqual(n, 0)
		self.assertEqual(row[0], "replaced text")

	def test_update_empty_content_rejected(self) -> None:
		out = server.save_memory(content="ok", generate_embedding=False)
		with self.assertRaises(ValueError):
			server.update_memory(out["id"], content="   ")


if __name__ == "__main__":
	unittest.main()
