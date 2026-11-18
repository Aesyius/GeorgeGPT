#!/usr/bin/env python3
"""CLI to restart or kill this repository's verified service process trees.

Startup stops Open WebUI, the RAG API, and Ollama before launching fresh
instances. Unverified listeners and the bootup process itself are protected.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
import json
import os
import random
from pathlib import Path
import sqlite3
import subprocess
import sys

from common.model_config import ModelSettings
from .model_warmup import warm_models
from common.paths import PROJECT_ROOT
from .service_config import BootError, StartupSettings, expand_path, service_url
# Established helper imports remain available from this CLI module.
from .service_runtime import LoadingBar, read_api_key, request_json, wait_until
from .windows_processes import (
    ProcessTree, command_arguments, descendants, executable_matches, hidden_startupinfo, launch,
    listener_pids, powershell_json, process_snapshot, stop_verified_process_tree,
)

WEBUI_LAUNCH_MARKER = "from open_webui import app; app()"
API_APP = "Interface.server:app"
# Recognize already-running instances from before the directory restructure.
VERIFIED_API_APPS = {API_APP, "vectorizer.server:app"}
PROCESS_IDENTITY_FIELDS = ("ExecutablePath", "CommandLine", "ParentProcessId", "CreationDate")


def print_startup_banner(stream=None) -> None:
    """Choose one banner supported by the output stream's encoding."""
    stream = stream if stream is not None else sys.stdout
    if stream is None:
        return
    artwork = Path(__file__).with_name("startup_banner.txt").read_text(encoding="utf-8")
    encoding = getattr(stream, "encoding", None) or "utf-8"
    supported = []
    for banner in artwork.split("\n\n"):
        if not banner.strip():
            continue
        try:
            banner.encode(encoding)
        except UnicodeEncodeError:
            continue
        supported.append(banner.strip("\n"))
    if not supported:
        return
    banner = random.choice(supported)
    stream.write("\n" + banner + "\n")
    stream.flush()


def configure_windowless_output(config_path: Path) -> None:
    """Keep pythonw status/errors in an append-only log when streams are absent."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    logs = Path(os.getenv("LOCALAPPDATA", str(PROJECT_ROOT))) / "Vectorizer" / "logs"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        logs = expand_path(config["logs_directory"], PROJECT_ROOT)
    except (OSError, ValueError, KeyError, TypeError):
        pass  # main reports configuration errors once stderr is available.
    logs.mkdir(parents=True, exist_ok=True)
    stream = open(logs / "bootup.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def verified_ollama_roots(config: dict, root: Path, processes: list[dict]) -> list[int]:
    """Plan termination without stopping anything; reject unexpected listeners."""
    executable = expand_path(config["ollama"]["executable"], root)
    if not executable.is_file():
        raise BootError(f"Ollama executable not found: {executable}")
    tree = ProcessTree(processes)
    server_roots = {
        pid for pid, process in tree.by_pid.items()
        if executable_matches(process, executable)
        and command_arguments(process.get("CommandLine") or "")[1:2] == ["serve"]
    }
    for pid in listener_pids(config["ollama"]["port"]):
        if pid not in server_roots:
            raise BootError(f"Ollama port {config['ollama']['port']} belongs to an unverified process (PID {pid}); not killing it.")

    # Stop a desktop launcher only if it is an ancestor of the verified server,
    # otherwise it could respawn the server while bootup is replacing it.
    app_roots = set()
    for pid in server_roots:
        ancestors = set()
        current = tree.by_pid[pid]
        while current:
            try:
                parent_pid = int(current.get("ParentProcessId", 0))
            except (TypeError, ValueError):
                break
            if parent_pid in ancestors or parent_pid not in tree.by_pid:
                break
            ancestors.add(parent_pid)
            parent = tree.by_pid[parent_pid]
            if (str(parent.get("Name", "")).lower() == "ollama app.exe"
                    and str(Path(parent.get("ExecutablePath") or "").parent.resolve()).casefold()
                    == str(executable.parent.resolve()).casefold()):
                app_roots.add(parent_pid)
            current = parent
    targets = server_roots | app_roots
    return sorted(pid for pid in targets if int(tree.by_pid[pid].get("ParentProcessId") or 0) not in targets)


def verified_webui_roots(config: dict, root: Path, processes: list[dict]) -> list[int]:
    """Return verified top-level WebUI process-tree roots without stopping them.

    Matches the configured Python executable and WebUI launch marker, includes
    descendants, and checks every configured-port listener belongs to those
    trees. Missing Python or an unverified listener raises `BootError`.
    `processes` is a caller-supplied Windows snapshot; ownership is rechecked
    later before termination.
    """
    python = expand_path(config["open_webui"]["python"], root)
    if not python.is_file():
        raise BootError(f"Open WebUI Python executable not found: {python}")
    tree = ProcessTree(processes)
    roots = {
        pid for pid, process in tree.by_pid.items()
        if str(process.get("Name", "")).lower() == "python.exe"
        and executable_matches(process, python)
        and WEBUI_LAUNCH_MARKER in (process.get("CommandLine") or "")
    }
    targets = set(roots)
    for pid in roots:
        targets.update(tree.descendants(pid))
    for pid in listener_pids(config["open_webui"]["port"]):
        if pid not in targets:
            raise BootError(f"Open WebUI port {config['open_webui']['port']} belongs to an unverified process (PID {pid}); not killing it.")
    return sorted(pid for pid in targets if int(tree.by_pid[pid].get("ParentProcessId") or 0) not in targets)


def verified_api_roots(config: dict, root: Path, processes: list[dict]) -> list[int]:
    """Identify this repo's Uvicorn launchers, including unhealthy/no-listener ones."""
    api = config["rag_api"]
    python = expand_path(api["python"], root)
    if not python.is_file():
        raise BootError(f"RAG API Python executable not found: {python}")
    tree = ProcessTree(processes)
    roots = set()
    for pid, process in tree.by_pid.items():
        if not executable_matches(process, python):
            continue
        arguments = command_arguments(process.get("CommandLine") or "")
        if len(arguments) >= 4 and arguments[1:3] == ["-m", "uvicorn"] and arguments[3] in VERIFIED_API_APPS:
            roots.add(pid)
    targets = set(roots)
    for pid in roots:
        targets.update(tree.descendants(pid))
    for pid in listener_pids(api["port"]):
        if pid not in targets:
            raise BootError(f"RAG API port {api['port']} belongs to an unverified process (PID {pid}); not killing it.")
    return sorted(pid for pid in targets if int(tree.by_pid[pid].get("ParentProcessId") or 0) not in targets)


def stop_service_roots(pids: list[int], description: str, port: int) -> None:
    """Stop preverified process trees, then wait for their listener port to clear.

    Calls the forceful tree-termination helper for each PID and polls up to
    30 seconds when targets exist. `pids` must already pass ownership and
    protection checks. Does not restart the service or remove persisted data.
    """
    for pid in pids:
        stop_verified_process_tree(pid, description)
    if pids:
        wait_until(f"{description} to stop", lambda: not listener_pids(port), timeout=30, interval=1)


def force_restart_ollama(config: dict, root: Path, processes: list[dict]) -> None:
    """Verify and stop existing Ollama trees as the shutdown half of a restart.

    Despite the compatibility name, this function does not launch a replacement;
    the caller must invoke `start_ollama` separately. Interrupts Ollama clients.
    """
    stop_service_roots(verified_ollama_roots(config, root, processes), "Ollama", config["ollama"]["port"])


def force_restart_webui(config: dict, root: Path, processes: list[dict]) -> None:
    """Verify and stop existing WebUI trees before a separately managed relaunch.

    Does not launch WebUI itself or reset its database; active chats can be
    interrupted. Ownership failures abort without killing unverified listeners.
    """
    stop_service_roots(verified_webui_roots(config, root, processes), "Open WebUI", config["open_webui"]["port"])


@dataclass(frozen=True)
class StopPlan:
    """Immutable description of verified shutdown targets for one service.

    Stores the port, initially selected root PIDs, and selector used to recheck
    ownership against a fresh snapshot before any destructive action.
    """
    description: str
    port: int
    pids: list[int]
    selector: Callable[[dict, Path, list[dict]], list[int]]


def ensure_unprotected(tree: ProcessTree, pids: list[int], protected: set[int], description: str) -> None:
    """Refuse termination when a target tree intersects protected process IDs.

    Evaluates the supplied snapshot only. Raises `BootError` if any root or
    descendant would include the current CLI or a service excluded from shutdown.
    Performs no OS termination or fresh process inspection.
    """
    for pid in pids:
        if ({pid} | tree.descendants(pid)) & protected:
            raise BootError(f"Refusing to stop {description}: its process tree includes the CLI or a service not selected for shutdown.")


def kill_current_processes(config: dict, root: Path, service: str = "all", *, dry_run: bool = False) -> None:
    """Verify selected services, then stop WebUI/API/Ollama in dependency order.

    Args:
        config: Bootup mapping with executable paths, ports, and service identities.
        root: Project root for relative configured paths.
        service: `all`, `ollama`, `webui`, or `api`.
        dry_run: Print verified targets without terminating anything.

    Protects the CLI and unselected listeners, checks fresh process identity
    before termination, and aborts on PID reuse/ownership changes. Real shutdown
    force-stops process trees and interrupts active clients; databases, indexes,
    environments, and logs are preserved. `BootError` reports unsafe ownership
    or failed shutdown; invalid selectors raise `ValueError`.
    """
    if service not in {"all", "ollama", "webui", "api"}:
        raise ValueError(f"unsupported service: {service}")
    processes = process_snapshot()
    original = ProcessTree(processes)
    selectors = [("webui", "Open WebUI", "open_webui", verified_webui_roots),
                 ("api", "RAG API", "rag_api", verified_api_roots),
                 ("ollama", "Ollama", "ollama", verified_ollama_roots)]
    unselected_ports = [config[section]["port"] for name, _, section, _ in selectors if service not in {"all", name}]

    def protected_pids():
        protected = {os.getpid()}
        for port in unselected_ports:
            protected.update(listener_pids(port))
        return protected

    protected = protected_pids()
    plans = []
    for name, description, section, selector in selectors:
        if service in {"all", name}:
            pids = selector(config, root, processes)
            ensure_unprotected(original, pids, protected, description)
            plans.append(StopPlan(description, config[section]["port"], pids, selector))
    for plan in plans:
        if not plan.pids:
            print(f"No verified {plan.description} processes are running.")
            continue
        if dry_run:
            print(f"Would kill verified {plan.description} process trees: {', '.join(map(str, plan.pids))}.")
            continue
        fresh = process_snapshot()
        fresh_tree = ProcessTree(fresh)
        current = plan.selector(config, root, fresh)
        # A previously selected parent tree may already have stopped this root.
        # Missing roots are harmless; new roots or PID reuse are not.
        remaining = [pid for pid in plan.pids if pid in fresh_tree.by_pid]
        if set(current) != set(remaining):
            raise BootError(f"{plan.description} processes changed during shutdown; retry after reviewing their state.")
        ensure_unprotected(fresh_tree, remaining, protected_pids(), plan.description)
        for pid in remaining:
            before, after = original.by_pid[pid], fresh_tree.by_pid[pid]
            if any(before.get(key) != after.get(key) for key in PROCESS_IDENTITY_FIELDS):
                raise BootError(f"{plan.description} PID {pid} changed identity; not killing it.")
        stop_service_roots(remaining, plan.description, plan.port)
    print("Dry run complete; no processes were killed." if dry_run else "Selected service shutdown complete.")


def verify_api_configuration(api_url: str, headers: dict, models: ModelSettings, ollama_url: str) -> None:
    """Compare the new API's authenticated runtime settings with expected startup values.

    Requests `/v1/config` using caller-supplied authorization headers and checks
    model names, allocation, retention, and Ollama URL. Raises `BootError` if the
    request fails or fields differ. Never logs the supplied API key.
    """
    try:
        _, actual = request_json(f"{api_url}/v1/config", headers=headers)
    except Exception as error:
        raise BootError("Cannot verify the newly started RAG API configuration; startup did not complete.") from error
    expected = models.runtime_configuration(ollama_url)
    mismatches = [key for key, value in expected.items() if not isinstance(actual, dict) or actual.get(key) != value]
    if mismatches:
        raise BootError(f"New RAG API configuration differs: {', '.join(mismatches)}; startup did not complete.")


def start_ollama(config: dict, settings: StartupSettings) -> None:
    """Launch configured local Ollama, verify installed models, and warm inference.

    Applies context/retention and two-model residency settings to the child,
    appends output to `ollama.log`, and polls readiness. Missing required model
    tags raise `BootError`; nothing is downloaded. `warm_models` exercises both
    models and checks joint residency before startup can continue.
    Assumes preflight has dealt with existing processes/port ownership.
    """
    ollama = config["ollama"]
    url = service_url(ollama)
    env = os.environ.copy()
    env.update({"OLLAMA_HOST": f"{ollama['host']}:{int(ollama['port'])}",
                "OLLAMA_CONTEXT_LENGTH": str(settings.models.context_window_tokens),
                "OLLAMA_KEEP_ALIVE": f"{settings.models.keep_alive_seconds}s",
                "OLLAMA_MAX_LOADED_MODELS": "2", "OLLAMA_NUM_PARALLEL": "1"})
    pid = launch([str(expand_path(ollama["executable"], settings.root)), "serve"],
                 cwd=settings.root, env=env, log_path=settings.logs / "ollama.log")
    print(f"Started Ollama (PID {pid}).")

    def installed_models():
        _, body = request_json(f"{url}/api/tags", timeout=5)
        return {item.get("name") for item in body.get("models", [])}

    installed = wait_until("Ollama API", installed_models, settings.timeout, settings.interval)
    required = {settings.models.chat_model, settings.models.embedding_model} | set(ollama.get("required_models", []))
    if missing := required - installed:
        raise BootError(f"Ollama is up but required models are missing: {sorted(missing)}")
    print(f"Verified Ollama at {url} with required models.")
    print(f"Selected chat model: {settings.models.chat_model}; embedding model: {settings.models.embedding_model}; "
          f"context window: {settings.models.context_window_tokens} tokens.")
    warm_models(url, settings.models, settings.timeout)


def start_rag_api(config: dict, settings: StartupSettings, api_key: str) -> None:
    """Prepare the selected embedding index, launch Uvicorn, and verify API readiness.

    Passes authoritative model/source settings and the secret through a child
    environment, not argv. Index preparation runs before the HTTP readiness
    timeout and can build/write vectors and BM25. Preparation errors prevent
    API launch. Then starts `Interface.server:app` on the configured loopback
    endpoint, logs to `rag-api.log`, and verifies model/runtime configuration
    with bearer-authenticated requests. Already launched services are not rolled
    back on a later failure.
    """
    api = config["rag_api"]
    python = expand_path(api["python"], settings.root)
    if not python.is_file():
        raise BootError(f"RAG API Python executable not found: {python}")
    ollama_url = service_url(config["ollama"])
    env = os.environ.copy()
    env.update(settings.models.environment(settings.models_path))
    env[config.get("api_key_env_var", "RAG_API_KEY")] = api_key
    env["RAG_API_KEY"] = api_key
    env["PYTHONUTF8"] = "1"
    env["OLLAMA_BASE_URL"] = ollama_url
    env["RAG_REPOSITORY_PATH"] = str(settings.repository_path)
    # Build before launching Uvicorn: a first embedding pass can legitimately
    # take longer than the service HTTP readiness timeout.
    print("Preparing the configured embedding model's index (also logged to index-preparation.log).", flush=True)
    settings.logs.mkdir(parents=True, exist_ok=True)
    terminal = any(stream is not None and stream.isatty() for stream in (sys.stdout, sys.stderr))
    result = subprocess.run(
        [str(python), "-m", "services.index_preparation",
         "--log-path", str(settings.logs / "index-preparation.log")],
        cwd=settings.root, env=env, stdout=sys.stdout, stderr=sys.stderr,
        startupinfo=None if terminal else hidden_startupinfo(),
        creationflags=0 if terminal else getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise BootError(f"Embedding index preparation failed; see {settings.logs / 'index-preparation.log'}. "
                        "The API and WebUI were not started.")
    pid = launch([str(python), "-m", "uvicorn", API_APP, "--host", api["host"],
                  "--port", str(api["port"])], cwd=settings.root, env=env, log_path=settings.logs / "rag-api.log")
    print(f"Started RAG API (PID {pid}).")
    headers = {"Authorization": f"Bearer {api_key}"}

    def ready():
        _, body = request_json(f"{service_url(api)}/v1/models", headers=headers, timeout=5)
        advertised = api["model_id"] in [item.get("id") for item in body.get("data", [])]
        if advertised:
            verify_api_configuration(service_url(api), headers, settings.models, ollama_url)
        return advertised

    wait_until(f"RAG API model {api['model_id']}", ready, settings.timeout, settings.interval)


def start_webui(config: dict, settings: StartupSettings, api_key: str) -> None:
    """Launch WebUI in its separate environment and wait for its local HTTP page.

    Supplies the GeorgeGPT OpenAI connection and key through the child
    environment, retains WebUI's persistent signing-key behavior, and disables
    client-side execution integrations. Creates the runtime working directory
    and appends diagnostics to `open-webui.log`. Does not create an account,
    reset storage, save a connection directly, or open the browser.
    Missing interpreter or readiness failures abort startup.
    """
    webui = config["open_webui"]
    python = expand_path(webui["python"], settings.root)
    if not python.is_file():
        raise BootError(f"Open WebUI Python executable not found: {python}")
    runtime_dir = expand_path(webui["runtime_directory"], settings.root)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.pop("WEBUI_SECRET_KEY", None)  # Let WebUI load its persistent key.
    env.update({"OPENAI_API_BASE_URLS": config["rag_api"]["openai_base_url"], "OPENAI_API_KEYS": api_key,
                "ENABLE_OPENAI_API": "true", "ENABLE_PLUGINS": "false", "ENABLE_TOOLS": "false",
                "ENABLE_FUNCTIONS": "false", "ENABLE_TOOL_SERVERS": "false"})
    pid = launch([str(python), "-c", WEBUI_LAUNCH_MARKER, "serve", "--host", webui["host"],
                  "--port", str(webui["port"])], cwd=runtime_dir, env=env, log_path=settings.logs / "open-webui.log")
    print(f"Started Open WebUI (PID {pid}).")

    def ready():
        status, _ = request_json(service_url(webui), timeout=5)
        return status == 200

    wait_until("Open WebUI HTTP endpoint", ready, settings.timeout, settings.interval)
    print(f"Verified Open WebUI at {service_url(webui)}.")


def start_services(config: dict, root: Path) -> None:
    """Restart the complete local stack using validated settings and its saved key.

    Resolves configuration, reads WebUI's saved connection secret, verifies/stops
    existing trees, then starts Ollama, API, and WebUI in dependency order.
    **Interrupts active chats and other clients of this Ollama instance.**
    Returns only after readiness checks succeed. Does not pull missing models;
    partial startup is not rolled back if a subsequent stage fails.
    """
    settings = StartupSettings.from_config(config, root)
    api_key = read_api_key(config, root)
    kill_current_processes(config, root)
    start_ollama(config, settings)
    start_rag_api(config, settings, api_key)
    start_webui(config, settings, api_key)
    print(f"All services verified. Logs: {settings.logs}")


def argument_parser() -> argparse.ArgumentParser:
    """Return the start/stop CLI parser without reading configuration or running services.

    Defines command aliases, custom config, selective stop, and stop dry-run
    flags. Cross-option validation is performed by `main` after parsing.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="start", choices=("start", "bootup", "kill", "stop"),
                        help="start/bootup: launch the stack (default); kill/stop: terminate verified services without restarting")
    parser.add_argument("--service", choices=("all", "ollama", "webui", "api"),
                        help="service to kill (default: all, meaning Open WebUI, the RAG API, and Ollama)")
    parser.add_argument("--dry-run", action="store_true", help="preview verified kill targets without stopping them (kill/stop only)")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config" / "bootup.json",
                        help="JSON configuration file (default: config/bootup.json in the project)")
    return parser


def main() -> int:
    """Run managed start/restart or verified shutdown from command-line arguments.

    Loads the selected bootup JSON, rejects stop-only options on start, and
    dispatches `start_services` or `kill_current_processes`. Returns 0 on success,
    1 for handled operational failures; argparse reports invalid input separately.
    Windowless launches route status/errors to logs. No automatic rollback occurs.
    """
    parser = argument_parser()
    args = parser.parse_args()
    stopping = args.command in {"kill", "stop"}
    if not stopping and (args.service is not None or args.dry_run):
        parser.error("--service and --dry-run are only supported with kill/stop")
    config_path = args.config.resolve()
    configure_windowless_output(config_path)
    if not stopping:
        print_startup_banner()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        parser.error(f"cannot read config {config_path}: {error}")
    if not stopping:
        try:
            timeout = int(config.get("startup_timeout_seconds", 240))
            interval = int(config.get("poll_interval_seconds", 3))
            if timeout < 1 or interval < 1:
                raise ValueError("startup_timeout_seconds and poll_interval_seconds must be positive")
        except (TypeError, ValueError) as error:
            parser.error(str(error))
    try:
        if stopping:
            kill_current_processes(config, PROJECT_ROOT, args.service or "all", dry_run=args.dry_run)
        else:
            start_services(config, PROJECT_ROOT)
        return 0
    except (BootError, OSError, KeyError, TypeError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        label = "PROCESS KILL" if stopping else "BOOTUP"
        print(f"{label} FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
