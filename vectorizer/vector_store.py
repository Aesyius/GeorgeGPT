"""Paginated Chroma access and shared incremental vector publication."""

from dataclasses import dataclass
import hashlib
import json
import sys

from .chunking import CHUNKING_VERSION, EMBEDDING_INPUT_VERSION, embedding_text
from .embeddings import embed_documents_with_retry

WRITE_BATCH_SIZE = 500


def source_generation(sources: dict, versions: dict) -> str:
    """Model-independent source identity; changing only models must not invalidate indexes."""
    payload = json.dumps({"sources": sources, "versions": versions}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def update_collection_metadata(collection, values: dict) -> None:
    """Merge bookkeeping without resubmitting immutable legacy HNSW settings."""
    metadata = {key: value for key, value in (collection.metadata or {}).items()
                if not key.startswith("hnsw:")}
    collection.modify(metadata={**metadata, **values})


def collection_embedding_model(collection) -> str | None:
    """Read ownership, with one bounded legacy sample; never scan all chunks."""
    model = (collection.metadata or {}).get("embedding_model")
    if model:
        return model
    if not collection.count():
        return None
    sample = collection.get(limit=1, include=["metadatas"])
    return next((item.get("embedding_model") for item in sample.get("metadatas", []) if item), None)


def batches(items: list, size: int):
    """Yield ordered list slices of at most `size` elements without mutating input.

    Empty input yields no batches. A nonpositive size raises `ValueError`.
    """
    if size < 1:
        raise ValueError("batch size must be positive")
    for start in range(0, len(items), size):
        yield items[start:start + size]


def collection_snapshot(collection, page_size: int = 256, include: list[str] | None = None, *, where: dict | None = None):
    """Yield Chroma result pages using offset/limit pagination.

    `include` defaults to documents and metadata; `where` is passed to Chroma
    for database-side filtering. Each yielded value is a Chroma result mapping,
    including a possibly empty terminal page. Requires a positive `page_size`.
    This is a paginated traversal, not a transactionally frozen snapshot.
    """
    if page_size < 1:
        raise ValueError("page size must be positive")
    offset = 0
    options = {"include": include or ["documents", "metadatas"]}
    if where is not None:
        options["where"] = where
    while True:
        page = collection.get(limit=page_size, offset=offset, **options)
        yield page
        count = len(page.get("ids", []))
        offset += count
        if count < page_size:
            break


def repository_ids(collection, repository_id: str, page_size: int = 256) -> tuple[set[str], dict[str, dict]]:
    """Return a source's stored ID set and metadata-by-ID mapping.

    Filters by `repository_id` in Chroma and checks ownership again while
    consuming bounded pages. Does not fetch vectors/documents or modify storage.
    """
    metadata = {}
    for page in collection_snapshot(collection, page_size, include=["metadatas"], where={"repository_id": repository_id}):
        for ident, item in zip(page.get("ids", []), page.get("metadatas", [])):
            if (item or {}).get("repository_id") == repository_id:
                metadata[ident] = item
    return set(metadata), metadata


def collection_documents(collection):
    """Stream `{id, text, metadata}` records from every collection page.

    Used to rebuild keyword storage without constructing a second corpus list.
    Stored embeddings are not fetched.
    """
    for page in collection_snapshot(collection):
        for ident, text, metadata in zip(page["ids"], page["documents"], page["metadatas"]):
            yield {"id": ident, "text": text, "metadata": metadata}


@dataclass(frozen=True)
class SyncResult:
    """Immutable counts of newly embedded, reused, and removed source chunks."""

    embedded_chunks: int
    reused_chunks: int
    removed_chunks: int

    def to_dict(self) -> dict:
        """Return the three synchronization counts under their public field names."""
        return {"embedded_chunks": self.embedded_chunks, "reused_chunks": self.reused_chunks,
                "removed_chunks": self.removed_chunks}


def synchronize_vectors(collection, documents: list[dict], old_ids: set[str], old_metadata: dict,
                        embedder, embedding_model: str, batch_size: int, progress) -> SyncResult:
    """Refresh reusable metadata, embed missing chunks, then delete stale vectors.

    `documents` contains prepared `{id, text, metadata}` records; `old_ids` and
    `old_metadata` must describe the same source. Reuse requires matching model
    and chunking/input versions. New embedding requests use the positive
    `batch_size`; metadata updates and deletions use `WRITE_BATCH_SIZE`.

    Returns a `SyncResult` and reports each phase through `progress`.
    A failed embedding/write leaves earlier batches intact and prevents reaching
    stale deletion. Does not update BM25, readiness manifests, or catalog entries;
    those are the indexing orchestrator's responsibility.
    """
    if batch_size < 1:
        raise ValueError("embedding batch size must be positive")
    by_id = {document["id"]: document for document in documents}
    expected = {"embedding_model": embedding_model, "chunking_version": CHUNKING_VERSION,
                "embedding_input_version": EMBEDDING_INPUT_VERSION}
    reusable_ids = {ident for ident in old_ids.intersection(by_id)
                    if all(old_metadata[ident].get(key) == value for key, value in expected.items())}
    reusable = [by_id[ident] for ident in reusable_ids]
    progress.start("Refreshing metadata", len(reusable))
    for batch in batches(reusable, WRITE_BATCH_SIZE):
        collection.update(ids=[item["id"] for item in batch], metadatas=[item["metadata"] for item in batch])
        progress.advance(len(batch))
    progress.finish()

    missing = [document for document in documents if document["id"] not in reusable_ids]
    progress.start("Embedding and writing chunks", len(missing))
    batch_total = (len(missing) + batch_size - 1) // batch_size
    for number, batch in enumerate(batches(missing, batch_size), start=1):
        texts = [embedding_text(item["text"], item["metadata"]) for item in batch]
        label = f"batch {number}/{batch_total} ({len(batch)} chunks)"
        if number == 1:
            prefix = "\n" if sys.stderr.isatty() else ""
            print(f"{prefix}Requesting Ollama embeddings for {label} with model {embedding_model!r}...",
                  file=sys.stderr, flush=True)
        try:
            vectors = embed_documents_with_retry(embedder, texts)
        except RuntimeError as error:
            raise RuntimeError(f"Embedding {label} with model {embedding_model!r} failed: {error}") from error
        if number == 1:
            print(f"Ollama returned embeddings; writing {label} to Chroma...", file=sys.stderr, flush=True)
        try:
            collection.upsert(ids=[item["id"] for item in batch], documents=[item["text"] for item in batch],
                              embeddings=vectors, metadatas=[item["metadata"] for item in batch])
        except Exception as error:
            raise RuntimeError(f"Chroma write failed for {label}: {error}") from error
        progress.advance(len(batch))
    progress.finish()

    stale = list(old_ids - by_id.keys())
    progress.start("Removing stale chunks", len(stale))
    for batch in batches(stale, WRITE_BATCH_SIZE):
        collection.delete(ids=batch)
        progress.advance(len(batch))
    progress.finish()
    return SyncResult(len(missing), len(reusable), len(stale))
