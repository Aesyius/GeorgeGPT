# GeorgeGPT — Repository RAG Chatbot

## How it works

GeorgeGPT answers questions using indexed repository code and documentation. The application retrieves relevant passages using Chroma semantic search and SQLite FTS5/BM25 keyword search, then sends that evidence to an Ollama chat model. Open WebUI connects through the authenticated `GeorgeGPT` API; its own Knowledge/RAG is not used.

The chatbot cannot execute commands, access additional files, or inspect live infrastructure. Its answers describe indexed evidence, not verified live system state.

## Initialize everything — Windows PowerShell

Install **64-bit Python 3.12** and **Ollama**, ensure Ollama is running, and open PowerShell in this project folder. Replace `C:\Repos\FAK` with your repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
py -3.12 -m venv .venv-webui
.\.venv-webui\Scripts\python.exe -m pip install open-webui
ollama pull qwen3.5:9b
ollama pull qwen3-embedding:0.6b
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

Confirm executable, WebUI database, and port settings in `config/bootup.json`. Models, context size, and retention are configured in `config/models.json`. For subsequent launches:

```powershell
.\.venv\Scripts\python.exe -m services.bootup start
# Stop all three services:
.\.venv\Scripts\python.exe -m services.bootup kill
```

Bootup reads the saved WebUI connection key, restarts verified service trees, warms both models, and checks readiness. Active chats are interrupted by a restart. Select **GeorgeGPT** in WebUI. Logs default to `%LOCALAPPDATA%\Vectorizer\logs`.

## How embedding works

1. **Filter and chunk:** eligible UTF-8 text is split around Python definitions and Markdown sections. Oversized sections are split with overlap. Common secrets, binaries, caches, and generated files are excluded; review filters before indexing sensitive repositories.
2. **Embed:** Ollama converts each chunk into a numeric vector. Relative file paths, headings, and symbols add context to the embedding input, while stored evidence retains the source text and line ranges.
3. **Store:** Chroma saves vectors and SQLite saves keyword-search rows under project-root `chroma_db/`. Reindexing reuses compatible unchanged vectors, embeds changes, and removes stale chunks. This does not train the chat model.

## Embed a repository

Replace the example path with your repository. Ollama must be running:

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

Keep the database path, collection, and embedding model consistent across indexing, verification, and querying. Set `RAG_COLLECTION` before bootup when using a custom collection. A different embedding model requires a matching index and explicit `--embedding-model` on the indexing/verification commands; changing only the chat model does not require reindexing.

**Code layout:** `vectorizer/` = RAG; `Interface/` = chat/API; `services/` = startup; `common/` = shared settings; `evaluation/` = evaluation tools. `Interface` is case-sensitive on Linux.
