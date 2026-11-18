"""Safe repository discovery and incremental indexing into Chroma + a local BM25 corpus."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from pathlib import Path
import subprocess

import chromadb

from .embeddings import create_embeddings
from .chunking import (CHUNKING_VERSION, EMBEDDING_INPUT_VERSION, DEFAULT_EMBEDDING_BYTES,
                       chunk_file, embedding_metadata)
from .keyword_index import bm25_path as _bm25_path, write_bm25 as _write_bm25, update_bm25 as _update_bm25
from .index_catalog import catalog_publication, manifest_mapping
from common.model_config import resolve_embedding_model
from common.paths import DEFAULT_DB_PATH, resolve_path, configured_repository
# Re-export the established discovery/storage helper imports for compatibility.
from .source_files import (IGNORED_DIRS, SECRET_EXTENSIONS, SECRET_NAME, TEXT_EXTENSIONS,
                           discover_files, is_indexable)
from .vector_store import (collection_snapshot as _collection_snapshot,
                           repository_ids as _repository_ids, synchronize_vectors, update_collection_metadata,
                           collection_embedding_model, source_generation)

def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def repository_chunk_id(repo_key: str, embedding_model: str, relative: str,
                        chunk_size: int, overlap: int, content_hash: str, index: int,
                        max_embedding_bytes: int = DEFAULT_EMBEDDING_BYTES) -> str:
    """Build a deterministic chunk ID for indexing and integrity comparison.

    Combines repository identity, embedding model, relative path, source content
    hash, chunk index, and chunking/embedding-input settings. Changing any of
    those inputs can invalidate reuse. This is a pure naming operation; it
    neither reads files nor creates vectors.
    """
    model_key = _hash(embedding_model)[:8]
    chunking_key = f"{CHUNKING_VERSION}:{EMBEDDING_INPUT_VERSION}:{chunk_size}:{overlap}:{max_embedding_bytes}"
    path_key = _hash(relative + chr(0) + chunking_key)[:12]
    return f"{repo_key}-{model_key}-{path_key}-{content_hash[:24]}-{index}"


def _revision(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "working-tree"


def index_repository(
    repository: str | Path | None = None,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    collection_name: str = "repo_documents",
    embedding_model: str | None = None,
    ollama_url: str | None = None,
    chunk_size: int = 1400,
    overlap: int = 180,
    batch_size: int = 32,
    max_embedding_bytes: int = DEFAULT_EMBEDDING_BYTES,
    progress=None,
) -> dict:
    """Incrementally synchronize one repository into Chroma and SQLite FTS5.

    Args:
        repository: Source directory, or the configured repository when omitted.
        db_path: Persistent database directory; explicit relative paths use cwd.
        collection_name: Actual destination collection, not a model-catalog lookup.
        embedding_model: Ollama embedding tag; omitted values use shared settings.
        ollama_url: Optional embedding endpoint override.
        chunk_size: Structural chunk character cap.
        overlap: Character overlap for long sections.
        batch_size: Positive maximum chunks per embedding request.
        max_embedding_bytes: Full embedding input byte cap, including context.
        progress: Optional progress reporter; defaults to `common.progress.ProgressBar`.

    Returns:
        Repository/revision, file/chunk counts, embedded/reused/removed counts,
        selected collection, and keyword-index path.

    Discovers eligible files, skips unreadable/undecodable text, reuses compatible
    vectors, embeds changed chunks, removes stale source chunks, and replaces
    this repository's keyword rows. Readiness metadata is published after both
    stores succeed. This is not a cross-store transaction: failed runs may retain
    completed batches for retry. Existing incompatible-model vectors are refused.
    Does not start Ollama, pull models, or register a base catalog on its own.
    """
    embedding_model = resolve_embedding_model(embedding_model)
    root = configured_repository() if repository is None else resolve_path(repository)
    db_dir = resolve_path(db_path)
    if not root.is_dir():
        raise ValueError(f"repository is not a directory: {root}")
    if batch_size < 1:
        raise ValueError("embedding batch size must be positive")
    files = discover_files(root)
    if not files:
        raise ValueError(f"no eligible source files found under {root}")
    if progress is None:
        from common.progress import ProgressBar
        progress = ProgressBar()
    revision = _revision(root)
    repo_key = _hash(str(root).casefold())[:20]
    prepared: list[dict] = []
    source_hashes = []
    progress.start("Scanning/chunking files", len(files))
    for file in files:
        relative = file.relative_to(root).as_posix()
        try:
            text = file.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            progress.advance()
            continue
        content_hash = _hash(text)
        source_hashes.append((relative, content_hash))
        for idx, chunk in enumerate(chunk_file(file, relative, chunk_size=chunk_size, overlap=overlap, text=text,
                                             max_embedding_bytes=max_embedding_bytes)):
            metadata = {
                "repository": str(root), "repository_id": repo_key, "revision": revision,
                "path": relative, "start_line": chunk["start_line"], "end_line": chunk["end_line"],
                "source": f"{root / relative}:{chunk['start_line']}-{chunk['end_line']}",
                **embedding_metadata(chunk, embedding_model, max_embedding_bytes),
            }
            doc_id = repository_chunk_id(repo_key, embedding_model, relative, chunk_size, overlap, content_hash, idx,
                                         max_embedding_bytes)
            prepared.append({"id": doc_id, "text": chunk["text"], "metadata": metadata})
        progress.advance()
    progress.finish()

    keyword_path = _bm25_path(db_dir, collection_name)
    with closing(chromadb.PersistentClient(path=str(db_dir))) as client:
        collection = client.get_or_create_collection(name=collection_name, metadata={"hnsw:space": "cosine"})
        previous_model = collection_embedding_model(collection)
        if previous_model is not None and previous_model != embedding_model:
            raise ValueError(f"Collection {collection_name!r} uses {previous_model}, not {embedding_model}. "
                             "Select a matching model-specific collection; existing vectors were not modified.")
        update_collection_metadata(collection, {"index_ready": False, "managed_ready": False})
        embedder = create_embeddings(embedding_model, ollama_url)
        old_ids, old_metadata = _repository_ids(collection, repo_key)
        result = synchronize_vectors(collection, prepared, old_ids, old_metadata, embedder,
                                     embedding_model, batch_size, progress)
        progress.start("Updating keyword index", 1)
        _update_bm25(keyword_path, str(root), prepared)
        progress.advance()
        progress.finish()
        # Small collection-level manifest: startup must not scan chunk metadata
        # to discover which repositories need embedding with a different model.
        metadata = dict(collection.metadata or {})
        sources = manifest_mapping(metadata, "index_sources")
        sources[str(root)] = {"chunk_size": chunk_size, "overlap": overlap,
                              "max_embedding_bytes": max_embedding_bytes}
        versions = manifest_mapping(metadata, "source_versions")
        versions[str(root)] = _hash(json.dumps([CHUNKING_VERSION, EMBEDDING_INPUT_VERSION, source_hashes]))
        generation = source_generation(sources, versions)
        publication = {"embedding_model": embedding_model, "index_ready": True,
                       "index_sources": json.dumps(sources, sort_keys=True), "index_generation": generation,
                       "source_versions": json.dumps(versions, sort_keys=True)}
        if "catalog_sources" in metadata:
            publication.update(catalog_publication(metadata, sources, versions))
        update_collection_metadata(collection, publication)
    return {"repository": str(root), "revision": revision, "files": len(files),
            "chunks": len(prepared), **result.to_dict(), "collection": collection_name,
            "keyword_index": str(keyword_path)}
