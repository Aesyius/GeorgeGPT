"""Retrieval metrics must not mistake duplicate passages for additional file recall."""

import unittest
from unittest.mock import Mock

from vectorizer.evaluate_retrieval import evaluate


class RetrievalEvaluationTests(unittest.TestCase):
    def test_path_recall_and_first_relevant_rank(self):
        retriever = Mock()
        retriever.retrieve.return_value = [
            {"id": "a", "text": "irrelevant", "metadata": {"path": "other.md"}},
            {"id": "b", "text": "evidence", "metadata": {"path": "docs\\guide.md"}},
            {"id": "c", "text": "more evidence", "metadata": {"path": "docs/guide.md"}},
        ]
        report = evaluate([{"id": "q", "question": "recover?", "expected_paths": ["docs/guide.md", "Makefile"]}], retriever)
        self.assertEqual(report["mean_path_recall_at_k"], 0.5)
        self.assertEqual(report["mean_reciprocal_rank"], 0.5)
        self.assertEqual(len(report["results"][0]["evidence"]), 3)

    def test_unlabelled_questions_do_not_distort_averages(self):
        retriever = Mock()
        retriever.retrieve.return_value = []
        report = evaluate([{"id": "q", "question": "unknown?"}], retriever)
        self.assertEqual(report["scored_questions"], 0)
        self.assertIsNone(report["mean_path_recall_at_k"])
