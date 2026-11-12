"""Atomic SQLite FTS5 publication, with streaming document-to-row conversion."""

from collections.abc import Iterable
from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3
import tempfile

CREATE_SCHEMA = (
    "CREATE VIRTUAL TABLE chunks USING fts5("
    "id UNINDEXED, text, path UNINDEXED, start_line UNINDEXED, end_line UNINDEXED, "
    "heading UNINDEXED, revision UNINDEXED, repository UNINDEXED, "
    "tokenize='unicode61 remove_diacritics 2')"
)
INSERT_ROW = "INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)"


def bm25_path(db_path: Path, collection: str) -> Path:
    return db_path / f"{collection}.bm25.sqlite3"


def document_rows(documents: Iterable[dict], *, repository: str = "unknown", source_fallback: bool = True):
    """Do not build a second in-memory copy of a repository's document corpus."""
    for document in documents:
        metadata = document.get("metadata") or {}
        fallback = metadata.get("source", "unknown") if source_fallback else "unknown"
        yield (
            document["id"], document["text"], metadata.get("path", fallback),
            str(metadata.get("start_line", "?")), str(metadata.get("end_line", "?")),
            str(metadata.get("heading", "")), str(metadata.get("revision", "legacy")),
            str(metadata.get("repository", repository)),
        )


@contextmanager
def atomic_database(path: Path):
    """Publish only a committed, closed database; retain the old one on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(temporary)) as database:
            yield database
            database.commit()
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_bm25(path: Path, documents: Iterable[dict]) -> None:
    """Atomically rebuild the keyword corpus for a collection."""
    with atomic_database(path) as database:
        database.execute(CREATE_SCHEMA)
        database.executemany(INSERT_ROW, document_rows(documents))


def update_bm25(path: Path, repository: str, documents: Iterable[dict]) -> None:
    """Replace one repository, preserving other repositories without loading them."""
    with atomic_database(path) as database:
        if path.is_file():
            with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as source:
                source.backup(database)
        else:
            database.execute(CREATE_SCHEMA)
        database.execute("DELETE FROM chunks WHERE repository = ?", (repository,))
        database.executemany(INSERT_ROW, document_rows(documents, repository=repository, source_fallback=False))
