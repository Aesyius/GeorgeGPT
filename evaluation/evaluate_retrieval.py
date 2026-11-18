"""Evaluate retrieval without generating answers or modifying the indexed corpus."""

from __future__ import annotations

import argparse
from contextlib import closing

from .artifacts import read_questions, save_report
from common.model_config import resolve_embedding_model
from common.paths import PROJECT_ROOT, resolve_path
from common.runtime_defaults import index_collection, index_db_path, ollama_base_url
from vectorizer.retrieval import Retriever


def evaluate(questions: list[dict], retriever) -> dict:
    """Retrieve each question and compute expected-path relevance proxies.

    `questions` contains string `id`/`question` and optional `expected_paths`.
    `retriever` supplies `retrieve(question)`. Returns per-question evidence,
    path recall, first relevant rank, reciprocal rank, and aggregate means
    over questions with expected paths. Path separators are normalized.

    Does not generate answers, save files, or modify the corpus. Matching a path
    is not proof that a passage supports the answer. Retriever failures propagate.
    """
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
    """Run retrieval-only evaluation and save a new JSON report.

    Requires a fresh `--output` path, a matching prepared index, and an available
    embedding model. Reads JSONL questions, calls `evaluate`, closes the index
    client, writes report metadata/results atomically, and prints aggregate
    path metrics. Refuses existing output; does not call the chat model.
    Use `--help` for supported index/question/retrieval flags.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=resolve_path, default=PROJECT_ROOT / "sample_questions/fak_questions.jsonl")
    parser.add_argument("--output", type=resolve_path, required=True, help="new JSON output file; never reuses old results")
    parser.add_argument("--db-path", type=resolve_path, default=index_db_path())
    parser.add_argument("--collection", default=index_collection())
    parser.add_argument("--embedding-model", default=None)
    parser.add_argument("--ollama-url", default=ollama_base_url())
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("top-k must be positive")
    if args.output.exists():
        parser.error("output already exists; choose a new file for an independent experiment")
    try:
        args.embedding_model = resolve_embedding_model(args.embedding_model)
        questions = read_questions(args.questions)
        retriever = Retriever(db_path=args.db_path, collection_name=args.collection,
                              embedding_model=args.embedding_model, ollama_url=args.ollama_url, top_k=args.top_k)
        with closing(retriever):
            report = evaluate(questions, retriever)
        report.update({"questions_file": str(args.questions), "db_path": str(args.db_path),
                       "collection": args.collection, "embedding_model": args.embedding_model, "top_k": args.top_k})
        save_report(args.output, report)
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))
    print(f"Path recall@{args.top_k}: {report['mean_path_recall_at_k']}")
    print(f"MRR@{args.top_k}: {report['mean_reciprocal_rank']}")
    print(f"Results: {args.output}")


if __name__ == "__main__":
    main()
