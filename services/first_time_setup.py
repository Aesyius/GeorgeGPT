"""Bootstrap API/WebUI with a secret held in memory until the user saves it."""

from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import webbrowser

from common.paths import PROJECT_ROOT, default_bootup_config
from .bootup import start_rag_api, start_webui
from .model_warmup import warm_models
from .service_config import BootError, StartupSettings, service_url
from .service_runtime import ConnectionNotConfigured, read_api_key, request_json
from .windows_processes import hidden_startupinfo, listener_pids


def bootstrap_key(config: dict, root: Path) -> str:
    """Reuse a saved API key or create an in-memory first-time bootstrap secret.

    Only `ConnectionNotConfigured` permits fallback to a supplied environment
    key (at least 32 characters) or a cryptographically random generated key.
    Existing-key mismatches remain errors. Does not persist or print the secret;
    the user saves it through WebUI's connection settings.
    """
    try:
        return read_api_key(config, root)
    except ConnectionNotConfigured:
        supplied = os.getenv(config.get("api_key_env_var", "RAG_API_KEY"), "").strip()
        if supplied and len(supplied) < 32:
            raise BootError("Use an API key of at least 32 characters for first-time setup.")
        return supplied or secrets.token_urlsafe(32)


def copy_key_to_clipboard(key: str) -> None:
    """Copy the supplied key to the Windows clipboard using subprocess stdin.

    The caller must obtain explicit user consent first; this helper does not
    prompt. The secret is not placed in argv/logs. Clipboard contents may be
    shared outside the application. PowerShell failure/timeout propagates.
    """
    subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "$input | Set-Clipboard"],
        input=key, text=True, capture_output=True, check=True, timeout=15,
        startupinfo=hidden_startupinfo(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )


def wait_for_saved_connection(config: dict, root: Path, key: str, *, read_input=input) -> None:
    """Prompt until WebUI contains the expected saved connection key.

    Reads the connection via `read_api_key` and compares secrets in constant
    time. Enter retries, C copies the key after explicit input, and Q raises
    `BootError` without stopping launched services. `read_input` is injectable
    for tests. Account creation and persistence remain WebUI operations; no
    direct configuration SQL writes are performed.
    """
    print("Create your administrator account in WebUI, then open Admin Settings > Connections.")
    print(f"Save an OpenAI-compatible connection: {config['rag_api']['openai_base_url']}")
    print("The API key is supplied to WebUI at launch. Keep WebUI Knowledge disabled; select GeorgeGPT.")
    while True:
        try:
            saved = read_api_key(config, root)
            if hmac.compare_digest(saved, key):
                return
            print("The saved connection key differs from the setup key; update it in WebUI.")
        except ConnectionNotConfigured:
            pass
        response = read_input("Enter after saving; C to copy the key (clipboard may be shared); Q to leave setup: ").strip().lower()
        if response == "q":
            raise BootError("Connection setup incomplete; bootstrap services remain running.")
        if response == "c":
            copy_key_to_clipboard(key)
            print("API key copied. Paste it into WebUI, save the connection, and clear your clipboard afterward.")


def bootstrap(config: dict, root: Path, *, open_browser: bool = True) -> None:
    """Launch the initial API/WebUI pair and guide the user through connection saving.

    Requires installed dependencies, running Ollama, and pulled chat/embedding
    models. Validates settings and free API/WebUI ports, obtains a bootstrap key,
    warms models, prepares/starts the API, and launches WebUI. `open_browser`
    controls only automatic browser opening, not prompts or service startup.
    Waits for the saved key before reporting completion. Failures do not roll
    back installed data or stop services that were already launched.
    """
    settings = StartupSettings.from_config(config, root)
    for section in ("rag_api", "open_webui"):
        if listener_pids(config[section]["port"]):
            raise BootError("API/WebUI ports are occupied. Stop the stack with python -m services.bootup kill, then retry.")
    key = bootstrap_key(config, root)
    _, body = request_json(service_url(config["ollama"]) + "/api/tags")
    installed = {item.get("name") for item in (body or {}).get("models", [])}
    missing = {settings.models.chat_model, settings.models.embedding_model} - installed
    if missing:
        raise BootError(f"Models are missing; run setup.ps1 to pull them: {sorted(missing)}")
    warm_models(service_url(config["ollama"]), settings.models, settings.timeout)
    start_rag_api(config, settings, key)
    start_webui(config, settings, key)
    if open_browser:
        webbrowser.open(service_url(config["open_webui"]))
    wait_for_saved_connection(config, root, key)
    print("First-time setup complete. For future launches: python -m services.bootup start")
    print("To stop services: python -m services.bootup kill")


def main(argv: list[str] | None = None) -> int:
    """Run first-time bootstrap with `--config` and optional `--no-browser`.

    Uses `argv` when supplied, otherwise the current command line. Returns 0 on
    completion or 1 for handled setup/interrupt failures, explaining that any
    launched services remain running. Does not install packages or pull models.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=default_bootup_config())
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.resolve().read_text(encoding="utf-8-sig"))
        bootstrap(config, PROJECT_ROOT, open_browser=not args.no_browser)
        return 0
    except (BootError, OSError, ValueError, KeyError, subprocess.SubprocessError, EOFError, KeyboardInterrupt) as error:
        print(f"SETUP INCOMPLETE: {error}", file=sys.stderr)
        print("Any bootstrap services already launched remain running. Stop them with python -m services.bootup kill.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
