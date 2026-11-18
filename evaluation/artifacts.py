"""Load evaluation questions and publish reports independently of inference."""

import json
import os
from pathlib import Path
import tempfile


def read_questions(path: Path) -> list[dict]:
    """Load JSONL question objects with unique string IDs."""
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


def save_report(path: Path, data: dict) -> None:
    """Write a UTF-8 JSON report and replace the destination only after success."""
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
        Path(temp_name).unlink(missing_ok=True)
