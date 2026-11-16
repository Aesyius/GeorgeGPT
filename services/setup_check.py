"""Standard-library-only preflight for a new Windows checkout, before pip installs."""

import argparse
import json
from pathlib import Path
import sys

# Also support `py -3.12 C:\path with spaces\services\setup_check.py` from any cwd.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.paths import PROJECT_ROOT, resolve_path
from services.service_config import BootError, StartupSettings


def check_setup(config_path: Path, repository: Path) -> StartupSettings:
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not repository.is_dir():
        raise ValueError(f"Repository is not a directory: {repository}")
    config["repository_path"] = str(repository)
    return StartupSettings.from_config(config, PROJECT_ROOT, repository=repository)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=resolve_path)
    parser.add_argument("--repository", required=True, type=resolve_path)
    args = parser.parse_args()
    try:
        if sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
            raise ValueError("Setup requires 64-bit Python 3.12.")
        check_setup(args.config, args.repository)
    except (BootError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"SETUP CHECK FAILED: {error}", file=sys.stderr)
        return 1
    print("Validated setup: loopback-only services and local SQLite storage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
