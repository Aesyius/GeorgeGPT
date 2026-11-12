"""Offline API contract and safety tests (FastAPI is optional for CLI users)."""

import importlib.util
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

AVAILABLE = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))
if AVAILABLE:
    from fastapi.testclient import TestClient
    from vectorizer.chat import ChatError
    from vectorizer.completion_queue import CompletionQueue
    from vectorizer.server import MAX_CONCURRENT_REQUESTS, ServerConfig, create_app


@unittest.skipUnless(AVAILABLE, "FastAPI and httpx are required for server tests")
class ServerTests(unittest.TestCase):
    def setUp(self):
        self.retriever = Mock()
        self.retriever.retrieve.return_value = [{"id": "1", "text": "code", "metadata": {
            "path": "app.py", "start_line": 1, "end_line": 3, "revision": "abc123"}, "score": 0.1}]
        self.app = create_app(ServerConfig(api_key="test-secret", chat_model="actual-ollama-model"), self.retriever)
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()
        self.addCleanup(self.client_context.__exit__, None, None, None)
        self.headers = {"Authorization": "Bearer test-secret"}
        self.payload = {"model": "GeorgeGPT", "messages": [{"role": "user", "content": "Explain"}]}

    def post(self, payload=None):
        return self.client.post("/v1/chat/completions", headers=self.headers,
                                json=self.payload if payload is None else payload)

    def test_required_config(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "RAG_API_KEY"):
                ServerConfig.from_env()
            with self.assertRaisesRegex(ValueError, "RAG_API_KEY"):
                with TestClient(create_app(retriever=self.retriever)):
                    pass
        with self.assertRaisesRegex(ValueError, "chat model"):
            ServerConfig(api_key="secret", chat_model="")

    def test_default_models_match_selected_ollama_tags(self):
        from vectorizer.model_config import DEFAULT_MODELS_CONFIG, ModelSettings
        selected = ModelSettings.from_file(DEFAULT_MODELS_CONFIG)
        with patch.dict(os.environ, {"RAG_API_KEY": "key"}, clear=True):
            config = ServerConfig.from_env()
        self.assertEqual(config.chat_model, selected.chat_model)
        self.assertEqual(config.embedding_model, selected.embedding_model)
        self.assertEqual(config.keep_alive_seconds, selected.keep_alive_seconds)

    def test_environment_and_constructor_contract(self):
        with patch.dict(os.environ, {"RAG_API_KEY": "key", "OLLAMA_CHAT_MODEL": "model",
                                     "RAG_DB_PATH": "my database", "RAG_COLLECTION": "custom",
                                     "OLLAMA_EMBEDDING_MODEL": "embed", "OLLAMA_BASE_URL": "http://ollama:11434",
                                     "OLLAMA_CONTEXT_WINDOW_TOKENS": "32768", "OLLAMA_KEEP_ALIVE_SECONDS": "3600"}, clear=True):
            config = ServerConfig.from_env()
        self.assertEqual(config.db_path, (Path.cwd() / "my database").resolve())
        self.assertEqual(config.context_window_tokens, 32768)
        self.assertEqual(config.keep_alive_seconds, 3600)
        # Inject a module so this test does not require Chroma or a real index.
        module = Mock()
        with patch.dict("sys.modules", {"vectorizer.retrieval": module}):
            with TestClient(create_app(config)):
                pass
        module.Retriever.assert_called_once_with(db_path=config.db_path, collection_name="custom",
            embedding_model="embed", ollama_url="http://ollama:11434", top_k=8, context_budget=18000, keep_alive_seconds=3600)

    def test_auth_and_models(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            self.assertEqual(self.client.get("/v1/models", headers=headers).status_code, 401)
            self.assertEqual(self.client.get("/v1/config", headers=headers).status_code, 401)
            self.assertEqual(self.client.post("/v1/chat/completions", headers=headers, json=self.payload).status_code, 401)
        with patch("vectorizer.server.hmac.compare_digest", return_value=True) as compare:
            result = self.client.get("/v1/models", headers=self.headers)
        compare.assert_called_once()
        self.assertEqual(result.json()["data"][0]["id"], "GeorgeGPT")
        self.assertNotIn("access-control-allow-origin", result.headers)

    def test_runtime_configuration_is_authenticated_and_contains_no_secrets(self):
        result = self.client.get("/v1/config", headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"chat_model": "actual-ollama-model", "embedding_model": "qwen3-embedding:0.6b",
                                         "context_window_tokens": 16384, "keep_alive_seconds": 1800,
                                         "ollama_url": "http://localhost:11434"})
        self.assertNotIn("test-secret", result.text)

    @patch("vectorizer.server.generate_answer", return_value="An answer.")
    def test_nonstream_answer_and_history_has_no_source_metadata(self, generate):
        self.payload["messages"].insert(0, {"role": "assistant", "content": "earlier"})
        result = self.post()
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertEqual(data["choices"][0]["message"]["content"], "An answer.")
        self.assertNotIn("sources", data)
        self.retriever.retrieve.assert_called_once_with("Explain", history=[{"role": "assistant", "content": "earlier"}])
        self.assertEqual(generate.call_args.args[1:], ("actual-ollama-model", "http://localhost:11434"))
        self.assertEqual(generate.call_args.kwargs["context_window_tokens"], 16384)
        self.assertEqual(generate.call_args.kwargs["keep_alive_seconds"], 1800)

    @patch("vectorizer.server.stream_answer")
    def test_stream_sse_without_source_metadata(self, stream):
        stream.return_value = iter(["An ", "answer."])
        # The production iterator is a closable generator.
        stream.return_value = (item for item in stream.return_value)
        result = self.post({**self.payload, "stream": True})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.headers["content-type"].startswith("text/event-stream"))
        records = [line[6:] for line in result.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(records[-1], "[DONE]")
        records = [json.loads(item) for item in records[:-1]]
        self.assertEqual(records[0]["choices"][0]["delta"]["role"], "assistant")
        self.assertEqual("".join(item["choices"][0]["delta"].get("content", "") for item in records), "An answer.")
        self.assertNotIn("sources", records[-1])
        self.assertEqual(records[-1]["choices"][0]["finish_reason"], "stop")
        self.retriever.retrieve.assert_called_once()
        self.assertEqual(stream.call_args.kwargs["context_window_tokens"], 16384)
        self.assertEqual(stream.call_args.kwargs["keep_alive_seconds"], 1800)

    @patch("vectorizer.server.generate_answer", return_value="answer")
    def test_custom_context_flows_from_server_configuration(self, generate):
        app = create_app(ServerConfig(api_key="test-secret", context_window_tokens=32768, keep_alive_seconds=600), self.retriever)
        with TestClient(app) as client:
            self.assertEqual(client.post("/v1/chat/completions", headers=self.headers, json=self.payload).status_code, 200)
        self.assertEqual(generate.call_args.kwargs["context_window_tokens"], 32768)
        self.assertEqual(generate.call_args.kwargs["keep_alive_seconds"], 600)

    def test_rejects_tools_models_and_invalid_messages(self):
        for payload in (
            {**self.payload, "model": "another-model"}, {**self.payload, "stream": "true"},
            {**self.payload, "messages": []}, {**self.payload, "messages": [{"role": "tool", "content": "x"}]},
            {**self.payload, "messages": [{"role": "user", "content": "x", "tool_calls": []}]},
            {**self.payload, "messages": [{"role": "user", "content": [{"type": "text", "text": "x"}]}]},
            {**self.payload, "messages": [{"role": "user", "content": "x" * 8001}]},
            {**self.payload, "messages": [{"role": "assistant", "content": "x"}]},
            {**self.payload, "messages": [{"role": [], "content": "x"}]},
            {**self.payload, "messages": [{"role": {}, "content": "x"}]},
        ):
            self.assertEqual(self.post(payload).status_code, 400)
        self.retriever.retrieve.assert_not_called()

    @patch("vectorizer.server.generate_answer", return_value="An answer.")
    def test_discards_openai_tool_declarations_without_execution(self, generate):
        payload = {
            **self.payload,
            "tools": [{"type": "function", "function": {"name": "explanation"}}],
            "tool_choice": "auto",
            "functions": [{"name": "explanation"}],
            "function_call": "auto",
        }
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        self.assertTrue(all("tool_calls" not in message for message in generate.call_args.args[0]))
        self.assertTrue(all("tools" not in message for message in generate.call_args.args[0]))

    @patch("vectorizer.server.generate_answer", return_value="An answer.")
    def test_accepts_but_does_not_forward_openai_max_tokens_hint(self, generate):
        result = self.post({**self.payload, "max_tokens": 256})
        self.assertEqual(result.status_code, 200)
        self.assertIn("answer", result.json()["choices"][0]["message"]["content"])
        self.assertNotIn("max_tokens", generate.call_args.args[0])
        self.assertEqual(self.post({**self.payload, "max_tokens": 0}).status_code, 400)

    def test_body_message_and_total_input_bounds(self):
        self.assertEqual(self.client.post("/v1/chat/completions", headers=self.headers, content="x" * 65537).status_code, 413)
        self.assertEqual(self.client.post("/v1/chat/completions", headers=self.headers, content="invalid").status_code, 400)
        self.assertEqual(self.client.post("/v1/chat/completions", headers=self.headers, content="[" * 2000 + "]" * 2000).status_code, 400)
        self.assertEqual(self.post({**self.payload, "messages": [{"role": "user", "content": "x"}] * 33}).status_code, 400)
        self.assertEqual(self.post({**self.payload, "messages": [{"role": "user", "content": "x" * 8000}] * 5}).status_code, 413)

    def test_concurrency_limit(self):
        queue = self.client.portal.call(lambda: CompletionQueue(max_waiting=0))
        self.app.state.completions = queue
        lease = self.client.portal.call(queue.acquire)
        try:
            result = self.post()
            self.assertEqual(result.status_code, 429)
            self.assertEqual(result.headers["retry-after"], "1")
            self.assertEqual(MAX_CONCURRENT_REQUESTS, 1)
            self.retriever.retrieve.assert_not_called()
        finally:
            lease.release()

    @patch("vectorizer.server.generate_answer", return_value="answer")
    def test_ordinary_overlap_waits_then_returns_an_answer(self, generate):
        queue = self.app.state.completions
        first = self.client.portal.call(queue.acquire)
        entered = threading.Event()
        acquire = queue.acquire

        async def signalled_acquire():
            entered.set()
            return await acquire()

        try:
            with patch.object(queue, "acquire", signalled_acquire), ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.post)
                self.assertTrue(entered.wait(timeout=2))
                first.release()
                result = future.result(timeout=5)
            self.assertEqual(result.status_code, 200)
            generate.assert_called_once()
        finally:
            first.release()

    def test_wait_timeout_is_bounded_and_does_not_run_retrieval(self):
        queue = self.client.portal.call(lambda: CompletionQueue(max_waiting=1, wait_timeout=0.02))
        self.app.state.completions = queue
        lease = self.client.portal.call(queue.acquire)
        try:
            result = self.post()
            self.assertEqual(result.status_code, 503)
            self.retriever.retrieve.assert_not_called()
        finally:
            lease.release()

    @patch("vectorizer.server.generate_answer", side_effect=ChatError("Ollama unavailable"))
    def test_upstream_and_retrieval_errors_release_slots(self, generate):
        self.assertEqual(self.post().status_code, 502)
        self.retriever.retrieve.side_effect = RuntimeError("private path or credentials")
        result = self.post()
        self.assertEqual(result.status_code, 503)
        self.assertNotIn("private", result.text)
        async def reacquire():
            return await asyncio.wait_for(self.app.state.completions.acquire(), timeout=0.2)

        lease = self.client.portal.call(reacquire)
        lease.release()

    @patch("vectorizer.server.stream_answer")
    def test_stream_errors_before_and_after_headers(self, stream):
        def failing(early):
            if not early:
                yield "partial"
            raise ChatError("Ollama stream failed")
        stream.return_value = failing(True)
        self.assertEqual(self.post({**self.payload, "stream": True}).status_code, 502)
        stream.return_value = failing(False)
        result = self.post({**self.payload, "stream": True})
        self.assertEqual(result.status_code, 200)
        self.assertIn('"type": "upstream_error"', result.text)
        self.assertTrue(result.text.endswith("data: [DONE]\n\n"))
        self.assertNotIn('"finish_reason": "stop"', result.text)


if __name__ == "__main__":
    unittest.main()
