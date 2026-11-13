"""Project-root filesystem paths shared across all application packages."""

import json
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "chroma_db"
DEFAULT_BOOTUP_CONFIG = PROJECT_ROOT / "config" / "bootup.json"


def resolve_path(value: str | Path) -> Path:
    """Expand user/environment variables and resolve relative paths from the cwd."""
    return Path(os.path.expandvars(str(value))).expanduser().resolve()


def repository_path_from_config(config: dict, root: Path = PROJECT_ROOT) -> Path:
    """Resolve bootup's repository setting without depending on service modules."""
    value = config.get("repository_path")
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("bootup.json repository_path must be nonempty text without surrounding whitespace")
    path = Path(os.path.expandvars(value)).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def configured_repository(config: dict | None = None, root: Path = PROJECT_ROOT) -> Path:
    """Resolve repository defaults, optionally reusing startup's loaded config."""
    if os.getenv("RAG_REPOSITORY_PATH"):
        return resolve_path(os.environ["RAG_REPOSITORY_PATH"])
    if config is not None:
        return repository_path_from_config(config, root)
    config_path = resolve_path(os.getenv("RAG_BOOTUP_CONFIG", str(DEFAULT_BOOTUP_CONFIG)))
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read repository configuration {config_path}: {error}") from error
    if not isinstance(config, dict):
        raise ValueError(f"Repository configuration {config_path} must be a JSON object")
    return repository_path_from_config(config)
