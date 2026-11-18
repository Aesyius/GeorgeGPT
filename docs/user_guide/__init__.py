r"""
# GeorgeGPT: start here

**GitHub repository:** [Aesyius/GeorgeGPT](https://github.com/Aesyius/GeorgeGPT)

GeorgeGPT answers questions about an indexed repository's code and documentation.
It runs locally on Windows with Ollama, Chroma, SQLite keyword search, and Open
WebUI. Indexing makes files searchable; it does **not** train the chat model.
GeorgeGPT cannot execute commands, modify files, or access infrastructure.

## Repository maintainer

**Cpl Jack Gan**

- **Unit:** CFNOC, B SQN CPT 4
- **Contact:** Reachable on Teams
- **Email:** [jack.gan@forces.gc.ca](mailto:jack.gan@forces.gc.ca)

## First time?

Read **[First-time setup](user_guide/setup.html)** from top to bottom.
It covers prerequisites, `setup.ps1`, the WebUI connection, and your first question.
You do not need to understand the Python API to use the application.

### Follow this path

1. [Set up GeorgeGPT](user_guide/setup.html): follow the seven installation
   steps and confirm that your first question works.
2. [Learn daily operation](user_guide/daily_use.html): start, stop, and refresh
   the index after source changes.
3. [Look up flags when needed](user_guide/cli.html): reference individual
   commands without repeating the installer.

> **Reading these docs:** start with the guide, not the Python API pages.
> Follow numbered setup steps in order. Later reference pages describe separate
> tasks; you do not need to run every command shown.

## What do you want to do?

| Task | Guide |
| --- | --- |
| Install and connect the application | [First-time setup](user_guide/setup.html) |
| Start, restart, stop, or update the index | [Daily use and bootup](user_guide/daily_use.html) |
| Understand the models, 12 GB VRAM target, and software components | [Technology stack](user_guide/tech_stack.html) |
| Look up a command or any supported flag | [Complete CLI reference](user_guide/cli.html) |
| Change models, ports, paths, or environment overrides | [Configuration](user_guide/configuration.html) |
| Resolve a failed setup or startup | [Troubleshooting](user_guide/troubleshooting.html) |

## Command conventions

All examples use **Windows PowerShell**, run from the GeorgeGPT project root.
Replace `C:\Projects\GeorgeGPT` with your checkout and `C:\Repos\MyProject`
with the repository you want the chatbot to answer questions about.
Quote paths containing spaces.

```powershell
Set-Location "C:\Projects\GeorgeGPT"
```

After setup, use `.\.venv\Scripts\python.exe`, not an unrelated system Python.
Environment activation is optional. Commands execute immediately; do not run
restart or indexing examples merely to read their help.

## Local-only operation

The managed stack uses loopback addresses only: Ollama on port **11434**,
the authenticated API on **8001**, and WebUI on **8080**, by default.
Do not add public listeners, firewall exceptions, or port forwarding.
Initial dependency and model downloads require internet. WebUI may also make
its own update/model checks. Retrieval and inference run locally.

Keep WebUI Knowledge/RAG disabled: GeorgeGPT already performs retrieval.
Review source filters before indexing sensitive repositories.

## Python API reference

These are implementation references, not installation instructions:

- [vectorizer](vectorizer.html): filtering, chunking, embeddings, indexing,
  storage, retrieval, and integrity auditing.
- [Interface](Interface.html): prompt, chat generation, query CLI, queue, and API.
- [services](services.html): setup, Windows process management, and warmup.
- [common](common.html): model settings, paths, and progress reporting.
- [evaluation](evaluation.html): retrieval and answer evaluation.

## Rebuild this documentation

With the project dependencies already installed, run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-docs.txt
.\.venv\Scripts\python.exe docs\build.py
.\.venv\Scripts\python.exe docs\check.py
Start-Process .\docs\html\index.html
```

The build uses pdoc to write **static HTML only** under `docs/html`.
Open `index.html` directly; no documentation server is required or started.
Search is disabled so navigation works without fetching a search index from
a server. Share the entire `docs/html` directory, preserving its subdirectories.
Only building requires Python and dependencies; reading the HTML does not.
The check validates local links/anchors and CLI flag coverage without running
services. Generated API defaults use `{PROJECT_ROOT}` in place of the build
machine's absolute checkout path.
"""
