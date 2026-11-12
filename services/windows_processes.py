"""Windows service process inspection, tree traversal, launch, and termination."""

import json
import os
from pathlib import Path
import shlex
import subprocess

from .service_config import BootError


def hidden_startupinfo():
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return startupinfo


def powershell_json(command: str):
    if os.name != "nt":
        raise BootError("This bootup script currently supports Windows only.")
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, check=False, timeout=30,
        startupinfo=hidden_startupinfo(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise BootError(f"Windows process inspection failed: {detail}")
    if not result.stdout.strip():
        return []
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise BootError("Could not parse Windows process inspection output.") from error
    return value if isinstance(value, list) else [value]


def process_snapshot() -> list[dict]:
    return powershell_json(
        "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,CreationDate "
        "| ConvertTo-Json -Compress -Depth 3"
    )


def listener_pids(port: int) -> set[int]:
    values = powershell_json(
        f"$ids = @(Get-NetTCPConnection -State Listen -LocalPort {int(port)} -ErrorAction SilentlyContinue "
        "| Select-Object -ExpandProperty OwningProcess -Unique); "
        "ConvertTo-Json -Compress -InputObject $ids"
    )
    result = set()
    for value in values:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


class ProcessTree:
    """Index a snapshot once rather than rebuilding parent maps for each root."""

    def __init__(self, processes: list[dict]):
        self.by_pid: dict[int, dict] = {}
        self.children: dict[int, set[int]] = {}
        for process in processes:
            try:
                pid = int(process["ProcessId"])
                parent = int(process["ParentProcessId"])
            except (KeyError, TypeError, ValueError):
                continue
            self.by_pid[pid] = process
            self.children.setdefault(parent, set()).add(pid)

    def descendants(self, root_pid: int) -> set[int]:
        found, pending = set(), [root_pid]
        while pending:
            for child in self.children.get(pending.pop(), set()):
                if child not in found:
                    found.add(child)
                    pending.append(child)
        return found


def descendants(processes: list[dict], root_pid: int) -> set[int]:
    """Compatibility helper for callers that do not retain a snapshot index."""
    return ProcessTree(processes).descendants(root_pid)


def executable_matches(process: dict, expected: Path) -> bool:
    actual = process.get("ExecutablePath") or ""
    try:
        return str(Path(actual).resolve()).casefold() == str(expected.resolve()).casefold()
    except (OSError, ValueError):
        return False


def command_arguments(command: str) -> list[str]:
    """Read simple Windows service argv without evaluating the command line."""
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return []
    return [token[1:-1] if len(token) > 1 and token[0] == token[-1] and token[0] in {'"', "'"}
            else token for token in tokens]


def stop_verified_process_tree(root_pid: int, description: str) -> None:
    result = subprocess.run(
        ["taskkill.exe", "/PID", str(root_pid), "/T", "/F"],
        capture_output=True, text=True, check=False, timeout=30,
        startupinfo=hidden_startupinfo(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    if result.returncode:
        raise BootError(f"Could not stop verified {description} process tree: {result.stderr.strip()}")
    print(f"Stopped verified {description} process tree (PID {root_pid}).")


def launch(command: list[str], *, cwd: Path, env: dict, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
    )
    # Do not combine CREATE_NO_WINDOW with DETACHED_PROCESS: Windows ignores it.
    with open(log_path, "a", encoding="utf-8", buffering=1) as stream:
        process = subprocess.Popen(
            command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stream,
            stderr=subprocess.STDOUT, creationflags=flags,
            startupinfo=hidden_startupinfo(), close_fds=True,
        )
    return process.pid
