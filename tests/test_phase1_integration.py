"""
Integration checks for fetch_memories defaults (limit=5), fields shaping, and validation.

Uses a temporary DB by patching server.DEFAULT_DB_URL so tests do not depend on
env vs mcp.json precedence (dbUrl from Cursor config wins over MEMORY_DB_URL).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class TestFetchMemoriesPhase1(unittest.TestCase):
	def setUp(self) -> None:
		fd, path = tempfile.mkstemp(suffix=".db")
		os.close(fd)
		self._db_path = path
		self._prev_default = server.DEFAULT_DB_URL
		server.DEFAULT_DB_URL = path

	def tearDown(self) -> None:
		server.DEFAULT_DB_URL = self._prev_default
		Path(self._db_path).unlink(missing_ok=True)

	def test_default_limit_fields_and_validation(self) -> None:
		for i in range(7):
			server.save_memory(
				content=f"content chunk number {i} alpha beta",
				title=f"title-{i}",
				tags=["test", f"t{i}"],
				source="integration-test",
				generate_embedding=False,
			)

		rows = server.fetch_memories(query=None, use_vector_search=False)
		self.assertEqual(len(rows), 5)
		titles = [r["title"] for r in rows]
		self.assertEqual(titles, [f"title-{j}" for j in range(6, 1, -1)])

		rows10 = server.fetch_memories(query=None, limit=10, use_vector_search=False)
		self.assertEqual(len(rows10), 7)

		slim = server.fetch_memories(
			query=None,
			limit=3,
			use_vector_search=False,
			fields=["id", "title"],
		)
		self.assertEqual(len(slim), 3)
		for r in slim:
			self.assertEqual(set(r.keys()), {"id", "title"})

		kw = server.fetch_memories(
			query="alpha",
			limit=5,
			use_vector_search=False,
			fields=["title", "content"],
		)
		self.assertGreaterEqual(len(kw), 1)
		for r in kw:
			self.assertEqual(set(r.keys()), {"title", "content"})

		with self.assertRaises(ValueError) as ctx:
			server.fetch_memories(query="x", fields=["nope"])
		self.assertIn("unknown", str(ctx.exception).lower())


if __name__ == "__main__":
	unittest.main()
