"""
Phase 3: fetch_memories filters (tags_any, source_prefix).
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


class TestPhase3Filters(unittest.TestCase):
	def setUp(self) -> None:
		fd, path = tempfile.mkstemp(suffix=".db")
		os.close(fd)
		self._db_path = path
		self._prev_default = server.DEFAULT_DB_URL
		server.DEFAULT_DB_URL = path

		server.save_memory(
			content="campaign infra note",
			title="campaign note",
			tags=["campaign", "infra"],
			source="silent-partner/pr",
			generate_embedding=False,
		)
		server.save_memory(
			content="frontend table note",
			title="table note",
			tags=["frontend", "ui"],
			source="silent-partner/ui",
			generate_embedding=False,
		)
		server.save_memory(
			content="other project note",
			title="other note",
			tags=["campaign"],
			source="other-project/docs",
			generate_embedding=False,
		)

	def tearDown(self) -> None:
		server.DEFAULT_DB_URL = self._prev_default
		Path(self._db_path).unlink(missing_ok=True)

	def test_recent_with_tags_filter(self) -> None:
		rows = server.fetch_memories(
			query=None, limit=10, use_vector_search=False, tags_any=["infra"]
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["title"], "campaign note")

	def test_recent_with_source_prefix_filter(self) -> None:
		rows = server.fetch_memories(
			query=None,
			limit=10,
			use_vector_search=False,
			source_prefix="silent-partner/",
		)
		self.assertEqual(len(rows), 2)
		self.assertTrue(all(r["source"].startswith("silent-partner/") for r in rows))

	def test_keyword_with_both_filters(self) -> None:
		rows = server.fetch_memories(
			query="note",
			limit=10,
			use_vector_search=False,
			tags_any=["campaign"],
			source_prefix="silent-partner/",
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["title"], "campaign note")

	def test_filter_validation(self) -> None:
		with self.assertRaises(ValueError):
			server.fetch_memories(query=None, tags_any=["   "])
		with self.assertRaises(ValueError):
			server.fetch_memories(query=None, source_prefix="   ")


if __name__ == "__main__":
	unittest.main()
