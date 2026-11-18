"""Validated model/runtime settings shared by RAG, service startup, and chat."""

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path

from .paths import PROJECT_ROOT, resolve_path

DEFAULT_CONTEXT_WINDOW_TOKENS = 16384
DEFAULT_KEEP_ALIVE_SECONDS = 1800
DEFAULT_MODELS_CONFIG = PROJECT_ROOT / "config" / "models.json"


def validate_context_window(value: int) -> None:
    """Reject non-integer context sizes or allocations smaller than 512 tokens.

    Raises `ValueError` rather than silently coercing strings or booleans.
    This validates configuration only; actual memory availability is not checked.
    """
    if type(value) is not int or value < 512:
        raise ValueError("context_window_tokens must be an integer of at least 512")


def validate_keep_alive(value: int) -> None:
    """Require a positive integer model-retention duration, in seconds.

    Raises `ValueError` for zero, negative values, booleans, or non-integers.
    """
    if type(value) is not int or value < 1:
        raise ValueError("keep_alive_seconds must be a positive integer")


def _environment_integer(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


@dataclass(frozen=True)
class ModelSettings:
    """Immutable, validated chat/embedding configuration shared across the stack.

    Model names are exact Ollama tags, not paths. Context and retention settings
    describe allocation and residency requests; they do not download models or
    guarantee GPU residency. Use `from_file` for authoritative managed settings
    and `from_env` for standalone commands that accept environment overrides.
    """
    chat_model: str = field(default_factory=lambda: ModelSettings.from_env().chat_model)
    embedding_model: str = field(default_factory=lambda: ModelSettings.from_env().embedding_model)
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS
    keep_alive_seconds: int = DEFAULT_KEEP_ALIVE_SECONDS

    def __post_init__(self):
        for name in ("chat_model", "embedding_model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{name.replace('_', ' ')} must be nonempty text without surrounding whitespace")
        validate_context_window(self.context_window_tokens)
        validate_keep_alive(self.keep_alive_seconds)

    def to_dict(self) -> dict:
        """Return a new dictionary containing the four model/runtime settings."""
        return asdict(self)

    def runtime_configuration(self, ollama_url: str) -> dict:
        """Return model settings plus the Ollama URL for API readiness comparison.

        The result contains no API key and is suitable for the authenticated
        runtime-configuration endpoint. No HTTP request is performed here.
        """
        return {**self.to_dict(), "ollama_url": ollama_url}

    def environment(self, config_path: Path) -> dict[str, str]:
        """Return string-valued environment overrides for managed child processes.

        `config_path` identifies the selected model JSON. The returned mapping
        includes both model names, context, and retention; callers must merge it
        into a child environment. The current process environment is unchanged.
        """
        return {"RAG_MODEL_CONFIG": str(config_path), "OLLAMA_CHAT_MODEL": self.chat_model,
                "OLLAMA_EMBEDDING_MODEL": self.embedding_model,
                "OLLAMA_CONTEXT_WINDOW_TOKENS": str(self.context_window_tokens),
                "OLLAMA_KEEP_ALIVE_SECONDS": str(self.keep_alive_seconds)}

    @classmethod
    def from_file(cls, path: Path) -> "ModelSettings":
        """Read UTF-8/BOM JSON and construct validated settings.

        Requires chat/embedding names and context size; retention is optional.
        Unknown keys, unreadable files, malformed JSON, and invalid values raise
        `ValueError`. Environment overrides are deliberately not applied.
        """
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot read model configuration {path}: {error}") from error
        required = {"chat_model", "embedding_model", "context_window_tokens"}
        allowed = required | {"keep_alive_seconds"}
        if not isinstance(payload, dict) or not required.issubset(payload) or set(payload) - allowed:
            raise ValueError(f"Model configuration {path} must contain only chat_model, embedding_model, context_window_tokens "
                             "and optional keep_alive_seconds")
        return cls(**payload)

    @classmethod
    def from_env(cls) -> "ModelSettings":
        """Load the selected model JSON and apply standalone environment overrides.

        `RAG_MODEL_CONFIG` chooses the file; `OLLAMA_CHAT_MODEL`,
        `OLLAMA_EMBEDDING_MODEL`, `OLLAMA_CONTEXT_WINDOW_TOKENS`, and
        `OLLAMA_KEEP_ALIVE_SECONDS` override its values. Invalid configuration
        raises `ValueError`; this method performs no model-server requests.
        """
        path = resolve_path(os.getenv("RAG_MODEL_CONFIG", str(DEFAULT_MODELS_CONFIG)))
        settings = cls.from_file(path)
        context = _environment_integer("OLLAMA_CONTEXT_WINDOW_TOKENS", settings.context_window_tokens)
        keep_alive = _environment_integer("OLLAMA_KEEP_ALIVE_SECONDS", settings.keep_alive_seconds)
        return cls(os.getenv("OLLAMA_CHAT_MODEL", settings.chat_model),
                   os.getenv("OLLAMA_EMBEDDING_MODEL", settings.embedding_model), context, keep_alive)


def resolve_embedding_model(model: str | None = None) -> str:
    """Return an explicit model unchanged, or resolve it through `ModelSettings.from_env`.

    Supplying a model avoids reading model configuration in this helper.
    """
    return ModelSettings.from_env().embedding_model if model is None else model


def resolve_model_names(chat_model: str | None = None,
                        embedding_model: str | None = None) -> tuple[str, str]:
    """Return `(chat_model, embedding_model)` with explicit arguments taking priority.

    Missing names come from `ModelSettings.from_env`. When both names are
    supplied, no model configuration file is read.
    """
    if chat_model is not None and embedding_model is not None:
        return chat_model, embedding_model
    settings = ModelSettings.from_env()
    return (chat_model if chat_model is not None else settings.chat_model,
            embedding_model if embedding_model is not None else settings.embedding_model)
