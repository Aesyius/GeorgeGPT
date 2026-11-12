# Repository chatbot architecture

The application is a Python package under `vectorizer/`, not a tool-using agent.

## Modules

| Area | Modules |
| --- | --- |
| Indexing orchestration | `vectorize` (CLI/single file), `repository_index` (repository preparation/publication) |
| Safe source discovery | `source_files` (allowlists, exclusions, size limits, deterministic discovery) |
| Text representation | `chunking` (structure, offsets, embedding context), `document_loader` (legacy document loader) |
| Persistence | `vector_store` (pagination/reuse/batches), `keyword_index` (atomic SQLite FTS5 publication) |
| Embedding transport | `embeddings` (Ollama client and retry policy) |
| Integrity audit | `verify_index` |
| Retrieval | `retrieval` (Chroma semantic search + SQLite FTS5/BM25) |
| Generation | `chat` (bounded evidence and direct Ollama requests) |
| Interfaces | `ask` (CLI), `server` (authenticated OpenAI-compatible API) |
| Completion admission | `completion_queue` (one active generation, bounded FIFO waits, cancellation-safe leases) |
| Evaluation | `evaluate_questions`, `evaluate_retrieval`, using `sample_questions/` |
| Service lifecycle | `bootup` (CLI, verified stop plans, per-service startup orchestration) |
| Service settings | `service_config` (resolved startup settings), `model_config` (validated model/context configuration) |
| Windows process operations | `windows_processes` (inspection, reusable process-tree index, hidden launch, termination) |
| Service readiness/secrets | `service_runtime` (HTTP checks, bounded waits, read-only WebUI credential resolution) |
| Shared utilities | `paths`, `model_config`, `progress` |

Indexing reads safe text files and persists vectors and keyword rows in the
existing project-root `chroma_db/`. Moving Python modules does not change that
location. Explicit relative database paths still resolve from the working
directory. Only the explicit verifier performs full integrity audits; runtime
startup does not scan every chunk's metadata.

## Partitioning and compatibility

- CLI entry points stay in their original modules. Existing `bootup` helper imports
  and `repository_index` discovery/storage helper imports remain available as
  compatibility re-exports; internal callers import the owning modules directly.
- File filtering, chunk boundaries, chunk-ID/version formats, JSON configuration,
  API contracts, model precedence, and persistent database schemas are unchanged
  by the readability refactor. Existing FAK vectors do not need rebuilding.
- Repository and single-file indexing share one incremental vector publisher:
  refresh reusable metadata, embed/write missing documents, then remove stale IDs
  in bounded batches. Evidence text remains separate from embedding-only context.
- Reuse lookups filter by repository ID in Chroma rather than scanning unrelated
  repositories. This indexing-time lookup is not a runtime startup audit.
- Keyword updates use streaming row conversion and a shared temporary-database
  transaction. A failed write leaves the previously published database intact.
  Chroma clients are closed on both successful and failed publication paths.
- Startup configuration resolution and process-ownership preflight precede service restarts.
  Each service has a separate launch/readiness function. Process trees are indexed
  once per snapshot for descendant and protected-process checks, while identity
  refresh and unverified-listener refusal remain mandatory before explicit kills.

Each question triggers application-managed retrieval, then a direct Ollama chat
request. Evidence stays internal: chat responses do not append source lists or
source metadata. The model has no execution tools or infrastructure access.
Open WebUI is a client of `vectorizer.server:app`, not a second RAG pipeline.

The manual bootup command hides child consoles and restarts verified Open WebUI,
RAG API, and Ollama trees. It stops all three in reverse dependency order, then
launches fresh instances with current model settings. See [startup](bootup.md).

`tests/fixtures/architecture.md` is historical infrastructure sample text used by
loader/embedding tests, not documentation of this application's architecture.
