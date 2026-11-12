"""Tests for retrying transient Ollama embedding-runner failures."""

import unittest
from unittest.mock import Mock, patch

from vectorizer.embeddings import embed_documents_with_retry


class EmbeddingRetryTests(unittest.TestCase):
    @patch("vectorizer.embeddings.time.sleep")
    def test_retries_then_returns_vectors(self, sleep):
        embedder = Mock()
        embedder.embed_documents.side_effect = [ConnectionError("runner unavailable"), [[0.1, 0.2]]]
        self.assertEqual(embed_documents_with_retry(embedder, ["chunk"]), [[0.1, 0.2]])
        self.assertEqual(embedder.embed_documents.call_count, 2)
        sleep.assert_called_once_with(1)

    @patch("vectorizer.embeddings.time.sleep")
    def test_raises_clear_error_after_limit(self, sleep):
        embedder = Mock()
        embedder.embed_documents.side_effect = ConnectionError("runner unavailable")
        with self.assertRaisesRegex(RuntimeError, "failed after 3 attempts"):
            embed_documents_with_retry(embedder, ["chunk"])
        self.assertEqual(embedder.embed_documents.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
