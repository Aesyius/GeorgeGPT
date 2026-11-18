"""Check first-time indexing prerequisites without opening the existing index."""

import argparse
from contextlib import closing
import math
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory


def _check_sources(repository: Path) -> str:
    from vectorizer.source_files import discover_files

    files = discover_files(repository)
    if not files:
        raise ValueError(f"No eligible source files found under {repository}. Check the source filters and repository path.")
    nonempty = False
    failures = []
    for path in files:
        try:
            if path.read_text(encoding="utf-8-sig").strip():
                nonempty = True
        except (OSError, UnicodeError) as error:
            failures.append(f"{path.relative_to(repository)}: {error}")
            if len(failures) == 5:
                break
    if failures:
        raise ValueError("Check permissions or convert files to UTF-8. First unreadable files:\n  "
                         + "\n  ".join(failures))
    if not nonempty:
        raise ValueError("All eligible source files are empty or whitespace-only.")
    return f"{len(files)} eligible files are readable as UTF-8."


def _check_storage(db_path: Path, collection: str) -> str:
    import chromadb
    from vectorizer.keyword_index import CREATE_SCHEMA

    parent = db_path
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.is_dir():
        raise ValueError(f"{parent} is not a directory. Check RAG_DB_PATH.")
    try:
        with TemporaryDirectory(prefix="georgegpt-preflight-", dir=parent) as temp:
            root = Path(temp)
            with closing(chromadb.PersistentClient(path=str(root / "chroma"))) as client:
                probe = client.create_collection(name=collection)
                probe.add(ids=["preflight"], embeddings=[[1.0, 0.0]])
            with closing(sqlite3.connect(root / "keyword.sqlite3")) as db:
                db.execute(CREATE_SCHEMA)
                db.commit()
    except Exception as error:
        raise RuntimeError(f"Cannot prepare a test index for {db_path} (collection {collection!r}). "
                           f"Check the path, permissions, free space, and RAG_COLLECTION: {error}") from error
    return f"Temporary Chroma and SQLite FTS5 storage passed for {db_path}."


def _check_embedding(model: str, url: str, timeout: int) -> str:
    from vectorizer.embeddings import create_embeddings, embed_documents_with_retry

    embedder = create_embeddings(model, url, timeout_seconds=timeout)
    vectors = embed_documents_with_retry(embedder, ["GeorgeGPT indexing preflight."], attempts=1)
    if len(vectors) != 1 or not vectors[0] or not all(math.isfinite(value) for value in vectors[0]):
        raise ValueError("Ollama did not return one finite, nonempty embedding.")
    return f"{model} returned a {len(vectors[0])}-dimensional embedding."


def main() -> int:
    """Print each setup probe result and return nonzero if any check fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("db_path", type=Path)
    parser.add_argument("collection")
    parser.add_argument("embedding_model")
    parser.add_argument("ollama_url")
    parser.add_argument("timeout", type=int)
    args = parser.parse_args()
    checks = (
        ("Repository", lambda: _check_sources(args.repository.resolve())),
        ("Index storage", lambda: _check_storage(args.db_path.resolve(), args.collection)),
        (f"Ollama embedding at {args.ollama_url}",
         lambda: _check_embedding(args.embedding_model, args.ollama_url, args.timeout)),
    )
    failures = 0
    for label, check in checks:
        print(f"[PREFLIGHT] Checking {label}...", flush=True)
        try:
            detail = check()
        except Exception as error:
            failures += 1
            print(f"[PREFLIGHT ERROR] {label}: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        else:
            print(f"[PREFLIGHT OK] {label}: {detail}", flush=True)
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
