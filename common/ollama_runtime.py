"""Shared Ollama availability check for interactive indexing commands."""

import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlsplit

import requests

from .paths import PROJECT_ROOT


def _ready(base_url: str) -> bool:
    try:
        response = requests.get(base_url.rstrip("/") + "/api/version", timeout=2)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("version"), str) or not body["version"]:
            raise ValueError("missing Ollama version")
        return True
    except requests.ConnectionError:
        return False
    except (requests.RequestException, ValueError) as error:
        raise RuntimeError(f"Cannot verify Ollama at {base_url}: {error}") from error


def ensure_ollama_running(base_url: str, *, timeout: float = 30) -> None:
    """Reuse a healthy server, or start a local one without restarting anything."""
    if timeout <= 0:
        raise ValueError("Ollama startup timeout must be positive")
    url = urlsplit(base_url)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.query or url.fragment):
        raise ValueError("Ollama URL must be HTTP(S), without credentials, query, or fragment")
    port = url.port or (443 if url.scheme == "https" else 80)
    if _ready(base_url):
        return
    if url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"} or url.path.rstrip("/"):
        raise RuntimeError(f"Ollama is unavailable at {base_url}; start that server and retry. "
                           "Only local HTTP Ollama servers can be started automatically.")
    executable = shutil.which("ollama")
    if not executable and os.name == "nt" and os.getenv("LOCALAPPDATA"):
        candidate = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama" / "ollama.exe"
        if candidate.is_file():
            executable = str(candidate)
    if not executable:
        raise RuntimeError("Ollama is not running and its executable was not found. "
                           "Install Ollama or add it to PATH, then retry.")
    logs = (Path(os.environ["LOCALAPPDATA"]) / "Vectorizer" / "logs"
            if os.getenv("LOCALAPPDATA") else PROJECT_ROOT / "runtime_logs")
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "ollama-vectorize.log"
    env = os.environ.copy()
    host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
    env["OLLAMA_HOST"] = f"{host}:{port}"
    print(f"Ollama is not running; starting it at {base_url}...", flush=True)
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [executable, "serve"], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), start_new_session=os.name != "nt",
        )
    deadline = time.monotonic() + timeout
    while True:
        # Probe before checking exit: another launcher may have won the bind race.
        if _ready(base_url):
            return
        if process.poll() is not None:
            raise RuntimeError(f"Ollama exited before becoming ready; see {log_path}.")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Ollama did not become ready within {timeout:g}s; see {log_path}.")
        time.sleep(0.5)
