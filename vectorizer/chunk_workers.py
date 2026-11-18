"""Ordered, bounded repository file preparation without storage dependencies."""

from collections import deque
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
import hashlib
from itertools import islice
import multiprocessing
from pathlib import Path

from .chunking import chunk_file

MAX_CHUNK_WORKERS = 10


def validate_chunk_workers(workers: int) -> None:
    """Raise ValueError unless the worker count is an integer from 1 to 10."""
    if type(workers) is not int or workers < 1:
        raise ValueError("chunk workers must be a positive integer")
    if workers > MAX_CHUNK_WORKERS:
        raise ValueError(f"chunk workers cannot exceed {MAX_CHUNK_WORKERS}")


def _prepare_file(path: Path, relative: str, chunk_size: int, overlap: int,
                  max_embedding_bytes: int) -> tuple[str, str, list[dict]] | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return None
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        chunks = chunk_file(path, relative, chunk_size=chunk_size, overlap=overlap,
                            text=text, max_embedding_bytes=max_embedding_bytes)
    except ValueError as error:
        raise ValueError(f"While chunking {relative}: {error}") from error
    except Exception as error:
        error.add_note(f"While chunking {relative}")
        raise
    return relative, content_hash, chunks


def iter_prepared_files(files: list[Path], root: Path, *, workers: int, chunk_size: int,
                        overlap: int, max_embedding_bytes: int
                        ) -> Iterator[tuple[str, str, list[dict]] | None]:
    """Yield one result per file in discovery order, including skips as None.

    Consume fully or close the iterator to release the pool. At most twice the
    effective worker count of file jobs can be submitted but not yet yielded.
    """
    validate_chunk_workers(workers)
    jobs = ((path, path.relative_to(root).as_posix(), chunk_size, overlap,
             max_embedding_bytes) for path in files)
    workers = min(workers, len(files))
    if workers <= 1:
        for job in jobs:
            yield _prepare_file(*job)
        return

    executor = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))
    pending = deque()
    try:
        for job in islice(jobs, workers * 2):
            pending.append(executor.submit(_prepare_file, *job))
        while pending:
            yield pending.popleft().result()
            if (job := next(jobs, None)) is not None:
                pending.append(executor.submit(_prepare_file, *job))
    except BrokenProcessPool as error:
        raise RuntimeError("Repository chunk worker pool stopped unexpectedly") from error
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
