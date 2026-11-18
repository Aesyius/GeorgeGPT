"""Run index preparation with live terminal progress and an append-only log."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
import traceback

from vectorizer.managed_index import main as prepare_index


class Tee:
    """Preserve terminal detection so ProgressBar renders in place."""

    def __init__(self, terminal, log):
        """Attach an optional live terminal and a required writable log stream."""
        self.terminal = terminal
        self.log = log

    def isatty(self):
        """Report the live terminal's capability, or false when no terminal exists."""
        return bool(self.terminal is not None and self.terminal.isatty())

    def write(self, text):
        """Write/flush text to the log and optional terminal; return character count."""
        self.log.write(text)
        self.log.flush()
        if self.terminal is not None:
            self.terminal.write(text)
            self.terminal.flush()
        return len(text)

    def flush(self):
        """Flush both destinations without closing either caller-owned stream."""
        self.log.flush()
        if self.terminal is not None:
            self.terminal.flush()


def main(argv=None) -> int:
    """Prepare an index while teeing progress/errors into an append-only log.

    `argv` supports tests/embedding; omitted values use command-line arguments.
    Requires `--log-path`, creates its parent directory, and delegates model/
    repository selection to managed-index defaults. Returns 0 on success or 1
    after logging a preparation traceback. Argparse errors exit separately.
    Can build/write indexes; this is not a read-only verification command.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-path", required=True, type=Path)
    args = parser.parse_args(argv)
    args.log_path.parent.mkdir(parents=True, exist_ok=True)
    with args.log_path.open("a", encoding="utf-8") as log:
        with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
            try:
                prepare_index()
            except Exception:
                traceback.print_exc()
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
