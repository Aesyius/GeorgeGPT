"""Offline regression tests for contextual embeddings and exact source chunks."""

from pathlib import Path
import unittest

from vectorizer.chunking import chunk_file, embedding_text
from vectorizer.repository_index import repository_chunk_id


class ChunkingTests(unittest.TestCase):
    def chunks(self, text, name="module.py", **kwargs):
        return chunk_file(Path(name), name, text=text, **kwargs)

    def assert_source_ranges(self, text, chunks):
        for chunk in chunks:
            self.assertEqual(chunk["text"], text[chunk["start_index"]:chunk["end_index"]])
            self.assertEqual(chunk["start_line"], text[:chunk["start_index"]].count("\n") + 1)
            self.assertEqual(chunk["end_line"], text[:chunk["end_index"] - 1].count("\n") + 1)

    def test_small_class_and_related_members_remain_whole(self):
        text = "class Service:\n    def start(self):\n        return True\n"
        chunks = self.chunks(text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["symbol"], "Service")
        self.assert_source_ranges(text, chunks)

    def test_large_class_methods_keep_qualified_symbols_and_decorators(self):
        text = "class Service:\n    @staticmethod\n    def start():\n" + "        value = 'payload'\n" * 25
        text += "        return value\n\n    def stop(self):\n        return False\n"
        chunks = self.chunks(text, chunk_size=250, overlap=25)
        start = [chunk for chunk in chunks if chunk["symbol"] == "Service.start"]
        self.assertGreater(len(start), 1)
        self.assertTrue(start[0]["text"].startswith("@staticmethod"))
        self.assertTrue(all("def start()" in chunk["signature"] for chunk in start))
        self.assertTrue(any(chunk["symbol"] == "Service.stop" for chunk in chunks))
        self.assert_source_ranges(text, chunks)

    def test_markdown_hierarchy_ignores_headings_inside_fences(self):
        text = "# Service\nintro\n## Recovery\n```sh\n# Not a heading\nrun recover\n```\n"
        chunks = self.chunks(text, "guide.md")
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[1]["heading_path"], "Service > Recovery")
        self.assertIn("# Not a heading", chunks[1]["text"])
        self.assert_source_ranges(text, chunks)

    def test_fitting_fence_is_not_cut_by_preceding_prose(self):
        block = "```sh\n" + "echo recover\n" * 8 + "```"
        text = "# Recovery\n" + "Instructions. " * 12 + "\n" + block + "\nDone.\n"
        chunks = self.chunks(text, "guide.md", chunk_size=220, overlap=30)
        self.assertTrue(any(block in chunk["text"] for chunk in chunks))
        self.assert_source_ranges(text, chunks)

    def test_unicode_and_context_fit_full_embedding_budget(self):
        text = "# Service\n## Recovery\n" + "配置修复🙂" * 300
        chunks = self.chunks(text, "guide.md", chunk_size=500, overlap=450, max_embedding_bytes=512)
        for chunk in chunks:
            metadata = {"path": "guide.md", **chunk}
            self.assertLessEqual(len(embedding_text(chunk["text"], metadata).encode("utf-8")), 512)
            self.assertLessEqual(len(chunk["text"]), 500)
        self.assert_source_ranges(text, chunks)
        self.assertEqual(chunks[-1]["end_index"], len(text))

    def test_context_does_not_modify_evidence(self):
        chunk = self.chunks("# Service\nsetting=true\n", "guide.md")[0]
        original = chunk["text"]
        enriched = embedding_text(original, {"path": "docs/guide.md", **chunk})
        self.assertTrue(enriched.startswith("File: docs/guide.md\nSection: Service\n"))
        self.assertTrue(enriched.endswith(original))
        self.assertEqual(chunk["text"], original)

    def test_invalid_python_falls_back_without_losing_content(self):
        text = "def broken(:\n    pass\n\ndef other():\n    return 2\n"
        chunks = self.chunks(text)
        self.assertTrue(any("def other" in chunk["text"] for chunk in chunks))
        self.assert_source_ranges(text, chunks)

    def test_empty_input_and_invalid_budget(self):
        self.assertEqual(self.chunks("  \n"), [])
        with self.assertRaises(ValueError):
            self.chunks("content", max_embedding_bytes=10)

    def test_chunking_reports_progress_without_changing_chunks(self):
        import io
        from vectorizer.progress import ProgressBar
        output = io.StringIO()
        text = "# Recovery\n" + "Recover the service.\n" * 50
        chunks = self.chunks(text, "guide.md", chunk_size=200, overlap=30,
                             progress=ProgressBar(stream=output))
        self.assertEqual(chunks, self.chunks(text, "guide.md", chunk_size=200, overlap=30))
        self.assertIn("Chunking guide.md (characters): 0/", output.getvalue())
        self.assertIn(f"{len(text)}/{len(text)} (100%)", output.getvalue())

    def test_no_overlap_only_tail_after_exact_sized_section(self):
        text = "a" * 200 + "\n\n"
        chunks = self.chunks(text, "plain.txt", chunk_size=200, overlap=30)
        self.assertEqual(len(chunks), 1)
        self.assert_source_ranges(text, chunks)

    def test_module_code_after_function_does_not_inherit_its_symbol(self):
        text = "def answer():\n    return 42\n\nSETTING = True\n"
        chunks = self.chunks(text)
        settings = next(chunk for chunk in chunks if "SETTING" in chunk["text"])
        self.assertEqual(settings["symbol"], "")
        self.assert_source_ranges(text, chunks)

    def test_module_code_after_large_class_restores_module_context(self):
        text = "class Service:\n    def start(self):\n" + "        value = 42\n" * 30
        text += "        return value\n\nSETTING = True\n"
        chunks = self.chunks(text, chunk_size=250, overlap=25)
        settings = next(chunk for chunk in chunks if "SETTING" in chunk["text"])
        self.assertEqual(settings["symbol"], "")
        self.assert_source_ranges(text, chunks)

    def test_version_and_budget_change_chunk_ids(self):
        from unittest.mock import patch
        args = ("repo", "model", "guide.md", 1400, 180, "hash", 0)
        original = repository_chunk_id(*args)
        self.assertNotEqual(original, repository_chunk_id(*args, max_embedding_bytes=512))
        with patch("vectorizer.repository_index.EMBEDDING_INPUT_VERSION", "future"):
            self.assertNotEqual(original, repository_chunk_id(*args))


if __name__ == "__main__":
    unittest.main()
