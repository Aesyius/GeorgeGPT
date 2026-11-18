"""Environment-backed defaults shared by standalone RAG entry points."""

import os
from pathlib import Path

from .paths import DEFAULT_DB_PATH, resolve_path

DEFAULT_COLLECTION = "repo_documents"
DEFAULT_OLLAMA_URL = "http://localhost:11434"


def index_db_path() -> Path:
    """Resolve the index directory, with relative overrides anchored to the cwd."""
    return resolve_path(os.getenv("RAG_DB_PATH", str(DEFAULT_DB_PATH)))


def index_collection() -> str:
    """Return the configured base collection name."""
    return os.getenv("RAG_COLLECTION", DEFAULT_COLLECTION)


def ollama_base_url() -> str:
    """Return the configured standalone embedding and chat endpoint."""
    return os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL)
