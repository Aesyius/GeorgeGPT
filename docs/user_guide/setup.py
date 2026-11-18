r"""
# First-time setup

Follow these steps in order. You will install GeorgeGPT, connect its chat
interface, and ask your first question.

> **Estimated installation time: approximately 30 minutes**, depending on
> your internet speed, hardware, and the size of the repository being indexed.
> Large model downloads or repositories may take longer.

**You need:** Windows 10/11 (64-bit), PowerShell 5.1 or newer, internet for the
initial downloads, and a local repository to search.

**Path examples:** `C:\Projects\GeorgeGPT` is the application folder.
`C:\Repos\MyProject` is the repository you want to ask questions about.
Replace both with your real paths and quote paths containing spaces.

## Step 1 — Install the prerequisites

1. Install **64-bit Python 3.12**. The `py` launcher is optional.
2. Open PowerShell and check your Python installation:

```powershell
py -3.12 --version
```

**Before continuing:** the command should report Python 3.12. If it is missing,
try `python --version`. If Python 3.12 is installed but isn't discoverable,
pass `-PythonPath "C:\Path\To\python.exe"` to `setup.ps1`.

**Disk space:** budget approximately **15–20 GB for the completed installation**
with the default `qwen3.5:9b` chat model, `qwen3-embedding:4B` embedding model,
and both Python environments. This is a planning estimate, not a fixed size.
Your source repository, its generated index, download caches, and additional
models require extra space. Aim for **at least 25–30 GB free before setup**
to leave room for downloads and temporary installation files; large repositories
or different model choices may need more.

## Step 2 — Download GeorgeGPT and open its folder

Download and extract the repository, or clone it if Git is installed:

```powershell
git clone https://github.com/Aesyius/GeorgeGPT.git C:\Projects\GeorgeGPT
```

Open the application folder in PowerShell:

```powershell
Set-Location "C:\Projects\GeorgeGPT"
```

**Before continuing:** confirm this folder contains `setup.ps1`.
Run every remaining command from this folder.
Do not copy another computer's `.venv` or `.venv-webui`; setup creates them
for this machine.

## Step 3 — Choose the repository and review configuration

Choose an existing repository directory, such as `C:\Repos\MyProject`.
It does not have to be the GeorgeGPT repository.

| Review | What to check |
| --- | --- |
| `config/models.json` | Chat model, embedding model, context window, and retention. |
| `config/bootup.json` | Shared defaults for Ollama, local ports, logs, and model config. Setup saves the chosen repository in Git-ignored `config/bootup.local.json` by default. |
| `vectorizer/source_files.py` | Source filters, especially before indexing sensitive repositories. |

The shipped model settings are `qwen3.5:9b`, `qwen3-embedding:4B`, a
16384-token context, and 1800 seconds retention. This targets a 12 GB VRAM
GPU; full GPU residency depends on available memory, quantization, and Ollama.

Keep all hosts on `127.0.0.1` or `localhost`. Ports must be distinct and free.
If you change the API port, update `rag_api.openai_base_url` to the matching
URL ending in `/v1`. See [configuration](configuration.html) for every setting.
If a default port is busy, follow
[port conflicts and changing ports](configuration.html#port-conflicts-and-changing-the-default-ports)
before running setup.

**Before continuing:** verify the source directory exists and your model
choices fit the machine. Filtering excludes common secrets, binaries, large
files, caches, environments, and generated directories, but cannot guarantee
every secret is excluded.

## Step 4 — Preview the installation

On a fresh checkout, proceed directly to the preview command below.
If this stack is already running, stop it first. **Stopping interrupts chats:**

```powershell
.\.venv\Scripts\python.exe -m services.bootup stop
```

Skip that stop command if no environment has been created yet.
Then preview setup using your actual repository path:

```powershell
.\setup.ps1 -RepositoryPath "C:\Repos\MyProject" -WhatIf
```

**Expected result:** the terminal shows the chosen repository, model names,
chunk worker count, and six planned stages. It does not install, download, index, change
configuration, launch services, create logs, or ask for installation consent.
Preview reads configuration but does not probe Python, Ollama, or service ports.

## Step 5 — Run setup and confirm

Remove `-WhatIf` to begin:

```powershell
.\setup.ps1 -RepositoryPath "C:\Repos\MyProject"
```

After the opening banner, answer **Continue with installation? Press Y/N**:

- **Y:** continue to the prerequisite checks and installation.
- **N:** exit without installation or configuration changes.
- Other answers repeat the prompt.

Once checks pass, setup saves the repository path and performs these stages:

| Stage | What happens |
| --- | --- |
| 1. Python environments | Creates or reuses `.venv` and `.venv-webui`. |
| 2. Dependencies | Upgrades pip and installs the pinned project and WebUI dependencies. |
| 3. Ollama models | Reuses installed models and downloads missing configured model weights. |
| 4. Repository index | Chunks eligible files and creates or updates vectors and keyword rows. |
| 5. Integrity audit | Verifies chunk, metadata, vector, and keyword-index consistency. |
| 6. Services and connection | Warms models, launches the API and WebUI, and waits for you to save the connection. |

Setup uses **4 chunk worker processes** by default. Workers read and chunk
eligible files in parallel; embeddings, database writes, and the integrity
audit are unchanged. Set `-ChunkWorkers` to any value from 1 to 10. For a small
repository, `-ChunkWorkers 1` avoids the overhead of starting worker processes:

```powershell
.\setup.ps1 -RepositoryPath "C:\Repos\MyProject" -ChunkWorkers 1
```

**While you wait:** numbered stages and timestamped messages show progress.
The loading bar measures steps completed, not time remaining.
First-time indexing and large model downloads can take a while.

**Next action:** when WebUI opens or setup asks you to save the connection,
continue to Step 6. Keep the setup terminal open.

If interrupted, setup does not roll back completed work. Environments,
downloads, configuration, and launched services may remain.

## Step 6 — Connect Open WebUI

Use the default URLs below unless you changed the configured ports.

1. Open [Open WebUI](http://127.0.0.1:8080) if the browser did not open.
2. Create your administrator account.
3. Open **Admin Settings → Connections**.
4. Save an OpenAI-compatible connection with the URL
   **`http://127.0.0.1:8001/v1`** and the setup API key.
5. If you need the key, type **C** at the setup terminal prompt, paste the
   copied key into WebUI, and save the connection.
6. Clear your clipboard, return to the terminal, and press **Enter** to
   verify the saved connection.
7. Select **GeorgeGPT** in WebUI. Keep WebUI Knowledge/RAG disabled.

**Expected result:** the terminal reports setup completed successfully and
the WebUI model list includes `GeorgeGPT`.

Setup supplies the key to WebUI at launch and reuses an existing matching
saved key. It does not reset your account or database. Clipboard contents
may be shared by the operating system; never include keys in screenshots,
chat, documentation, or reports.

Typing **Q** leaves connection setup incomplete. Services already launched
remain running; use the stop command in Step 4 to shut them down.

## Step 7 — Ask your first question

Ask about a real file, symbol, or feature in the indexed repository.
For example: “What does the main entry point do?”

You can also ask from the same project-root PowerShell window while Ollama
is running:

```powershell
.\.venv\Scripts\python.exe -m Interface.ask "What does the main entry point do?"
```

**Expected result:** an answer grounded in the indexed repository.
An integrity PASS is not proof of answer accuracy, and source code does not
prove a running system's current state.

**You are ready for normal use.** Follow [Daily use and bootup](daily_use.html)
for subsequent starts, stops, and index updates. Do not repeat the full
installer for an ordinary launch.

## Optional — Detailed output and logs

Dependency installation and model pulls are quiet by default. To show their
detailed output in the terminal too, use:

```powershell
.\setup.ps1 -RepositoryPath "C:\Repos\MyProject" -Verbose
```

In both modes, detailed pip/Ollama stdout and stderr are saved to a unique
`setup-YYYYMMDD-HHMMSS-PID.log` in `logs_directory`, normally
`%LOCALAPPDATA%\Vectorizer\logs`. The terminal prints the exact location.
Indexing progress, verification output, and connection prompts remain visible.

On a dependency/model command failure, setup shows its exit code, recent
output, and log location. The final summary includes each stage's elapsed time,
including time spent waiting for your input. Progress indicators clear on exit.
The setup log is not a complete terminal transcript and does not record
interactive credentials.

## If PowerShell blocks the script

Only for a trusted checkout, use this process-only policy bypass:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -RepositoryPath "C:\Repos\MyProject"
```

Do not change machine-wide execution policy just to run setup.
For other failures, use [Troubleshooting](troubleshooting.html).

## Setup flags

| Parameter | Default | Meaning |
| --- | --- | --- |
| `-RepositoryPath PATH` | Required | Existing source directory; saved in the selected bootup JSON. Relative paths resolve from the current directory. |
| `-ConfigPath PATH` | Local bootup config if present, otherwise the shared template | Existing bootup JSON; supplied relative paths resolve from the current directory. |
| `-PythonPath PATH` | Discover Python via the launcher or `PATH` | Explicit path to a 64-bit Python 3.12 interpreter. |
| `-NoBrowser` | Off | Do not open the browser automatically. Services and connection prompts still run. |
| `-WhatIf` | Off | Offline preview; no installation, consent prompts, or persistent changes. |
| `-ChunkWorkers` | `4` | Parallel file preparation processes during first-time indexing (1–10). Use `1` for serial chunking. |
| `-EmbeddingTimeoutSeconds` | `120` | Ollama HTTP timeout in seconds per embedding attempt (1–3600). Indexing errors appear in the terminal. |
| `-Confirm` | PowerShell default | Additional ShouldProcess confirmation before the setup action. |
| `-Verbose` | Off | Also show detailed pip/Ollama output in the terminal. |

Standard PowerShell common parameters are also accepted.
Use `Get-Help .\setup.ps1 -Full` for the syntax supported by your shell.
See the [complete CLI reference](cli.html) for the rest of the commands.
"""
