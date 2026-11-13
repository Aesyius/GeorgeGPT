# GeorgeGPT — Repository RAG Chatbot

## How it works

GeorgeGPT is a Portuguese RAG enabled LLM that answers questions using indexed repository code and documentation. The application retrieves relevant passages using Chroma semantic search and SQLite FTS5/BM25 keyword search, then sends that evidence to an Ollama chat model. Open WebUI connects through the authenticated `GeorgeGPT` API; its own Knowledge/RAG is not used.

The chatbot cannot execute commands, access additional files, or inspect live infrastructure. Its answers describe indexed evidence, not verified live system state.

## Initialize everything — Windows PowerShell

Install **64-bit Python 3.12** and **Ollama**, ensure Ollama is running, and open PowerShell in this project folder. Replace `C:\Repos\FAK` with your repository:

```powershell
.\setup.ps1 -RepositoryPath "C:\Repos\FAK"
# Preview without installing, downloading, indexing, or starting anything:
.\setup.ps1 -RepositoryPath "C:\Repos\FAK" -WhatIf
```

The setup script:
- Creates or reuses `.venv` and `.venv-webui`, then installs dependencies.
- Reads `config/models.json`, reuses installed models, and downloads only missing models.
- Indexes your repository, verifies the index, and launches the API and WebUI.
- Saves the selected repository as `repository_path` in `config/bootup.json` for future launches.
- Opens the browser for administrator registration and connection setup; existing saved API keys are reused.

Stop an already-running stack before setup. In WebUI, create your administrator account and save the OpenAI-compatible connection at **`http://127.0.0.1:8001/v1`**. The API key is supplied to WebUI without being printed; if needed, type **C** at the setup prompt to copy it, then clear your clipboard after saving. Press **Enter** to verify the connection. Keep WebUI Knowledge disabled and select **GeorgeGPT**.

Options: `-NoBrowser` skips opening the browser; `-ConfigPath` selects another bootup config. Setup leaves launched services running if interrupted; use the stop command below. If PowerShell blocks a trusted checkout:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -RepositoryPath "C:\Repos\FAK"
```

This bypass applies only to that PowerShell process; it does not change the machine policy.

<details>
<summary>Manual setup alternative</summary>

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
py -3.12 -m venv .venv-webui
.\.venv-webui\Scripts\python.exe -m pip install open-webui
$models = Get-Content .\config\models.json -Raw | ConvertFrom-Json
ollama pull $models.chat_model
ollama pull $models.embedding_model
.\.venv\Scripts\python.exe -m vectorizer.vectorize "C:\Repos\FAK"
```

**First-time connection setup:** start the API in one terminal using your own long, random API key:

```powershell
$env:RAG_API_KEY = "replace-with-your-own-long-random-secret"
.\.venv\Scripts\python.exe -m uvicorn Interface.server:app --host 127.0.0.1 --port 8001
```

In another terminal, start WebUI:

```powershell
.\.venv-webui\Scripts\python.exe -c "from open_webui import app; app()" serve --host 127.0.0.1 --port 8080
```

Open **http://127.0.0.1:8080**, create the administrator account, and save an OpenAI-compatible connection with URL **`http://127.0.0.1:8001/v1`** and the same API key. Keep WebUI Knowledge disabled.

</details>

### Subsequent launches

The repository (`repository_path`), executable paths, WebUI database, and ports are configured in `config/bootup.json`; models, context size, and retention are in `config/models.json`.

`repository_path` can be absolute or relative to this project root. Vectorize and
verification use it when their positional repository argument is omitted:

```powershell
.\.venv\Scripts\python.exe -m vectorizer.vectorize
.\.venv\Scripts\python.exe -m vectorizer.verify_index
```

Explicit CLI paths and `RAG_REPOSITORY_PATH` override the configured default.
For standalone commands, `RAG_BOOTUP_CONFIG` selects an alternate bootup JSON file;
`services.bootup --config` passes its selected repository to the API and preparation process.

```powershell
.\.venv\Scripts\python.exe -m services.bootup start
# Stop all three services:
.\.venv\Scripts\python.exe -m services.bootup kill
```

Bootup reads the saved WebUI connection key, restarts verified service trees, warms both models, and checks readiness. Active chats are interrupted by a restart. Select **GeorgeGPT** in WebUI. Logs default to `%LOCALAPPDATA%\Vectorizer\logs`.

### Change the embedding model

Change `embedding_model` in `config/models.json`, ensure that exact Ollama tag is installed, and restart:

```powershell
.\.venv\Scripts\python.exe -m services.bootup start
```

Vectorize, the API, verification, querying, and evaluation share a model-index catalog.
API startup reuses a completed matching vectorize index without embedding it again.
When switching models, vectorize/API select an isolated collection and BM25 index.
If missing, API startup re-embeds the repository before accepting queries.
The original index and other model indexes are preserved;
switching back reuses a completed matching index. Changing only the chat model
does not rebuild embeddings. Configuration changes take effect on restart, not live.

The first build may take longer than a normal startup. Terminal launches show a
live loading bar; progress/errors are also written to
`%LOCALAPPDATA%\Vectorizer\logs\index-preparation.log`. Windowless launches keep
logging without requiring a terminal. Failed builds stop startup
instead of serving mismatched vectors. A retry resumes compatible completed batches.
No model downloads happen automatically.

New repository indexing runs record source paths and chunk settings in a small
collection manifest. Bootup/API use `repository_path` from the selected bootup
configuration, including when preparing an older index without a source manifest.
For a temporary source override, explicitly set its path before launching:

```powershell
$env:RAG_REPOSITORY_PATH = "C:\Repos\FAK"
.\.venv\Scripts\python.exe -m services.bootup start
```

`RAG_DB_PATH` chooses the database directory; `RAG_COLLECTION` chooses the **base**
collection/source manifest, not the effective model-specific API collection.
Standalone Uvicorn startup performs the same preparation. All commands and low-level
embedding/retrieval helpers read the configured model when no explicit model is supplied.
`--embedding-model` overrides the default; query/evaluation chat flags override `chat_model`.
`RAG_MODEL_CONFIG` selects an alternate model config file, and
`OLLAMA_EMBEDDING_MODEL` overrides the config default, just as for the API.
Missing or invalid model configuration fails clearly rather than silently selecting 0.6B.
`RAG_DB_PATH` and `RAG_COLLECTION` are shared CLI defaults; explicit flags still win.

## How embedding works

1. **Filter and chunk:** eligible UTF-8 text is split around Python definitions and Markdown sections. Oversized sections are split with overlap. Common secrets, binaries, caches, and generated files are excluded; review filters before indexing sensitive repositories.
2. **Embed:** Ollama converts each chunk into a numeric vector. Relative file paths, headings, and symbols add context to the embedding input, while stored evidence retains the source text and line ranges.
3. **Store:** Chroma saves vectors and SQLite saves keyword-search rows under project-root `chroma_db/`. Reindexing reuses compatible unchanged vectors, embeds changes, and removes stale chunks. This does not train the chat model.

## Embed a repository

Replace the example path with your repository. The vectorize CLI checks Ollama
first and starts `ollama serve` automatically if the configured local HTTP server
is stopped. Ollama must be installed and the embedding model already pulled.
Existing servers are left alone; remote servers must be started separately.
An automatically started server remains running after indexing. Startup output is
saved to `%LOCALAPPDATA%\Vectorizer\logs\ollama-vectorize.log` on Windows.

```powershell
.\.venv\Scripts\python.exe -m vectorizer.vectorize "C:\Repos\FAK"
.\.venv\Scripts\python.exe -m vectorizer.verify_index "C:\Repos\FAK"
```

Rerun indexing after source changes. Verification audits index consistency, not answer accuracy. Available options:

```powershell
.\.venv\Scripts\python.exe -m vectorizer.vectorize --help
# Example: a separate collection with explicit chunk settings
.\.venv\Scripts\python.exe -m vectorizer.vectorize "C:\Repos\FAK" --collection my_repo --chunk-size 1400 --overlap 180
```

Keep the database path and base collection consistent across commands. Set `RAG_COLLECTION` before bootup when using a custom base collection. Change `embedding_model` in `config/models.json` and rerun vectorize to build or refresh that model's index; it selects a separate collection automatically if the base belongs to another model. Querying and verification resolve the same model-specific collection. Other model indexes are preserved. Reindex after source changes; unchanged source snapshots remain reusable when switching models. Single-file indexing remains explicit: choose a separate `--collection` when changing its embedding model.

**Code layout:** `vectorizer/` = RAG; `Interface/` = chat/API; `services/` = startup; `common/` = shared settings; `evaluation/` = evaluation tools. `Interface` is case-sensitive on Linux.

Index manifests and model-specific collection lookup live in
`vectorizer/index_catalog.py`; preparation/build orchestration lives in
`vectorizer/managed_index.py`. Retrieval and verification resolve existing indexes
without importing or triggering the build orchestrator.
