"""Smoke test loading and chunking the repository's architecture document."""

from pathlib import Path
import unittest

from vectorizer.document_loader import load_and_split_text


class ArchitectureLoadTest(unittest.TestCase):
    def test_load_and_split_architecture(self) -> None:
        source = Path(__file__).resolve().parent / "fixtures" / "architecture.md"
        text = source.read_text(encoding="utf-8")
        documents = load_and_split_text(source, chunk_size=1000, chunk_overlap=150)

        self.assertTrue(text.strip(), "architecture.md should not be empty")
        self.assertTrue(documents, "architecture.md should produce at least one chunk")
        self.assertTrue(all(document.page_content.strip() for document in documents))
        self.assertTrue(all(document.metadata["source"] == str(source) for document in documents))
        self.assertTrue(all(document.metadata["start_index"] >= 0 for document in documents))

        print(f"Loaded {source.name}: {len(text)} characters -> {len(documents)} chunks")
        for index, document in enumerate(documents):
            print(f"\n{'=' * 20} Chunk {index} (start={document.metadata['start_index']}) {'=' * 20}")
            print(document.page_content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
