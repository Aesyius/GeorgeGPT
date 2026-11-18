#!/usr/bin/env python3
"""Read-only integrity audit for a repository's Chroma vectors and BM25 index."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import sqlite3
import sys

import chromadb

from .chunking import CHUNKING_VERSION, EMBEDDING_INPUT_VERSION, DEFAULT_EMBEDDING_BYTES, chunk_file
from .keyword_index import bm25_path as _bm25_path
from .index_catalog import matching_index
from .source_files import discover_files
from .vector_store import collection_snapshot as _collection_snapshot
from common.model_config import resolve_embedding_model
from common.paths import DEFAULT_DB_PATH, resolve_path, configured_repository
from common.progress import ProgressBar
from .repository_index import (
    _hash,
    repository_chunk_id,
)

KNOWN_VECTOR_DIMENSIONS = {"qwen3-embedding:0.6b": 1024, "qwen3-embedding:4b": 2560,
                          "qwen3-embedding:8b": 4096}


def verify_repository_index(repository: str | Path | None = None, *,
                            db_path: str | Path = DEFAULT_DB_PATH,
                            collection_name: str = "repo_documents",
                            embedding_model: str | None = None,
                            chunk_size: int = 1400, overlap: int = 180,
                            page_size: int = 256,
                            max_embedding_bytes: int = DEFAULT_EMBEDDING_BYTES) -> dict:
    """Audit current repository chunks against persisted vectors and keyword rows.

    Uses the same model, chunk size, overlap, and embedding byte cap as indexing;
    callers must supply matching settings. `collection_name` is resolved through
    the model catalog. `page_size` bounds vector reads and must be positive.

    Returns a report with `ok`, source/stored counts, vector dimensions,
    inconsistency counts, skipped files, and bounded diagnostic ID samples.
    Checks metadata, finite vector values, dimension consistency, and FTS IDs.
    Known embedding tags also have an expected vector dimension.

    Reads source files and scans stored vector pages; never calls Ollama or
    repairs the corpus. Run explicitly for maintenance, not routine startup.
    Configuration, filesystem, storage, and decoding errors may propagate.
    """
    embedding_model = resolve_embedding_model(embedding_model)
    root = configured_repository() if repository is None else resolve_path(repository)
    db_dir = resolve_path(db_path)
    if not root.is_dir():
        raise ValueError(f"repository is not a directory: {root}")
    if page_size < 1:
        raise ValueError("page size must be positive")
    collection_name = matching_index(db_dir, collection_name, embedding_model)

    repo_key = _hash(str(root).casefold())[:20]
    files = discover_files(root)
    expected: dict[str, tuple[str, int, int]] = {}
    scan = ProgressBar()
    scan.start("Recomputing expected source chunks", len(files))
    skipped: list[str] = []
    for file in files:
        relative = file.relative_to(root).as_posix()
        try:
            text = file.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            skipped.append(relative)
            scan.advance()
            continue
        content_hash = _hash(text)
        chunks = chunk_file(file, relative, chunk_size=chunk_size, overlap=overlap, text=text,
                            max_embedding_bytes=max_embedding_bytes)
        for index, chunk in enumerate(chunks):
            ident = repository_chunk_id(repo_key, embedding_model, relative, chunk_size,
                                        overlap, content_hash, index, max_embedding_bytes)
            expected[ident] = (relative, chunk["start_line"], chunk["end_line"])
        scan.advance()
    scan.finish()

    client = chromadb.PersistentClient(path=str(db_dir))
    collection = client.get_collection(name=collection_name)
    actual: set[str] = set()
    bad_metadata: list[str] = []
    invalid_vectors: list[str] = []
    dimensions: set[int] = set()
    audit = ProgressBar()
    total_records = collection.count()
    audit.start("Auditing stored vectors", total_records)
    for page in _collection_snapshot(collection, page_size, include=["metadatas", "embeddings"]):
        for ident, metadata, vector in zip(page.get("ids", []), page.get("metadatas", []), page.get("embeddings", [])):
            metadata = metadata or {}
            if metadata.get("repository_id") != repo_key:
                continue
            actual.add(ident)
            expected_location = expected.get(ident)
            if (metadata.get("embedding_model") != embedding_model or expected_location is None
                    or metadata.get("chunking_version") != CHUNKING_VERSION
                    or metadata.get("embedding_input_version") != EMBEDDING_INPUT_VERSION
                    or metadata.get("max_embedding_bytes") != max_embedding_bytes
                    or metadata.get("path") != expected_location[0]
                    or metadata.get("start_line") != expected_location[1]
                    or metadata.get("end_line") != expected_location[2]):
                bad_metadata.append(ident)
            try:
                dimension = len(vector)
                dimensions.add(dimension)
                if dimension == 0 or not all(math.isfinite(float(value)) for value in vector):
                    invalid_vectors.append(ident)
            except (TypeError, ValueError):
                invalid_vectors.append(ident)
        audit.advance(len(page.get("ids", [])))
    audit.finish()

    missing = expected.keys() - actual
    unexpected = actual - expected.keys()
    bm25_path = _bm25_path(db_dir, collection_name)
    bm25_ids: set[str] = set()
    if bm25_path.is_file():
        db = sqlite3.connect(f"{bm25_path.as_uri()}?mode=ro", uri=True)
        try:
            bm25_ids = {row[0] for row in db.execute("SELECT id FROM chunks WHERE repository = ?", (str(root),))}
        finally:
            db.close()
    missing_bm25 = expected.keys() - bm25_ids
    extra_bm25 = bm25_ids - expected.keys()

    result = {
        "repository": str(root), "collection": collection_name,
        "embedding_model": embedding_model, "files": len(files), "skipped_files": skipped,
        "expected_chunks": len(expected), "stored_chunks": len(actual),
        "missing_vectors": len(missing), "unexpected_vectors": len(unexpected),
        "bad_metadata": len(bad_metadata), "invalid_vectors": len(invalid_vectors),
        "vector_dimensions": sorted(dimensions), "bm25_rows": len(bm25_ids),
        "missing_bm25_rows": len(missing_bm25), "unexpected_bm25_rows": len(extra_bm25),
        "ok": not (skipped or missing or unexpected or bad_metadata or invalid_vectors
                   or missing_bm25 or extra_bm25 or len(dimensions) != 1
                    or (embedding_model.lower() in KNOWN_VECTOR_DIMENSIONS
                        and dimensions != {KNOWN_VECTOR_DIMENSIONS[embedding_model.lower()]})),
        "details": {
            "skipped_files": skipped[:10], "missing_vector_ids": list(missing)[:10],
            "unexpected_vector_ids": list(unexpected)[:10], "bad_metadata_ids": bad_metadata[:10],
            "invalid_vector_ids": invalid_vectors[:10],
            "missing_bm25_ids": list(missing_bm25)[:10], "unexpected_bm25_ids": list(extra_bm25)[:10],
        },
    }
    client.close()
    return result


def main() -> None:
    """Run the audit CLI, print its report, and exit nonzero on audit failures.

    Accepts repository/index/chunking overrides; `--help` lists all flags.
    A failed report or skipped source files exits with status 1. Argument and
    handled operational errors are reported through argparse.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=resolve_path, default=None,
                        help="repository directory (default: configured repository_path)")
    parser.add_argument("--db-path", type=resolve_path, default=resolve_path(os.getenv("RAG_DB_PATH", str(DEFAULT_DB_PATH))))
    parser.add_argument("--collection", default=os.getenv("RAG_COLLECTION", "repo_documents"))
    parser.add_argument("--embedding-model", default=None, help="override the configured embedding model")
    parser.add_argument("--chunk-size", type=int, default=1400)
    parser.add_argument("--overlap", type=int, default=180)
    parser.add_argument("--max-embedding-bytes", type=int, default=DEFAULT_EMBEDDING_BYTES)
    args = parser.parse_args()
    try:
        args.embedding_model = resolve_embedding_model(args.embedding_model)
        result = verify_repository_index(args.path, db_path=args.db_path,
            collection_name=args.collection, embedding_model=args.embedding_model,
            chunk_size=args.chunk_size, overlap=args.overlap, max_embedding_bytes=args.max_embedding_bytes)
    except (OSError, UnicodeError, ValueError, RuntimeError, sqlite3.Error) as error:
        parser.error(str(error))
    print(f"Verification: {'PASS' if result['ok'] else 'FAIL'}")
    for key in ("repository", "collection", "embedding_model", "files", "expected_chunks",
                "stored_chunks", "missing_vectors", "unexpected_vectors", "bad_metadata",
                "invalid_vectors", "vector_dimensions", "bm25_rows", "missing_bm25_rows",
                "unexpected_bm25_rows"):
        print(f"{key}: {result[key]}")
    if result["skipped_files"] or not result["ok"]:
        print(f"details: {result['details']}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
