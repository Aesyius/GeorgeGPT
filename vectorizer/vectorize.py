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
from .embeddings import create_embeddings
from .model_config import DEFAULT_EMBEDDING_MODEL
from .paths import DEFAULT_DB_PATH, resolve_path
from .chunking import chunk_file
from .keyword_index import bm25_path as _bm25_path, write_bm25 as _write_bm25
from .repository_index import DEFAULT_REPOSITORY, index_repository, repository_chunk_id
from .progress import ProgressBar
from .vector_store import (collection_documents, synchronize_vectors,
                           collection_snapshot as _collection_snapshot, repository_ids as _repository_ids)


def index_file(source: Path, db_path: Path, collection_name: str, embedding_model: str,
               ollama_url: str, chunk_size: int, overlap: int,
               max_embedding_bytes: int = DEFAULT_EMBEDDING_BYTES) -> int:
    """Retain the original single-text-file workflow, with source citations/BM25."""
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
        embedder = create_embeddings(embedding_model, ollama_url)
        previous_ids, previous_metadata = _repository_ids(collection, source_id)
        synchronize_vectors(collection, prepared, previous_ids, previous_metadata, embedder,
                            embedding_model, 32, progress)
        progress.start("Updating keyword index", 1)
        _write_bm25(_bm25_path(db_path, collection_name), collection_documents(collection))
        progress.advance()
        progress.finish()
    return len(prepared)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=resolve_path, default=DEFAULT_REPOSITORY,
                        help="repository directory to index (default: configured FAK path), or a UTF-8 text file")
    parser.add_argument("--db-path", type=resolve_path, default=DEFAULT_DB_PATH,
                        help="Chroma directory (default: chroma_db beside this script)")
    parser.add_argument("--collection", default="repo_documents")
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--chunk-size", type=int, default=1400, help="maximum characters per structural chunk")
    parser.add_argument("--overlap", type=int, default=180, help="overlap for long sections")
    parser.add_argument("--batch-size", type=int, default=32, help="maximum chunks per Ollama embedding request")
    parser.add_argument("--max-embedding-bytes", type=int, default=DEFAULT_EMBEDDING_BYTES,
                        help="UTF-8 byte cap including source context (conservative token-size proxy)")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    try:
        if args.path.is_dir():
            result = index_repository(args.path, db_path=args.db_path, collection_name=args.collection,
                                      embedding_model=args.embedding_model, ollama_url=args.ollama_url,
                                      chunk_size=args.chunk_size, overlap=args.overlap, batch_size=args.batch_size,
                                      max_embedding_bytes=args.max_embedding_bytes)
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
