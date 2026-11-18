"""Dependency-free CLI progress reporting for indexing and evaluation stages."""

from __future__ import annotations

import sys
from typing import TextIO


class ProgressBar:
    """Report bounded-work progress without terminal or third-party dependencies.

    Interactive streams redraw one line; redirected streams emit periodic text
    updates suitable for logs. Counts measure completed work, not remaining time.
    """

    def __init__(self, stream: TextIO | None = None, width: int = 28) -> None:
        """Select the output stream (stderr by default) and bar width.

        Width is clamped to at least ten characters. Terminal capability is
        detected once; construction itself emits no output.
        """
        self.stream = stream or sys.stderr
        self.width = max(10, width)
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.label = ""
        self.total = 0
        self.current = 0
        self._last_non_tty = -1

    def start(self, label: str, total: int) -> None:
        """Reset the stage and render its initial state.

        Negative totals become zero; empty stages complete immediately.
        Starting a stage replaces the previous label and counters.
        """
        self.label, self.total, self.current = label, max(0, total), 0
        self._last_non_tty = -1
        self._render(force=True)
        if self.total == 0:
            self.finish()

    def advance(self, amount: int = 1) -> None:
        """Add completed work, clamp to the total, and render when appropriate."""
        self.current = min(self.total, self.current + amount)
        self._render()

    def finish(self) -> None:
        """Mark the stage complete and terminate an interactive progress line."""
        if self.current != self.total:
            self.current = self.total
            self._render(force=True)
        if self.tty:
            self.stream.write("\n")
            self.stream.flush()

    def _render(self, force: bool = False) -> None:
        percent = 100 if not self.total else int(self.current * 100 / self.total)
        filled = int(self.width * percent / 100)
        bar = "=" * filled + ">" * (filled < self.width) + " " * max(0, self.width - filled - 1)
        if self.tty:
            self.stream.write(f"\r{self.label} [{bar}] {self.current}/{self.total} {percent:3d}%")
            self.stream.flush()
        elif force or self.current == self.total or self.current - self._last_non_tty >= max(1, self.total // 20):
            print(f"{self.label}: {self.current}/{self.total} ({percent}%) [{bar}]", file=self.stream, flush=True)
            self._last_non_tty = self.current
