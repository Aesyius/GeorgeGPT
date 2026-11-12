"""Authenticated, bounded OpenAI-compatible facade for local repository RAG."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import hmac
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .chat import (ChatError, MAX_QUESTION_CHARS, bounded_history, build_messages,
                   generate_answer, prepare_evidence, stream_answer)
from .completion_queue import CompletionQueue, CompletionQueueFull
from .paths import DEFAULT_DB_PATH, resolve_path
from .model_config import (DEFAULT_CHAT_MODEL, DEFAULT_EMBEDDING_MODEL,
                           DEFAULT_CONTEXT_WINDOW_TOKENS, DEFAULT_KEEP_ALIVE_SECONDS, ModelSettings)

MODEL_ID = "GeorgeGPT"
MAX_BODY_BYTES = 65536
MAX_MESSAGES = 32
MAX_INPUT_CHARS = 32000
# Run one generation at a time, but accept ordinary overlapping WebUI tasks.
MAX_CONCURRENT_REQUESTS = 1
MAX_WAITING_REQUESTS = 4
REQUEST_QUEUE_TIMEOUT_SECONDS = 90
# Open WebUI may advertise client-side tools to OpenAI-compatible providers.
# These declarations are accepted only for compatibility and are discarded;
# this service never forwards or executes them. Tool-call conversation messages
# remain forbidden.
IGNORED_TOOL_DECLARATIONS = {"tools", "tool_choice", "functions", "function_call"}
FORBIDDEN_MESSAGE_FIELDS = {"tools", "tool_choice", "functions", "function_call", "tool_calls"}


@dataclass(frozen=True)
class ServerConfig:
    api_key: str
    chat_model: str = DEFAULT_CHAT_MODEL
    db_path: Path = DEFAULT_DB_PATH
    collection_name: str = "repo_documents"
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    ollama_url: str = "http://localhost:11434"
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS
    keep_alive_seconds: int = DEFAULT_KEEP_ALIVE_SECONDS

    def __post_init__(self):
        if not self.api_key or not self.api_key.strip():
            raise ValueError("RAG_API_KEY is required")
        ModelSettings(self.chat_model, self.embedding_model, self.context_window_tokens, self.keep_alive_seconds)
        url = urlsplit(self.ollama_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("OLLAMA_BASE_URL must be an HTTP(S) base URL without credentials, query, or fragment")

    @classmethod
    def from_env(cls):
        models = ModelSettings.from_env()
        return cls(
            api_key=os.getenv("RAG_API_KEY", ""),
            chat_model=models.chat_model,
            db_path=resolve_path(os.getenv("RAG_DB_PATH", str(DEFAULT_DB_PATH))),
            collection_name=os.getenv("RAG_COLLECTION", "repo_documents"),
            embedding_model=models.embedding_model,
            ollama_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            context_window_tokens=models.context_window_tokens,
            keep_alive_seconds=models.keep_alive_seconds,
        )


def _validate(payload):
    if not isinstance(payload, dict):
        raise HTTPException(400, "Request must be a JSON object")
    if payload.get("model") != MODEL_ID:
        raise HTTPException(400, f"Only model {MODEL_ID!r} is supported")
    if type(payload.get("stream", False)) is not bool:
        raise HTTPException(400, "stream must be a boolean")
    # Open WebUI sends max_tokens as a standard OpenAI-compatible request
    # field. The RAG service keeps its own bounded generation settings, so
    # accept but intentionally do not forward this client hint to Ollama.
    unsupported = set(payload) - {"model", "messages", "stream", "max_tokens"} - IGNORED_TOOL_DECLARATIONS
    if unsupported:
        raise HTTPException(400, "Unsupported request fields: " + ", ".join(sorted(unsupported)))
    if "max_tokens" in payload and (type(payload["max_tokens"]) is not int or payload["max_tokens"] < 1):
        raise HTTPException(400, "max_tokens must be a positive integer")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
        raise HTTPException(400, f"messages must contain 1-{MAX_MESSAGES} text messages")
    total = 0
    for message in messages:
        if not isinstance(message, dict) or FORBIDDEN_MESSAGE_FIELDS.intersection(message):
            raise HTTPException(400, "Invalid message; tools are not supported")
        if set(message) - {"role", "content"}:
            raise HTTPException(400, "Only role and content are supported in messages")
        if not isinstance(message.get("role"), str) or message["role"] not in {"system", "user", "assistant"} or not isinstance(message.get("content"), str):
            raise HTTPException(400, "Messages must have system/user/assistant role and text content")
        total += len(message["content"])
    if total > MAX_INPUT_CHARS:
        raise HTTPException(413, "Message text exceeds the input limit")
    question = messages[-1]["content"]
    if messages[-1]["role"] != "user" or not question.strip() or len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(400, f"Last message must be a nonempty user question of at most {MAX_QUESTION_CHARS} characters")
    return question, bounded_history(messages[:-1]), payload.get("stream", False)


def create_app(config: ServerConfig | None = None, retriever=None) -> FastAPI:
    """Create the app; validate configuration and load the index at startup.

    Explicit config/retriever injection supports offline tests and embedding.
    No CORS middleware or tool/execution routes are installed.
    """
    @asynccontextmanager
    async def lifespan(app):
        app.state.config = config if config is not None else ServerConfig.from_env()
        if retriever is None:
            from .retrieval import Retriever

            cfg = app.state.config
            app.state.retriever = await run_in_threadpool(
                Retriever, db_path=cfg.db_path, collection_name=cfg.collection_name,
                embedding_model=cfg.embedding_model, ollama_url=cfg.ollama_url,
                top_k=8, context_budget=18000, keep_alive_seconds=cfg.keep_alive_seconds,
            )
        else:
            app.state.retriever = retriever
        app.state.completions = CompletionQueue(MAX_WAITING_REQUESTS, REQUEST_QUEUE_TIMEOUT_SECONDS)
        yield

    app = FastAPI(title="Repository RAG", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"message": str(error.detail), "type": "invalid_request_error" if error.status_code < 500 else "server_error"}},
            headers=error.headers,
        )

    def authenticate(request):
        expected = ("Bearer " + app.state.config.api_key).encode("utf-8")
        supplied = request.headers.get("authorization", "").encode("utf-8")
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(401, "Invalid or missing bearer API key", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/v1/models")
    async def models(request: Request):
        authenticate(request)
        return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "created": 0, "owned_by": "local-rag"}]}

    @app.get("/v1/config")
    async def runtime_config(request: Request):
        authenticate(request)
        cfg = app.state.config
        models = ModelSettings(cfg.chat_model, cfg.embedding_model, cfg.context_window_tokens, cfg.keep_alive_seconds)
        return models.runtime_configuration(cfg.ollama_url)

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        authenticate(request)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                raise HTTPException(413, "Request body exceeds the size limit")
            body.extend(chunk)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPException(400, "Invalid JSON request")
        question, history, streaming = _validate(payload)
        try:
            lease = await app.state.completions.acquire()
        except CompletionQueueFull as error:
            raise HTTPException(429, "The request queue is full; please retry shortly", headers={"Retry-After": "1"}) from error
        except TimeoutError as error:
            raise HTTPException(503, "Timed out waiting for the model; please retry", headers={"Retry-After": "1"}) from error
        release = lease.release

        cfg = app.state.config
        upstream = None
        streaming_handoff = False
        try:
            if await request.is_disconnected():
                raise HTTPException(499, "Client disconnected while waiting for the model")
            try:
                raw = await run_in_threadpool(app.state.retriever.retrieve, question, history=history)
                evidence = prepare_evidence(raw)
                messages = build_messages(question, evidence, history)
            except (ValueError, RuntimeError) as error:
                raise HTTPException(503, "Repository retrieval unavailable; check index and embedding configuration") from error
            identifier = "chatcmpl-" + uuid.uuid4().hex
            created = int(time.time())
            def chunk(delta, finish=None):
                return {"id": identifier, "object": "chat.completion.chunk", "created": created,
                        "model": MODEL_ID, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

            if not streaming:
                try:
                    answer = await run_in_threadpool(generate_answer, messages, cfg.chat_model, cfg.ollama_url,
                                                     context_window_tokens=cfg.context_window_tokens,
                                                     keep_alive_seconds=cfg.keep_alive_seconds)
                except ChatError as error:
                    raise HTTPException(502, str(error)) from error
                return {"id": identifier, "object": "chat.completion", "created": created,
                        "model": MODEL_ID, "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}]}

            upstream = stream_answer(messages, cfg.chat_model, cfg.ollama_url,
                                     context_window_tokens=cfg.context_window_tokens,
                                     keep_alive_seconds=cfg.keep_alive_seconds)
            sentinel = object()
            # Prime before sending HTTP headers, so early upstream errors become 502.
            try:
                first = await run_in_threadpool(next, upstream, sentinel)
            except ChatError as error:
                raise HTTPException(502, str(error)) from error

            def event(value):
                return "data: " + json.dumps(value, ensure_ascii=False) + "\n\n"

            def close_stream():
                try:
                    upstream.close()
                finally:
                    release()

            async def events():
                try:
                    yield event(chunk({"role": "assistant"}))
                    if first is not sentinel:
                        yield event(chunk({"content": first}))
                    while True:
                        value = await run_in_threadpool(next, upstream, sentinel)
                        if value is sentinel:
                            break
                        yield event(chunk({"content": value}))
                    yield event(chunk({}, "stop"))
                except ChatError as error:
                    yield event({"error": {"message": str(error), "type": "upstream_error"}})
                finally:
                    close_stream()
                yield "data: [DONE]\n\n"

            response = StreamingResponse(events(), media_type="text/event-stream",
                                         headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                                         background=BackgroundTask(close_stream))
            streaming_handoff = True
            return response
        finally:
            if not streaming_handoff:
                if upstream is not None:
                    upstream.close()
                release()

    return app


app = create_app()
