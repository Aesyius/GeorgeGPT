"""Shared filesystem paths for the command-line tools."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "chroma_db"


def resolve_path(value: str | Path) -> Path:
    """Expand user/environment variables and resolve relative paths from the cwd."""
    return Path(os.path.expandvars(str(value))).expanduser().resolve()
