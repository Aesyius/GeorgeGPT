"""Warm real inference paths before advertising the application as ready."""

import time

from Interface.chat import SYSTEM_PROMPT
from common.model_config import ModelSettings
from .service_config import BootError
from .service_runtime import request_json


def warm_models(ollama_url: str, models: ModelSettings, timeout: int) -> None:
    """Exercise embeddings and short generation, then verify joint residency.

    Loading weights alone does not exercise first-prompt GPU/runtime setup.
    No repository scan, credentials, or user conversation is involved here.
    `ollama_url` selects the server, `models` supplies exact tags/allocation,
    and `timeout` bounds each warmup request in seconds. Performs one short
    tool-free chat and one embedding request, then checks `/api/ps` contains
    both names. This verifies joint loading, not guaranteed full GPU residency.
    Invalid responses, request failures, or eviction raise `BootError`.
    """
    base = ollama_url.rstrip("/")
    # Load the larger model first. Ollama can conservatively evict an already
    # loaded small model while estimating a larger model's initial allocation.
    requests = [
        ("chat", "/api/chat", {"model": models.chat_model, "stream": False, "think": False,
                              "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                           {"role": "user", "content": "Reply with OK."}],
                              "options": {"num_ctx": models.context_window_tokens, "num_predict": 8},
                              "keep_alive": models.keep_alive_seconds}),
        ("embedding", "/api/embed", {"model": models.embedding_model,
                                    "input": "Repository retrieval startup warmup.",
                                    "keep_alive": models.keep_alive_seconds}),
    ]
    for kind, endpoint, payload in requests:
        print(f"Warming {kind} model: {payload['model']}...", flush=True)
        started = time.monotonic()
        try:
            _, body = request_json(base + endpoint, payload=payload, timeout=timeout)
            if not isinstance(body, dict) or body.get("error"):
                raise ValueError("invalid warmup response")
            if kind == "embedding":
                vectors = body.get("embeddings")
                valid = isinstance(vectors, list) and len(vectors) == 1 and isinstance(vectors[0], list) and bool(vectors[0])
            else:
                message = body.get("message") or {}
                valid = (body.get("done") is True and isinstance(message, dict)
                         and isinstance(message.get("content"), str) and bool(message["content"].strip())
                         and not message.get("tool_calls"))
            if not valid:
                raise ValueError("warmup did not produce the expected output")
        except (OSError, ValueError) as error:
            raise BootError(f"{kind.capitalize()} model warmup failed for {payload['model']}; check ollama.log.") from error
        print(f"Warmed {kind} model in {time.monotonic() - started:.2f}s.", flush=True)
    try:
        _, body = request_json(base + "/api/ps", timeout=5)
        if not isinstance(body, dict) or not isinstance(body.get("models"), list):
            raise ValueError("invalid resident-model response")
        if not all(isinstance(item, dict) for item in body["models"]):
            raise ValueError("invalid resident-model entries")
        resident = {item.get("name", item.get("model")) for item in body["models"]}
    except (OSError, ValueError) as error:
        raise BootError("Cannot verify resident models after warmup; check ollama.log.") from error
    missing = {models.chat_model, models.embedding_model} - resident
    if missing:
        raise BootError(f"Warmup models did not remain loaded together: {sorted(missing)}. "
                        "Check available memory and reduce the context window if necessary.")
