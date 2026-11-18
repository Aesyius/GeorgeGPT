"""Build portable SQLite file URIs for local read-only database access."""

from pathlib import Path


def read_only_sqlite_uri(path: Path) -> str:
    """Encode an absolute SQLite file URI, preserving literal path characters."""
    return f"{path.absolute().as_uri()}?mode=ro"
