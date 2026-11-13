"""Paginated Chroma access and shared incremental vector publication."""

from dataclasses import dataclass
import hashlib
import json

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
    if size < 1:
        raise ValueError("batch size must be positive")
    for start in range(0, len(items), size):
        yield items[start:start + size]


def collection_snapshot(collection, page_size: int = 256, include: list[str] | None = None, *, where: dict | None = None):
    """Yield bounded pages, optionally restricted to one repository at the DB."""
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
    metadata = {}
    for page in collection_snapshot(collection, page_size, include=["metadatas"], where={"repository_id": repository_id}):
        for ident, item in zip(page.get("ids", []), page.get("metadatas", [])):
            if (item or {}).get("repository_id") == repository_id:
                metadata[ident] = item
    return set(metadata), metadata


def collection_documents(collection):
    for page in collection_snapshot(collection):
        for ident, text, metadata in zip(page["ids"], page["documents"], page["metadatas"]):
            yield {"id": ident, "text": text, "metadata": metadata}


@dataclass(frozen=True)
class SyncResult:
    embedded_chunks: int
    reused_chunks: int
    removed_chunks: int

    def to_dict(self) -> dict:
        return {"embedded_chunks": self.embedded_chunks, "reused_chunks": self.reused_chunks,
                "removed_chunks": self.removed_chunks}


def synchronize_vectors(collection, documents: list[dict], old_ids: set[str], old_metadata: dict,
                        embedder, embedding_model: str, batch_size: int, progress) -> SyncResult:
    """Refresh, publish, then remove stale vectors; never remove first on failure."""
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
    for batch in batches(missing, batch_size):
        texts = [embedding_text(item["text"], item["metadata"]) for item in batch]
        vectors = embed_documents_with_retry(embedder, texts)
        collection.upsert(ids=[item["id"] for item in batch], documents=[item["text"] for item in batch],
                          embeddings=vectors, metadatas=[item["metadata"] for item in batch])
        progress.advance(len(batch))
    progress.finish()

    stale = list(old_ids - by_id.keys())
    progress.start("Removing stale chunks", len(stale))
    for batch in batches(stale, WRITE_BATCH_SIZE):
        collection.delete(ids=batch)
        progress.advance(len(batch))
    progress.finish()
    return SyncResult(len(missing), len(reusable), len(stale))
