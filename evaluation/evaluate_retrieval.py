"""Evaluate retrieval without generating answers or modifying the indexed corpus."""

from __future__ import annotations

import argparse
import os

from .evaluate_questions import _read_questions, _save_atomic
from common.model_config import DEFAULT_EMBEDDING_MODEL
from common.paths import DEFAULT_DB_PATH, PROJECT_ROOT, resolve_path
from vectorizer.retrieval import Retriever


def evaluate(questions: list[dict], retriever) -> dict:
    """Score path-level relevance proxies; retain passages for completeness review."""
    results = []
    scored = []
    for question in questions:
        evidence = retriever.retrieve(question["question"])
        expected = {path.replace("\\", "/") for path in question.get("expected_paths", [])}
        paths = [str(item.get("metadata", {}).get("path", "")).replace("\\", "/") for item in evidence]
        matched = expected.intersection(paths)
        first_rank = next((rank for rank, path in enumerate(paths, 1) if path in expected), None)
        result = {"id": question["id"], "question": question["question"],
                  "expected_paths": sorted(expected), "matched_paths": sorted(matched),
                  "path_recall_at_k": len(matched) / len(expected) if expected else None,
                  "first_relevant_rank": first_rank,
                  "reciprocal_rank": 1 / first_rank if first_rank else 0.0,
                  "evidence": evidence}
        results.append(result)
        if expected:
            scored.append(result)
    return {"question_count": len(results), "scored_questions": len(scored),
            "mean_path_recall_at_k": sum(item["path_recall_at_k"] for item in scored) / len(scored) if scored else None,
            "mean_reciprocal_rank": sum(item["reciprocal_rank"] for item in scored) / len(scored) if scored else None,
            "note": "Path relevance is a proxy, not proof that a passage fully supports an answer.",
            "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=resolve_path, default=PROJECT_ROOT / "sample_questions/fak_questions.jsonl")
    parser.add_argument("--output", type=resolve_path, required=True, help="new JSON output file; never reuses old results")
    parser.add_argument("--db-path", type=resolve_path, default=DEFAULT_DB_PATH)
    parser.add_argument("--collection", default="repo_documents")
    parser.add_argument("--embedding-model", default=os.getenv("OLLAMA_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL))
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("top-k must be positive")
    if args.output.exists():
        parser.error("output already exists; choose a new file for an independent experiment")
    try:
        questions = _read_questions(args.questions)
        retriever = Retriever(db_path=args.db_path, collection_name=args.collection,
                              embedding_model=args.embedding_model, ollama_url=args.ollama_url, top_k=args.top_k)
        try:
            report = evaluate(questions, retriever)
        finally:
            retriever._client.close()
        report.update({"questions_file": str(args.questions), "db_path": str(args.db_path),
                       "collection": args.collection, "embedding_model": args.embedding_model, "top_k": args.top_k})
        _save_atomic(args.output, report)
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))
    print(f"Path recall@{args.top_k}: {report['mean_path_recall_at_k']}")
    print(f"MRR@{args.top_k}: {report['mean_reciprocal_rank']}")
    print(f"Results: {args.output}")


if __name__ == "__main__":
    main()
