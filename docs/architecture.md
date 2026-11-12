# Repository chatbot architecture

The application is split into Python packages by responsibility, not a tool-using agent. `Interface` has a capital I and must be imported with that spelling on every platform.

## Modules

| Area | Modules |
| --- | --- |
| Indexing orchestration | `vectorizer.vectorize` (CLI/single file), `vectorizer.repository_index` (repository preparation/publication) |
| Safe source discovery | `vectorizer.source_files` (allowlists, exclusions, size limits, deterministic discovery) |
| Text representation | `vectorizer.chunking` (structure, offsets, embedding context), `vectorizer.document_loader` (legacy document loader) |
| Persistence | `vectorizer.vector_store` (pagination/reuse/batches), `vectorizer.keyword_index` (atomic SQLite FTS5 publication) |
| Embedding transport | `vectorizer.embeddings` (Ollama client and retry policy) |
| Integrity audit | `vectorizer.verify_index` |
| Retrieval | `vectorizer.retrieval` (Chroma semantic search + SQLite FTS5/BM25) |
| Generation | `Interface.chat` (system prompt, bounded evidence, direct Ollama chat requests) |
| Interfaces | `Interface.ask` (CLI), `Interface.server` (authenticated OpenAI-compatible API) |
| Completion admission | `Interface.completion_queue` (one active generation, bounded FIFO waits, cancellation-safe leases) |
| Evaluation | `evaluation.evaluate_questions`, `evaluation.evaluate_retrieval`, using `sample_questions/` |
| Service lifecycle | `services.bootup` (CLI, verified stop plans, per-service startup orchestration) |
| Service settings | `services.service_config` (resolved startup settings), `common.model_config` (validated model/context configuration) |
| Windows process operations | `services.windows_processes` (inspection, reusable process-tree index, hidden launch, termination) |
| Service readiness/secrets | `services.service_runtime` (HTTP checks, bounded waits, read-only WebUI credential resolution) |
| Model warmup | `services.model_warmup` (chat/embedding inference readiness and residency) |
| Shared utilities | `common.paths`, `common.model_config`, `common.progress` |

Indexing reads safe text files and persists vectors and keyword rows in the
existing project-root `chroma_db/`. Moving Python modules does not change that
location. Explicit relative database paths still resolve from the working
directory. Only the explicit verifier performs full integrity audits; runtime
startup does not scan every chunk's metadata.

## Partitioning and compatibility

- RAG CLI entry points stay under `vectorizer`. Chat entry points move to
  `Interface`, startup to `services`, and evaluation to `evaluation`. Old non-RAG
  module forwarding stubs are not left under `vectorizer`; update imports and
  shortcuts using the README migration table. Existing RAG storage/discovery
  helper re-exports within `vectorizer.repository_index` remain available.
- RAG and common modules do not import chat, lifecycle, or evaluation code.
  Chat/evaluation consume RAG; service warmup consumes the chat system prompt.
  New package initializers have no startup, network, or model-loading actions.
- File filtering, chunk boundaries, chunk-ID/version formats, JSON configuration,
  API contracts, model precedence, and persistent database schemas are unchanged
  by the directory restructure. Existing FAK vectors do not need rebuilding.
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
Open WebUI is a client of `Interface.server:app`, not a second RAG pipeline.

The manual bootup command hides child consoles and restarts verified Open WebUI,
RAG API, and Ollama trees. It stops all three in reverse dependency order, then
launches fresh instances with current model settings. See [startup](bootup.md).
Verified old `vectorizer.server:app` processes can still be identified during
transition; fresh launches always use `Interface.server:app`.

`tests/fixtures/architecture.md` is historical infrastructure sample text used by
loader/embedding tests, not documentation of this application's architecture.
