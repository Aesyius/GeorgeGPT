"""Integration test that embeds every architecture.md chunk with Ollama."""

import json
import os
from pathlib import Path
import unittest

import requests

from vectorizer.document_loader import load_and_split_text
from vectorizer.embeddings import create_embeddings
from vectorizer.model_config import DEFAULT_EMBEDDING_MODEL


class OllamaEmbeddingTest(unittest.TestCase):
    def test_embed_all_architecture_chunks(self) -> None:
        source = Path(__file__).resolve().parent / "fixtures" / "architecture.md"
        documents = load_and_split_text(source, chunk_size=1000, chunk_overlap=150)
        chunks = [document.page_content for document in documents]
        self.assertTrue(chunks, "architecture.md should produce chunks")

        model = os.getenv("OLLAMA_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        try:
            tags_response = requests.get(f"{ollama_url}/api/tags", timeout=3)
            tags_response.raise_for_status()
        except requests.RequestException as error:
            self.skipTest(f"Ollama is unavailable at {ollama_url}: {error}")
        installed_models = [item.get("name", "") for item in tags_response.json().get("models", [])]
        if not any(name == model or name.startswith(f"{model}:") for name in installed_models):
            self.skipTest(f"Ollama model {model!r} is not pulled; run `ollama pull {model}`")

        print(f"Embedding {len(chunks)} chunks from {source.name} with Ollama model {model}...", flush=True)
        vectors = create_embeddings(model, ollama_url).embed_documents(chunks)

        self.assertEqual(len(vectors), len(chunks))
        self.assertTrue(vectors[0], "embedding vectors should not be empty")
        self.assertTrue(all(len(vector) == len(vectors[0]) for vector in vectors))

        print(f"Received {len(vectors)} embeddings; dimension={len(vectors[0])}.")
        for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
            print(f"\n{'=' * 16} Chunk {index} {'=' * 16}")
            print(chunk)
            print(f"Embedding ({len(vector)} values):")
            print(json.dumps(vector))


if __name__ == "__main__":
    unittest.main(verbosity=2)
