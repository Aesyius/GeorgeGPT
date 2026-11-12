"""Load text files into LangChain documents split for RAG indexing."""

from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .paths import resolve_path


def load_and_split_text(
    source: Path,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
) -> list[Document]:
    """Read a UTF-8 text file and return chunks with source/start metadata."""
    if chunk_size < 1 or chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk size must be positive and overlap must be non-negative and smaller than chunk size")

    source = resolve_path(source)
    # Windows editors may save UTF-8 with a BOM; do not embed it as content.
    text = source.read_text(encoding="utf-8-sig")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        add_start_index=True,
    )
    return splitter.create_documents([text], metadatas=[{"source": str(source)}])
