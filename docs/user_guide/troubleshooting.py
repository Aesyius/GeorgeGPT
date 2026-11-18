r"""
# Troubleshooting

[Home](../user_guide.html) · [Setup](setup.html) · [Configuration](configuration.html)

Start with the specific error and the relevant log. Do not delete environments,
databases, signing keys, or indexes as a generic fix. Redact secrets before
sharing configuration or diagnostics.

## Python or PowerShell

**`py` is not found, or Python 3.12 is unavailable:** install 64-bit Python 3.12
with the launcher, reopen PowerShell, and check `py -3.12 --version`.
An unrelated `python` on PATH does not establish the required version.

**Running scripts is disabled:** for a trusted checkout, use the process-only
execution-policy example in [setup](setup.html). Do not weaken system-wide policy.

**`.venv\Scripts\python.exe` is missing:** on a fresh checkout run setup first.
If the environment already exists but is incomplete, setup will not overwrite
it. Inspect and repair deliberately; do not recursively delete unrelated paths.

**Module not found:** use the project interpreter from the project root.
WebUI uses `.venv-webui`, not the API `.venv`.

## Ollama is unavailable or models are missing

Start the Ollama application and check the local endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags
ollama list
```

Use configured ports if different. Verify `ollama.executable` if setup cannot
find the executable. Model tags must match `config/models.json` exactly.
If Ollama is missing, setup offers installation with a **Y/N** prompt.
Only **Y** downloads and executes the official installation script; **N**
stops setup. If an installation is found but the configured path is wrong,
correct `ollama.executable` instead. See [setup](setup.html#if-setup-does-not-detect-ollama).
Setup downloads missing models; normal bootup does not. Pull the required tag
or rerun setup after stopping the stack.

Vectorize can launch a stopped local HTTP Ollama automatically; query and
evaluation commands should not be assumed to do so.

## API/WebUI port is busy or process ownership is unknown

If it is this stack, stop it with the same configuration used to start it:

```powershell
.\.venv\Scripts\python.exe -m services.bootup stop --dry-run
.\.venv\Scripts\python.exe -m services.bootup stop
```

Stopping interrupts chats and may affect other Ollama users.
For an unrelated application, stop it through its normal controls or choose
another free **loopback** port. Update the API base URL and WebUI connection
if changing the API port. Never bypass ownership checks by blindly terminating
the port owner.

## WebUI cannot connect or GeorgeGPT is missing

- Confirm the API started successfully; read `rag-api.log`.
- Use the URL ending in `/v1`, normally `http://127.0.0.1:8001/v1`.
- Save the setup key in **Admin Settings → Connections**, then return to the
  terminal and press Enter.
- Select `GeorgeGPT`, not a raw Ollama model. Keep WebUI Knowledge disabled.
- A `401` means missing/mismatched bearer credentials. Do not reset your
  database; correct the saved connection or stale environment override.
- A normal bootup before the first connection was saved cannot recover a key
  that was never persisted. Complete the setup/bootstrap connection workflow.

## Setup was interrupted or Q was entered

Services already launched remain running, and partial installations/downloads
may remain. Stop the managed stack, correct the cause, start Ollama again for
the setup prerequisite check, then rerun setup. Existing compatible
environments, installed models, index work, and saved keys can be reused.
Setup is not a rollback transaction.

## Index preparation is slow or fails

Read `index-preparation.log` and check Ollama/model availability. A new embedder
may need to build a model-specific index before the API becomes ready.
Windowless startup has no terminal progress; inspect logs instead.
Do not force a mismatched embedding model onto existing vectors.

After source changes, rerun vectorize and verify with the same database,
collection, embedding model, and chunk settings. Verification failure details
can identify missing vectors, bad metadata, or keyword-index inconsistency.
An explicit verification scan is a maintenance command, not a startup step.
Preserve existing data; compatible completed batches may be reusable on retry.

## Answers are vague, irrelevant, or wrong

Check that the intended repository was indexed and refreshed. Mention real
symbols, paths, or error messages in the question. The CLI positional source
override does not change bootup's saved repository. Inspect retrieval with
`evaluation.evaluate_retrieval` and your own expected-path question set.

An integrity PASS does not prove retrieval relevance or answer correctness.
Source code also does not prove what is currently running on your machine.

## Requests fail under load

The API generates one answer at a time with up to four waiting requests.
`429` indicates a full queue; `503` can indicate a queue timeout (90 seconds)
or retrieval unavailability. Reduce simultaneous WebUI tasks and inspect the
error/log rather than repeatedly restarting. `502` indicates an upstream
chat failure; inspect Ollama and the API log.

## GPU use or memory pressure

Check `ollama ps` after a question and optionally `nvidia-smi`.
The shipped models aim for full residency on a 12 GB VRAM GPU, not a guarantee.
Free memory, reduce configured context, or select smaller compatible models.
Pull new tags and restart after changes; an embedding-model change can require
a new index.

## Documentation build fails

Install `requirements-docs.txt` into the project environment, which must also
have `requirements.txt` installed for Python API imports. Then rerun:

```powershell
.\.venv\Scripts\python.exe docs\build.py
```

No Ollama server, API key, index, or WebUI installation is needed merely to
build/read the documentation. Open `docs/html/index.html` directly and keep
the entire output directory together. Rebuild after updating guide sources
or public Python APIs.
"""
