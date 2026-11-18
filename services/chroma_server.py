"""Provision and start the local, pinned Chroma server inside WSL2."""

import argparse
from base64 import b64encode
from pathlib import Path
import os
import subprocess
import sys
import time
from urllib.error import URLError

from common.chroma_endpoint import CHROMA_VERSION, check_chroma_server, chroma_port
from .windows_processes import launch

_INSTALL = """
set -eu
umask 077
root="$HOME/.local/share/georgegpt-chroma"
mkdir -p "$root"
case "$(stat -f -c %T "$root")" in
    9p|drvfs|cifs|smb2|msdos|fuseblk) echo 'Chroma needs a Linux filesystem, not a Windows mount.' >&2; exit 1 ;;
esac
venv="$root/venv-$1"
python3 -m venv "$venv"
"$venv/bin/python" -m pip install --disable-pip-version-check "chromadb==$1"
"""

_START = """
set -eu
umask 077
root="$HOME/.local/share/georgegpt-chroma"
cli="$root/venv-$1/bin/chroma"
[ -x "$cli" ] || { echo 'Install the WSL Chroma server with python -m services.chroma_server install.' >&2; exit 1; }
data="$root/data-$2"
mkdir -p "$data"
case "$(stat -f -c %T "$data")" in
    9p|drvfs|cifs|smb2|msdos|fuseblk) echo 'Chroma needs a Linux filesystem, not a Windows mount.' >&2; exit 1 ;;
esac
exec flock -n "$data/.georgegpt-server.lock" "$cli" run --host 127.0.0.1 --port "$2" --path "$data"
"""


def _wsl_command(distro: str, *args: str) -> list[str]:
    return ["wsl.exe", "--distribution", distro, "--cd", "~", "--exec", *args]


def _script_command(distro: str, script: str, *args: str) -> list[str]:
    # Avoid passing multiline shell code containing Windows command-line quotes.
    encoded = b64encode(script.encode("utf-8")).decode("ascii")
    return _wsl_command(distro, "bash", "-c", "set -o pipefail; printf %s $1 | base64 -d | bash -s -- ${@:2}",
                        "georgegpt-chroma", encoded, *args)


def _check_wsl(distro: str) -> None:
    help_text = (f"Install and initialize a WSL2 distro named {distro!r} "
                 "(for Ubuntu, use wsl --install -d Ubuntu), then install python3-venv and util-linux inside it.")
    try:
        result = subprocess.run(_wsl_command(distro, "uname", "-r"), capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Cannot run WSL2. {help_text}") from error
    if result.returncode or b"wsl2" not in result.stdout.lower():
        raise RuntimeError(f"WSL2 distro {distro!r} is unavailable or not using a standard WSL2 kernel. {help_text}")


def ensure_chroma_server(log_path: Path, *, install: bool = False) -> None:
    """Reuse a compatible loopback server or launch the pinned WSL2 server."""
    port = chroma_port()
    try:
        check_chroma_server(port)
    except RuntimeError as error:
        # Do not replace an HTTP listener, wrong version, or misbehaving server.
        if not isinstance(error.__cause__, URLError) or not isinstance(error.__cause__.reason, ConnectionRefusedError):
            raise
    else:
        print(f"Reusing a compatible Chroma endpoint at 127.0.0.1:{port}.", flush=True)
        return

    distro = os.getenv("RAG_CHROMA_WSL_DISTRO", "Ubuntu").strip()
    if not distro:
        raise ValueError("RAG_CHROMA_WSL_DISTRO must name your WSL2 distribution.")
    _check_wsl(distro)
    if install:
        print(f"Installing Chroma {CHROMA_VERSION} in WSL2 ({distro})...", flush=True)
        result = subprocess.run(_script_command(distro, _INSTALL, CHROMA_VERSION))
        if result.returncode:
            raise RuntimeError("WSL Chroma installation failed. Check the output above and install python3-venv in WSL.")
    pid = launch(_script_command(distro, _START, CHROMA_VERSION, str(port)),
                 cwd=Path.home(), env=os.environ.copy(), log_path=log_path)
    print(f"Starting Chroma in WSL2 ({distro}, PID {pid}); logs: {log_path}", flush=True)
    deadline = time.monotonic() + 30
    while True:
        try:
            check_chroma_server(port)
        except RuntimeError as error:
            if (not isinstance(error.__cause__, URLError)
                    or not isinstance(error.__cause__.reason, ConnectionRefusedError)):
                raise
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Chroma did not become reachable on Windows localhost:{port}. "
                                   f"Check WSL2 localhost forwarding and {log_path}.") from error
            time.sleep(0.5)
        else:
            print(f"Chroma ready at 127.0.0.1:{port}.", flush=True)
            return


def main() -> int:
    """Start Chroma, optionally provisioning its pinned WSL2 environment first."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "install"))
    parser.add_argument("log_path", nargs="?", type=Path)
    args = parser.parse_args()
    log_path = args.log_path or Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "Vectorizer" / "logs" / "chroma.log"
    try:
        ensure_chroma_server(log_path.resolve(), install=args.action == "install")
    except (OSError, RuntimeError, ValueError) as error:
        print(f"CHROMA START FAILED: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
