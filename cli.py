import argparse
import json

from server import (
	backfill_embeddings,
	doctor,
	fetch_memories,
	reembed_embeddings,
	retrieve_memories,
	save_memory,
)


def _parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Local Brain CLI for the shared memory database.",
	)
	subparsers = parser.add_subparsers(dest="command", required=True)

	save_parser = subparsers.add_parser("save", help="Save a memory.")
	save_parser.add_argument("--content", required=True, help="Main text content of the memory.")
	save_parser.add_argument("--title", help="Optional short title.")
	save_parser.add_argument("--tags", nargs="*", help="Optional tags (space separated).")
	save_parser.add_argument("--source", help="Optional source identifier.")
	save_parser.add_argument(
		"--no-embedding",
		action="store_true",
		help="Do not generate an embedding for this memory.",
	)

	subparsers.add_parser(
		"backfill-embeddings",
		help="Embed memories that do not have a vector yet.",
	)
	subparsers.add_parser(
		"reembed",
		help="Re-embed memories whose vector is missing or was built with another model.",
	)
	subparsers.add_parser(
		"doctor",
		help="Check the database path, embedding coverage, and the embedding API.",
	)

	list_parser = subparsers.add_parser("list", help="Show the newest memories.")
	list_parser.add_argument("--limit", type=int, default=20, help="How many rows to show (max 50).")

	search_parser = subparsers.add_parser("search", help="Search memories.")
	search_parser.add_argument("query", help="Text to search for.")
	search_parser.add_argument("--limit", type=int, default=5, help="How many rows to return (max 50).")
	search_parser.add_argument(
		"--keyword",
		action="store_true",
		help="Match title and content text instead of embeddings.",
	)
	subparsers.add_parser(
		"dashboard",
		help="Open the live memory graph at http://127.0.0.1:8765",
	)

	return parser.parse_args()


def _print_rows(rows: list) -> None:
	if not rows:
		print("No matches.")
		return
	for row in rows:
		score = row.get("score")
		score_text = "" if score is None else f" score={score}"
		title = row.get("title") or ""
		print(f"{row.get('id')}\t{row.get('retrieval', '')}{score_text}\t{title}")


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
	elif args.command == "reembed":
		result = reembed_embeddings(db_url=None)
		print(json.dumps(result, indent=2))
	elif args.command == "doctor":
		print(json.dumps(doctor(ping=True), indent=2))
	elif args.command == "list":
		rows = fetch_memories(query=None, limit=args.limit, use_vector_search=False)
		_print_rows(rows)
	elif args.command == "dashboard":
		from dashboard import main as dashboard_main

		dashboard_main()
	elif args.command == "search":
		found = retrieve_memories(
			query=args.query,
			limit=args.limit,
			use_vector_search=not args.keyword,
		)
		print(f"retrieval: {found['retrieval']}")
		_print_rows(found["results"])
	else:
		raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
	main()
