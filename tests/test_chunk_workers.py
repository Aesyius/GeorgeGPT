import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from contextlib import closing
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from unittest.mock import Mock, call, patch

import chromadb

from vectorizer.chunk_workers import iter_prepared_files, validate_chunk_workers
from vectorizer.repository_index import index_repository
from vectorizer.source_files import discover_files


class ChunkWorkersTest(unittest.TestCase):
    def setUp(self):
        temp = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.root = temp / "repo"
        self.root.mkdir()
        (self.root / "a.py").write_text("class Example:\n    def test(self):\n        return 1\n", encoding="utf-8")
        (self.root / "b.md").write_bytes("# Café\r\n\r\n```\r\n# not a heading\r\n```\r\n".encode("utf-8-sig"))
        (self.root / "c.txt").write_bytes(b"")
        (self.root / "d.txt").write_bytes(b"\xff")
        self.db_path = temp / "db"

    def prepare(self, files, workers):
        return iter_prepared_files(files, self.root, workers=workers, chunk_size=200,
                                   overlap=50, max_embedding_bytes=512)

    def test_spawn_matches_serial_including_empty_and_skipped_files(self):
        files = discover_files(self.root) + [self.root / "missing.txt"]
        serial = list(self.prepare(files, 1))
        self.assertEqual(list(self.prepare(files, 2)), serial)
        self.assertEqual([result[0] for result in serial[:3]], ["a.py", "b.md", "c.txt"])
        self.assertEqual(serial[0][2][0]["symbol"], "Example")
        self.assertEqual(serial[1][2][0]["heading"], "Café")
        self.assertEqual(serial[1][1], hashlib.sha256("# Café\n\n```\n# not a heading\n```\n".encode()).hexdigest())
        self.assertEqual(serial[2][2], [])
        self.assertEqual(serial[3:], [None, None])

    def test_bounds_submission_and_preserves_order(self):
        files = [self.root / f"{i}.txt" for i in range(10)]
        futures = [Future() for _ in files]
        for i in reversed(range(len(futures))):
            futures[i].set_result(i)
        with patch("vectorizer.chunk_workers.ProcessPoolExecutor") as pool:
            executor = pool.return_value
            executor.submit.side_effect = futures
            with closing(self.prepare(files, 2)) as results:
                self.assertEqual(next(results), 0)
                self.assertEqual(executor.submit.call_count, 4)
                self.assertEqual(next(results), 1)
                self.assertEqual(executor.submit.call_count, 5)
            executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)

    def test_one_file_does_not_start_pool(self):
        with patch("vectorizer.chunk_workers.ProcessPoolExecutor") as pool:
            self.assertEqual(len(list(self.prepare([self.root / "a.py"], 2))), 1)
            pool.assert_not_called()

    def test_invalid_worker_counts_and_limits(self):
        for count in (0, -1, 1.5, True):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "positive integer"):
                validate_chunk_workers(count)
        validate_chunk_workers(10)
        with self.assertRaisesRegex(ValueError, "cannot exceed 10"):
            validate_chunk_workers(11)
        with patch("vectorizer.chunk_workers.ProcessPoolExecutor") as pool:
            with self.assertRaisesRegex(ValueError, "at least 512"):
                index_repository(self.root, db_path=self.db_path, chunk_workers=2, max_embedding_bytes=511)
            pool.assert_not_called()

    def test_worker_error_stops_before_storage(self):
        (self.root / "e.md").write_text("# " + "x" * 600, encoding="utf-8")
        with patch("vectorizer.repository_index.chromadb.PersistentClient") as client:
            with self.assertRaisesRegex(ValueError, "While chunking e.md:"):
                index_repository(self.root, db_path=self.db_path, chunk_workers=2, max_embedding_bytes=512,
                                 progress=Mock())
            client.assert_not_called()

    def test_broken_pool_is_reported_and_shut_down(self):
        with patch("vectorizer.chunk_workers.ProcessPoolExecutor") as pool:
            executor = pool.return_value
            executor.submit.return_value.result.side_effect = BrokenProcessPool()
            with self.assertRaisesRegex(RuntimeError, "chunk worker pool stopped"):
                list(self.prepare(discover_files(self.root), 2))
            executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)

    def test_index_reuses_vectors_when_worker_count_changes(self):
        embedder = Mock()
        embedder.embed_documents.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
        parent_pid = os.getpid()
        progress = Mock()

        def check_progress(*args):
            self.assertEqual(os.getpid(), parent_pid)

        progress.start.side_effect = progress.advance.side_effect = progress.finish.side_effect = check_progress
        with patch("vectorizer.repository_index.create_embeddings", return_value=embedder):
            options = dict(db_path=self.db_path, embedding_model="test-model", progress=progress)
            serial = index_repository(self.root, **options)
            self.assertEqual(serial["embedded_chunks"], serial["chunks"])
            self.assertTrue(serial["chunks"])
            before = self.snapshot()
            progress.reset_mock()
            embedder.embed_documents.reset_mock()
            parallel = index_repository(self.root, chunk_workers=2, **options)
            self.assertEqual(parallel["embedded_chunks"], 0)
            self.assertEqual(parallel["reused_chunks"], serial["chunks"])
            self.assertEqual(parallel["removed_chunks"], 0)
            self.assertEqual(self.snapshot(), before)
            embedder.embed_documents.assert_not_called()
            self.assertEqual(progress.start.call_args_list[0].args, ("Scanning/chunking files", 4))
            self.assertEqual(progress.advance.call_args_list[:4], [call()] * 4)

    def snapshot(self):
        with closing(chromadb.PersistentClient(path=str(self.db_path))) as client:
            collection = client.get_collection("repo_documents")
            return collection.metadata, collection.get(include=["documents", "metadatas"])
