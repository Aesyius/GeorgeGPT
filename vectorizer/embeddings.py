"""Shared LangChain Ollama embeddings for indexing and retrieval."""

import os
import sys
import time

from langchain_ollama import OllamaEmbeddings
from common.model_config import resolve_embedding_model, DEFAULT_KEEP_ALIVE_SECONDS, validate_keep_alive


def create_embeddings(
    model: str | None = None,
    base_url: str | None = None,
    *, keep_alive_seconds: int | None = None,
) -> OllamaEmbeddings:
    """Configure the shared Ollama embedding client without embedding any text.

    Explicit `model` and `base_url` override configured/environment defaults.
    Retention comes from `keep_alive_seconds`, then `OLLAMA_KEEP_ALIVE_SECONDS`,
    then the shared default. Returns a LangChain `OllamaEmbeddings` client.
    Invalid retention raises `ValueError`; actual server/model availability is
    established when a caller makes an embedding request.
    """
    if keep_alive_seconds is None:
        keep_alive_seconds = int(os.getenv("OLLAMA_KEEP_ALIVE_SECONDS", str(DEFAULT_KEEP_ALIVE_SECONDS)))
    validate_keep_alive(keep_alive_seconds)
    return OllamaEmbeddings(
        model=resolve_embedding_model(model),
        base_url=base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        keep_alive=keep_alive_seconds,
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
            if attempt == attempts:
                raise RuntimeError(f"Ollama embedding request failed after {attempts} attempts: {error}") from error
            delay = 2 ** (attempt - 1)
            print(f"Ollama embedding request failed (attempt {attempt}/{attempts}); retrying in {delay}s: {error}",
                  file=sys.stderr, flush=True)
            time.sleep(delay)
    raise AssertionError("unreachable")
