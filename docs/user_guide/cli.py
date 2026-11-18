r"""
# Complete CLI reference

[Home](../user_guide.html) · [Setup](setup.html) · [Configuration](configuration.html)

## Before running commands

This is a lookup reference, not an installation sequence. Start with the
[seven-step setup guide](setup.html) for a new installation, or
[daily-use procedures](daily_use.html) for an existing one.

Use PowerShell from the project root, with dependencies installed.
Every argparse command below accepts `-h` / `--help`, which prints syntax and
exits without executing its workflow. For example:

```powershell
.\.venv\Scripts\python.exe -m vectorizer.vectorize --help
```

`vectorizer.managed_index` is the exception: it has **no argument parser**,
so do not pass `--help` to it (it would still prepare an index).
Python API modules without a `__main__` entry point are not additional CLIs.

## Shared indexing/query/evaluation flags

These flags apply to `vectorize`, `verify_index`, `Interface.ask`, and both
evaluation commands, unless stated otherwise.

| Flag | Default / source | Purpose |
| --- | --- | --- |
| `--db-path PATH` | `RAG_DB_PATH`, otherwise project-root `chroma_db` | Persistent index directory; explicit relative paths resolve from the current directory. |
| `--collection NAME` | `RAG_COLLECTION`, otherwise `repo_documents` | Base collection/catalog; repository workflows resolve the embedding-model-specific index. |
| `--embedding-model NAME` | Configured model, with environment overrides | Exact Ollama embedding tag; must match the indexed vectors. |
| `--ollama-url URL` | `OLLAMA_BASE_URL`, otherwise `http://localhost:11434` | Ollama endpoint. Not accepted or required by `verify_index`. |

Explicit model flags win over environment/config defaults. See
[configuration precedence](configuration.html). There is no universal
`--config` flag for indexing/query/evaluation; use the appropriate environment
variables instead.

## setup.ps1 — install and bootstrap

See [the setup guide](setup.html) for the full interactive workflow.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `-RepositoryPath PATH` | Required | Existing source directory; persisted in the selected bootup JSON. |
| `-ConfigPath PATH` | Local bootup config if present, otherwise the shared template | Bootup configuration to use; setup writes to the local copy when using the shared template. |
| `-PythonPath PATH` | Discover Python via the launcher or `PATH` | Explicit path to a 64-bit Python 3.12 executable. |
| `-NoBrowser` | Off | Suppress automatic WebUI browser opening only. |
| `-WhatIf` | Off | Read configuration and preview offline, without changes or prerequisite probes. |
| `-Confirm` | PowerShell default | Prompt before the setup action. |
| `-Verbose` | Off | Also show pip/Ollama command output in the terminal; it is logged in both modes. |

As an advanced PowerShell script it also accepts standard common parameters
such as `-Verbose`, `-Debug`, `-ErrorAction`, `-WarningAction`,
`-InformationAction`, `-ProgressAction` (where supported by your PowerShell),
`-ErrorVariable`, `-WarningVariable`, `-InformationVariable`, `-OutVariable`,
`-OutBuffer`, and `-PipelineVariable`. These are PowerShell controls, not
additional installation features. `Get-Help .\setup.ps1 -Full` reports those
available in your installed PowerShell.

```powershell
.\setup.ps1 -RepositoryPath "C:\Repos\MyProject" -NoBrowser
.\setup.ps1 -RepositoryPath "C:\Repos\MyProject" -Verbose
```

Setup status messages are timestamped, with six-stage progress and elapsed-time
summaries. Detailed dependency/model output is quiet by default and saved to
`setup-YYYYMMDD-HHMMSS-PID.log` in `logs_directory`. Failure output includes the
exit code and a short log tail. Indexing progress and connection prompts remain
visible. `-WhatIf` creates no log and runs no loading bars.

## services.bootup — managed stack

```powershell
.\.venv\Scripts\python.exe -m services.bootup [COMMAND] [OPTIONS]
```

| Argument / flag | Default | Meaning |
| --- | --- | --- |
| `COMMAND` | `start` | `start` / `bootup` restart the stack; `kill` / `stop` stop verified process trees. |
| `--config PATH` | Project `config/bootup.json` | Existing bootup JSON; supplied relative paths resolve from the current directory. |
| `--service CHOICE` | `all` when stopping | `all`, `ollama`, `webui`, or `api`; kill/stop only. |
| `--dry-run` | Off | Preview verified shutdown targets; kill/stop only. |

**Start is a restart, not a no-op when already running.** It interrupts chats
and restarts Ollama, which can affect other Ollama users. No selective start
or start dry-run is supported.

```powershell
.\.venv\Scripts\python.exe -m services.bootup stop --service ollama --dry-run
```

## vectorizer.vectorize — create/update an index

Requires an installed embedding model. Can start a stopped local HTTP Ollama;
automatically launched Ollama remains running.

```powershell
.\.venv\Scripts\python.exe -m vectorizer.vectorize "C:\Repos\MyProject"
```

| Argument / flag | Default | Meaning |
| --- | --- | --- |
| `path` | Configured repository | Optional directory, or one UTF-8 text file. CLI path does not change saved configuration. |
| Shared flags | Above | Database, collection, embedding model, and Ollama endpoint. |
| `--chunk-size N` | `1400` | Maximum structural chunk characters. |
| `--overlap N` | `180` | Character overlap for long sections. |
| `--batch-size N` | `32` | Positive maximum chunks per embedding request for repository indexing. |
| `--chunk-workers N` | `1` | File preparation processes for repositories only. `1` is serial; the maximum is `10`. |
| `--embedding-timeout N` | `120` | Ollama HTTP timeout in seconds per attempt (1–3600) for repository or single-file indexing. |
| `--max-embedding-bytes N` | `4096` | UTF-8 embedding input cap, including source context; a conservative token-size proxy. |

The single-file workflow uses a fixed embedding batch size of 32 even when
`--batch-size` is supplied. Changing single-file embedding models requires
an explicitly separate collection.

Direct CLI runs default to one worker. [First-time setup](setup.html) passes
four workers by default. Try `--chunk-workers 2` or `--chunk-workers 4` for
large repositories. This parallelizes file preparation, not Ollama embeddings
or database writes. Small repositories may be faster without the overhead of
starting worker processes.

```powershell
.\.venv\Scripts\python.exe -m vectorizer.vectorize "C:\Repos\MyProject" --chunk-size 1200 --overlap 150 --batch-size 16 --max-embedding-bytes 4096
.\.venv\Scripts\python.exe -m vectorizer.vectorize ".\README.md" --collection notes_demo
```

Indexing writes vectors and keyword rows and removes stale chunks for the
source being synchronized. Preserve the database; do not delete it to refresh.
Repository output reports files, chunks, newly embedded/reused/removed chunks,
revision, selected collection, and keyword-index path.

## vectorizer.verify_index — explicit integrity audit

Read-only audit of repository chunking, metadata, vectors, and BM25 rows.
Ollama need not be running. This is not a single-file audit CLI.

```powershell
.\.venv\Scripts\python.exe -m vectorizer.verify_index "C:\Repos\MyProject"
```

| Argument / flag | Default | Meaning |
| --- | --- | --- |
| `path` | Configured repository | Optional repository directory to compare against the index. |
| Shared flags except `--ollama-url` | Above | Select matching database, collection, and embedder. |
| `--chunk-size N` | `1400` | Must match indexing. |
| `--overlap N` | `180` | Must match indexing. |
| `--max-embedding-bytes N` | `4096` | Must match indexing. |

There is no `--batch-size` flag. A failed audit or skipped files causes
nonzero exit status; review details on stderr as well as `Verification`.
An audit does not repair data or assess answer quality.

```powershell
.\.venv\Scripts\python.exe -m vectorizer.verify_index "C:\Repos\MyProject" --chunk-size 1200 --overlap 150
```

## Interface.ask — one terminal question

Needs an existing matching index and running Ollama with chat/embedding models.
Does not require WebUI or a running GeorgeGPT API, and does not take an API key.

```powershell
.\.venv\Scripts\python.exe -m Interface.ask "How does authentication work?"
```

| Argument / flag | Default | Meaning |
| --- | --- | --- |
| `question` | Required | One quoted question. |
| Shared flags | Above | Select the index, embedder, and Ollama. |
| `--model NAME` | Configured chat model | Exact chat model tag; this flag is **not** `--chat-model`. |
| `--top-k N` | `8` | Positive maximum retrieved passages; answer context remains bounded (at most 8 evidence items). |

```powershell
.\.venv\Scripts\python.exe -m Interface.ask "Where is logging configured?" --top-k 4 --model qwen3.5:9b
```

The command prints an answer or reports an error with nonzero status.
It does not offer an interactive multi-turn shell.

## evaluation.evaluate_questions — batch answers

Requires a matching index and running Ollama with both models.
Uses project-root `sample_questions/fak_questions.jsonl` by default; those
questions may describe a different repository from yours. Supply your own set.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--questions PATH` | Project `sample_questions/fak_questions.jsonl` | Input JSONL file. |
| `--output PATH` | Project `sample_questions/fak_answers.json` | Answer JSON, atomically saved after each question. |
| Shared flags | Above | Select index and Ollama. |
| `--chat-model NAME` | Configured chat model | Chat model override (unlike `Interface.ask --model`). |
| `--top-k N` | `8` | Positive retrieval limit; answer evidence is still capped at 8 items. |

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_questions --questions ".\my_questions.jsonl" --output ".\results\answers.json" --top-k 8
```

Existing compatible output is used to resume completed answers. The command
rejects conflicting model/collection/question-file metadata, but does not
detect every changed index, question content, or setting. **Use a fresh output
path for independent experiments** or changed questions. Inspect `COMPLETE`
and per-answer errors: the process can finish normally with failed answers.
It generates answers for review, not an automatic correctness grade.

## evaluation.evaluate_retrieval — retrieval-only evaluation

Requires a matching index and running embedding model, but no chat generation.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--questions PATH` | Project `sample_questions/fak_questions.jsonl` | Input JSONL. |
| `--output PATH` | Required | New JSON output file; refuses to reuse an existing file. |
| Shared flags | Above | Select index and Ollama embedding endpoint. |
| `--top-k N` | `8` | Positive retrieval limit. |

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_retrieval --questions ".\my_questions.jsonl" --output ".\results\retrieval-01.json" --top-k 8
```

Reports path recall and reciprocal rank using `expected_paths`. These are
relevance proxies, not proof that evidence fully supports an answer.
No `--chat-model` or `--model` flag is accepted.

### Question file format

One JSON object per line; each must have unique string `id` and string
`question`. Optional fields include `category`, `expected_paths`, and
`pass_criteria`. Use paths relative to the indexed repository, with `/`.

```json
{"id":"auth-01","question":"Where is authentication checked?","expected_paths":["src/auth.py"],"pass_criteria":["Explains the authentication check"]}
```

Replace the example path with a real path. `pass_criteria` is retained for
human review; it is not automatically scored. Without expected paths,
path-level scoring is unavailable for that question.

## Support commands — usually invoked by setup/bootup

### services.setup_check

Standard-library-only prerequisite/configuration validation. Does not install,
index, or launch services. `setup.ps1` separately probes Ollama and busy ports.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--config PATH` | Required | Bootup JSON to validate. |
| `--repository PATH` | Required | Existing repository directory. |

```powershell
py -3.12 .\services\setup_check.py --config ".\config\bootup.json" --repository "C:\Repos\MyProject"
```

With project dependencies installed, `python -m services.setup_check` also
works with the same flags.

### services.first_time_setup

Bootstrap the WebUI connection **after** dependencies, models, and index have
been prepared. Not a substitute for the full PowerShell installer.
Ollama must be running; API/WebUI ports must be free.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--config PATH` | Project `config/bootup.json` | Existing bootup JSON. |
| `--no-browser` | Off | Do not open a browser; connection prompt still runs. |

```powershell
.\.venv\Scripts\python.exe -m services.first_time_setup --no-browser
```

It launches API/WebUI and waits for connection saving. Leaving or interrupting
setup does not shut down launched services.

### services.index_preparation

Bootup helper that prepares the selected model's index, echoes progress to
the terminal, and appends diagnostics to a log. It can **write/build an index**;
it is not an audit or dry run.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--log-path PATH` | Required | Append-only log destination; parent directories are created. Relative paths use the current directory. |

```powershell
.\.venv\Scripts\python.exe -m services.index_preparation --log-path ".\runtime_logs\manual-preparation.log"
```

Uses the model, repository, database, collection, and Ollama environment
settings described in [configuration](configuration.html).

### vectorizer.managed_index

Internal entry point behind index preparation. Accepts **no flags or positional
arguments**, including no `--help`. Uses environment/configuration defaults.
Requires embedding availability if building; publishes only completed indexes.
Ordinarily use vectorize for explicit updates or let bootup prepare the index.

```powershell
.\.venv\Scripts\python.exe -m vectorizer.managed_index
```

## Manual API/WebUI launches (advanced)

Prefer managed bootup. For troubleshooting, with Ollama running and the index
ready, set a long random API key in the first terminal, then:

```powershell
# Set RAG_API_KEY securely in this terminal before running this command.
.\.venv\Scripts\python.exe -m uvicorn Interface.server:app --host 127.0.0.1 --port 8001
```

In a second project-root terminal, use WebUI's separate environment:

```powershell
.\.venv-webui\Scripts\python.exe -c "from open_webui import app; app()" serve --host 127.0.0.1 --port 8080
```

The uvicorn target is `Interface.server:app`. These are third-party launchers:
the examples show required local settings, not every upstream launcher option.
Use `python -m uvicorn --help` or WebUI's `serve --help` for their full options.
Keep hosts on loopback. Save the matching WebUI connection key; do not run
manual and managed listeners on the same ports simultaneously.
Manual foreground processes can be stopped with Ctrl+C.
"""
