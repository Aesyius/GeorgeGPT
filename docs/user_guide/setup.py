r"""
# First-time setup

[Home](../user_guide.html) · [Daily use](daily_use.html) · [CLI flags](cli.html)

## 1. Install prerequisites

Use Windows 10/11 (64-bit), PowerShell 5.1 or newer, and:

- **64-bit Python 3.12**, including the **py launcher**.
- **Ollama**, installed and running locally.
- Internet access and enough disk space for two Python environments, model
  weights, and your index. First indexing can take substantially longer than
  subsequent starts.

Check from PowerShell:

```powershell
py -3.12 --version
ollama --version
```

If a command is missing, install the prerequisite and reopen PowerShell.
Start the Ollama application before running setup.

### If setup does not detect Ollama

If `setup.ps1` cannot find Ollama, it asks **Install Ollama now? Press Y/N**.
Press **Y** to download and run the installer below, or **N** to stop setup
without installing. Invalid answers prompt again. `-WhatIf` never prompts
for installation or downloads anything.

```powershell
irm https://ollama.com/install.ps1 | iex
```

You can also run this command manually in PowerShell.
It downloads and immediately executes Ollama's installation script from
the internet. Only run it if you trust the source; review the script first
if required by your security policy.

Setup rechecks the configured executable after installation, then checks the
local Ollama endpoint. If Ollama is not running yet, start the application
and rerun `setup.ps1`; reopen PowerShell if needed.
If Ollama is found on PATH or in its default installation directory but the
configured path is wrong, setup asks you to correct `ollama.executable` in
`config/bootup.json` instead of reinstalling.

### Download GeorgeGPT

Download/extract the project, or clone it if Git is installed:

```powershell
git clone https://github.com/Aesyius/GeorgeGPT.git C:\Projects\GeorgeGPT
Set-Location "C:\Projects\GeorgeGPT"
```

Do not copy another computer's `.venv` or `.venv-webui`. Setup creates them
for this machine. Choose a short, writable project path.

## 2. Review configuration and your source repository

The repository to search is **not necessarily GeorgeGPT itself**.
Choose an existing local directory, such as `C:\Repos\MyProject`.
Do not use the example path literally unless it exists.

- `config/models.json`: chat model, embedding model, context, and retention.
  The shipped selections are `qwen3.5:9b`, `qwen3-embedding:4B`, a 16384-token
  context, and 1800 seconds retention. This targets a 12 GB VRAM GPU; full GPU
  residency depends on available memory, quantization, and Ollama.
- `config/bootup.json`: service executables, ports, logs, model-config path,
  and repository. Setup saves your selected repository here.
- If Ollama is installed outside `%LOCALAPPDATA%\Programs\Ollama`, update
  `ollama.executable` before continuing.

Keep hosts on `127.0.0.1` or `localhost`. Ports must be distinct and available.
If changing the API port, also change `rag_api.openai_base_url` to the matching
URL ending in `/v1`.

Review `vectorizer/source_files.py` before indexing sensitive content.
Actual `.env` files, common credentials, binaries, large files, caches,
environments, and generated directories are filtered, but filtering is not
a guarantee that every secret is excluded.

## 3. Preview, then run setup

**If this stack is already running**, stop it first (this interrupts chats):

```powershell
.\.venv\Scripts\python.exe -m services.bootup kill
```

Skip that command on a fresh checkout with no `.venv`.
Preview setup without installing, downloading, indexing, starting services,
or changing configuration:

```powershell
.\setup.ps1 -RepositoryPath "C:\Repos\MyProject" -WhatIf
```

Preview still reads the repository and JSON configuration. It deliberately
does not check Python/Ollama availability or service ports.

Run the actual setup:

```powershell
.\setup.ps1 -RepositoryPath "C:\Repos\MyProject"
```

Setup:

1. Checks Python 3.12, Ollama availability, local configuration, and free
   API/WebUI ports before installation.
2. Saves the repository path in the selected bootup JSON.
3. Creates or reuses `.venv` and `.venv-webui`, upgrades their pip, and installs
   the pinned runtime and WebUI dependencies.
4. Downloads missing configured models; already installed models are reused.
5. Indexes eligible text and verifies vector/metadata/keyword consistency.
6. Warms models and launches the API and WebUI.
7. Waits for you to save and verify the WebUI connection.

Wait for progress to finish; do not treat a long embedding stage as a failure
without checking its output. Setup is not transactional: if interrupted,
environments, downloaded models, saved configuration, and launched services
can remain.

### Progress, timestamps, and quiet output

Setup displays six numbered stages with timestamps, elapsed time per stage,
and a final timing summary. The overall PowerShell loading bar shows **steps
completed**, not an estimate of time remaining. Timings include time spent
waiting for your input, such as saving the WebUI connection.

Dependency installation and Ollama pulls are **quiet by default**. You still
see which model is downloading or being reused, and a timestamped completion
message. Detailed pip/Ollama stdout and stderr are saved to a unique
`setup-YYYYMMDD-HHMMSS-PID.log` in the configured `logs_directory` (normally
`%LOCALAPPDATA%\Vectorizer\logs`). The terminal prints the exact log location.

To also show detailed dependency and model output in the terminal:

```powershell
.\setup.ps1 -RepositoryPath "C:\Repos\MyProject" -Verbose
```

Quiet mode does not invent a download percentage. Use `-Verbose` to see native
download output. Indexing progress, verification output, and interactive WebUI
connection prompts remain visible in both modes. The setup log is not a full
terminal transcript: it captures status/timing messages and dependency/model
command output, not interactive credentials.

On a dependency/model command failure, setup shows the exit code, recent output,
the log location, and the failed stage's elapsed time. Progress is cleared on
exit. `-WhatIf` does not start timers/progress bars or create setup logs.

If a trusted checkout is blocked by execution policy, use a process-only
bypass, not a machine-wide policy change:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -RepositoryPath "C:\Repos\MyProject"
```

## 4. Finish the Open WebUI connection

1. Open **http://127.0.0.1:8080** if the browser did not open.
2. Create your administrator account.
3. Open **Admin Settings → Connections** and save an OpenAI-compatible
   connection to **http://127.0.0.1:8001/v1**.
4. Use the setup API key. At the terminal prompt, type **C** to copy it if
   necessary, paste it into the connection, save, and clear your clipboard.
   Clipboard contents may be shared by your operating system.
5. Press **Enter** in the setup terminal to check the saved connection.
6. Select **GeorgeGPT** in WebUI. Leave WebUI Knowledge/RAG disabled.

Use configured ports instead if you changed them. Setup supplies the key to
WebUI at launch and reuses an existing matching saved key; it does not reset
your account or database.

Typing **Q** leaves connection setup incomplete; services already launched
remain running. Use the stop command above if you want them shut down.
Never paste your API key into chat, documentation, screenshots, or bug reports.

## 5. Ask your first question

Ask about something you know exists in the indexed repository, for example:
“What does the main entry point do?” Include a real filename or symbol for a
more focused answer. An answer is not proof of current running-system state.

You can also use the terminal while Ollama is running:

```powershell
.\.venv\Scripts\python.exe -m Interface.ask "What does the main entry point do?"
```

Success means setup reports completion, WebUI offers `GeorgeGPT`, and a question
returns an answer grounded in your repository. Integrity verification checks
index consistency, not answer correctness.

Next time, use [bootup](daily_use.html), not the full installer.
See [troubleshooting](troubleshooting.html) if setup stops.

## Setup flags

| Parameter | Default | Meaning |
| --- | --- | --- |
| `-RepositoryPath PATH` | Required | Existing directory to index; saved in the selected bootup JSON. Relative paths resolve from the shell's current directory. |
| `-ConfigPath PATH` | Project `config/bootup.json` | Existing bootup JSON. A supplied relative path resolves from the current directory. |
| `-NoBrowser` | Off | Do not automatically open WebUI; services and connection prompt still run. |
| `-WhatIf` | Off | Offline preview; no installation or persistent changes. |
| `-Confirm` | Off by default | Ask before the setup action through PowerShell ShouldProcess. |
| `-Verbose` | Off | Show pip/Ollama output in the terminal as well as saving it to the setup log. |

Standard PowerShell common parameters are also accepted; they do not replace
the setup-specific checks. List PowerShell syntax with
`Get-Help .\setup.ps1 -Full`.

```powershell
.\setup.ps1 -RepositoryPath "C:\Repos\MyProject" -ConfigPath ".\config\bootup.json" -NoBrowser
```
"""
