"""Pure process graph tests; no actual process inspection or termination."""

import unittest

from vectorizer.windows_processes import ProcessTree, command_arguments, descendants


class ProcessTreeTests(unittest.TestCase):
    def test_graph_handles_branches_and_malformed_records(self):
        processes = [{"ProcessId": 1, "ParentProcessId": 0},
                     {"ProcessId": 2, "ParentProcessId": 1},
                     {"ProcessId": 3, "ParentProcessId": 2},
                     {"ProcessId": 4, "ParentProcessId": 1},
                     {"ProcessId": "invalid", "ParentProcessId": 1}, {}]
        tree = ProcessTree(processes)
        self.assertEqual(tree.descendants(1), {2, 3, 4})
        self.assertEqual(tree.descendants(2), {3})
        self.assertEqual(tree.descendants(999), set())
        self.assertEqual(descendants(processes, 1), {2, 3, 4})

    def test_cycles_terminate(self):
        tree = ProcessTree([{"ProcessId": 1, "ParentProcessId": 2}, {"ProcessId": 2, "ParentProcessId": 1}])
        self.assertEqual(tree.descendants(1), {1, 2})

    def test_windows_service_argv_preserves_quoted_paths(self):
        command = '"C:\\My Repository\\.venv\\Scripts\\python.exe" -m uvicorn "vectorizer.server:app" --port 8001'
        self.assertEqual(command_arguments(command), [r"C:\My Repository\.venv\Scripts\python.exe", "-m", "uvicorn",
                                                     "vectorizer.server:app", "--port", "8001"])
        self.assertEqual(command_arguments('"unterminated'), [])
