"""Shared publication invariants, bounded batches, and repository-scoped reads."""

import io
import unittest
from unittest.mock import Mock, patch

from vectorizer.chunking import CHUNKING_VERSION, EMBEDDING_INPUT_VERSION
from vectorizer.progress import ProgressBar
from vectorizer.vector_store import collection_snapshot, repository_ids, synchronize_vectors


class VectorStoreTests(unittest.TestCase):
    def test_repository_queries_are_filtered_and_paginated(self):
        collection = Mock()
        metadata = {"repository_id": "repo"}
        collection.get.side_effect = [
            {"ids": ["one", "two"], "metadatas": [metadata, metadata]},
            {"ids": ["three"], "metadatas": [metadata]},
        ]
        ids, stored = repository_ids(collection, "repo", page_size=2)
        self.assertEqual(ids, {"one", "two", "three"})
        self.assertEqual(len(stored), 3)
        calls = collection.get.call_args_list
        self.assertEqual([call.kwargs["offset"] for call in calls], [0, 2])
        self.assertTrue(all(call.kwargs["where"] == {"repository_id": "repo"} for call in calls))
        self.assertTrue(all(call.kwargs["include"] == ["metadatas"] for call in calls))

    def test_invalid_page_size_fails_before_reading(self):
        collection = Mock()
        with self.assertRaises(ValueError):
            list(collection_snapshot(collection, page_size=0))
        collection.get.assert_not_called()

    @patch("vectorizer.vector_store.embed_documents_with_retry", side_effect=RuntimeError("unavailable"))
    def test_embedding_failure_never_removes_old_vectors(self, embed):
        collection = Mock()
        documents = [{"id": "new", "text": "source", "metadata": {"path": "guide.md"}}]
        with self.assertRaises(RuntimeError):
            synchronize_vectors(collection, documents, {"old"}, {"old": {}}, Mock(), "model", 32,
                                ProgressBar(stream=io.StringIO()))
        collection.delete.assert_not_called()
        collection.upsert.assert_not_called()

    def test_unchanged_documents_are_reused_without_embedding(self):
        metadata = {"embedding_model": "model", "chunking_version": CHUNKING_VERSION,
                    "embedding_input_version": EMBEDDING_INPUT_VERSION}
        documents = [{"id": "same", "text": "source", "metadata": metadata}]
        embedder, collection = Mock(), Mock()
        result = synchronize_vectors(collection, documents, {"same"}, {"same": metadata}, embedder, "model", 32,
                                     ProgressBar(stream=io.StringIO()))
        self.assertEqual(result.to_dict(), {"embedded_chunks": 0, "reused_chunks": 1, "removed_chunks": 0})
        embedder.embed_documents.assert_not_called()
        collection.delete.assert_not_called()
        collection.update.assert_called_once()

    def test_single_file_stale_deletes_are_bounded(self):
        collection = Mock()
        stale = {str(index) for index in range(1201)}
        result = synchronize_vectors(collection, [], stale, {}, Mock(), "model", 32,
                                     ProgressBar(stream=io.StringIO()))
        sizes = [len(call.kwargs["ids"]) for call in collection.delete.call_args_list]
        self.assertEqual(sizes, [500, 500, 201])
        self.assertEqual(result.removed_chunks, len(stale))
