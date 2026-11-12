"""Application-managed hybrid retrieval; it exposes search, never execution tools."""

from __future__ import annotations

from pathlib import Path
import re
import sqlite3

import chromadb

from .embeddings import create_embeddings
from common.model_config import DEFAULT_EMBEDDING_MODEL
from common.paths import DEFAULT_DB_PATH, resolve_path
from .keyword_index import bm25_path as _bm25_path


class Retriever:
    """Merge semantic Chroma matches with local SQLite FTS5 BM25 results."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        collection_name: str = "repo_documents",
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        ollama_url: str | None = None,
        top_k: int = 8,
        context_budget: int = 18000,
        keep_alive_seconds: int | None = None,
    ) -> None:
        if top_k < 1 or context_budget < 1000:
            raise ValueError("top_k must be positive and context_budget at least 1000")
        self.db_path = resolve_path(db_path)
        self.collection_name = collection_name
        self.top_k = top_k
        self.context_budget = context_budget
        self.embedding_model = embedding_model
        self.ollama_url = ollama_url
        self._client = chromadb.PersistentClient(path=str(self.db_path))
        self._collection = self._client.get_collection(name=collection_name)
        options = {} if keep_alive_seconds is None else {"keep_alive_seconds": keep_alive_seconds}
        self._embedder = create_embeddings(embedding_model, ollama_url, **options)
        self._bm25 = _bm25_path(self.db_path, collection_name)

    def _keyword_search(self, question: str, limit: int) -> list[dict]:
        if not self._bm25.is_file():
            return []
        terms = list(dict.fromkeys(re.findall(r"[\w./:-]+", question.lower())))
        if not terms:
            return []
        # Quote all tokens so user-provided FTS syntax is never interpreted.
        query = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms[:32])
        db = None
        try:
            db = sqlite3.connect(f"file:{self._bm25.as_posix()}?mode=ro", uri=True)
            rows = db.execute(
                "SELECT id, text, path, start_line, end_line, heading, revision, repository, bm25(chunks) AS rank "
                "FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT ?", (query, limit)
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            if db is not None:
                db.close()
        return [
            {"id": row[0], "text": row[1], "metadata": {"path": row[2], "start_line": int(row[3]) if str(row[3]).isdecimal() else "?", "end_line": int(row[4]) if str(row[4]).isdecimal() else "?", "heading": row[5], "revision": row[6], "repository": row[7], "source": f"{row[2]}:{row[3]}-{row[4]}"}, "score": float(-row[8]), "_rank": index + 1}
            for index, row in enumerate(rows)
        ]

    def retrieve(self, question: str, history: list[dict] | None = None) -> list[dict]:
        """Return evidence ranked by reciprocal-rank fusion within the context budget."""
        question = question.strip()
        if not question:
            return []
        count = self._collection.count()
        if not count:
            return []
        candidates: dict[str, dict] = {}
        query_vector = self._embedder.embed_query(question)
        vector_result = self._collection.query(
            query_embeddings=[query_vector], n_results=min(count, max(self.top_k * 4, 20)),
            include=["documents", "metadatas", "distances"],
        )
        for rank, (doc_id, text, metadata, distance) in enumerate(zip(
            vector_result.get("ids", [[]])[0], vector_result.get("documents", [[]])[0],
            vector_result.get("metadatas", [[]])[0], vector_result.get("distances", [[]])[0],
        ), 1):
            candidates[doc_id] = {"id": doc_id, "text": text, "metadata": metadata or {}, "score": 1.0 / (60 + rank), "_vector_rank": rank, "_keyword_rank": None, "_distance": distance}

        for item in self._keyword_search(question, min(count, max(self.top_k * 4, 20))):
            existing = candidates.get(item["id"])
            if existing:
                existing["score"] += 1.0 / (60 + item["_rank"])
                existing["_keyword_rank"] = item["_rank"]
            else:
                item["score"] = 1.0 / (60 + item["_rank"])
                item["_keyword_rank"] = item.pop("_rank")
                item["_vector_rank"] = None
                candidates[item["id"]] = item

        ranked = sorted(candidates.values(), key=lambda item: item["score"], reverse=True)
        output, used = [], 0
        for item in ranked:
            text = item["text"]
            if used and used + len(text) > self.context_budget:
                continue
            if len(text) > self.context_budget:
                text = text[:self.context_budget]
            clean = {key: value for key, value in item.items() if not key.startswith("_")}
            clean["text"] = text
            output.append(clean)
            used += len(text)
            if len(output) >= self.top_k:
                break
        return output
