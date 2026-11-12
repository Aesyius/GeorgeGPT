# Repository RAG chatbot (Ollama + Chroma + Open WebUI)

This project indexes the FAK infrastructure repository and answers questions using retrieved code and documentation. It does **not** have agent tools: the model cannot inspect the filesystem, connect to Proxmox, run commands, or change infrastructure. Retrieval is performed once by the application before each answer. Users can paste command output/logs for analysis.

The API is an OpenAI-compatible facade for Open WebUI, but generation goes directly to Ollama. Open WebUI Knowledge/RAG is not involved.

## Setup (Windows PowerShell)

Run commands from the project root. Python entry points now use `-m vectorizer.<command>`; the former root-level scripts have been removed.

## Layout

- `vectorizer/` — application modules and CLI entry points.
- Indexing internals are partitioned into source filtering (`source_files`), structural representation (`chunking`), vector publication (`vector_store`), and atomic keyword storage (`keyword_index`).
- Service management separates CLI/orchestration (`bootup`), resolved settings (`service_config`), Windows process operations (`windows_processes`), and readiness/credential checks (`service_runtime`). See [module responsibilities](docs/architecture.md#modules).
- `config/bootup.json` — manual service startup configuration (paths are relative to the project root).
- `docs/` — [architecture](docs/architecture.md) and [service startup](docs/bootup.md).
- `tests/` and `tests/fixtures/` — tests and sample source documents.
- `sample_questions/` — evaluation questions and locally generated answers.
- `chroma_db/`, `runtime_logs/`, `.venv/`, `.venv-webui/` — local state, excluded from Git; preserved during the refactor.

## Installation

Use 64-bit Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull qwen3-embedding:0.6b
ollama pull qwen2.5-coder:7b
```

The configured defaults match the models currently installed in Ollama: `qwen3-embedding:0.6b` for embeddings and `qwen2.5-coder:7b` for chat. The second pull command is only needed if that model is not already present. Verify with `ollama list`. Ollama must be reachable at `http://localhost:11434` (or set `OLLAMA_BASE_URL`). Index the infrastructure repository. The configured default is the operator-provided FAK path; pass a path explicitly if it moves:

```powershell
.\.venv\Scripts\python.exe -m vectorizer.vectorize "C:\Users\Cyber.DESKTOP-MAG5LP5.000\Desktop\Project\FAK"
```

Index only reads FAK. It ignores real `.env` files, common credentials/private-key filenames and extensions, binary/large files, virtual environments, generated directories, and caches. Text examples such as `*.env.example` can be indexed. Review the printed file/chunk totals and exclusions for your repository before relying on the corpus. The CLI reports progress for scanning/chunking, embedding/writing vectors, stale cleanup, and the keyword index (a terminal bar in an interactive terminal, periodic counters otherwise). Unchanged chunks reuse stored vectors. Chroma operations are paginated to avoid loading a full collection into one query. Chroma vectors and a SQLite FTS5 BM25 index are stored under `chroma_db` beside this project by default. A repository reindex publishes new chunks before removing the prior snapshot; a new BM25 snapshot is atomically replaced after the vector update.

The indexer also accepts one UTF-8 text file for the original lightweight workflow. Shared options:

```powershell
.\.venv\Scripts\python.exe -m vectorizer.vectorize --help
```

### Contextual embeddings and structural chunks

Both repository and single-file indexing embed a source-derived prefix containing the relative file path, Markdown heading ancestry, and Python symbol/signature where available. The prefix is **not** inserted into stored evidence or the BM25 text. Chat prompts and retrieval ranking are unchanged.

Python uses AST boundaries, including decorators and indented methods, with a conservative fallback for invalid syntax. Small classes/functions stay together; oversized containers are subdivided with enclosing context retained in embedding inputs. Markdown headings inside fenced blocks are ignored, and fitting fenced blocks are kept intact. Source offsets and line ranges refer to the original text, not the prefix.

`--chunk-size` and `--overlap` remain character-based. `--max-embedding-bytes` (default 4096) additionally caps the **entire enriched embedding input** in UTF-8 bytes. This is a conservative sizing proxy, not an exact model-token count or a model-independent context-window guarantee; tune it for other embedding models. No tokenizer download is required. Oversized blocks/lines are split rather than silently truncated; excessively large context prefixes fail explicitly.

Chunk identities include the chunking version, embedding-input version, size, overlap, and byte cap. Old raw-text vectors will not be reused after reindexing. **Existing indexes are not automatically changed.** First compare against a separate experimental collection:

```powershell
# Capture the existing retrieval baseline before reindexing.
.\.venv\Scripts\python.exe -m vectorizer.evaluate_retrieval --collection repo_documents --output runtime_logs/retrieval-baseline.json
# Build new vectors separately, keeping the production collection intact.
.\.venv\Scripts\python.exe -m vectorizer.vectorize "C:\Users\Cyber.DESKTOP-MAG5LP5.000\Desktop\Project\FAK" --collection repo_documents_context_v1
.\.venv\Scripts\python.exe -m vectorizer.verify_index "C:\Users\Cyber.DESKTOP-MAG5LP5.000\Desktop\Project\FAK" --collection repo_documents_context_v1
.\.venv\Scripts\python.exe -m vectorizer.evaluate_retrieval --collection repo_documents_context_v1 --output runtime_logs/retrieval-context-v1.json
```

The retrieval evaluator makes no chat-model calls, refuses existing output files, and records passages for manual completeness review. Compare expected-path recall@8 and first-relevant-result MRR with the same questions, source revision, embedding model, and retrieval settings. These are file-relevance proxies, not measured answer accuracy. Evaluate representative questions before selecting the experimental collection with `RAG_COLLECTION`. Pass matching custom chunking/byte-cap options to the verifier.

After indexing, run the read-only integrity audit to compare the current source chunks against Chroma IDs, stored vector dimensions/model metadata, and BM25 rows:

```powershell
.\.venv\Scripts\python.exe -m vectorizer.verify_index "C:\Users\Cyber.DESKTOP-MAG5LP5.000\Desktop\Project\FAK"
```

This performs a paginated full scan of stored vectors and can take time for a large repository. Runtime/API startup deliberately does not scan chunk metadata; use this verifier when checking chunking and embedding consistency. `PASS` confirms corpus/vector/index consistency; it does not measure whether semantic retrieval ranks the best passages, so also test representative questions.

## Ask questions

The CLI uses Ollama directly and answers from retrieved repository evidence:

```powershell
.\.venv\Scripts\python.exe -m vectorizer.ask "Why would diagnose-vlr-online fail after a rebuild?"
```

It combines exact-term FTS5/BM25 search with semantic Chroma retrieval. The answer distinguishes repository behavior, historical documentation, and user-provided evidence; indexed snapshots do not establish live infrastructure state. Source paths and citation lists are not included in chat responses.

## Connect Open WebUI

Set a long random API key. The installed Qwen chat and embedding models are defaults; environment variables can override them:

```powershell
$env:RAG_API_KEY = "replace-with-a-long-random-secret"
# Optional overrides:
# $env:RAG_DB_PATH = "C:\path\to\chroma_db"
# $env:RAG_COLLECTION = "repo_documents"
# $env:OLLAMA_CHAT_MODEL = "qwen2.5-coder:7b"
# $env:OLLAMA_EMBEDDING_MODEL = "qwen3-embedding:0.6b"
# $env:OLLAMA_BASE_URL = "http://localhost:11434"
.\.venv\Scripts\python.exe -m uvicorn vectorizer.server:app --host 127.0.0.1 --port 8001
```

Configure an OpenAI-compatible connection in Open WebUI:

- Base URL: `http://host.docker.internal:8001/v1` when WebUI is in Docker Desktop and the API runs on Windows; otherwise use the API host reachable from WebUI.
- API key: the same `RAG_API_KEY`.
- Model: `GeorgeGPT`.
- Disable Open WebUI Knowledge for this model to avoid a second unrelated RAG pipeline.

The API binds to loopback in this example, authenticates bearer tokens, and limits request size/concurrency. For Open WebUI compatibility it ignores tool/function declarations and standard `max_tokens` hints; it never forwards or executes tools. Tool-call messages remain rejected. If Open WebUI is in Docker, loopback binding may not be reachable from its container. Only if needed, bind to `0.0.0.0` and restrict inbound access to the Docker/host private network with firewall rules; do not expose this service publicly without TLS and network-level controls. Do not put Proxmox credentials in the chatbot environment.

To manually launch and verify the stack, edit `config/bootup.json` if paths/models change, then run:

```powershell
.\.venv\Scripts\pythonw.exe -m vectorizer.bootup
```

For the service CLI, use `python.exe -m vectorizer.bootup start` to force-restart all three stack services, `kill --dry-run` to preview targets, or `kill --service webui` / `kill --service api` / `kill --service ollama` to stop one verified service. `kill` with no service stops WebUI, the RAG API, and Ollama, including their child processes. Unrelated processes and bootup itself remain protected. See [service CLI commands](docs/bootup.md#cli-commands).

Select the API's underlying models and context window in **`config/models.json`** (`chat_model`, `embedding_model`, `context_window_tokens`; default context: 16384 tokens). `config/bootup.json` references it through `models_config`. Bootup validates this file and explicitly passes its settings to the child processes, overriding inherited model settings. Both chat response paths send `num_ctx` to Ollama. The API is always restarted to apply current code and settings; its authenticated `/v1/config` is checked after startup. Changing the embedding model requires a matching reindex. See [configuration and precedence](docs/bootup.md#model-and-context-configuration).

Bootup now warms chat and embedding inference before declaring readiness. Optional `keep_alive_seconds` in `config/models.json` defaults to **1800 seconds (30 minutes)** and is refreshed by chat/embedding requests. The API runs one active completion with up to four FIFO waiting positions (90-second wait limit), so normal overlapping WebUI tasks no longer trigger an immediate busy error. Queue saturation returns HTTP 429; an expired wait returns HTTP 503. Normal prompt evaluation, earlier queued requests, and answer generation still take time. See [first-message latency](docs/bootup.md#first-message-latency).

Use `pythonw.exe` to launch without opening a terminal window. The script force-restarts verified Open WebUI, RAG API, and Ollama process trees, including unhealthy instances. All selected services are identified before the first kill; unrelated listeners cause refusal. Active chats and generation requests are interrupted by a restart. It reads the API key from the configured Open WebUI database and checks any `RAG_API_KEY` environment override against it. Logs, including bootup output and errors, are written under `%LOCALAPPDATA%\Vectorizer\logs`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m compileall -q vectorizer tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

The Ollama embedding integration test uses `qwen3-embedding:0.6b` by default (or `OLLAMA_EMBEDDING_MODEL`). To run the API server, install the full `requirements.txt` (including FastAPI and Uvicorn).

Run the sample question evaluation with `python -m vectorizer.evaluate_questions`. See [service startup](docs/bootup.md) for the manual restart contract. The architecture document under `tests/fixtures/` is sample infrastructure content for chunking tests, not this chatbot's architecture.
