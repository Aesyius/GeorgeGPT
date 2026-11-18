r"""
# Daily use and bootup

[Home](../user_guide.html) · [First-time setup](setup.html) · [CLI flags](cli.html)

Run all examples in PowerShell from the GeorgeGPT project root after setup.

**Choose the task you need below.** These are separate procedures, not one
script to execute from top to bottom. Check that a command succeeds before
moving to the next command in the same procedure.

## Start or restart

**Starting performs a restart and interrupts active chats.** Bootup verifies
service identity before stopping the managed WebUI, API, and Ollama process
trees. Ollama may be shared with other local applications; restarting or
stopping it affects those clients too.

```powershell
.\.venv\Scripts\python.exe -m services.bootup start
```

Omitting `start` has the same effect; `bootup` is another alias.
Bootup reads the saved WebUI connection key, restarts verified services, warms
models, prepares a matching index, and checks readiness. It does not download
missing models. Open **http://127.0.0.1:8080** manually after startup.

For windowless startup after confirming normal startup works:

```powershell
.\.venv\Scripts\pythonw.exe -m services.bootup start
```

There is no console progress for this launch; inspect the logs if it fails.
Use the Python executable for troubleshooting.

## Stop services

Preview verified shutdown targets without stopping them:

```powershell
.\.venv\Scripts\python.exe -m services.bootup kill --dry-run
```

Stop the whole stack:

```powershell
.\.venv\Scripts\python.exe -m services.bootup kill
```

`stop` is an alias for `kill`. Stop only one service:

```powershell
.\.venv\Scripts\python.exe -m services.bootup stop --service webui
.\.venv\Scripts\python.exe -m services.bootup stop --service api --dry-run
```

Service choices are `all`, `ollama`, `webui`, and `api`.
`--service` and `--dry-run` are **only valid with kill/stop**; there is no
selective-start flag. A successful stop does not delete indexes, accounts,
environments, or logs. Unrecognized listener ownership is a reason to
investigate, not to kill arbitrary processes.

To use an alternate configuration, pass it explicitly for both start and stop:

```powershell
.\.venv\Scripts\python.exe -m services.bootup start --config ".\config\bootup.json"
.\.venv\Scripts\python.exe -m services.bootup stop --config ".\config\bootup.json"
```

## Refresh changed repository files

For a predictable maintenance window, stop the stack first, then index,
verify, and restart:

```powershell
.\.venv\Scripts\python.exe -m services.bootup stop
.\.venv\Scripts\python.exe -m vectorizer.vectorize
.\.venv\Scripts\python.exe -m vectorizer.verify_index
.\.venv\Scripts\python.exe -m services.bootup start
```

Check each command succeeds before continuing. Vectorize can start a stopped
local HTTP Ollama server automatically; it remains running afterward.
The embedding model must already be installed.

Indexing reuses compatible unchanged vectors, embeds new/changed chunks, and
removes stale chunks for the indexed source. Verification reports
`Verification: PASS` or `FAIL`; a failed audit exits nonzero. It does not
require Ollama, repair data, or measure answer accuracy.

To change the default source repository, edit `repository_path` in
`config/bootup.json`, then reindex, verify, and restart. Supplying a positional
path to vectorize only overrides that invocation; it does not save a new
bootup repository setting.

Keep database, collection, embedding model, and custom chunk settings
consistent across indexing, verification, and querying.

## Change models

Edit `config/models.json`, pull the exact new model tags, then restart:

```powershell
$models = Get-Content .\config\models.json -Raw | ConvertFrom-Json
ollama pull $models.chat_model
ollama pull $models.embedding_model
.\.venv\Scripts\python.exe -m services.bootup start
```

This `ollama` command assumes the executable is on PATH and uses its normal
local endpoint. Use the configured executable and local port if different.
Restart interrupts chats. A new embedder can trigger a lengthy index build
before the API accepts questions. Existing model-specific indexes are kept;
switching back can reuse a matching completed snapshot.
Changing only the chat model does not rebuild embeddings.

Check `ollama ps` after a question: full GPU residency is indicated by `100% GPU`
for both models. Free GPU memory or reduce context if models spill onto CPU.

## Find logs

Default location: `%LOCALAPPDATA%\Vectorizer\logs`.

| File | What to inspect |
| --- | --- |
| `setup-YYYYMMDD-HHMMSS-PID.log` | Setup timestamps/timings and detailed pip/Ollama pull output; one file per real setup run |
| `index-preparation.log` | Bootup index progress and preparation errors |
| `ollama-vectorize.log` | Ollama started automatically by vectorize |
| `ollama.log` | Ollama during managed bootup |
| `rag-api.log` | Retrieval, generation, and API errors |
| `open-webui.log` | WebUI startup/connection failures |

```powershell
Get-Content "$env:LOCALAPPDATA\Vectorizer\logs\rag-api.log" -Tail 60
```

Use `logs_directory` in your bootup JSON if customized. Preserve logs and
databases while troubleshooting, and redact secrets before sharing output.
"""
