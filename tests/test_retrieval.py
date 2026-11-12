"""Offline hybrid search tests (no embeddings service or Chroma server required)."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from vectorizer.repository_index import _write_bm25
from vectorizer.retrieval import Retriever


class RetrievalTests(unittest.TestCase):
    @patch("vectorizer.retrieval.create_embeddings")
    @patch("vectorizer.retrieval.chromadb.PersistentClient")
    def test_initialization_does_not_scan_collection_metadata(self, persistent_client, create_embeddings):
        collection = Mock()
        persistent_client.return_value.get_collection.return_value = collection
        with tempfile.TemporaryDirectory() as temp:
            retriever = Retriever(db_path=temp, embedding_model="test-embedding")
        self.assertIs(retriever._collection, collection)
        collection.get.assert_not_called()
        create_embeddings.assert_called_once_with("test-embedding", None)

    def make_retriever(self, db_path, vector_ids, vector_docs, keyword_docs, *, top_k=5, budget=1000):
        _write_bm25(db_path / "repo_documents.bm25.sqlite3", keyword_docs)
        instance = Retriever.__new__(Retriever)
        instance.db_path = db_path
        instance.collection_name = "repo_documents"
        instance.top_k = top_k
        instance.context_budget = budget
        instance._bm25 = db_path / "repo_documents.bm25.sqlite3"
        instance._collection = Mock()
        instance._collection.count.return_value = len(vector_ids)
        instance._collection.query.return_value = {
            "ids": [vector_ids], "documents": [[item["text"] for item in vector_docs]],
            "metadatas": [[item["metadata"] for item in vector_docs]],
            "distances": [[0.1] * len(vector_ids)],
        }
        instance._embedder = Mock()
        instance._embedder.embed_query.return_value = [0.0, 1.0]
        return instance

    @staticmethod
    def doc(ident, text, path):
        return {"id": ident, "text": text, "metadata": {
            "path": path, "start_line": 1, "end_line": 2, "heading": "", "revision": "abc", "repository": "FAK"}}

    def test_hybrid_retrieval_promotes_exact_terms_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp)
            vector = [self.doc("semantic", "A concept explanation", "docs.md"), self.doc("exact", "make diagnose-vlr-online runs checks", "Makefile")]
            retriever = self.make_retriever(db, ["semantic", "exact"], vector, [self.doc("exact", "make diagnose-vlr-online runs checks", "Makefile")])
            results = retriever.retrieve("diagnose-vlr-online")
            self.assertEqual([item["id"] for item in results][0], "exact")
            self.assertEqual(len([item for item in results if item["id"] == "exact"]), 1)
            self.assertEqual(results[0]["metadata"]["path"], "Makefile")
            retriever._embedder.embed_query.assert_called_once_with("diagnose-vlr-online")

    def test_context_budget_and_top_k(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp)
            docs = [self.doc(str(i), "x" * 80, f"{i}.md") for i in range(4)]
            retriever = self.make_retriever(db, [str(i) for i in range(4)], docs, [], top_k=3, budget=100)
            result = retriever.retrieve("explain")
            self.assertEqual(len(result), 1)
            self.assertLessEqual(sum(len(item["text"]) for item in result), 100)

    def test_keyword_results_allow_legacy_unknown_line_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp)
            doc = self.doc("legacy", "make diagnose-controller-authority", "Makefile")
            doc["metadata"].pop("start_line")
            doc["metadata"].pop("end_line")
            _write_bm25(db / "repo_documents.bm25.sqlite3", [doc])
            instance = Retriever.__new__(Retriever)
            instance._bm25 = db / "repo_documents.bm25.sqlite3"
            results = instance._keyword_search("diagnose-controller-authority", 5)
            self.assertEqual(results[0]["metadata"]["start_line"], "?")


if __name__ == "__main__":
    unittest.main()
