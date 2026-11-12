#!/usr/bin/env python3
"""Ask a grounded question against the local repository index using Ollama."""

from __future__ import annotations

import argparse
import os
import sys

from .chat import ChatError, answer_question
from common.model_config import DEFAULT_CHAT_MODEL, DEFAULT_EMBEDDING_MODEL
from common.paths import DEFAULT_DB_PATH, resolve_path
from vectorizer.retrieval import Retriever


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", help="question to answer from the indexed repository")
    parser.add_argument("--db-path", type=resolve_path, default=resolve_path(os.getenv("RAG_DB_PATH", str(DEFAULT_DB_PATH))))
    parser.add_argument("--collection", default=os.getenv("RAG_COLLECTION", "repo_documents"))
    parser.add_argument("--embedding-model", default=os.getenv("OLLAMA_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL))
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--model", default=os.getenv("OLLAMA_CHAT_MODEL", DEFAULT_CHAT_MODEL), help="Ollama chat model name")
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be positive")
    try:
        retriever = Retriever(db_path=args.db_path, collection_name=args.collection,
                              embedding_model=args.embedding_model, ollama_url=args.ollama_url,
                              top_k=args.top_k)
        print(answer_question(args.question, retriever, args.model, args.ollama_url))
    except (ChatError, ValueError, RuntimeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
