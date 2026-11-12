"""Offline boot warmup contract and HTTP transport tests."""

import io
import json
import os
import unittest
from unittest.mock import patch

from vectorizer.embeddings import create_embeddings
from vectorizer.model_config import ModelSettings
from vectorizer.model_warmup import warm_models
from vectorizer.service_config import BootError
from vectorizer.service_runtime import request_json


class WarmupTests(unittest.TestCase):
    def responses(self, models):
        return [(200, {"done": True, "message": {"content": "OK"}}),
                (200, {"embeddings": [[0.1, 0.2]]}),
                (200, {"models": [{"name": models.chat_model}, {"name": models.embedding_model}]})]

    @patch("vectorizer.model_warmup.request_json")
    def test_warms_both_inference_paths_then_checks_residency(self, request):
        models = ModelSettings("chat", "embed", 8192, 3600)
        request.side_effect = self.responses(models)
        warm_models("http://ollama/", models, 120)
        calls = request.call_args_list
        self.assertEqual([call.args[0] for call in calls], ["http://ollama/api/chat", "http://ollama/api/embed", "http://ollama/api/ps"])
        for call in calls[:2]:
            self.assertEqual(call.kwargs["payload"]["keep_alive"], 3600)
            self.assertEqual(call.kwargs["timeout"], 120)
        chat = calls[0].kwargs["payload"]
        self.assertEqual(chat["options"], {"num_ctx": 8192, "num_predict": 8})
        self.assertFalse(chat["think"])
        self.assertFalse(chat["stream"])
        self.assertTrue(chat["messages"][0]["content"].startswith("You are George,"))

    @patch("vectorizer.model_warmup.request_json")
    def test_failures_do_not_report_successful_warmup(self, request):
        models = ModelSettings()
        for embedding_response in (None, {}, {"embeddings": []}, {"error": "unavailable"}):
            request.reset_mock()
            request.side_effect = [self.responses(models)[0], (200, embedding_response)]
            with self.subTest(response=embedding_response), self.assertRaisesRegex(BootError, "Embedding model warmup failed"):
                warm_models("http://ollama", models, 120)
            self.assertEqual(request.call_count, 2)
        request.side_effect = OSError("offline")
        with self.assertRaises(BootError):
            warm_models("http://ollama", models, 120)

    @patch("vectorizer.model_warmup.request_json")
    def test_joint_residency_is_required(self, request):
        models = ModelSettings()
        values = self.responses(models)
        values[-1] = (200, {"models": [{"name": models.chat_model}]})
        request.side_effect = values
        with self.assertRaisesRegex(BootError, "did not remain loaded together"):
            warm_models("http://ollama", models, 120)

    @patch("vectorizer.model_warmup.request_json")
    def test_empty_chat_answer_is_a_warmup_failure(self, request):
        models = ModelSettings()
        values = self.responses(models)
        values[0] = (200, {"done": True, "message": {"content": ""}})
        request.side_effect = values
        with self.assertRaisesRegex(BootError, "Chat model warmup failed"):
            warm_models("http://ollama", models, 120)

    @patch("vectorizer.service_runtime.urllib.request.urlopen")
    def test_json_post_does_not_mutate_headers(self, urlopen):
        response = io.BytesIO(b'{"done": true}')
        response.headers = {"Content-Type": "application/json"}
        response.status = 200
        urlopen.return_value = response
        headers = {"Authorization": "Bearer test"}
        self.assertEqual(request_json("http://ollama/api/chat", headers=headers, payload={"model": "chat"}), (200, {"done": True}))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"model": "chat"})
        self.assertEqual(headers, {"Authorization": "Bearer test"})

    @patch("vectorizer.embeddings.OllamaEmbeddings")
    def test_embedding_requests_receive_retention_policy(self, factory):
        create_embeddings("embed", "http://ollama", keep_alive_seconds=3600)
        self.assertEqual(factory.call_args.kwargs["keep_alive"], 3600)
        with patch.dict(os.environ, {"OLLAMA_KEEP_ALIVE_SECONDS": "600"}):
            create_embeddings("embed", "http://ollama")
        self.assertEqual(factory.call_args.kwargs["keep_alive"], 600)
