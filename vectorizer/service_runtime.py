"""Startup readiness checks and read-only WebUI credential resolution."""

import hmac
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request

from .service_config import BootError, expand_path


class LoadingBar:
    """Compact waiting indicator; bounded log output when redirected."""

    def __init__(self, label: str, width: int = 28):
        self.label, self.width = label, width
        self.last_bucket = -1
        self.interactive = sys.stdout.isatty()

    def update(self, percent: int, *, complete: bool = False) -> None:
        percent = max(0, min(100, int(percent)))
        bucket = percent // 10
        if not self.interactive and bucket == self.last_bucket and not complete:
            return
        self.last_bucket = bucket
        filled = round(self.width * percent / 100)
        bar = "=" * filled + " " * (self.width - filled)
        line = f"{self.label} [{bar}] {percent:3d}%"
        if self.interactive:
            print("\r" + line + ("\n" if complete else ""), end="", flush=True)
        else:
            print(line, flush=True)


def wait_until(description: str, check, timeout: int, interval: int):
    started = time.monotonic()
    deadline = started + timeout
    last_error = None
    bar = LoadingBar(f"Waiting for {description}")
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                bar.update(100, complete=True)
                return value
        except Exception as error:
            last_error = error
        elapsed = time.monotonic() - started
        bar.update(min(95, int(elapsed * 100 / timeout)))
        time.sleep(interval)
    bar.update(95, complete=True)
    detail = f" Last error: {last_error}" if last_error else ""
    raise BootError(f"Timed out waiting for {description}.{detail}")


def request_json(url: str, *, headers: dict | None = None, timeout: int = 5, payload: dict | None = None):
    request_headers = dict(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        body = json.load(response) if "json" in content_type.lower() else None
        return response.status, body


def read_api_key(config: dict, root: Path) -> str:
    """Read the saved connection key and compare overrides without logging secrets."""
    env_name = config.get("api_key_env_var", "RAG_API_KEY")
    env_key = os.getenv(env_name, "").strip()
    db_path = expand_path(config["rag_api"]["open_webui_database"], root)
    if not db_path.is_file():
        raise BootError(f"Open WebUI database not found: {db_path}. Set {env_name} or configure an existing WebUI key.")
    try:
        connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            rows = connection.execute(
                'SELECT "key", value FROM config '
                'WHERE "key" IN ("openai.enable", "openai.api_base_urls", "openai.api_keys")'
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise BootError(f"Could not read the Open WebUI connection configuration: {error}") from error
    settings = {}
    for name, value in rows:
        try:
            settings[name] = json.loads(value)
        except (ValueError, TypeError):
            settings[name] = value
    base_urls = settings.get("openai.api_base_urls") or []
    keys = settings.get("openai.api_keys") or []
    base_url = config["rag_api"]["openai_base_url"]
    if settings.get("openai.enable") is False:
        raise BootError("Open WebUI's OpenAI-compatible connection is disabled in its database.")
    if base_url not in base_urls:
        raise BootError(f"Open WebUI does not have the configured RAG API URL: {base_url}")
    index = base_urls.index(base_url)
    if index >= len(keys) or not isinstance(keys[index], str) or not keys[index].strip():
        raise BootError("Open WebUI has no API key saved for the RAG API connection.")
    saved_key = keys[index].strip()
    if env_key and not hmac.compare_digest(env_key, saved_key):
        raise BootError(f"{env_name} does not match the key saved in Open WebUI. Update the connection and environment "
                        "together, or unset the environment override.")
    return saved_key
