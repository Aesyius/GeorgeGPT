# GeorgeGPT — Repository RAG Chatbot

GeorgeGPT is a Portuguese chatbot that answers questions about your repository's code and documentation. It runs locally with **Ollama** for chat and embeddings, **Chroma** for semantic retrieval, **SQLite FTS5/BM25** for keyword retrieval, and **Open WebUI** as the chat interface.

### How it improves accuracy

- **RAG (retrieval-augmented generation):** gives the chat model relevant repository passages alongside your question, reducing reliance on general knowledge and helping limit unsupported answers.
- **Indexing:** splits files into focused chunks, preserving paths, headings, symbols, and line ranges. Embeddings turn those chunks into searchable vectors; indexing does not train the chat model.
- **Semantic search + BM25:** semantic search finds similar meaning, while BM25 matches exact terms such as function names, configuration keys, and error messages. Combining their rankings helps retrieve evidence for both conceptual and precise questions.
- **Stronger embedding models:** can improve code and technical retrieval, but require more memory and compute. Larger models do not guarantee better results—compare them on questions about your repository.

When you ask a question, the embedder converts the question into a vector, retrieves relevant stored chunks, and passes that evidence to the chat model. The repository is not re-embedded for every question. Accuracy depends on source quality and freshness, so reindex after changes.

Open WebUI connects through the authenticated **GeorgeGPT** API. Its own Knowledge/RAG feature is not used.

## HTML documentation

The pdoc documentation includes a first-time setup walkthrough, bootup guide,
complete project CLI/flag reference, configuration, troubleshooting, and Python APIs.
After installing the project dependencies, build the static HTML:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-docs.txt
.\.venv\Scripts\python.exe docs\build.py
.\.venv\Scripts\python.exe docs\check.py
Start-Process .\docs\html\index.html
```

Open `docs/html/index.html` directly in your browser; no documentation web server
is started or needed. Share the whole `docs/html` folder to preserve page links.
Documentation dependencies are separate from application requirements.

## User setup guide

### 1. Install prerequisites

On **Windows 10/11 (64-bit)** with **PowerShell 5.1 or newer**, install:

- **64-bit Python 3.12**. The `py` launcher is optional; setup also checks `PATH`.
  For a Python install not on `PATH`, pass `-PythonPath "C:\Path\To\python.exe"`.
- **Ollama**. Setup looks at the configured executable, then `PATH`, then the
  standard per-user install location.

If Ollama is missing, `setup.ps1` offers to run
`irm https://ollama.com/install.ps1 | iex` after a **Y/N** consent prompt.
Only **Y** runs the downloaded installer; **N** stops setup. Whether Ollama was
already installed or just installed, setup reuses a running local server or
asks **Start Ollama now? Press Y/N**.
**Y** starts the loopback-only server and waits for readiness; **N** stops setup
so you can start Ollama manually. `-WhatIf` never installs or starts Ollama.

Download and extract this repository to a short, writable path such as
`C:\Projects\GeorgeGPT`, or clone it:

```powershell
git clone https://github.com/Aesyius/GeorgeGPT.git C:\Projects\GeorgeGPT
cd C:\Projects\GeorgeGPT
```

Git is optional when downloading a ZIP. Do not copy another machine's `.venv`
or `.venv-webui`; setup creates environments for the new machine. Allow space
for Python dependencies, model weights, and the repository index.

**Local-only operation:** Ollama, the API, and WebUI bind to `127.0.0.1`
(or `localhost`). Their default ports—11434, 8001, and 8080—are internal to this
computer. Setup and bootup reject external/wildcard addresses; no firewall rules,
router port forwarding, or public listeners are added. Installing packages and
pulling models requires internet; WebUI may also perform its existing model/update
checks. Repository retrieval and chat inference run locally.

Open PowerShell in this project folder. If this stack is already running, stop it before repeating setup:

```powershell
.\.venv\Scripts\python.exe -m services.bootup kill
```

### 2. Review configuration

| File | Settings |
| --- | --- |
| `config/models.json` | Chat model, embedding model, context window, and model retention |
| `config/bootup.json` | Shared defaults for repository path, executables, ports, and logs |
| `config/bootup.local.json` | Optional Git-ignored copy for machine-specific settings; takes precedence by default |

Choose models that fit your hardware. Setup downloads missing models using the exact tags in `config/models.json`.

**Recommended setup — 12 GB VRAM**

The current configuration targets **100% GPU residency for both models on a 12 GB VRAM GPU**, balancing repository retrieval quality with room for chat generation:

| Setting | Recommendation |
| --- | --- |
| Chat model | `qwen3.5:9b` |
| Embedding model | `qwen3-embedding:4B` |
| Chat context window | `16384` tokens |
| Model retention | `1800` seconds |

The 4B embedder provides richer retrieval representations than the smaller 0.6B variant, while the 9B chat model handles answers grounded in retrieved evidence. Keeping both GPU-resident avoids repeated model swapping between retrieval and generation.

Full GPU residency is a target, not a guarantee: quantization, Ollama version, context allocation, and other GPU applications affect memory use. Check `ollama ps` after a query for **`100% GPU` on both model rows**, and use `nvidia-smi` to inspect total VRAM consumption. If either model spills onto the CPU, reduce the context window or free GPU memory.

The `repository_path` setting accepts an absolute path or a path relative to this project root. By default, setup saves your selection in `config/bootup.local.json`. If you already customized `config/bootup.json`, copy it to `config/bootup.local.json` before sharing your changes.

### 3. Run setup

Replace the example path with the repository you want to search:

```powershell
.\setup.ps1 -RepositoryPath "C:\Repos\FAK"
```

Setup creates or reuses the Python environments, installs dependencies, pulls missing models, indexes and verifies your repository, and launches the API and Open WebUI.
First-time indexing uses **4 worker processes** for file preparation. Use
`-ChunkWorkers 1` to run serially or choose a value from 1 to 10. This does not
parallelize embeddings, database writes, or verification.
Tested direct dependencies are pinned in `requirements.txt` and
`requirements-webui.txt`, including the WebUI version used by saved-connection
handling. The repository path is saved for the current machine; the shipped
`../FAK` value is only a portable default, not a required folder name.

If Ollama is installed outside the standard location and isn’t on `PATH`,
set `ollama.executable` in your local bootup config. Setup saves a discovered
non-default executable for future managed launches. If another program uses one of
the local ports, choose a different port and update `rag_api.openai_base_url` to
match when changing the API port. Keep all hosts on loopback.

Review source filters before indexing sensitive repositories. Common secrets, binaries, caches, and generated files are excluded.

Optional setup commands:

```powershell
# Validate local configuration using Python's standard library only:
py -3.12 .\services\setup_check.py --config .\config\bootup.json --repository "C:\Repos\FAK"

# Preview without installing, downloading, indexing, or starting services:
.\setup.ps1 -RepositoryPath "C:\Repos\FAK" -WhatIf

# Skip opening the browser:
.\setup.ps1 -RepositoryPath "C:\Repos\FAK" -NoBrowser

# Disable parallel file preparation:
.\setup.ps1 -RepositoryPath "C:\Repos\FAK" -ChunkWorkers 1

# Show detailed pip/Ollama output (quiet and logged by default):
.\setup.ps1 -RepositoryPath "C:\Repos\FAK" -Verbose
```

Setup displays timestamped stages, a steps-completed progress bar, and elapsed
times. Detailed pip/Ollama output is saved to a unique `setup-*.log` under the
configured log directory (normally `%LOCALAPPDATA%\Vectorizer\logs`).
Failures show recent command output and the log location. Indexing progress
and WebUI connection prompts remain visible. `-WhatIf` creates no setup log.

Use `-ConfigPath` to select another bootup configuration. If PowerShell blocks a trusted checkout, use a process-only policy bypass:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -RepositoryPath "C:\Repos\FAK"
```

### 4. Connect Open WebUI

1. Open **http://127.0.0.1:8080** and create your administrator account.
2. In **Admin Settings → Connections**, save an OpenAI-compatible connection with URL **`http://127.0.0.1:8001/v1`**.
3. Use the API key supplied during setup. Type **C** at the setup prompt to copy it if needed, then clear your clipboard after saving.
4. Press **Enter** in the setup terminal to verify the saved connection.
5. Select **GeorgeGPT** and keep WebUI Knowledge disabled.

These URLs use the default ports. Existing saved connection keys are reused. If setup is interrupted, services already launched remain running; use the stop command to shut them down.

<details>
<summary>Manual installation alternative</summary>

First copy `config/bootup.json` to `config/bootup.local.json`, set `repository_path` in the copy, and choose models in `config/models.json`. With Ollama running:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
py -3.12 -m venv .venv-webui
.\.venv-webui\Scripts\python.exe -m pip install -r requirements-webui.txt

$models = Get-Content .\config\models.json -Raw | ConvertFrom-Json
ollama pull $models.chat_model
ollama pull $models.embedding_model
.\.venv\Scripts\python.exe -m vectorizer.vectorize
.\.venv\Scripts\python.exe -m vectorizer.verify_index
```

Start the API in one terminal with your own long, random key:

```powershell
$env:RAG_API_KEY = "replace-with-your-own-long-random-secret"
.\.venv\Scripts\python.exe -m uvicorn Interface.server:app --host 127.0.0.1 --port 8001
```

Start WebUI in another terminal:

```powershell
.\.venv-webui\Scripts\python.exe -c "from open_webui import app; app()" serve --host 127.0.0.1 --port 8080
```

Save the connection described above using the same API key. Once it is saved, use the normal bootup command for subsequent launches.

</details>

## Usage guide

### Start and stop

Run commands from the project root:

```powershell
# Start or restart Ollama, the API, and Open WebUI:
.\.venv\Scripts\python.exe -m services.bootup start

# Stop the stack:
.\.venv\Scripts\python.exe -m services.bootup kill
```

Bootup reads the saved connection key, restarts verified service trees, warms the selected models, prepares the matching index, and checks readiness. Restarting interrupts active chats.

### Ask questions

Open **http://127.0.0.1:8080**, select **GeorgeGPT**, and ask about the indexed repository. Include relevant symbols, paths, or error messages when you know them.

You can also query from the terminal while Ollama is running:

```powershell
.\.venv\Scripts\python.exe -m Interface.ask "How does repository indexing work?"
```

### Update the index

After changing repository files:

```powershell
.\.venv\Scripts\python.exe -m vectorizer.vectorize
.\.venv\Scripts\python.exe -m vectorizer.verify_index
```

Both commands use the configured repository and embedding model. Reindexing reuses compatible unchanged vectors, embeds new or changed chunks, and removes stale chunks. Verification audits vector, metadata, and keyword-index consistency—not answer accuracy.

The vectorize CLI starts a stopped local HTTP Ollama server automatically. Ollama must be installed and the embedding model already pulled; remote servers must be started separately. An automatically started server remains running afterward.

If a source file cannot be read as UTF-8, indexing stops before changing the index. Check the file's encoding or permissions, then retry.

To change repositories, update `repository_path` in `config/bootup.local.json`, run the two commands above, and restart the stack. A positional path can override the repository for an individual command. Use a new collection if you intend to keep the two repositories' indexes separate.

### Change models

1. Edit `chat_model` or `embedding_model` in `config/models.json`.
2. Pull the selected models:

   ```powershell
   $models = Get-Content .\config\models.json -Raw | ConvertFrom-Json
   ollama pull $models.chat_model
   ollama pull $models.embedding_model
   ```

3. Restart:

   ```powershell
   .\.venv\Scripts\python.exe -m services.bootup start
   ```

Changing only the chat model does not rebuild embeddings. Changing the embedder selects a matching model-specific index or builds it before accepting questions. Existing model indexes are preserved, and switching back reuses a completed matching snapshot.

Vectorize and API startup share the same index catalog, so a matching completed vectorize run is not embedded again. Configuration changes take effect on restart, not during an active session. Normal startup does not download missing models.

### Progress and logs

First-time indexing with a new embedder can take longer than a normal startup. Terminal launches show a loading bar; windowless launches continue writing logs.

Logs default to **`%LOCALAPPDATA%\Vectorizer\logs`**:

| Log | Purpose |
| --- | --- |
| `index-preparation.log` | Index build progress and preparation failures |
| `ollama-vectorize.log` | Ollama output when started automatically by vectorize |
| `ollama.log` | Ollama output during normal bootup |
| `rag-api.log` | API requests and errors |
| `open-webui.log` | WebUI output |

If preparation fails, startup stops rather than serving mismatched vectors. Review the log and retry; compatible completed embedding batches can be reused. Index data persists under project-root **`chroma_db/`** by default.

<details>
<summary>Advanced configuration and overrides</summary>

- `RAG_REPOSITORY_PATH`: temporary repository override.
- `RAG_DB_PATH`: alternate database directory.
- `RAG_COLLECTION`: base collection/catalog name; commands resolve its model-specific index.
- `RAG_BOOTUP_CONFIG`: alternate bootup JSON for standalone commands. Use `services.bootup --config PATH` for stack startup.
- `RAG_MODEL_CONFIG`: alternate model JSON for standalone commands.
- `OLLAMA_BASE_URL`: alternate Ollama endpoint for standalone commands.
- `OLLAMA_EMBEDDING_MODEL` / `OLLAMA_CHAT_MODEL`: model overrides for standalone commands and direct API launches. Bootup passes the models selected by its JSON configuration.

Do not inherit another application's `DATA_DIR` or `DATABASE_*` settings:
setup/startup reject overrides that conflict with the configured local WebUI
SQLite file. Existing databases, connection keys, and signing keys are not reset.

Explicit CLI flags such as `--embedding-model`, `--db-path`, and `--collection` override command defaults. Keep the database and base collection consistent between indexing, querying, verification, and startup. Use `--help` on individual commands for additional options.

For single-file indexing, use a collection separate from your repository index, even when using the same embedding model.

</details>
