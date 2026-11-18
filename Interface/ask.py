#!/usr/bin/env python3
"""Ask a grounded question against the local repository index using Ollama."""

from __future__ import annotations

import argparse
from contextlib import closing
import sys

from .chat import ChatError, answer_question
from common.model_config import resolve_model_names
from common.paths import resolve_path
from common.runtime_defaults import index_collection, index_db_path, ollama_base_url
from vectorizer.retrieval import Retriever


def main() -> None:
    """Answer one CLI question using the existing hybrid index and Ollama.

    Parses index/model/endpoint overrides, retrieves evidence, and prints the
    generated answer. Requires a matching index and running chat/embedding
    models; does not start services or require WebUI/API credentials.
    Invalid `top_k` is an argparse error; handled retrieval/generation failures
    print to stderr and exit with status 1. Use `--help` for supported flags.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", help="question to answer from the indexed repository")
    parser.add_argument("--db-path", type=resolve_path, default=index_db_path())
    parser.add_argument("--collection", default=index_collection())
    parser.add_argument("--embedding-model", default=None)
    parser.add_argument("--ollama-url", default=ollama_base_url())
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--model", default=None, help="Ollama chat model name (default: configured chat model)")
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be positive")
    try:
        args.model, args.embedding_model = resolve_model_names(args.model, args.embedding_model)
        retriever = Retriever(db_path=args.db_path, collection_name=args.collection,
                              embedding_model=args.embedding_model, ollama_url=args.ollama_url,
                              top_k=args.top_k)
        with closing(retriever):
            print(answer_question(args.question, retriever, args.model, args.ollama_url))
    except (ChatError, ValueError, RuntimeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
