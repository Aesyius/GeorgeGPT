"""Atomic keyword publication must retain the previous DB after partial failures."""

from pathlib import Path
import sqlite3
import tempfile
import unittest

from vectorizer.keyword_index import update_bm25, write_bm25


class KeywordIndexTests(unittest.TestCase):
    def test_failed_stream_does_not_replace_original_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            original = [{"id": "old", "text": "previous corpus", "metadata": {"repository": "repo"}}]
            write_bm25(path, original)
            before = path.read_bytes()

            def failing_documents():
                yield {"id": "new", "text": "partial", "metadata": {"repository": "repo"}}
                raise RuntimeError("interrupted")

            for writer in (lambda: write_bm25(path, failing_documents()),
                           lambda: update_bm25(path, "repo", failing_documents())):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    writer()
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_streaming_update_preserves_other_repositories(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            write_bm25(path, iter([{"id": "keep", "text": "other", "metadata": {"repository": "other"}}]))
            update_bm25(path, "repo", iter([{"id": "new", "text": "new", "metadata": {"repository": "repo"}}]))
            database = sqlite3.connect(path)
            try:
                self.assertEqual(database.execute("SELECT id FROM chunks ORDER BY id").fetchall(), [("keep",), ("new",)])
            finally:
                database.close()
