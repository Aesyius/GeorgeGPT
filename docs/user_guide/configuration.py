r"""
# Configuration and overrides

[Home](../user_guide.html) · [Daily use](daily_use.html) · [CLI reference](cli.html)

## Model configuration

`config/models.json` controls managed startup/API models and runtime allocation.

| Key | Shipped value | Meaning |
| --- | --- | --- |
| `chat_model` | `qwen3.5:9b` | Exact Ollama model tag for answers. |
| `embedding_model` | `qwen3-embedding:4B` | Exact Ollama tag for indexing and query vectors. |
| `context_window_tokens` | `16384` | Context allocation; integer at least 512. |
| `keep_alive_seconds` | `1800` | Positive integer retention in Ollama; optional, defaults to 1800. |

Model names must be nonempty with no surrounding whitespace. Required keys
are chat model, embedding model, and context window. Unknown keys are rejected.
Pull missing models before restarting; normal bootup does not download them.
Settings take effect on restart, not midway through an active chat.

## Bootup configuration

`config/bootup.json` describes this computer. Do not copy another user's
repository path or environment directories without reviewing them.

| Key | Default/shipped setting | Purpose |
| --- | --- | --- |
| `repository_path` | Machine-specific after setup | Repository directory; setup persists your choice. |
| `models_config` | `config/models.json` | Model JSON path. |
| `startup_timeout_seconds` | `240` | Positive timeout for startup checks; index preflight is separate from HTTP readiness timeout. |
| `poll_interval_seconds` | `3` | Positive readiness polling interval. |
| `api_key_env_var` | `RAG_API_KEY` | Environment variable name used for managed API credentials. |
| `logs_directory` | `%LOCALAPPDATA%/Vectorizer/logs` | Persistent service logs. |
| `ollama.executable` | `%LOCALAPPDATA%/Programs/Ollama/ollama.exe` | Installed Ollama executable. |
| `ollama.host`, `ollama.port` | `127.0.0.1`, `11434` | Ollama loopback listener. |
| `rag_api.python` | `.venv/Scripts/python.exe` | API environment interpreter. |
| `rag_api.host`, `rag_api.port` | `127.0.0.1`, `8001` | API loopback listener. |
| `rag_api.model_id` | `GeorgeGPT` | WebUI model selection identifier. |
| `rag_api.openai_base_url` | `http://127.0.0.1:8001/v1` | Must match API host/port plus `/v1`; also used to locate the saved connection. |
| `rag_api.open_webui_database` | `.venv-webui/Lib/site-packages/open_webui/data/webui.db` | Existing local SQLite storage for WebUI and saved connection. |
| `open_webui.python` | `.venv-webui/Scripts/python.exe` | Separate WebUI environment interpreter. |
| `open_webui.host`, `open_webui.port` | `127.0.0.1`, `8080` | WebUI loopback listener. |
| `open_webui.runtime_directory` | `%LOCALAPPDATA%/Vectorizer/open-webui-runtime` | WebUI launch working directory. |

Hosts must be `localhost` or `127.0.0.1`, ports must be distinct integers from
1–65535. Never use `0.0.0.0`, public IPs, port forwarding, or firewall rules.
Changing ports also requires updating your saved WebUI connection when relevant.
The API itself exposes the fixed model ID `GeorgeGPT`; leave `model_id` unchanged.

Setup only manages the project `.venv` and `.venv-webui`; custom configured
interpreter paths require manual environment management.

## Port conflicts and changing the default ports

A port conflict means another process is already listening on a port GeorgeGPT
needs. The defaults are **11434 (Ollama)**, **8001 (API)**, and **8080 (WebUI)**.
Setup may report a busy port; bootup refuses to stop an unverified port owner.
Do not kill an unfamiliar process or open firewall rules to work around this.

From project-root PowerShell, inspect listeners:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 11434,8001,8080 -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

Either stop the conflicting application through its normal controls or choose
unused ports in `config/bootup.json`. Example alternatives:

| Service | Change this key | Example | Also update |
| --- | --- | --- | --- |
| API | `rag_api.port` | `8002` | `rag_api.openai_base_url` to `http://127.0.0.1:8002/v1`, and the saved WebUI connection URL. |
| WebUI | `open_webui.port` | `8081` | Open `http://127.0.0.1:8081` in your browser; update bookmarks. |
| Ollama | `ollama.port` | `11435` | Standalone commands need `--ollama-url http://127.0.0.1:11435` or matching `OLLAMA_BASE_URL`. |

The examples are not guaranteed free ports. Check your chosen ports with the
same command before using them. Keep all hosts on loopback and choose distinct
ports in the 1–65535 range.

### Existing installation

1. **If changing the API port:** while the old WebUI is still running, add and
   save the new OpenAI-compatible URL in **Admin Settings → Connections**, using
   the same API key. Keep the old connection until the new endpoint works.
   Normal bootup looks up its key by the exact configured URL; changing only
   the JSON URL can otherwise prevent startup.
2. Stop the stack **before editing its current ports**. This interrupts chats
   and other clients of its Ollama instance:

```powershell
.\.venv\Scripts\python.exe -m services.bootup stop
```

3. Edit the keys above in `config/bootup.json`, then start with the new settings:

```powershell
.\.venv\Scripts\python.exe -m services.bootup start
```

4. Open the new WebUI address, select `GeorgeGPT`, and test a question.
   Remove an obsolete connection only after the new one works. Reindexing is
   not required just because a port changed.

For a **first-time installation**, edit ports before setup and use the matching
WebUI/API URLs when saving the connection. Ollama must already be reachable
at the configured host/port for the prerequisite check. Managed bootup supplies
its configured Ollama URL automatically; standalone commands do not read that
port from bootup JSON.

## Paths and precedence

- Configured repository and service paths are relative to the **project root**;
  absolute paths remain absolute. Environment variables in paths are expanded.
- Explicit CLI paths, including relative `--db-path`, are relative to the
  **current working directory**. Repository CLI overrides are temporary.
- Default `chroma_db` is always project-root, even from a different directory.
- `setup.ps1 -RepositoryPath` and a supplied `-ConfigPath` resolve from the
  shell's current directory. Setup then runs its work from the project root.
- `services.bootup --config PATH` explicitly selects bootup configuration;
  `RAG_BOOTUP_CONFIG` is not a replacement for that flag.
- Standalone model selection is explicit CLI flag, then model environment
  override, then the selected model JSON. Keep embedding selection consistent
  across indexing, querying, and verification.
- Managed bootup reads its selected model JSON and passes authoritative model
  settings to its child processes instead of inheriting standalone model
  overrides. `RAG_REPOSITORY_PATH` can still override the configured repository.

## Environment variables

PowerShell assignments affect the current process and subsequently launched
children; they are not saved to JSON. Remove a temporary override afterward:

```powershell
$env:RAG_REPOSITORY_PATH = "C:\Repos\MyProject"
.\.venv\Scripts\python.exe -m vectorizer.vectorize
Remove-Item Env:RAG_REPOSITORY_PATH
```

| Variable | Purpose / default |
| --- | --- |
| `RAG_REPOSITORY_PATH` | Temporary repository override before configured `repository_path`. |
| `RAG_DB_PATH` | Local BM25 directory and Chroma server namespace key; default project-root `chroma_db`. |
| `RAG_CHROMA_PORT` | Chroma loopback port on Windows and in WSL2 (default `8010`). |
| `RAG_CHROMA_WSL_DISTRO` | Initialized WSL2 distribution (default `Ubuntu`). |
| `RAG_COLLECTION` | Base collection/catalog; default `repo_documents`. |
| `RAG_BOOTUP_CONFIG` | Bootup JSON used by standalone repository resolution; default project `config/bootup.json`. |
| `RAG_MODEL_CONFIG` | Standalone model JSON; default project `config/models.json`. |
| `OLLAMA_BASE_URL` | Standalone Ollama URL; default `http://localhost:11434`. Managed bootup supplies its configured local URL. |
| `OLLAMA_CHAT_MODEL` | Standalone/direct-API chat-model override. |
| `OLLAMA_EMBEDDING_MODEL` | Standalone/direct-API embedding-model override. |
| `OLLAMA_CONTEXT_WINDOW_TOKENS` | Standalone/direct-API context override, integer at least 512. |
| `OLLAMA_KEEP_ALIVE_SECONDS` | Standalone/direct-API positive retention seconds. |
| `RAG_API_KEY` | Bearer secret for direct API launches; managed setup can use a supplied key of at least 32 characters or generate one. Do not commit it. |
| `OLLAMA_HOST` | Ollama CLI/server setting, distinct from the application's `OLLAMA_BASE_URL`. Setup temporarily sets this to the configured loopback URL when pulling models. |

Standalone query/evaluation flags do not include a context/retention flag.
The CLI answer helper uses its generation defaults; do not assume API/bootup
allocation changes identically affect every standalone answer command.

Do not inherit another application's `DATA_DIR`, `DATABASE_URL`,
`DATABASE_TYPE`, or database-server settings. Setup/startup reject settings
that conflict with the configured local WebUI SQLite database.
Investigate the environment rather than deleting or resetting existing data.

## Persistence and credentials

`chroma_db/` contains local SQLite keyword indexes, including
model-specific indexes/catalog metadata. WebUI accounts, saved connections,
and signing keys are separate. Preserve these directories and runtime logs.
Do not switch embedders inside a single-file collection; choose another name.

Managed startup reads the saved connection key. An inherited key that disagrees
with the saved key is a configuration error, not permission to overwrite it.
Keep API keys out of commands you share, source control, and diagnostics.
"""
