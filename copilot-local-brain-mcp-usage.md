# Local Brain MCP – Copilot Usage Rules

Use these rules when VS Code Copilot is deciding how to call the Local Brain MCP tools (`save_memory`, `fetch_memories`, `update_memory`, `delete_memory`, `backfill_all_embeddings`).

- **Use env vars for the DB path**
  - The memory database location is controlled by environment variables on the MCP server host.
  - The effective path is taken from env `dbUrl`, then `DB_URL`, then `MEMORY_DB_URL`, else `memory.db`.
  - The `dbUrl` *tool argument* is intentionally ignored for DB path resolution.
  - **Do not pass a `dbUrl` argument from Copilot tool calls**; assume all clients share the env-configured DB.

- **Always provide `content` to `save_memory`**
  - `save_memory` **requires** a non-empty `content` string; calls without it are invalid and will fail validation.
  - `title`, `tags`, and `source` are **optional but strongly recommended** so later search feels useful across projects.
  - `tags` should be a **list of short strings** (e.g. `["silent-partner","campaign","bugfix"]`), not a comma-separated string.
  - `source` should identify where the memory came from (e.g. `"cursor-agent"`, `"silent-partner/docs/campaigns.md"`, `"stark-hci/README"`).
  - Avoid extremely large blobs in `content`; prefer concise summaries with links/paths to the underlying source code or docs.

- **`update_memory` and `delete_memory` (hygiene)**
  - Use **`update_memory`** to fix a row in place (pass only fields to change).
  - If `content` changes, use `generate_embedding=true` (default) when an embedding provider is configured so RAG stays aligned; use `generate_embedding=false` only when you intentionally want to drop the vector until a later backfill.
  - Use **`delete_memory`** to remove stale or mistaken rows; embeddings are removed with the row.
  - `update_memory` should raise if the id does not exist or if no update fields are provided; `delete_memory` returns `{ deleted: false }` when the id is missing.

- **Embeddings and vector search (RAG)**
  - Embeddings are optional; they are generated when `generate_embedding=true` and a provider is configured.
  - Provider selection is controlled by `EMBEDDING_PROVIDER` (`openai` or `openrouter`) and `EMBEDDING_MODEL`, plus the appropriate API key (`OPENAI_API_KEY` or `OPENROUTER_API_KEY`).
  - **Do not treat vector search as mandatory for every `fetch_memories` call.** The tool defaults to `use_vector_search=true`, but you should **choose the mode on purpose**: semantic search fits paraphrased or fuzzy intent; keyword search fits exact strings and **avoids embedding the query** when a provider is configured (lower embedding API usage and more predictable hits).
  - When `use_vector_search=true` and embeddings are configured, the server typically **embeds the query** to score memories—an extra remote call per search. Use `use_vector_search=false` when keyword search is enough so Copilot does not pay that cost unnecessarily.
  - When `use_vector_search=true`, if embeddings are unavailable (or no embedding records exist yet), `fetch_memories` should fall back to keyword search over `content`/`title` rather than returning empty results.
  - If `query` is omitted, null, or blank/whitespace-only, `fetch_memories` returns the most recent memories (latest-first) as a “recent history” view instead of performing a semantic/keyword search.
  - Use `backfill_all_embeddings` after configuring an embedding provider so historical memories get embedded.
  - Use **`use_vector_search=false`** when matching **exact** phrases, stack traces, error strings, project names, or known keywords in `title`/`content`.
  - Use **`use_vector_search=true`** when the user’s wording may not match stored text (paraphrase, vague intent, or “something we decided about X” without exact terms).

- **`fetch_memories` — token-conscious usage**
  - Default `limit` is **5** (max 50). Prefer the **smallest** `limit` that still answers the question (e.g. 3 for a narrow check); raise it only when you need broader context.
  - Optional **`fields`**: pass a subset of `id`, `created_at`, `title`, `content`, `tags`, `source` to return only those keys per row. Omit `content` when you only need ids/titles/metadata, then fetch full rows in a follow-up if needed.
  - Optional **`tags_any`** and **`source_prefix`**: apply these whenever possible to narrow recall before answer generation and reduce irrelevant context.
  - For tasks that depend on prior project decisions or stored facts, call `fetch_memories` **early** with a short `query` (or blank for recent history) instead of re-deriving everything from chat.

- **Cross-project behavior**
  - Multiple Cursor projects can share the same memory store safely if they point to the same MCP endpoint / DB configuration.
  - Remember that the DB path is resolved relative to the MCP server environment, not the individual client workspace.
