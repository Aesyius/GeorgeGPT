"""Tests for the dependency-free CLI progress reporter."""

import io
import unittest

from vectorizer.progress import ProgressBar


class ProgressBarTests(unittest.TestCase):
    def test_non_tty_reports_start_progress_and_completion(self):
        output = io.StringIO()
        bar = ProgressBar(stream=output)
        bar.start("Embedding", 3)
        bar.advance()
        bar.advance(2)
        bar.finish()
        self.assertIn("Embedding: 0/3", output.getvalue())
        self.assertIn("Embedding: 3/3 (100%)", output.getvalue())
        self.assertIn("[" + "=" * 28 + "]", output.getvalue())
        self.assertNotIn("\r", output.getvalue())

    def test_empty_stage_completes(self):
        output = io.StringIO()
        ProgressBar(stream=output).start("No work", 0)
        self.assertIn("No work: 0/0 (100%)", output.getvalue())

    def test_tty_updates_the_bar_in_place(self):
        output = io.StringIO()
        output.isatty = lambda: True
        bar = ProgressBar(stream=output)
        bar.start("Chunking", 2)
        bar.advance(2)
        bar.finish()
        self.assertIn("\rChunking [", output.getvalue())
        self.assertTrue(output.getvalue().endswith("100%\n"))


if __name__ == "__main__":
    unittest.main()
