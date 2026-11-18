"""Atomic SQLite FTS5 publication, with streaming document-to-row conversion."""

from collections.abc import Iterable
from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3
import tempfile

from common.sqlite_paths import read_only_sqlite_uri
from common.chroma_endpoint import chroma_port

CREATE_SCHEMA = (
    "CREATE VIRTUAL TABLE chunks USING fts5("
    "id UNINDEXED, text, path UNINDEXED, start_line UNINDEXED, end_line UNINDEXED, "
    "heading UNINDEXED, revision UNINDEXED, repository UNINDEXED, "
    "tokenize='unicode61 remove_diacritics 2')"
)
INSERT_ROW = "INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)"


def bm25_path(db_path: Path, collection: str) -> Path:
    """Return the collection-specific SQLite FTS5 path without accessing disk."""
    return db_path / f"{collection}.http-{chroma_port()}.bm25.sqlite3"


def document_rows(documents: Iterable[dict], *, repository: str = "unknown", source_fallback: bool = True):
    """Yield SQLite insertion tuples from `{id, text, metadata}` documents.

    Converts location fields to strings and supplies legacy/default metadata.
    `repository` supplies missing ownership; `source_fallback` allows legacy
    source strings to substitute for paths. Consumes the iterable lazily.
    """
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
    """Yield a temporary SQLite connection and atomically publish it on success.

    Creates the parent directory and a sibling temporary database. On normal
    context exit, commits, closes, and replaces `path`. Exceptions leave the
    original destination in place and remove the temporary file.
    The caller is responsible for populating the schema/data.
    """
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
    """Replace a collection's entire keyword database from a document iterable.

    Creates the FTS5 schema and streams all records through `document_rows`.
    Uses `atomic_database`, so failed publication preserves the old destination.
    This replaces all repositories represented in that keyword database.
    """
    with atomic_database(path) as database:
        database.execute(CREATE_SCHEMA)
        database.executemany(INSERT_ROW, document_rows(documents))


def update_bm25(path: Path, repository: str, documents: Iterable[dict]) -> None:
    """Atomically replace one repository's keyword rows while retaining other sources.

    Copies an existing database via SQLite backup, removes rows matching the
    exact repository string, and inserts the supplied document iterable.
    A missing database is initialized with the FTS5 schema. No vectors are read
    or modified; failed publication retains the previous keyword file.
    """
    with atomic_database(path) as database:
        if path.is_file():
            with closing(sqlite3.connect(read_only_sqlite_uri(path), uri=True)) as source:
                source.backup(database)
        else:
            database.execute(CREATE_SCHEMA)
        database.execute("DELETE FROM chunks WHERE repository = ?", (repository,))
        database.executemany(INSERT_ROW, document_rows(documents, repository=repository, source_fallback=False))
