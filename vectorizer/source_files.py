"""Conservative repository discovery, independent of vector and keyword storage."""

import os
from pathlib import Path
import re

from common.paths import resolve_path

TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".rst", ".py", ".sh", ".bash", ".nix",
    ".yaml", ".yml", ".toml", ".json", ".lock", ".ini", ".cfg", ".conf",
    ".service", ".target", ".mk", ".make", ".xml", ".dockerfile", ".tf",
    ".hcl", ".sql", ".ps1", ".bat", ".cmd", ".env.example",
}
IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", ".venv-webui", "venv", "env", "node_modules", "vendor",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist",
    "build", "target", "chroma_db", "site-packages", ".idea", ".vscode",
}
SECRET_NAME = re.compile(
    r"(^|[._-])(\.env|secrets?|credentials?|id_rsa|id_ed25519|known_hosts|authorized_keys)([._-]|$)",
    re.IGNORECASE,
)
SECRET_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".kdbx", ".sqlite", ".db"}
MAX_SOURCE_BYTES = 2_000_000


def is_indexable(path: Path, root: Path) -> bool:
    """Check filename/type policy for a path under a repository root.

    Rejects known generated directories, actual environment files, common
    credential names/extensions, and unsupported formats. Example environment
    templates are permitted. This helper does not read contents, check size, or
    rule out every possible secret; `discover_files` adds filesystem safeguards.
    A path outside `root` raises `ValueError`.
    """
    relative = path.relative_to(root)
    if any(part.lower() in IGNORED_DIRS for part in relative.parts):
        return False
    name = path.name.lower()
    if name == ".env.example" or name.endswith(".env.example"):
        return True
    if name == ".env" or name.endswith(".env") or name.endswith(".secret"):
        return False
    if SECRET_NAME.search(name) or path.suffix.lower() in SECRET_EXTENSIONS:
        return False
    if path.name.startswith(".") and name not in {".gitignore", ".dockerignore", ".gitattributes", ".editorconfig"}:
        return False
    return path.suffix.lower() in TEXT_EXTENSIONS or name in {"makefile", "dockerfile", "justfile"}


def discover_files(root: str | Path) -> list[Path]:
    """Return eligible source paths in deterministic directory/filename order.

    Resolves `root`, prunes ignored directories and symlinks, applies
    `is_indexable`, and skips files over `MAX_SOURCE_BYTES`. Traversal or stat
    errors propagate to prevent an incomplete scan from deleting indexed chunks.
    Does not open file contents or use Git ignore rules.
    A nonexistent/non-directory root raises `ValueError`.
    """
    root = resolve_path(root)
    if not root.is_dir():
        raise ValueError(f"repository is not a directory: {root}")

    def raise_walk_error(error: OSError) -> None:
        raise error

    found = []
    for directory, dirs, files in os.walk(root, onerror=raise_walk_error):
        current = Path(directory)
        dirs[:] = sorted(name for name in dirs if name.lower() not in IGNORED_DIRS and not (current / name).is_symlink())
        for filename in sorted(files):
            path = current / filename
            if is_indexable(path, root) and not path.is_symlink() and path.stat().st_size <= MAX_SOURCE_BYTES:
                found.append(path)
    return found
