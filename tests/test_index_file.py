"""Single-file indexing shares contextual representations without altering evidence."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from vectorizer.chunking import EMBEDDING_INPUT_VERSION
from vectorizer.vectorize import index_file


class IndexFileTests(unittest.TestCase):
    @patch("vectorizer.vectorize._write_bm25")
    @patch("vectorizer.vectorize._repository_ids", return_value=(set(), {}))
    @patch("vectorizer.vectorize.create_embeddings")
    @patch("vectorizer.vectorize.chromadb.PersistentClient")
    def test_embedding_input_and_stored_source_are_separate(self, persistent, factory, previous, bm25):
        collection = Mock()
        persistent.return_value.get_or_create_collection.return_value = collection
        collection.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        factory.return_value.embed_documents.return_value = [[0.1, 0.2]]
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "guide.md"
            source.write_text("# Service\n## Recovery\nrun recover\n", encoding="utf-8")
            count = index_file(source, Path(temp) / "db", "test", "model", "http://localhost:11434", 1400, 180)
            self.assertEqual(count, 2)
            embedded = factory.return_value.embed_documents.call_args.args[0]
            self.assertIn("Section: Service > Recovery", embedded[1])
            stored = collection.upsert.call_args.kwargs
            self.assertEqual(stored["documents"][1], "## Recovery\nrun recover")
            self.assertEqual(stored["metadatas"][1]["embedding_input_version"], EMBEDDING_INPUT_VERSION)
            self.assertEqual(stored["metadatas"][1]["start_line"], 2)
            persistent.return_value.close.assert_called_once()
