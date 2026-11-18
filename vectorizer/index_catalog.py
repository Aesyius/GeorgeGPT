"""Collection manifests and model-index lookup, independent of index building."""

from contextlib import closing
import hashlib
import json
from pathlib import Path

import chromadb
from chromadb.errors import NotFoundError

from common.paths import resolve_path
from .vector_store import collection_embedding_model, source_generation, update_collection_metadata


def manifest_mapping(metadata: dict, key: str, fallback: str | None = None) -> dict:
    """Decode a JSON-object metadata entry, optionally using a legacy fallback key.

    Missing keys resolve to an empty mapping. Non-JSON values and non-object
    JSON raise `ValueError` naming the manifest. Only supplied metadata is read.
    """
    value = metadata.get(key, metadata.get(fallback, "{}") if fallback else "{}")
    try:
        mapping = json.loads(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid collection manifest {key!r}: expected a JSON object") from error
    if not isinstance(mapping, dict):
        raise ValueError(f"Invalid collection manifest {key!r}: expected a JSON object")
    return mapping


def catalog_publication(metadata: dict, sources: dict, versions: dict) -> dict:
    """Return merged catalog source/version JSON and its generation fingerprint.

    Combines `sources` and `versions` with existing catalog or legacy manifests.
    Does not write metadata or alter another model's collection snapshot.
    """
    all_sources = manifest_mapping(metadata, "catalog_sources", "index_sources")
    all_sources.update(sources)
    all_versions = manifest_mapping(metadata, "catalog_versions", "source_versions")
    all_versions.update(versions)
    return {
        "catalog_sources": json.dumps(all_sources, sort_keys=True),
        "catalog_versions": json.dumps(all_versions, sort_keys=True),
        "catalog_generation": source_generation(all_sources, all_versions),
    }


def snapshot_generation(metadata: dict, *, is_base: bool) -> str | None:
    """Read the legacy/base or managed snapshot generation from metadata.

    Base collections use `index_generation`; managed model indexes use
    `source_generation`. Returns `None` when no generation has been published.
    """
    return metadata.get("index_generation" if is_base else "source_generation")


def select_index_collection(db_path: str | Path, base_collection: str, embedding_model: str) -> str:
    """Choose a collection name for indexing without creating that collection.

    Prefers a registered model index, otherwise reuses a matching/empty base.
    If the base belongs to a different model, returns a deterministic isolated
    name. Opens persistent Chroma to inspect bounded ownership/catalog metadata;
    callers must build and register the chosen index separately.
    """
    with closing(chromadb.PersistentClient(path=str(resolve_path(db_path)))) as client:
        try:
            base = client.get_collection(name=base_collection)
        except NotFoundError:
            return base_collection
        registered = manifest_mapping(base.metadata or {}, "model_indexes")
        if embedding_model in registered:
            return registered[embedding_model]
        if collection_embedding_model(base) == embedding_model or not base.count():
            return base_collection
        key = json.dumps([base_collection, embedding_model])
        return "rag-model-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def register_collection(client, base_collection: str, collection_name: str, embedding_model: str) -> None:
    """Publish a completed index's model mapping and catalog generation.

    Uses an already-open Chroma client. Returns without publishing if the target
    lacks readiness or a source manifest; otherwise updates base catalog metadata
    and the target's source generation. Does not embed, audit, or copy vectors.
    """
    collection = client.get_collection(name=collection_name)
    metadata = collection.metadata or {}
    sources = manifest_mapping(metadata, "index_sources")
    if not metadata.get("index_ready") or not sources:
        return
    base = client.get_or_create_collection(name=base_collection)
    base_metadata = base.metadata or {}
    publication = catalog_publication(base_metadata, sources, manifest_mapping(metadata, "source_versions"))
    # Older completed indexes have no content fingerprints. Retain their snapshot
    # generation rather than invalidating compatible vectors during adoption.
    if not manifest_mapping(publication, "catalog_versions"):
        publication["catalog_generation"] = metadata["index_generation"]
    indexes = manifest_mapping(base_metadata, "model_indexes")
    indexes[embedding_model] = collection_name
    publication["model_indexes"] = json.dumps(indexes, sort_keys=True)
    update_collection_metadata(base, publication)
    # Re-fetch because base and collection may refer to the same collection.
    collection = client.get_collection(name=collection_name)
    update_collection_metadata(collection, {"source_generation": publication["catalog_generation"]})


def register_index(db_path: str | Path, base_collection: str, collection_name: str,
                   embedding_model: str) -> None:
    """Open the persistent client and delegate catalog publication to `register_collection`.

    The target collection must already exist; no embedding or index build occurs.
    """
    with closing(chromadb.PersistentClient(path=str(resolve_path(db_path)))) as client:
        register_collection(client, base_collection, collection_name, embedding_model)


def matching_index(db_path: str | Path, base_collection: str, embedding_model: str) -> str:
    """Resolve a model's catalog mapping and reject known stale/incompatible state.

    Returns the actual collection name. Reads collection-level metadata only.
    Missing collections propagate storage errors; known model mismatch,
    incomplete readiness, or catalog-generation mismatch raises `ValueError`.
    Legacy collections without full manifests retain compatibility behavior.
    Does not create a prepared index or repair mismatches.
    """
    with closing(chromadb.PersistentClient(path=str(resolve_path(db_path)))) as client:
        base = client.get_collection(name=base_collection)
        base_metadata = base.metadata or {}
        name = manifest_mapping(base_metadata, "model_indexes").get(embedding_model, base_collection)
        metadata = client.get_collection(name=name).metadata or {}
        model = metadata.get("embedding_model")
        if model is not None and model != embedding_model:
            raise ValueError(f"Index {name!r} uses {model}, not {embedding_model}; run vectorize for the configured model.")
        generation = base_metadata.get("catalog_generation")
        actual = snapshot_generation(metadata, is_base=name == base_collection)
        stale = generation is not None and actual != generation
        if metadata.get("index_sources") and (metadata.get("index_ready") is False or stale):
            raise ValueError(f"Index {name!r} is incomplete or stale; run vectorize for {embedding_model}.")
        return name
