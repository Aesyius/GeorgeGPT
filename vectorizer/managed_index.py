"""Select and prepare model-specific API indexes without modifying legacy vectors."""

from contextlib import closing, contextmanager
import hashlib
import json
import logging
from pathlib import Path
import sqlite3

from chromadb.errors import NotFoundError

from common.model_config import ModelSettings
from common.paths import resolve_path, configured_repository
from common.chroma_endpoint import chroma_port
from common.runtime_defaults import index_collection, index_db_path, ollama_base_url
from .chroma_client import open_chroma
from .keyword_index import bm25_path
from .index_catalog import manifest_mapping, register_collection, snapshot_generation
# Preserve imports used by existing CLI callers while keeping lookup independent
# of build orchestration.
from .index_catalog import select_index_collection, register_index, matching_index
from .repository_index import index_repository
from .vector_store import update_collection_metadata, collection_embedding_model

logger = logging.getLogger(__name__)
MANAGED_VERSION = 1


def _collection(client, name: str):
    try:
        return client.get_collection(name=name)
    except NotFoundError:
        return None


def _sources(metadata: dict, repository: str | Path | None) -> dict:
    known = manifest_mapping(metadata, "catalog_sources", "index_sources")
    if repository is not None:
        root = str(resolve_path(repository))
        return {root: known.get(root, {})}
    return known or {str(configured_repository()): {}}


def _candidate_ready(collection, base_name: str, metadata: dict, sources: dict,
                     model: str, db_path: Path) -> bool:
    if collection is None:
        return False
    candidate = collection.metadata or {}
    # Older vectorize runs published a source manifest only after BM25 completion.
    completed = candidate.get("index_ready", bool(candidate.get("index_generation") and candidate.get("index_sources")))
    generation = snapshot_generation(candidate, is_base=collection.name == base_name)
    expected_generation = metadata.get("catalog_generation", metadata.get("index_generation"))
    return bool(completed and collection_embedding_model(collection) == model
                and manifest_mapping(candidate, "index_sources") == sources
                and generation == expected_generation
                and collection.count() > 0 and bm25_path(db_path, collection.name).is_file())


def _build_target(base_name: str, candidate_name: str, candidate: dict, metadata: dict,
                  model: str, sources: dict) -> tuple[str, dict]:
    # Keep the serialized identity and naming stable for already-persisted indexes.
    identity = json.dumps({"base_collection": base_name, "model": model,
                           "sources": sources, "version": MANAGED_VERSION}, sort_keys=True)
    name = "rag-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    candidate_sources = manifest_mapping(candidate, "index_sources")
    if (candidate_name != base_name and candidate.get("embedding_model") == model and candidate_sources
            and all(sources.get(root) == options for root, options in candidate_sources.items())):
        name = candidate_name
    expected = {"managed_identity": identity,
                "source_generation": str(metadata.get("catalog_generation", metadata.get("index_generation", "legacy"))),
                "managed_ready": True}
    return name, expected


def _publish_ready(client, base_name: str, name: str, model: str, expected: dict | None = None) -> None:
    collection = client.get_collection(name=name)
    update_collection_metadata(collection, {"embedding_model": model, "index_ready": True, **(expected or {})})
    register_collection(client, base_name, name, model)


@contextmanager
def _preparation_lock(db_path: Path):
    # SQLite releases the cross-process lock even if a builder crashes.
    with closing(sqlite3.connect(db_path / f"managed-index-http-{chroma_port()}.sqlite3", timeout=86400)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        yield


def prepare_index(db_path: str | Path, base_collection: str, embedding_model: str,
                  ollama_url: str, repository: str | Path | None = None) -> str:
    """Reuse only a completed matching index, or resume/build one before serving.

    Args:
        db_path: Persistent Chroma/keyword storage directory.
        base_collection: Catalog collection name used to select model indexes.
        embedding_model: Exact embedding model tag for the requested snapshot.
        ollama_url: Embedding endpoint used if a build is necessary.
        repository: Optional source override; otherwise use catalog/config sources.

    Returns:
        The actual prepared collection name, ready for retriever construction.

    Collection-level manifests are constant-size startup reads. Legacy indexes
    without a manifest use bootup.json's repository_path, or an explicit
    RAG_REPOSITORY_PATH; their vectors and keyword indexes are never rewritten.
    A SQLite cross-process lock serializes preparation. A needed build reads
    source files, calls Ollama, writes vectors/BM25, and publishes completion
    only afterward. Existing model indexes are preserved. Missing source roots
    or an empty result raise `ValueError`; storage/embedding errors propagate.
    """
    db_path = resolve_path(db_path)
    db_path.mkdir(parents=True, exist_ok=True)
    with _preparation_lock(db_path):
        with closing(open_chroma(db_path, create=True)) as client:
            base = _collection(client, base_collection)
            metadata = (base.metadata or {}) if base is not None else {}
            sources = _sources(metadata, repository)
            registered = manifest_mapping(metadata, "model_indexes")
            candidate_name = registered.get(embedding_model, base_collection)
            candidate = _collection(client, candidate_name)
            candidate_metadata = (candidate.metadata or {}) if candidate is not None else {}
            if _candidate_ready(candidate, base_collection, metadata, sources, embedding_model, db_path):
                if not candidate_metadata.get("index_ready") or not candidate_metadata.get("embedding_model"):
                    _publish_ready(client, base_collection, candidate_name, embedding_model)
                logger.info("Reusing vectorized index %s for %s", candidate_name, embedding_model)
                return candidate_name
            name, expected = _build_target(base_collection, candidate_name, candidate_metadata,
                                           metadata, embedding_model, sources)
            collection = client.get_or_create_collection(
                name=name, embedding_function=None, metadata={"hnsw:space": "cosine", "managed_ready": False})
            actual = collection.metadata or {}
            if (all(actual.get(key) == value for key, value in expected.items())
                    and collection.count() > 0 and bm25_path(db_path, name).is_file()):
                _publish_ready(client, base_collection, name, embedding_model)
                logger.info("Using index %s for %s", name, embedding_model)
                return name
            for root in sources:
                if not resolve_path(root).is_dir():
                    raise ValueError(f"Cannot build index for {embedding_model}: repository is unavailable: {root}. "
                                     "Set RAG_REPOSITORY_PATH to the repository to index.")
            update_collection_metadata(collection, {"managed_ready": False})
            logger.warning("Preparing index %s for %s; queries will be available after indexing completes.",
                           name, embedding_model)
            for root, options in sources.items():
                index_repository(root, db_path=db_path, collection_name=name,
                                 embedding_model=embedding_model, ollama_url=ollama_url, **options)
            if not collection.count():
                raise ValueError(f"Index {name} contains no chunks; API startup aborted.")
            # Publish readiness only after both vectors and keyword rows succeed.
            _publish_ready(client, base_collection, name, embedding_model, expected)
            logger.warning("Index %s is ready for %s.", name, embedding_model)
            return name


def main() -> None:
    """Prepare an index from environment/configuration defaults for bootup.

    Reads the selected models, database, base collection, Ollama URL, and
    repository, then calls `prepare_index`. This entry point has no argument
    parser: even `--help` is not a supported preview. Preparation may write data
    and is separate from the service HTTP readiness timeout.
    """
    logging.basicConfig(level=logging.INFO)
    settings = ModelSettings.from_env()
    prepare_index(index_db_path(), index_collection(), settings.embedding_model,
                  ollama_base_url(), configured_repository())


if __name__ == "__main__":
    main()
