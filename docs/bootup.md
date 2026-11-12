# Windows service CLI for GeorgeGPT

From the project root run `.\.venv\Scripts\pythonw.exe -m services.bootup` for no console window, or use `python.exe` instead for visible progress. Configuration lives in `config/bootup.json`; relative executable/data paths resolve from the project root, not the configuration directory. Windowless status and errors go to `bootup.log` in the configured logs directory.

## CLI commands

```powershell
# Start and verify the stack (bootup is an alias; no command still means start).
.\.venv\Scripts\python.exe -m services.bootup start
# Preview which process trees would be killed; changes nothing.
.\.venv\Scripts\python.exe -m services.bootup kill --dry-run
# Force-kill verified WebUI, RAG API, and Ollama trees without restarting them.
.\.venv\Scripts\python.exe -m services.bootup kill
# Target one service (stop is an alias for kill).
.\.venv\Scripts\python.exe -m services.bootup kill --service webui
.\.venv\Scripts\python.exe -m services.bootup kill --service api
.\.venv\Scripts\python.exe -m services.bootup stop --service ollama
# Use a different configuration file.
.\.venv\Scripts\python.exe -m services.bootup --config config/bootup.json start
```

`kill`/`stop` default to `--service all`, which means **Open WebUI, the RAG API, and Ollama**, never arbitrary PIDs or all Python processes. These commands force-terminate their verified process trees (`taskkill /T /F`); active chat/generation requests are interrupted. They do not launch services, require an API key, read the WebUI database, or delete data files. Exit status is 0 on success (including already-stopped services), 1 on an operational failure, and 2 on invalid CLI arguments.

All selected services are inspected before the first kill. An unrelated listener causes refusal, and a tree containing bootup itself or a listening service not selected for shutdown is protected. Process identities are refreshed before stopping each service; new processes or changed identities require operator review/retry. Roots already terminated by another selected parent tree are not killed twice. `--service` and `--dry-run` apply only to `kill`/`stop`.

API ownership requires the configured repository Python executable and the exact command `python.exe -m uvicorn Interface.server:app ...`; matching a port or a command-line substring alone is insufficient. For transition only, the new bootup also recognizes verified instances using the former `vectorizer.server:app` target. Worker/redirector descendants are included. Verified API launchers are stopped even when unhealthy or not listening. A manually launched API using an unrecognized executable/command must be stopped manually rather than bypassing ownership checks. Other repository commands such as indexing, tests, editors, and OpenCode are not service targets. Update old bootup shortcuts to `services.bootup`; old Python import/CLI modules are not kept inside the RAG package.

## Model and context configuration

Edit `config/models.json` before starting services:

```json
{
  "chat_model": "qwen2.5-coder:7b",
  "embedding_model": "qwen3-embedding:0.6b",
  "context_window_tokens": 16384,
  "keep_alive_seconds": 1800
}
```

`config/bootup.json` points to this file with `"models_config": "config/models.json"`. That reference resolves from the project root, even with a custom bootup configuration file. The three model/context fields are required; `keep_alive_seconds` is optional and defaults to 1800 (30 minutes). Unknown fields are rejected. Model names must be nonempty Ollama tags without surrounding whitespace, the context window must be an integer of at least 512, and keep-alive must be a positive integer of seconds. Invalid or missing configuration aborts startup before any processes are stopped. Use full installed model tags (including their tag suffix). Pull new models with `ollama pull MODEL` before starting; bootup checks both selected models against Ollama's installed list. An optional `ollama.required_models` array in the bootup file can require additional installed models, but does not select them.

For **bootup**, the JSON file is authoritative over inherited model environment variables. It launches Ollama with `OLLAMA_CONTEXT_LENGTH`, `OLLAMA_KEEP_ALIVE`, `OLLAMA_MAX_LOADED_MODELS=2`, and `OLLAMA_NUM_PARALLEL=1`. It launches the API with `RAG_MODEL_CONFIG`, `OLLAMA_CHAT_MODEL`, `OLLAMA_EMBEDDING_MODEL`, `OLLAMA_CONTEXT_WINDOW_TOKENS`, and `OLLAMA_KEEP_ALIVE_SECONDS` set explicitly. The API sends the configured context window as `options.num_ctx` on every chat request, and chat/embedding requests refresh the configured `keep_alive` retention period. Open WebUI still selects `GeorgeGPT`; it does not choose the underlying Ollama model.

For a **standalone API launch**, precedence is explicit `ServerConfig` injection (when used in Python), otherwise environment overrides > the selected JSON file. `RAG_MODEL_CONFIG` selects the JSON file (default: `config/models.json` at the project root); explicit relative environment paths resolve from the current working directory. Existing `OLLAMA_CHAT_MODEL` / `OLLAMA_EMBEDDING_MODEL` overrides remain supported, along with `OLLAMA_CONTEXT_WINDOW_TOKENS` and `OLLAMA_KEEP_ALIVE_SECONDS`. This file governs the API/bootup workflow; standalone CLI model-selection flags are unchanged.

The API exposes its effective chat model, embedding model, context window, and Ollama URL through authenticated `GET /v1/config`, without exposing keys or filesystem paths. Bootup always kills the verified existing API and launches a fresh instance with the selected settings, regardless of the old instance's health or configuration. It checks this endpoint when the new API becomes ready. A mismatch in the new instance causes startup to fail. No manual API stop is needed for routine code/model/context changes.

The context window covers instructions, evidence, conversation history, and generated output—not just retrieved text. Larger settings increase memory use; use values supported by the selected model and available hardware. Existing character-based input/evidence safety limits and the 4096-token generation cap remain unchanged, so a smaller window can still cause Ollama to truncate inputs. There is no tokenizer-based prompt-fitting guarantee or automatic model download/fallback.

Changing only the chat model or context window does **not** require reindexing. Changing the embedding model **does**: rebuild into a separate collection using `vectorizer.vectorize --embedding-model MODEL --collection NEW_COLLECTION`, verify with the matching model/collection, and set `RAG_COLLECTION` before bootup. Bootup inherits the collection setting; it does not rebuild or scan the index automatically. Existing databases, environments, and logs remain intact.

### First-message latency

Before starting the API/WebUI, bootup submits a short, non-thinking chat request using George's system prompt and the configured context window, then one real embedding request. Loading the larger chat model first avoids Ollama's conservative initial memory estimates evicting the smaller embedding model. This moves weight loading, first-prompt evaluation, and initial inference setup into startup. It verifies both models remain resident through `/api/ps`; a failed warmup or eviction prevents a successful ready report. No repository scan, user messages, or credentials are used in warmup.

Both models are retained for 30 minutes after their last respective request by default. Increasing this keeps memory occupied longer; it is not a guarantee against eviction under memory pressure. After expiration, cold-start costs can recur. New questions still require retrieval and prompt evaluation, so warmup does not eliminate all time-to-first-token latency.

The API runs one active completion to match Ollama's generation slot and accepts up to four waiting requests in a bounded FIFO queue. Ordinary overlapping user chats and WebUI title/tag/follow-up tasks wait rather than immediately receiving a busy error. Waiting is asynchronous, does not occupy worker threads, and is limited to 90 seconds. Only a full queue returns HTTP 429; an expired wait returns HTTP 503, both with `Retry-After: 1`. Cancelled waiters return their queue positions, and streamed/non-streamed requests release their generation lease exactly once. Disconnected queued clients are checked before retrieval/generation begins. This does not disable WebUI task settings, remove the time needed for earlier requests, or control direct Ollama clients.

Chat generation explicitly sends `think: false`, as does warmup. This avoids Qwen's default hidden-thinking phase consuming the 4096-token generation budget before any visible answer is returned. Empty final answers now produce an upstream error instead of a successful blank response. Non-thinking mode does not remove the system prompt's technical-completeness requirements.

## Goal

Start Ollama, the repository RAG API, and Open WebUI in the right order after sign-in. Keep the API and WebUI bound to loopback, preserve their shared API key, and expose no tools or Proxmox access.

## Startup sequence

1. **Load configuration and secrets**
   - Resolve project paths from the script location, not the current directory.
   - Read the RAG API key from the saved Open WebUI connection in its database. If `RAG_API_KEY` is set, require it to match the saved key. Do not put secrets in the script, repository, command-line arguments, or logs.
   - Reuse a persistent Open WebUI secret key so restarts do not invalidate login sessions.
2. **Check existing listeners/processes**
   - Check ports `11434`, `8001`, and `8080` and inspect each listener's executable and command line.
   - Verify all selected service processes before stopping any of them, including repository-specific API ownership.
   - If a port belongs to an unexpected process, stop and report a conflict; never kill an unrelated process just because it owns a configured port.
   - Stop verified process trees in reverse dependency order: Open WebUI, RAG API, then Ollama. Healthy, unhealthy, and no-listener API instances are all restarted.
3. **Start Ollama**
   - Launch the configured `ollama.exe serve` after all verified old services have stopped.
   - Wait for Ollama and verify the selected chat/embedding models are available.
   - Warm both inference paths and verify their joint residency before launching the API/WebUI.
4. **Start the RAG API**
   - Set `RAG_API_KEY` and `OLLAMA_BASE_URL` in the child process environment.
   - Launch `.venv\Scripts\python.exe -m uvicorn Interface.server:app --host 127.0.0.1 --port 8001`.
   - Wait for an authenticated `/v1/models` request to return `GeorgeGPT`, and check `/v1/config` matches the selected settings.
5. **Start Open WebUI**
   - Launch the installed Open WebUI Python CLI on `127.0.0.1:8080` with the same API base URL and key.
   - Set `ENABLE_PLUGINS=false`, `ENABLE_TOOLS=false`, `ENABLE_FUNCTIONS=false`, and `ENABLE_TOOL_SERVERS=false`.
   - Wait for the WebUI HTTP endpoint to respond successfully.
6. **Report status**
   - Print service names, PIDs, health results, and log locations only. Never print secret values.
   - Use distinct append-only logs under `%LOCALAPPDATA%\Vectorizer\logs` or the existing `runtime_logs` directory.

## Companion stop/restart behavior

- Normal bootup is force mode: stop verified WebUI, API, and Ollama trees, then relaunch Ollama, API, and WebUI in dependency order. There is no healthy-API reuse path.
- `kill`/`stop` stops all three services by default. `--service api`, `webui`, or `ollama` selects only one service.
- Unrelated listeners, PID identity changes, and the bootup process itself remain protected. No broad `taskkill /IM python.exe` or machine-wide process termination is used.
- Keep Open WebUI's data/database directory intact. Do not delete or recreate its database during startup or recovery.

## Installation and verification

- Run the bootup script manually when the stack is needed; do not create a Scheduled Task or Windows service.
- Test first with a manual run, then test a repeated force-restart for port-conflict handling and secret-preserving WebUI restarts.
- Verify WebUI can list `GeorgeGPT`, submit a chat, and receive answers without source lists/metadata. Confirm tool/function declarations do not execute and tool-call messages are still rejected.
- Document recovery steps for Ollama/model failures, invalid API key, occupied ports, and missing index.
