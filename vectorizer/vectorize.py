#!/usr/bin/env python3
"""Index a repository (or one UTF-8 text file) with safe hybrid RAG metadata."""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import os
from pathlib import Path

import chromadb

from .chunking import DEFAULT_EMBEDDING_BYTES, embedding_metadata
from .chunk_workers import MAX_CHUNK_WORKERS, validate_chunk_workers
from .embeddings import create_embeddings
from common.model_config import resolve_embedding_model
from common.ollama_runtime import ensure_ollama_running
from common.paths import DEFAULT_DB_PATH, resolve_path, configured_repository
from .chunking import chunk_file
from .keyword_index import bm25_path as _bm25_path, write_bm25 as _write_bm25
from .repository_index import index_repository, repository_chunk_id
from .index_catalog import select_index_collection, register_index
from common.progress import ProgressBar
from .vector_store import (collection_documents, synchronize_vectors,
                           collection_snapshot as _collection_snapshot, repository_ids as _repository_ids,
                           collection_embedding_model, update_collection_metadata)


def index_file(source: Path, db_path: Path, collection_name: str, embedding_model: str,
               ollama_url: str, chunk_size: int, overlap: int,
               max_embedding_bytes: int = DEFAULT_EMBEDDING_BYTES) -> int:
    """Index one UTF-8/BOM file and return its prepared chunk count.

    Takes an actual collection name and explicit model/chunking settings.
    Reuses compatible vectors for this source, embeds missing chunks in fixed
    batches of 32, removes stale source vectors, and rebuilds collection BM25
    rows. Does not register a repository catalog or start Ollama itself.

    Empty input or a collection owned by another embedder raises `ValueError`.
    Use a separate collection when changing single-file embedding models.
    Partial completed writes can remain if later work fails.
    """
    source = resolve_path(source)
    original = source.read_text(encoding="utf-8-sig")
    progress = ProgressBar()
    documents = chunk_file(source, source.name, chunk_size=chunk_size, overlap=overlap, text=original,
                           max_embedding_bytes=max_embedding_bytes, progress=progress)
    if not documents:
        raise ValueError("input file contains no text to index")
    source_id = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:20]
    content_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()
    prepared = []
    for index, chunk in enumerate(documents):
        ident = "file-" + repository_chunk_id(source_id, embedding_model, source.name, chunk_size, overlap,
                                             content_hash, index, max_embedding_bytes)
        metadata = {"repository": str(source.parent), "repository_id": source_id,
                    "revision": "working-tree", "path": source.name, "source": str(source),
                    "start_line": chunk["start_line"], "end_line": chunk["end_line"],
                    "chunk_index": index, "start_index": chunk["start_index"],
                    **embedding_metadata(chunk, embedding_model, max_embedding_bytes)}
        prepared.append({"id": ident, "text": chunk["text"], "metadata": metadata})
    with closing(chromadb.PersistentClient(path=str(db_path))) as client:
        collection = client.get_or_create_collection(name=collection_name, metadata={"hnsw:space": "cosine"})
        previous_model = collection_embedding_model(collection)
        if previous_model is not None and previous_model != embedding_model:
            raise ValueError(f"Collection {collection_name!r} uses {previous_model}, not {embedding_model}. "
                             "Use a separate --collection for single-file indexing with a different model.")
        update_collection_metadata(collection, {"index_ready": False, "managed_ready": False})
        embedder = create_embeddings(embedding_model, ollama_url)
        previous_ids, previous_metadata = _repository_ids(collection, source_id)
        synchronize_vectors(collection, prepared, previous_ids, previous_metadata, embedder,
                            embedding_model, 32, progress)
        progress.start("Updating keyword index", 1)
        _write_bm25(_bm25_path(db_path, collection_name), collection_documents(collection))
        progress.advance()
        progress.finish()
        update_collection_metadata(collection, {"embedding_model": embedding_model})
    return len(prepared)


def main() -> None:
    """Parse indexing CLI options and dispatch directory or single-file indexing.

    Resolves default repository/model settings, ensures Ollama is reachable
    (starting local HTTP Ollama when possible), and prints an indexing summary.
    Directory runs select/register model-specific catalog entries. Single-file
    runs use the requested collection directly. `--help` lists all options;
    handled validation/operational failures are reported through argparse.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=resolve_path, default=None,
                        help="repository directory or UTF-8 file (default: repository_path in config/bootup.json)")
    parser.add_argument("--db-path", type=resolve_path, default=resolve_path(os.getenv("RAG_DB_PATH", str(DEFAULT_DB_PATH))),
                        help="Chroma directory (default: chroma_db beside this script)")
    parser.add_argument("--collection", default=os.getenv("RAG_COLLECTION", "repo_documents"))
    parser.add_argument("--embedding-model", default=None,
                        help="override the embedding model selected by config/models.json (or RAG_MODEL_CONFIG)")
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--chunk-size", type=int, default=1400, help="maximum characters per structural chunk")
    parser.add_argument("--overlap", type=int, default=180, help="overlap for long sections")
    parser.add_argument("--batch-size", type=int, default=32, help="maximum chunks per Ollama embedding request")
    parser.add_argument("--chunk-workers", type=int, default=1,
                        help=f"file preparation processes for repositories (1-{MAX_CHUNK_WORKERS}; default: 1)")
    parser.add_argument("--max-embedding-bytes", type=int, default=DEFAULT_EMBEDDING_BYTES,
                        help="UTF-8 byte cap including source context (conservative token-size proxy)")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    try:
        validate_chunk_workers(args.chunk_workers)
        if args.path is None:
            args.path = configured_repository()
        if not (args.path.is_dir() or args.path.is_file()):
            parser.error(f"not a repository or file: {args.path}")
        args.embedding_model = resolve_embedding_model(args.embedding_model)
        print(f"Embedding model: {args.embedding_model}", flush=True)
        ensure_ollama_running(args.ollama_url)
        if args.path.is_dir():
            selected_collection = select_index_collection(args.db_path, args.collection, args.embedding_model)
            result = index_repository(args.path, db_path=args.db_path, collection_name=selected_collection,
                                      embedding_model=args.embedding_model, ollama_url=args.ollama_url,
                                       chunk_size=args.chunk_size, overlap=args.overlap, batch_size=args.batch_size,
                                        max_embedding_bytes=args.max_embedding_bytes, chunk_workers=args.chunk_workers)
            register_index(args.db_path, args.collection, selected_collection, args.embedding_model)
            print(f"Indexed {result['files']} files / {result['chunks']} chunks ({result['embedded_chunks']} newly embedded, "
                  f"{result['reused_chunks']} reused, {result['removed_chunks']} stale removed) from {result['repository']} "
                  f"(revision {result['revision']}) into '{result['collection']}'.")
            print(f"Keyword index: {result['keyword_index']}")
        elif args.path.is_file():
            count = index_file(args.path, args.db_path, args.collection, args.embedding_model,
                                args.ollama_url, args.chunk_size, args.overlap, args.max_embedding_bytes)
            print(f"Indexed {count} chunks from {args.path} into '{args.collection}'.")
        else:
            parser.error(f"not a repository or file: {args.path}")
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
