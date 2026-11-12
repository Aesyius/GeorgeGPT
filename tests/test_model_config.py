"""Model JSON schema and standalone environment precedence, without Ollama."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vectorizer.model_config import ModelSettings


class ModelConfigTests(unittest.TestCase):
    def test_file_and_environment_precedence(self):
        settings = {"chat_model": "custom-chat:7b", "embedding_model": "custom-embed:small",
                    "context_window_tokens": 32768, "keep_alive_seconds": 1800}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            path.write_text(json.dumps(settings), encoding="utf-8")
            self.assertEqual(ModelSettings.from_file(path).to_dict(), settings)
            with patch.dict(os.environ, {"RAG_MODEL_CONFIG": str(path)}, clear=True):
                self.assertEqual(ModelSettings.from_env().to_dict(), settings)
                os.environ["OLLAMA_CHAT_MODEL"] = "override"
                os.environ["OLLAMA_EMBEDDING_MODEL"] = "override-embed"
                os.environ["OLLAMA_CONTEXT_WINDOW_TOKENS"] = "8192"
                os.environ["OLLAMA_KEEP_ALIVE_SECONDS"] = "3600"
                self.assertEqual(ModelSettings.from_env(), ModelSettings("override", "override-embed", 8192, 3600))
                os.environ["OLLAMA_CONTEXT_WINDOW_TOKENS"] = "invalid"
                with self.assertRaisesRegex(ValueError, "OLLAMA_CONTEXT_WINDOW_TOKENS"):
                    ModelSettings.from_env()

    def test_old_three_field_configs_remain_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            payload = {"chat_model": "chat", "embedding_model": "embed", "context_window_tokens": 8192}
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(ModelSettings.from_file(path).keep_alive_seconds, 1800)

    def test_invalid_keep_alive_values_are_rejected(self):
        for value in (True, False, 0, -1, "1800", 1.5, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "keep_alive_seconds"):
                ModelSettings(keep_alive_seconds=value)
        with patch.dict(os.environ, {"OLLAMA_KEEP_ALIVE_SECONDS": "bad"}, clear=True):
            with self.assertRaisesRegex(ValueError, "OLLAMA_KEEP_ALIVE_SECONDS"):
                ModelSettings.from_env()

    def test_invalid_schema_values_and_missing_files_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            with self.assertRaisesRegex(ValueError, "Cannot read"):
                ModelSettings.from_file(path)
            for payload in ([], {}, {**ModelSettings().to_dict(), "typo": 1}):
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "must contain only"):
                    ModelSettings.from_file(path)
            path.write_text("invalid JSON", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Cannot read"):
                ModelSettings.from_file(path)
        for value in (True, False, 0, -1, 511, "16384", 16384.0):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "context_window_tokens"):
                ModelSettings(context_window_tokens=value)
        for field in ("chat_model", "embedding_model"):
            for value in ("", "  ", " model ", None, []):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    ModelSettings(**{field: value})
