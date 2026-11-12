"""Read-only index integrity verification tests against a tiny local Chroma fixture."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from vectorizer.model_config import DEFAULT_EMBEDDING_MODEL
from vectorizer.chunking import CHUNKING_VERSION, EMBEDDING_INPUT_VERSION, DEFAULT_EMBEDDING_BYTES
from vectorizer.repository_index import _hash, _write_bm25, repository_chunk_id
from vectorizer.verify_index import verify_repository_index


class FakeCollection:
    def __init__(self, records=()):
        self.records = {record[0]: record[1:] for record in records}

    def count(self):
        return len(self.records)

    def get(self, limit=None, offset=0, include=None):
        ids = list(self.records)[offset:]
        if limit is not None:
            ids = ids[:limit]
        result = {"ids": ids}
        if "metadatas" in include:
            result["metadatas"] = [self.records[ident][0] for ident in ids]
        if "embeddings" in include:
            result["embeddings"] = [self.records[ident][1] for ident in ids]
        return result


class VerifyIndexTests(unittest.TestCase):
    def test_verifier_checks_source_vectors_metadata_dimension_and_bm25(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, db_path = base / "FAK", base / "db"
            root.mkdir()
            path = root / "README.md"
            path.write_text("# Verify\nindexed evidence", encoding="utf-8")
            repo_key = _hash(str(root.resolve()).casefold())[:20]
            content_hash = _hash(path.read_text(encoding="utf-8-sig"))
            ident = repository_chunk_id(repo_key, DEFAULT_EMBEDDING_MODEL, "README.md", 1400, 180, content_hash, 0)
            metadata = {"repository": str(root.resolve()), "repository_id": repo_key,
                "embedding_model": DEFAULT_EMBEDDING_MODEL, "revision": "working-tree",
                "chunking_version": CHUNKING_VERSION, "embedding_input_version": EMBEDDING_INPUT_VERSION,
                "max_embedding_bytes": DEFAULT_EMBEDDING_BYTES,
                "path": "README.md", "start_line": 1, "end_line": 2, "heading": "Verify"}
            collection = FakeCollection([(ident, metadata, [0.0] * 1024)])
            _write_bm25(db_path / "repo_documents.bm25.sqlite3", [
                {"id": ident, "text": "# Verify\n\nindexed evidence", "metadata": metadata}])
            client = self._client(collection)
            with patch("vectorizer.verify_index.chromadb.PersistentClient", return_value=client):
                result = verify_repository_index(root, db_path=db_path)
            self.assertTrue(result["ok"], result["details"])
            self.assertEqual(result["expected_chunks"], 1)
            self.assertEqual(result["stored_chunks"], 1)
            self.assertEqual(result["vector_dimensions"], [1024])

    @patch("vectorizer.verify_index.chromadb.PersistentClient")
    def test_verifier_detects_missing_bm25_and_vectors(self, persistent):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, db_path = base / "FAK", base / "db"
            root.mkdir()
            (root / "README.md").write_text("# Missing", encoding="utf-8")
            persistent.return_value = self._client(FakeCollection())
            result = verify_repository_index(root, db_path=db_path)
            self.assertFalse(result["ok"])
            self.assertEqual(result["missing_vectors"], 1)
            self.assertEqual(result["missing_bm25_rows"], 1)

    @staticmethod
    def _client(collection):
        client = Mock()
        client.get_collection.return_value = collection
        return client


if __name__ == "__main__":
    unittest.main()
