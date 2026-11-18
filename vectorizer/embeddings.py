"""Shared LangChain Ollama embeddings for indexing and retrieval."""

import os
import sys
import time

from httpx import TimeoutException
from langchain_ollama import OllamaEmbeddings
from common.model_config import resolve_embedding_model, DEFAULT_KEEP_ALIVE_SECONDS, validate_keep_alive
from common.runtime_defaults import ollama_base_url

DEFAULT_EMBEDDING_TIMEOUT = 120


def validate_embedding_timeout(timeout_seconds: int) -> None:
    """Raise ValueError unless the indexing request timeout is 1–3600 seconds."""
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise ValueError("embedding timeout must be an integer from 1 to 3600 seconds")


def create_embeddings(
    model: str | None = None,
    base_url: str | None = None,
    *, keep_alive_seconds: int | None = None, timeout_seconds: int | None = None,
) -> OllamaEmbeddings:
    """Configure the shared Ollama embedding client without embedding any text.

    Explicit `model` and `base_url` override configured/environment defaults.
    Retention comes from `keep_alive_seconds`, then `OLLAMA_KEEP_ALIVE_SECONDS`,
    then the shared default. Returns a LangChain `OllamaEmbeddings` client.
    `timeout_seconds` sets the client HTTP timeout when supplied.
    Invalid retention raises `ValueError`; actual server/model availability is
    established when a caller makes an embedding request.
    """
    if keep_alive_seconds is None:
        keep_alive_seconds = int(os.getenv("OLLAMA_KEEP_ALIVE_SECONDS", str(DEFAULT_KEEP_ALIVE_SECONDS)))
    validate_keep_alive(keep_alive_seconds)
    options = {}
    if timeout_seconds is not None:
        validate_embedding_timeout(timeout_seconds)
        options["client_kwargs"] = {"timeout": timeout_seconds}
    return OllamaEmbeddings(
        model=resolve_embedding_model(model),
        base_url=base_url or ollama_base_url(),
        keep_alive=keep_alive_seconds,
        **options,
    )


def embed_documents_with_retry(embedder: OllamaEmbeddings, texts: list[str], attempts: int = 3) -> list[list[float]]:
    """Embed one text batch with bounded retries and exponential backoff.

    Returns vectors in the embedder's input order. Each failed attempt is logged
    to stderr; delays are 1, 2, 4, ... seconds. All embedder exceptions are retried,
    not just network failures. `attempts` must be positive. Exhaustion raises
    `RuntimeError` chained from the last error. Persistence is the caller's job,
    so earlier successfully stored batches are not rolled back.
    """
    if attempts < 1:
        raise ValueError("attempts must be positive")
    for attempt in range(1, attempts + 1):
        try:
            return embedder.embed_documents(texts)
        except Exception as error:
            detail = str(error).strip() or type(error).__name__
            if isinstance(error, TimeoutException):
                detail = (f"request timed out ({detail}); check the Ollama server log, "
                          "reduce the embedding batch size, or increase the embedding timeout")
            if attempt == attempts:
                raise RuntimeError(f"Ollama embedding request failed after {attempts} attempts: {detail}") from error
            delay = 2 ** (attempt - 1)
            print(f"Ollama embedding request failed (attempt {attempt}/{attempts}); retrying in {delay}s: {detail}",
                  file=sys.stderr, flush=True)
            time.sleep(delay)
    raise AssertionError("unreachable")
