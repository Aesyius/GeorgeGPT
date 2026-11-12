"""Resolved startup settings; no process, network, or database side effects."""

from dataclasses import dataclass
import os
from pathlib import Path

from .model_config import ModelSettings


class BootError(RuntimeError):
    """A safe startup condition could not be met."""


def expand_path(value: str, root: Path) -> Path:
    path = Path(os.path.expandvars(value))
    return (path if path.is_absolute() else root / path).resolve()


def service_url(service: dict) -> str:
    return f"http://{service['host']}:{int(service['port'])}"


@dataclass(frozen=True)
class StartupSettings:
    root: Path
    models_path: Path
    models: ModelSettings
    logs: Path
    timeout: int
    interval: int

    @classmethod
    def from_config(cls, config: dict, root: Path) -> "StartupSettings":
        timeout = int(config.get("startup_timeout_seconds", 240))
        interval = int(config.get("poll_interval_seconds", 3))
        if timeout < 1 or interval < 1:
            raise ValueError("startup_timeout_seconds and poll_interval_seconds must be positive")
        models_path = expand_path(config.get("models_config", "config/models.json"), root)
        return cls(root, models_path, ModelSettings.from_file(models_path),
                   expand_path(config["logs_directory"], root), timeout, interval)
