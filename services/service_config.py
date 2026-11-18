"""Resolved startup settings; no process, network, or database side effects."""

from dataclasses import dataclass
import os
from pathlib import Path

from common.model_config import ModelSettings
from common.chroma_endpoint import chroma_port
from common.paths import configured_repository


class BootError(RuntimeError):
    """A safe startup condition could not be met."""


def expand_path(value: str, root: Path) -> Path:
    """Expand environment variables and resolve a service path relative to `root`.

    Absolute paths are retained. Does not create or require the destination.
    Unlike `common.paths.resolve_path`, this helper does not expand `~`.
    """
    path = Path(os.path.expandvars(value))
    return (path if path.is_absolute() else root / path).resolve()


def service_url(service: dict) -> str:
    """Format an HTTP base URL from a service mapping's `host` and `port`.

    Formatting does not validate loopback safety; call `validate_local_services`
    before using managed service settings.
    """
    return f"http://{service['host']}:{int(service['port'])}"


def validate_local_services(config: dict) -> None:
    """Validate managed listeners, distinct ports, and the saved-connection URL.

    Ollama/API/WebUI hosts must be `127.0.0.1` or `localhost`; ports must be
    distinct integers in 1–65535. The OpenAI base URL must match the API endpoint
    plus `/v1`. Raises `BootError` on unsafe/inconsistent values. Performs no
    socket probes, firewall changes, or service launches.
    """
    ports = []
    for section in ("ollama", "rag_api", "open_webui"):
        service = config[section]
        if service["host"] not in {"127.0.0.1", "localhost"}:
            raise BootError(f"{section}.host must be 127.0.0.1 or localhost; external listeners are not supported.")
        port = service["port"]
        if type(port) is not int or not 1 <= port <= 65535:
            raise BootError(f"{section}.port must be a local service port between 1 and 65535.")
        ports.append(port)
    if len(set(ports)) != len(ports):
        raise BootError("Ollama, API, and WebUI need different local ports on this computer.")
    if chroma_port() in ports:
        raise BootError("RAG_CHROMA_PORT must differ from the Ollama, API, and WebUI ports.")
    if config["rag_api"]["openai_base_url"].rstrip("/") != service_url(config["rag_api"]) + "/v1":
        raise BootError("The WebUI connection URL must match the configured local RAG API URL plus /v1.")


def validate_webui_storage(config: dict, root: Path) -> None:
    """Reject inherited WebUI storage settings that conflict with local SQLite.

    Compares `DATA_DIR`/`DATABASE_*` environment settings with the configured
    WebUI database path. Raises `BootError` instead of silently redirecting,
    migrating, resetting, or contacting another application's database.
    """
    database = expand_path(config["rag_api"]["open_webui_database"], root)
    data_dir = os.getenv("DATA_DIR")
    if data_dir and expand_path(data_dir, root) != database.parent:
        raise BootError("DATA_DIR conflicts with the configured local WebUI database. Unset it or update bootup.json.")
    database_url = os.getenv("DATABASE_URL")
    if database_url and database_url.replace("\\", "/") != "sqlite:///" + database.as_posix():
        raise BootError("DATABASE_URL conflicts with the configured local SQLite database. Unset it before setup/startup.")
    if os.getenv("DATABASE_TYPE") not in {None, "", "sqlite"}:
        raise BootError("This stack uses local SQLite storage; unset inherited DATABASE_TYPE overrides.")
    if all(os.getenv(name) for name in ("DATABASE_TYPE", "DATABASE_USER", "DATABASE_HOST", "DATABASE_NAME")):
        raise BootError("Unset inherited DATABASE_* server settings; this stack uses the configured local SQLite file.")


@dataclass(frozen=True)
class StartupSettings:
    """Resolved project/model/log paths and timing settings for one managed launch.

    Created by `from_config` after local-listener and storage validation.
    Holds immutable settings only; constructing it does not start processes,
    probe ports, prepare an index, or mutate credentials.
    """
    root: Path
    models_path: Path
    models: ModelSettings
    logs: Path
    timeout: int
    interval: int
    repository_path: Path

    @classmethod
    def from_config(cls, config: dict, root: Path, *, repository: Path | None = None) -> "StartupSettings":
        """Validate a bootup mapping and resolve paths/models for this machine.

        `root` anchors relative service paths. An explicit `repository` wins
        over configured/environment source resolution. Model JSON is read
        directly, deliberately ignoring standalone model overrides. Positive
        startup/poll timing values are required. Raises `BootError` for unsafe
        service/storage settings and `ValueError` for invalid models/timing.
        """
        validate_local_services(config)
        validate_webui_storage(config, root)
        timeout = int(config.get("startup_timeout_seconds", 240))
        interval = int(config.get("poll_interval_seconds", 3))
        if timeout < 1 or interval < 1:
            raise ValueError("startup_timeout_seconds and poll_interval_seconds must be positive")
        models_path = expand_path(config.get("models_config", "config/models.json"), root)
        return cls(root=root, models_path=models_path, models=ModelSettings.from_file(models_path),
                   logs=expand_path(config["logs_directory"], root), timeout=timeout, interval=interval,
                   repository_path=repository.resolve() if repository is not None else configured_repository(config, root))
