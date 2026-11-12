#!/usr/bin/env python3
"""Run the sample retrieval/answer evaluation set and atomically save JSON results."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from Interface.chat import answer_question
from common.model_config import DEFAULT_CHAT_MODEL, DEFAULT_EMBEDDING_MODEL
from common.paths import DEFAULT_DB_PATH, PROJECT_ROOT, resolve_path
from vectorizer.retrieval import Retriever


def _read_questions(path: Path) -> list[dict]:
    questions = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL at {path}:{number}: {error}") from error
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("question"), str):
            raise ValueError(f"expected an object with string id/question at {path}:{number}")
        questions.append(item)
    if not questions:
        raise ValueError(f"no questions found in {path}")
    if len({item["id"] for item in questions}) != len(questions):
        raise ValueError(f"question IDs must be unique in {path}")
    return questions


def _save_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.stem + "_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> None:
    root = PROJECT_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=resolve_path, default=root / "sample_questions" / "fak_questions.jsonl")
    parser.add_argument("--output", type=resolve_path, default=root / "sample_questions" / "fak_answers.json")
    parser.add_argument("--db-path", type=resolve_path, default=resolve_path(os.getenv("RAG_DB_PATH", str(DEFAULT_DB_PATH))))
    parser.add_argument("--collection", default=os.getenv("RAG_COLLECTION", "repo_documents"))
    parser.add_argument("--embedding-model", default=os.getenv("OLLAMA_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL))
    parser.add_argument("--chat-model", default=os.getenv("OLLAMA_CHAT_MODEL", DEFAULT_CHAT_MODEL))
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be positive")

    try:
        questions = _read_questions(args.questions)
        existing = {}
        if args.output.exists():
            previous = json.loads(args.output.read_text(encoding="utf-8"))
            previous_questions = previous.get("questions_file")
            if ((previous_questions is not None and previous_questions != str(args.questions))
                    or previous.get("collection") != args.collection):
                parser.error(f"existing answer file belongs to another evaluation/index: {args.output}")
            existing = {item["id"]: item for item in previous.get("answers", [])
                        if item.get("status") == "answered"}
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))

    base = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": r"C:\Users\Cyber.DESKTOP-MAG5LP5.000\Desktop\Project\FAK",
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
    try:
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
            _save_atomic(args.output, base)
    finally:
        retriever._client.close()
    base["complete"] = len(base["answers"]) == len(questions) and all(
        item.get("status") == "answered" for item in base["answers"])
    _save_atomic(args.output, base)
    print(f"ANSWER_FILE={args.output}")
    print(f"COMPLETE={base['complete']} ANSWERS={len(base['answers'])} "
          f"ERRORS={sum(item.get('status') != 'answered' for item in base['answers'])}")


if __name__ == "__main__":
    main()
