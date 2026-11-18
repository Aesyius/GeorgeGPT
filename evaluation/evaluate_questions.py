#!/usr/bin/env python3
"""Run the sample retrieval/answer evaluation set and atomically save JSON results."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json

from Interface.chat import answer_question
from common.model_config import resolve_model_names
from common.paths import PROJECT_ROOT, resolve_path, configured_repository
from common.runtime_defaults import index_collection, index_db_path, ollama_base_url
from vectorizer.retrieval import Retriever
from .artifacts import read_questions, save_report


def main() -> None:
    """Generate/reuse answers for a JSONL evaluation set and save JSON checkpoints.

    Parses question/output/index/model flags, validates unique string IDs, and
    resumes compatible `answered` entries by ID. Each new question retrieves
    evidence and calls Ollama; per-question errors are recorded without aborting
    the batch. Atomically replaces output after each result and prints final
    `ANSWER_FILE`/`COMPLETE` status. A normal return can still contain failed
    answers, so inspect `complete` and each result's status.

    Use a fresh output path after changing source data/questions/settings:
    resume checks do not detect every possible experiment difference.
    `pass_criteria` is retained for human review, not automatically graded.
    """
    root = PROJECT_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=resolve_path, default=root / "sample_questions" / "fak_questions.jsonl")
    parser.add_argument("--output", type=resolve_path, default=root / "sample_questions" / "fak_answers.json")
    parser.add_argument("--db-path", type=resolve_path, default=index_db_path())
    parser.add_argument("--collection", default=index_collection())
    parser.add_argument("--embedding-model", default=None)
    parser.add_argument("--chat-model", default=None)
    parser.add_argument("--ollama-url", default=ollama_base_url())
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be positive")

    try:
        args.chat_model, args.embedding_model = resolve_model_names(args.chat_model, args.embedding_model)
        questions = read_questions(args.questions)
        existing = {}
        if args.output.exists():
            previous = json.loads(args.output.read_text(encoding="utf-8"))
            previous_questions = previous.get("questions_file")
            if ((previous_questions is not None and previous_questions != str(args.questions))
                    or previous.get("collection") != args.collection):
                parser.error(f"existing answer file belongs to another evaluation/index: {args.output}")
            if (previous.get("embedding_model") != args.embedding_model
                    or previous.get("chat_model") != args.chat_model):
                parser.error(f"existing answer file uses different models; choose a new --output: {args.output}")
            existing = {item["id"]: item for item in previous.get("answers", [])
                        if item.get("status") == "answered"}
    except (OSError, ValueError) as error:
        parser.error(str(error))

    base = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": str(configured_repository()),
        "questions_file": str(args.questions),
        "collection": args.collection,
        "embedding_model": args.embedding_model,
        "chat_model": args.chat_model,
        "question_count": len(questions),
        "complete": False,
        "answers": [],
    }
    retriever = Retriever(db_path=args.db_path, collection_name=args.collection,
                          embedding_model=args.embedding_model, ollama_url=args.ollama_url,
                          top_k=args.top_k)
    with closing(retriever):
        for index, question in enumerate(questions, 1):
            if question["id"] in existing:
                result = existing[question["id"]]
                print(f"[{index}/{len(questions)}] {question['id']}: reusing completed answer", flush=True)
            else:
                print(f"[{index}/{len(questions)}] {question['id']}: retrieving and generating", flush=True)
                result = {key: question[key] for key in
                          ("id", "category", "question", "expected_paths", "pass_criteria") if key in question}
                try:
                    result["answer"] = answer_question(question["question"], retriever, args.chat_model, args.ollama_url)
                    result["status"] = "answered"
                except Exception as error:
                    result["status"] = "error"
                    result["error"] = f"{type(error).__name__}: {error}"
                print(f"[{index}/{len(questions)}] {question['id']}: {result['status']}", flush=True)
            base["answers"].append(result)
            base["complete"] = False
            save_report(args.output, base)
    base["complete"] = len(base["answers"]) == len(questions) and all(
        item.get("status") == "answered" for item in base["answers"])
    save_report(args.output, base)
    print(f"ANSWER_FILE={args.output}")
    print(f"COMPLETE={base['complete']} ANSWERS={len(base['answers'])} "
          f"ERRORS={sum(item.get('status') != 'answered' for item in base['answers'])}")


if __name__ == "__main__":
    main()
