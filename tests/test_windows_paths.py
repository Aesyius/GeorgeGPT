"""Offline regression tests for Windows-friendly paths and text files."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vectorizer.document_loader import load_and_split_text
from vectorizer.paths import DEFAULT_DB_PATH, resolve_path


class WindowsPathsTest(unittest.TestCase):
    def test_default_database_stays_at_project_root(self) -> None:
        self.assertEqual(
            DEFAULT_DB_PATH,
            Path(__file__).resolve().parents[1] / "chroma_db",
        )
        self.assertTrue(DEFAULT_DB_PATH.is_absolute())

    def test_environment_variables_and_spaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vectorizer tests ") as directory:
            with patch.dict(os.environ, {"VECTORIZER_TEST_DIR": directory}):
                variable = "%VECTORIZER_TEST_DIR%" if os.name == "nt" else "$VECTORIZER_TEST_DIR"
                self.assertEqual(
                    resolve_path(f"{variable}/my database"),
                    Path(directory).resolve() / "my database",
                )

    def test_relative_path(self) -> None:
        self.assertEqual(resolve_path("my database"), Path.cwd() / "my database")

    def test_windows_utf8_bom_crlf_and_unicode_filename(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vectorizer tests ") as directory:
            source = Path(directory) / "notes café.txt"
            source.write_bytes("First café line.\r\nSecond line.\r\n".encode("utf-8-sig"))
            documents = load_and_split_text(source)
            self.assertEqual(len(documents), 1)
            self.assertEqual(documents[0].page_content, "First café line.\nSecond line.")
            self.assertEqual(documents[0].metadata["source"], str(source.resolve()))
            self.assertEqual(documents[0].metadata["start_index"], 0)

    def test_plain_utf8_still_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "notes.txt"
            source.write_text("Plain UTF-8 text.", encoding="utf-8")
            self.assertEqual(load_and_split_text(source)[0].page_content, "Plain UTF-8 text.")


if __name__ == "__main__":
    unittest.main()
