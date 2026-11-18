"""Source-faithful structural chunks and deterministic embedding-only context."""

from __future__ import annotations

import ast
from bisect import bisect_right
from pathlib import Path
import re

CHUNKING_VERSION = "structural-v2"
EMBEDDING_INPUT_VERSION = "context-v1"
DEFAULT_EMBEDDING_BYTES = 4096


def embedding_metadata(chunk: dict, model: str, max_bytes: int) -> dict:
    """Return structural and version metadata for a chunk's stored vector.

    `chunk` must contain heading, symbol, and signature fields from `chunk_file`.
    `model` and `max_bytes` record the embedding settings so later indexing can
    distinguish reusable vectors from incompatible ones. The input is unchanged.
    """
    return {"heading": chunk["heading"] or "", "heading_path": chunk["heading_path"],
            "symbol": chunk["symbol"], "signature": chunk["signature"],
            "embedding_model": model, "chunking_version": CHUNKING_VERSION,
            "embedding_input_version": EMBEDDING_INPUT_VERSION, "max_embedding_bytes": max_bytes}


def embedding_text(text: str, metadata: dict) -> str:
    """Prefix source text with available file, section, symbol, and signature context.

    Returns the embedding input string without changing the stored evidence.
    Empty metadata fields are omitted. `chunk_file` budgets this prefix as part
    of the UTF-8 embedding byte limit.
    """
    fields = [("File", metadata.get("path")),
              ("Section", metadata.get("heading_path") or metadata.get("heading")),
              ("Symbol", metadata.get("symbol")),
              ("Signature", metadata.get("signature"))]
    prefix = "\n".join(f"{label}: {value}" for label, value in fields if value)
    return prefix + "\n\n" + text


def _boundaries(text: str, path: Path, relative: str, chunk_size: int,
                max_embedding_bytes: int) -> list[tuple[int, dict]]:
    """Return line starts with enclosing context; never parse fenced Markdown as headings."""
    boundaries = {0: {}}
    lines = text.splitlines(keepends=True)
    if path.suffix.lower() == ".py":
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            tree = None
        if tree is not None:
            def visit(node, parents=(), parent_context=None):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        names = (*parents, child.name)
                        start = min([child.lineno] + [d.lineno for d in child.decorator_list]) - 1
                        # Keep multiline signatures without copying the function body.
                        end = child.body[0].lineno - 1 if child.body else child.lineno
                        signature = "".join(lines[child.lineno - 1:max(child.lineno, end)]).strip()
                        context = {"heading": child.name, "symbol": ".".join(names), "signature": signature[:300]}
                        boundaries[start] = context
                        body = "".join(lines[start:child.end_lineno]).rstrip()
                        # Keep small classes/functions (including their related members)
                        # whole; only subdivide oversized containers.
                        if (len(body) > chunk_size or len(embedding_text(body, {"path": relative, **context}).encode("utf-8")) > max_embedding_bytes):
                            visit(child, names, context)
                        boundaries[child.end_lineno] = parent_context or {}
                    else:
                        visit(child, parents, parent_context)
            visit(tree)
            return sorted(boundaries.items())

    headings: list[tuple[int, str]] = []
    fence = None
    for index, line in enumerate(lines):
        if path.suffix.lower() in {".md", ".markdown", ".txt", ".rst"}:
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
            if marker:
                run, tail = marker.groups()
                if fence is None:
                    fence = (run[0], len(run))
                elif run[0] == fence[0] and len(run) >= fence[1] and not tail.strip():
                    fence = None
                continue
            if fence:
                continue
            heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
            if heading:
                level, title = len(heading[1]), heading[2]
                headings = [(depth, name) for depth, name in headings if depth < level]
                headings.append((level, title))
                boundaries[index] = {"heading": title, "heading_path": " > ".join(name for _, name in headings)}
        elif path.suffix.lower() in {".py", ".sh", ".bash", ".nix", ".mk", ".make"} or path.name.lower() in {"makefile", "justfile"}:
            if re.match(r"^(?:async\s+)?(?:def|class)\s+\w+|^(?:[A-Za-z_][\w.-]*\s*[:?+]?=)|^\s*(?:function\s+\w+|[A-Za-z_][\w.-]*\s*\(\))|^[\w./%-]+\s*:(?!=)", line):
                boundaries[index] = {"heading": line.strip()[:300], "signature": line.strip()[:300]}
    return sorted(boundaries.items())


def _fenced_spans(text: str, path: Path) -> list[tuple[int, int]]:
    """Protect fitting Markdown code blocks from splits caused by preceding prose."""
    if path.suffix.lower() not in {".md", ".markdown", ".txt", ".rst"}:
        return []
    spans, fence, offset = [], None, 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if marker:
            run, tail = marker.groups()
            if fence is None:
                fence = (run[0], len(run), offset)
            elif run[0] == fence[0] and len(run) >= fence[1] and not tail.strip():
                spans.append((fence[2], offset + len(line)))
                fence = None
        offset += len(line)
    if fence:
        spans.append((fence[2], len(text)))
    return spans


def chunk_file(path: Path, relative: str, *, chunk_size: int = 1400, overlap: int = 180,
               text: str | None = None, max_embedding_bytes: int = DEFAULT_EMBEDDING_BYTES,
               progress=None) -> list[dict]:
    """Split contiguous source spans, with exact offsets and a full embedding byte cap.

    Args:
        path: Source file path; read as UTF-8/BOM only when `text` is omitted.
        relative: Repository-relative path used in structural context.
        chunk_size: Character cap per chunk, at least 200.
        overlap: Nonnegative long-section overlap, smaller than `chunk_size`.
        text: Optional already-loaded source; avoids a second file read.
        max_embedding_bytes: Byte cap including source-context prefixes, at least 512.
        progress: Optional object with `start`, `advance`, and `finish` methods.

    Returns:
        Chunk dictionaries with original text, character offsets, one-based line
        ranges, and heading/symbol context. No embeddings or storage writes occur.

    UTF-8 bytes are a conservative size proxy for byte-based tokenizers, not an
    exact token count. This avoids downloading a second tokenizer at indexing time.
    Python/Markdown structure guides boundaries; outer whitespace is removed
    while offsets are adjusted. Invalid limits or oversized context raise
    `ValueError`; file/encoding errors propagate when loading from disk.
    """
    if chunk_size < 200 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk size must be at least 200 and overlap must be non-negative and smaller than chunk size")
    if max_embedding_bytes < 512:
        raise ValueError("embedding byte budget must be at least 512")
    text = text if text is not None else path.read_text(encoding="utf-8-sig")
    if progress is not None:
        progress.start(f"Chunking {relative} (characters)", len(text))
    completed = 0
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    boundaries = _boundaries(text, path, relative, chunk_size, max_embedding_bytes)
    fenced_spans = _fenced_spans(text, path)
    chunks = []
    for index, (line, context) in enumerate(boundaries):
        start = offsets[min(line, len(lines))]
        end_line = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(lines)
        end = offsets[min(end_line, len(lines))]
        while end > start and text[end - 1].isspace():
            end -= 1
        metadata = {"path": relative, **context}
        overhead = len(embedding_text("", metadata).encode("utf-8"))
        budget = max_embedding_bytes - overhead
        if budget < 4:
            raise ValueError(f"embedding context exceeds byte budget for {relative}")
        position = start
        while position < end:
            # Trim only outer whitespace, adjusting source offsets to match it.
            while position < end and text[position].isspace():
                position += 1
            if position == end:
                break
            stop = min(end, position + chunk_size)
            low, high = position, stop
            while low < high:
                mid = (low + high + 1) // 2
                if len(text[position:mid].encode("utf-8")) <= budget:
                    low = mid
                else:
                    high = mid - 1
            stop = low
            if stop < end:
                boundary = text.rfind("\n", position + (stop - position) // 2, stop)
                if boundary > position:
                    stop = boundary + 1
                for block_start, block_end in fenced_spans:
                    if position < block_end and block_start < stop < block_end:
                        if (block_end - block_start <= chunk_size
                                and len(text[block_start:block_end].encode("utf-8")) <= budget):
                            stop = block_start if position < block_start else block_end
                        break
            trimmed_stop = stop
            while trimmed_stop > position and text[trimmed_stop - 1].isspace():
                trimmed_stop -= 1
            if trimmed_stop > position:
                chunks.append({"text": text[position:trimmed_stop],
                               "start_index": position, "end_index": trimmed_stop,
                               "start_line": bisect_right(offsets, position),
                               "end_line": bisect_right(offsets, trimmed_stop - 1),
                               "heading": "", "heading_path": "", "symbol": "", "signature": "", **context})
            if progress is not None:
                progress.advance(max(0, stop - completed))
                completed = max(completed, stop)
            if stop >= end:
                break
            # Do not emit an overlap-only tail or get stuck with a large overlap.
            next_position = max(position + 1, stop - min(overlap, (stop - position) // 2))
            for block_start, block_end in fenced_spans:
                if (block_start <= next_position < block_end <= stop
                        or stop == block_start):
                    next_position = stop
                    break
            position = next_position
    if progress is not None:
        progress.finish()
    return chunks
