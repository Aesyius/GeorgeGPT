"""Project-root filesystem paths shared across all application packages."""

import json
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "chroma_db"
DEFAULT_BOOTUP_CONFIG = PROJECT_ROOT / "config" / "bootup.json"
LOCAL_BOOTUP_CONFIG = DEFAULT_BOOTUP_CONFIG.with_name("bootup.local.json")


def default_bootup_config() -> Path:
    """Prefer the machine-local configuration over the tracked defaults."""
    return LOCAL_BOOTUP_CONFIG if LOCAL_BOOTUP_CONFIG.is_file() else DEFAULT_BOOTUP_CONFIG


def resolve_path(value: str | Path) -> Path:
    """Expand user/environment variables and return an absolute normalized path.

    Relative values use the current working directory, not the project root.
    Resolution does not require the destination to exist and creates nothing.
    """
    return Path(os.path.expandvars(str(value))).expanduser().resolve()


def repository_path_from_config(config: dict, root: Path = PROJECT_ROOT) -> Path:
    """Resolve `repository_path` from a supplied bootup mapping.

    Relative configured paths use `root`, unlike explicit CLI paths. Expands
    environment variables and the user home directory. Missing, empty, or
    whitespace-padded values raise `ValueError`; existence is checked by callers.
    """
    value = config.get("repository_path")
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("bootup.json repository_path must be nonempty text without surrounding whitespace")
    path = Path(os.path.expandvars(value)).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def configured_repository(config: dict | None = None, root: Path = PROJECT_ROOT) -> Path:
    """Choose the repository from an override, supplied mapping, or bootup JSON.

    `RAG_REPOSITORY_PATH` wins first. Otherwise use `config` when supplied, or
    read the file selected by `RAG_BOOTUP_CONFIG`. Configured relative repository
    paths use `root`; explicit environment paths use the current directory.
    Malformed or unreadable configuration raises `ValueError`.
    """
    if os.getenv("RAG_REPOSITORY_PATH"):
        return resolve_path(os.environ["RAG_REPOSITORY_PATH"])
    if config is not None:
        return repository_path_from_config(config, root)
    config_path = resolve_path(os.getenv("RAG_BOOTUP_CONFIG") or default_bootup_config())
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read repository configuration {config_path}: {error}") from error
    if not isinstance(config, dict):
        raise ValueError(f"Repository configuration {config_path} must be a JSON object")
    return repository_path_from_config(config)
