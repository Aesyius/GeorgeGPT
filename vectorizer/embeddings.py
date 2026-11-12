"""Shared LangChain Ollama embeddings for indexing and retrieval."""

import os
import sys
import time

from langchain_ollama import OllamaEmbeddings
from .model_config import DEFAULT_EMBEDDING_MODEL, DEFAULT_KEEP_ALIVE_SECONDS, validate_keep_alive


def create_embeddings(
    model: str = DEFAULT_EMBEDDING_MODEL,
    base_url: str | None = None,
    *, keep_alive_seconds: int | None = None,
) -> OllamaEmbeddings:
    """Create the Ollama embedding client used for both documents and queries."""
    if keep_alive_seconds is None:
        keep_alive_seconds = int(os.getenv("OLLAMA_KEEP_ALIVE_SECONDS", str(DEFAULT_KEEP_ALIVE_SECONDS)))
    validate_keep_alive(keep_alive_seconds)
    return OllamaEmbeddings(
        model=model,
        base_url=base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        keep_alive=keep_alive_seconds,
    )


def embed_documents_with_retry(embedder: OllamaEmbeddings, texts: list[str], attempts: int = 3) -> list[list[float]]:
    """Retry transient Ollama runner failures without discarding completed batches."""
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
