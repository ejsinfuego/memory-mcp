import argparse
import json
from typing import List, Optional

from server import backfill_embeddings, save_memory


def _parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Local Brain MCP CLI helper for saving memories.",
	)

	subparsers = parser.add_subparsers(dest="command", required=True)

	save_parser = subparsers.add_parser(
		"save",
		help="Save a memory into the configured SQLite database.",
	)
	save_parser.add_argument(
		"--content",
		required=True,
		help="Main text content of the memory.",
	)
	save_parser.add_argument(
		"--title",
		help="Optional short title for the memory.",
	)
	save_parser.add_argument(
		"--tags",
		nargs="*",
		help="Optional list of tags (space separated).",
	)
	save_parser.add_argument(
		"--source",
		help="Optional source identifier (e.g. 'cursor', 'cli', 'web').",
	)
	save_parser.add_argument(
		"--no-embedding",
		action="store_true",
		help="Do not generate/store an embedding for this memory.",
	)

	subparsers.add_parser(
		"backfill-embeddings",
		help="Generate and store embeddings for memories that don't have one (requires OPENAI_API_KEY or OpenRouter).",
	)

	return parser.parse_args()


def main() -> None:
	args = _parse_args()

	if args.command == "save":
		result = save_memory(
			content=args.content,
			title=args.title,
			tags=args.tags or None,
			source=args.source,
			dbUrl=None,
			generate_embedding=not args.no_embedding,
		)
		print(json.dumps(result, indent=2))
	elif args.command == "backfill-embeddings":
		result = backfill_embeddings(db_url=None)
		print(json.dumps(result, indent=2))
		if result["total_without"] == 0:
			print("No memories missing embeddings.")
		elif result["failed"] > 0:
			print(f"Backfilled {result['backfilled']}, {result['failed']} failed (check API key and network).")
		else:
			print(f"Backfilled {result['backfilled']} memories.")
	else:
		raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
	main()

