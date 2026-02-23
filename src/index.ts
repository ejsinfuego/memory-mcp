import { FastMCP } from "fastmcp";
import Database from "better-sqlite3";
import { z } from "zod";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_DB_URL =
	process.env.dbUrl ??
	process.env.DB_URL ??
	process.env.MEMORY_DB_URL ??
	"memory.db";

const CREATE_TABLE_SQL = `
  CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    title TEXT,
    content TEXT NOT NULL,
    tags TEXT,
    source TEXT
  );
`;

const dbCache = new Map<string, Database>();

function resolveDbPath(dbUrl?: string): string {
	const raw = dbUrl ?? DEFAULT_DB_URL;

	if (raw.startsWith("file:")) {
		return fileURLToPath(new URL(raw));
	}

	// Treat as filesystem path; make relative paths relative to the process cwd
	return path.isAbsolute(raw) ? raw : path.join(process.cwd(), raw);
}

function getDb(dbUrl?: string): Database {
	const resolvedPath = resolveDbPath(dbUrl);

	let db = dbCache.get(resolvedPath);
	if (!db) {
		db = new Database(resolvedPath);
		db.pragma("journal_mode = WAL");
		db.exec(CREATE_TABLE_SQL);
		dbCache.set(resolvedPath, db);
	}

	return db;
}

const server = new FastMCP({
	name: "Local Brain MCP",
	version: "1.0.0",
	instructions:
		"This server stores and retrieves your personal memories in a local SQLite database called memory.db. " +
		"Use the save_memory tool to persist new memories and fetch_memories to search across everything you've saved. " +
		"The data never leaves this machine; remote access should go through your Cloudflare tunnel / gateway.",
});

server.addTool({
	name: "save_memory",
	description:
		"Save a memory snippet into the local SQLite database. Use this for notes, decisions, and context you want available across devices.",
	parameters: z.object({
		content: z.string().min(1, "content must not be empty"),
		title: z.string().optional(),
		tags: z.array(z.string()).optional(),
		source: z.string().optional(),
		dbUrl: z
			.string()
			.describe(
				"Optional database URL or path. Supports file: URLs or filesystem paths. Defaults to MEMORY_DB_URL env or ./memory.db.",
			)
			.optional(),
	}),
	async execute({ content, title, tags, source, dbUrl }) {
		const db = getDb(dbUrl);

		const stmt = db.prepare(
			"INSERT INTO memories (content, title, tags, source) VALUES (?, ?, ?, ?)",
		);

		const info = stmt.run(
			content,
			title ?? null,
			tags ? JSON.stringify(tags) : null,
			source ?? null,
		);

		const id = Number(info.lastInsertRowid);

		return JSON.stringify(
			{
				id,
				title: title ?? null,
				content,
				tags: tags ?? [],
				source: source ?? null,
			},
			null,
			2,
		);
	},
});

server.addTool({
	name: "fetch_memories",
	description:
		"Search memories stored in the local SQLite database by text query. Results are ordered by recency.",
	parameters: z.object({
		query: z.string().min(1, "query must not be empty"),
		limit: z
			.number()
			.int()
			.positive()
			.max(50)
			.optional(),
		dbUrl: z
			.string()
			.describe(
				"Optional database URL or path. Supports file: URLs or filesystem paths. Defaults to MEMORY_DB_URL env or ./memory.db.",
			)
			.optional(),
	}),
	async execute({ query, limit, dbUrl }) {
		const db = getDb(dbUrl);

		const max = limit ?? 10;

		const stmt = db.prepare(
			`SELECT id, created_at, title, content, tags, source
       FROM memories
       WHERE content LIKE ? OR IFNULL(title, '') LIKE ?
       ORDER BY datetime(created_at) DESC, id DESC
       LIMIT ?`,
		);

		const rows = stmt.all(`%${query}%`, `%${query}%`, max) as Array<{
			id: number;
			created_at: string;
			title: string | null;
			content: string;
			tags: string | null;
			source: string | null;
		}>;

		const normalized = rows.map((row) => ({
			id: row.id,
			created_at: row.created_at,
			title: row.title,
			content: row.content,
			tags: row.tags ? JSON.parse(row.tags) : [],
			source: row.source,
		}));

		return JSON.stringify(normalized, null, 2);
	},
});

server
	.start({
		// This starts an HTTP streaming MCP endpoint at /mcp
		// and an SSE endpoint at /sse on localhost:3000.
		transportType: "httpStream",
		httpStream: {
			port: 3000,
		},
	})
	.then(() => {
		// eslint-disable-next-line no-console
		console.log(
			"Local Brain MCP listening on http://localhost:3000 (MCP: /mcp, SSE: /sse)",
		);
	})
	.catch((error) => {
		// eslint-disable-next-line no-console
		console.error("Failed to start Local Brain MCP server", error);
		process.exit(1);
	});
