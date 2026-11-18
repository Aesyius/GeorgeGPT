"""Windows service process inspection, tree traversal, launch, and termination."""

import json
import os
from pathlib import Path
import shlex
import subprocess

from .service_config import BootError


def hidden_startupinfo():
    """Return Windows subprocess startup flags that suppress a new visible window.

    Returns `None` on other operating systems. No process is launched.
    """
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return startupinfo


def powershell_json(command: str):
    """Run trusted PowerShell inspection code and normalize JSON output to a list.

    Windows-only, hidden, noninteractive execution has a 30-second timeout.
    Empty stdout becomes `[]`; a single JSON value becomes a one-item list.
    Nonzero status, malformed JSON, or non-Windows use raises `BootError`.
    `command` is executable code and must not contain untrusted user input.
    """
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
    """Read Windows process identity/parent/command metadata through CIM.

    Returns process dictionaries for later verification, not live handles.
    Snapshot data can become stale; destructive operations must revalidate it.
    Inspection errors propagate from `powershell_json`.
    """
    return powershell_json(
        "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,CreationDate "
        "| ConvertTo-Json -Compress -Depth 3"
    )


def listener_pids(port: int) -> set[int]:
    """Return Windows PIDs currently listening on the requested local TCP port.

    Uses a read-only PowerShell query and integer-normalizes ownership values.
    An empty result means no listeners were reported, not proof of ownership.
    Does not stop a process or restrict the listener's network interface.
    """
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
        """Index valid PID/parent entries from a snapshot; skip malformed records."""
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
        """Return reachable child PIDs from the stored snapshot without OS calls.

        Traversal tracks visited children to terminate on malformed cycles.
        Results describe the snapshot, not guaranteed current process identity.
        """
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
    """Compare a snapshot executable path with `expected`, case-insensitively.

    Resolves path spellings and returns `False` for path-resolution failures.
    Matching the executable alone does not verify service command arguments.
    """
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
    """Force-stop an already-verified Windows process tree using `taskkill /T /F`.

    This is destructive and interrupts active work. The caller must verify
    ownership, identity, and protected descendants before calling; this helper
    performs no independent ownership check. Logs successful termination and
    raises `BootError` when taskkill reports failure.
    """
    result = subprocess.run(
        ["taskkill.exe", "/PID", str(root_pid), "/T", "/F"],
        capture_output=True, text=True, check=False, timeout=30,
        startupinfo=hidden_startupinfo(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    if result.returncode:
        raise BootError(f"Could not stop verified {description} process tree: {result.stderr.strip()}")
    print(f"Stopped verified {description} process tree (PID {root_pid}).")


def launch(command: list[str], *, cwd: Path, env: dict, log_path: Path) -> int:
    """Launch a background Windows service and return its PID immediately.

    Uses explicit argv, working directory, and environment; forces Python UTF-8
    output, disables stdin, and appends stdout/stderr to `log_path`.
    Creates the log directory if needed and suppresses a visible console.
    Does not wait for readiness or verify model/listener configuration; callers
    perform those checks. Process/filesystem launch errors propagate.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
    )
    # Do not combine CREATE_NO_WINDOW with DETACHED_PROCESS: Windows ignores it.
    with open(log_path, "a", encoding="utf-8", buffering=1) as stream:
        process = subprocess.Popen(
            command, cwd=cwd, env={**env, "PYTHONUTF8": "1"}, stdin=subprocess.DEVNULL, stdout=stream,
            stderr=subprocess.STDOUT, creationflags=flags,
            startupinfo=hidden_startupinfo(), close_fds=True,
        )
    return process.pid
