"""Offline tests for repository filtering, structural chunks, and the keyword index."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from vectorizer.repository_index import _update_bm25, _write_bm25, chunk_file, discover_files, index_repository, is_indexable
from vectorizer.paths import resolve_path


class RepositoryIndexTests(unittest.TestCase):
    def test_discovery_excludes_credentials_generated_files_and_binaries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = resolve_path(temp)
            for relative, content in {
                "README.md": "docs", "deploy/main.py": "code", "config/app.env.example": "safe template",
                "config/app.env": "SECRET=real", "id_rsa": "private", "tls.pem": "private",
                ".env": "SECRET=real", "node_modules/pkg/readme.md": "generated",
                ".venv/lib/pkg.py": "generated", "image.png": "binary",
            }.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            outside = root.parent / (root.name + "-outside.md")
            outside.write_text("outside", encoding="utf-8")
            self.addCleanup(outside.unlink, missing_ok=True)
            try:
                (root / "linked.md").symlink_to(outside)
            except (OSError, NotImplementedError):
                pass
            found = {path.relative_to(root).as_posix() for path in discover_files(root)}
            self.assertEqual(found, {"README.md", "deploy/main.py", "config/app.env.example"})

    def test_markdown_heading_chunks_retain_line_ranges(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "guide.md"
            path.write_text("# Intro\nfirst paragraph\n\n## Recovery\nrun make recover\n", encoding="utf-8")
            chunks = chunk_file(path, "guide.md", chunk_size=300, overlap=20)
            self.assertEqual(len(chunks), 2)
            self.assertEqual(chunks[0]["heading"], "Intro")
            self.assertEqual(chunks[1]["heading"], "Recovery")
            self.assertEqual(chunks[1]["start_line"], 4)
            self.assertIn("make recover", chunks[1]["text"])

    def test_python_symbols_are_boundaries(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "module.py"
            path.write_text("def alpha():\n    return 1\n\nclass Beta:\n    pass\n", encoding="utf-8")
            chunks = chunk_file(path, "module.py", chunk_size=300, overlap=20)
            self.assertEqual(len(chunks), 2)
            self.assertIn("def alpha", chunks[0]["text"])
            self.assertIn("class Beta", chunks[1]["text"])

    def test_bm25_index_is_sqlite_fts_and_atomic_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "index.sqlite3"
            docs = [{"id": "chunk-1", "text": "make diagnose-vlr-online checks network", "metadata": {
                "path": "Makefile", "start_line": 4, "end_line": 6, "heading": "", "revision": "abc", "repository": "FAK"}}]
            _write_bm25(path, docs)
            db = sqlite3.connect(path)
            try:
                row = db.execute("SELECT id,path FROM chunks WHERE chunks MATCH ?", ('"diagnose-vlr-online"',)).fetchone()
            finally:
                db.close()
            self.assertEqual(row, ("chunk-1", "Makefile"))

    def test_bm25_update_replaces_only_one_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "index.sqlite3"
            first = [self._doc("old-a", "alpha old text", "a.md", "repo-a"),
                     self._doc("keep-b", "beta retained text", "b.md", "repo-b")]
            _write_bm25(path, first)
            changed = [self._doc("new-a", "alpha revised text", "a.md", "repo-a")]
            _update_bm25(path, "repo-a", changed)
            db = sqlite3.connect(path)
            try:
                rows = db.execute("SELECT id FROM chunks ORDER BY id").fetchall()
                self.assertEqual(rows, [("keep-b",), ("new-a",)])
                self.assertIsNone(db.execute("SELECT id FROM chunks WHERE chunks MATCH 'old' ").fetchone())
            finally:
                db.close()

    @staticmethod
    def _doc(ident, text, path, repository):
        return {"id": ident, "text": text, "metadata": {"path": path, "start_line": 1,
                "end_line": 2, "heading": "", "revision": "abc", "repository": repository}}

    @patch("vectorizer.repository_index.create_embeddings")
    @patch("vectorizer.repository_index.chromadb.PersistentClient")
    def test_reindex_reuses_unchanged_vectors_and_removes_old_chunks(self, persistent, embedding_factory):
        class Collection:
            def __init__(self):
                self.docs = {}
                self.metadata = {}

            def get(self, where=None, include=None, limit=None, offset=0):
                ids = list(self.docs)
                if where:
                    ids = [ident for ident in ids if self.metadata[ident].get("repository_id") == where["repository_id"]]
                if offset:
                    ids = ids[offset:]
                if limit is not None:
                    ids = ids[:limit]
                return {"ids": ids, "documents": [self.docs[i] for i in ids], "metadatas": [self.metadata[i] for i in ids]}

            def upsert(self, ids, documents, embeddings, metadatas):
                for ident, text, meta in zip(ids, documents, metadatas):
                    self.docs[ident], self.metadata[ident] = text, meta

            def update(self, ids, metadatas):
                for ident, meta in zip(ids, metadatas):
                    self.metadata[ident] = meta

            def delete(self, ids):
                for ident in ids:
                    self.docs.pop(ident, None)
                    self.metadata.pop(ident, None)

        collection = Collection()
        client = Mock()
        client.get_or_create_collection.return_value = collection
        client.get_collection.return_value = collection
        persistent.return_value = client
        embedder = Mock()
        embedder.embed_documents.side_effect = lambda chunks: [[0.1, 0.2] for _ in chunks]
        embedding_factory.return_value = embedder
        with tempfile.TemporaryDirectory() as temp:
            root, db = Path(temp) / "FAK", Path(temp) / "db"
            root.mkdir()
            source = root / "README.md"
            source.write_text("# Current\nStable content", encoding="utf-8")
            first = index_repository(root, db_path=db)
            first_calls = embedder.embed_documents.call_count
            embedded = embedder.embed_documents.call_args.args[0][0]
            self.assertTrue(embedded.startswith("File: README.md\nSection: Current\n"))
            self.assertEqual(list(collection.docs.values()), ["# Current\nStable content"])
            second = index_repository(root, db_path=db)
            self.assertGreater(first_calls, 0)
            self.assertEqual(embedder.embed_documents.call_count, first_calls)
            self.assertEqual(first["embedded_chunks"], first["chunks"])
            self.assertEqual(second["embedded_chunks"], 0)
            self.assertEqual(second["reused_chunks"], first["chunks"])
            source.write_text("# New\nChanged content", encoding="utf-8")
            third = index_repository(root, db_path=db)
            self.assertGreater(third["embedded_chunks"], 0)
            self.assertGreater(third["removed_chunks"], 0)
            self.assertTrue(all(meta["path"] == "README.md" for meta in collection.metadata.values()))
            # A record with incompatible/missing format metadata must be embedded again.
            for meta in collection.metadata.values():
                meta.pop("embedding_input_version")
            fourth = index_repository(root, db_path=db)
            self.assertEqual(fourth["embedded_chunks"], fourth["chunks"])
            self.assertEqual(client.close.call_count, 4)

    @patch("vectorizer.repository_index.chromadb.PersistentClient")
    def test_client_is_closed_when_collection_initialization_fails(self, persistent):
        persistent.return_value.get_or_create_collection.side_effect = RuntimeError("unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("source", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                index_repository(root, db_path=root / "db")
        persistent.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
