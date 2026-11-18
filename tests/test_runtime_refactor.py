"""Regression checks for shared defaults, report I/O, and retriever ownership."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from common.runtime_defaults import index_collection, index_db_path, ollama_base_url
from evaluation.artifacts import read_questions, save_report
from Interface.server import ServerConfig, create_app


class RuntimeRefactorTests(unittest.TestCase):
    def test_environment_defaults_are_read_at_call_time(self):
        with patch.dict(os.environ, {"RAG_DB_PATH": "test index", "RAG_COLLECTION": "test_collection",
                                    "OLLAMA_BASE_URL": "http://localhost:12000"}):
            self.assertEqual(index_db_path(), Path("test index").resolve())
            self.assertEqual(index_collection(), "test_collection")
            self.assertEqual(ollama_base_url(), "http://localhost:12000")

    def test_evaluation_report_round_trip_and_failed_write_preserves_report(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            questions_path = root / "questions.jsonl"
            question = {"id": "01", "question": "Qual é a resposta?"}
            questions_path.write_text(json.dumps(question), encoding="utf-8")
            self.assertEqual(read_questions(questions_path), [question])
            output = root / "reports" / "results.json"
            save_report(output, question)
            with self.assertRaises(TypeError):
                save_report(output, {"invalid": object()})
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), question)
            self.assertEqual(list(output.parent.iterdir()), [output])

    def test_api_closes_only_owned_retriever(self):
        config = ServerConfig(api_key="test-key", chat_model="chat", embedding_model="embedding")
        injected = Mock()
        owned = Mock()
        with (
            patch("vectorizer.managed_index.prepare_index", return_value="test_collection"),
            patch("vectorizer.retrieval.Retriever", return_value=owned),
        ):
            for retriever in (None, injected):
                with TestClient(create_app(config, retriever)) as client:
                    response = client.get("/v1/models", headers={"Authorization": "Bearer test-key"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["data"][0]["id"], "GeorgeGPT")
        owned.close.assert_called_once_with()
        injected.close.assert_not_called()
