"""Offline launch/path regressions; never start or stop actual services."""

import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import call, patch

from vectorizer import bootup
from vectorizer.paths import PROJECT_ROOT
from vectorizer.model_config import ModelSettings


class BootupTests(unittest.TestCase):
    def startup_fixture(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        config = json.loads((PROJECT_ROOT / "config/bootup.json").read_text(encoding="utf-8"))
        models = ModelSettings("selected-chat:7b", "selected-embed:small", 32768)
        models_path = root / "models.json"
        models_path.write_text(json.dumps(models.to_dict()), encoding="utf-8")
        config["models_config"] = str(models_path)
        config["logs_directory"] = str(root / "logs")
        config["open_webui"]["runtime_directory"] = str(root / "runtime")
        for section, key in (("ollama", "executable"), ("rag_api", "python"), ("open_webui", "python")):
            executable = root / f"{section}.exe"
            executable.touch()
            config[section][key] = str(executable)
        config_path = root / "bootup.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return config_path, config, models

    def process_fixture(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        ollama, python = root / "ollama.exe", root / "python.exe"
        ollama.touch()
        python.touch()
        config = {"ollama": {"executable": str(ollama), "port": 11434},
                  "open_webui": {"python": str(python), "port": 8080},
                   "rag_api": {"python": str(python), "port": 8001}}
        processes = [
            {"ProcessId": 101, "ParentProcessId": 1, "Name": "ollama.exe", "ExecutablePath": str(ollama),
             "CommandLine": f'"{ollama}" serve', "CreationDate": "one"},
            {"ProcessId": 202, "ParentProcessId": 1, "Name": "python.exe", "ExecutablePath": str(python),
             "CommandLine": f'"{python}" -c "{bootup.WEBUI_LAUNCH_MARKER}" serve', "CreationDate": "two"},
            {"ProcessId": 303, "ParentProcessId": 1, "Name": "python.exe", "ExecutablePath": str(python),
             "CommandLine": 'python -m uvicorn vectorizer.server:app', "CreationDate": "three"},
        ]
        return root, config, processes

    def test_config_paths_resolve_from_project_root(self):
        config = json.loads((PROJECT_ROOT / "config" / "bootup.json").read_text(encoding="utf-8"))
        self.assertEqual(
            bootup.expand_path(config["rag_api"]["python"], PROJECT_ROOT),
            PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
        )

    @patch("vectorizer.bootup.subprocess.Popen")
    def test_service_launch_has_no_console_and_redirects_output(self, popen):
        popen.return_value.pid = 123
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid = bootup.launch(["service.exe"], cwd=root, env={}, log_path=root / "service.log")
            self.assertEqual(pid, 123)
            args, kwargs = popen.call_args
            self.assertEqual(args[0], ["service.exe"])
            self.assertTrue(kwargs["creationflags"] & 0x08000000)  # CREATE_NO_WINDOW
            self.assertFalse(kwargs["creationflags"] & 0x00000008)  # DETACHED_PROCESS
            self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stdout"].name, str(root / "service.log"))
            self.assertTrue(kwargs["stdout"].closed)

    def test_pythonw_output_uses_configured_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "bootup.json"
            config_path.write_text(json.dumps({"logs_directory": str(root / "logs")}), encoding="utf-8")
            with patch.object(bootup.sys, "stdout", None), patch.object(bootup.sys, "stderr", None):
                bootup.configure_windowless_output(config_path)
                stream = bootup.sys.stdout
                try:
                    self.assertIs(bootup.sys.stderr, stream)
                    print("Boot status", file=stream, flush=True)
                finally:
                    stream.close()
            self.assertIn("Boot status", (root / "logs" / "bootup.log").read_text(encoding="utf-8"))

    def test_console_output_is_preserved(self):
        output = io.StringIO()
        with patch.object(bootup.sys, "stdout", output), patch.object(bootup.sys, "stderr", output):
            bootup.configure_windowless_output(Path("missing.json"))
            self.assertIs(bootup.sys.stdout, output)

    @patch("vectorizer.bootup.launch")
    @patch("vectorizer.bootup.kill_current_processes", side_effect=bootup.BootError("unverified RAG API listener"))
    @patch("vectorizer.bootup.read_api_key", return_value="test-secret")
    @patch("vectorizer.bootup.force_restart_ollama")
    @patch("vectorizer.bootup.force_restart_webui")
    def test_unverified_listener_aborts_without_launching_services(self, webui, ollama, key, kill, launch):
        with patch("sys.argv", ["bootup"]), patch.object(bootup.sys, "stderr", io.StringIO()):
            self.assertEqual(bootup.main(), 1)
        webui.assert_not_called()
        ollama.assert_not_called()
        launch.assert_not_called()
        kill.assert_called_once()

    @patch("vectorizer.bootup.kill_current_processes")
    @patch("vectorizer.bootup.read_api_key")
    @patch("vectorizer.bootup.launch")
    def test_cli_kill_dispatches_without_secrets_or_startup(self, launch, read_key, kill):
        for command, service in (("kill", "webui"), ("stop", "api")):
            with self.subTest(command=command), patch("sys.argv", ["bootup", command, "--service", service, "--dry-run"]):
                self.assertEqual(bootup.main(), 0)
                self.assertEqual(kill.call_args.args[2], service)
                self.assertTrue(kill.call_args.kwargs["dry_run"])
        read_key.assert_not_called()
        launch.assert_not_called()

    def test_start_rejects_kill_only_flags(self):
        with patch("sys.argv", ["bootup", "start", "--dry-run"]), patch.object(bootup.sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                bootup.main()
        self.assertEqual(error.exception.code, 2)

    @patch("vectorizer.bootup.kill_current_processes", side_effect=bootup.BootError("unverified listener"))
    def test_kill_failure_returns_nonzero(self, kill):
        output = io.StringIO()
        with patch("sys.argv", ["bootup", "kill"]), patch.object(bootup.sys, "stderr", output):
            self.assertEqual(bootup.main(), 1)
        self.assertIn("PROCESS KILL FAILED", output.getvalue())

    @patch("vectorizer.bootup.wait_until")
    @patch("vectorizer.bootup.stop_verified_process_tree")
    @patch("vectorizer.bootup.listener_pids")
    @patch("vectorizer.bootup.process_snapshot")
    def test_kill_all_stops_webui_then_api_then_ollama(self, snapshot, listeners, stop, wait):
        root, config, processes = self.process_fixture()
        snapshot.return_value = processes
        listeners.side_effect = lambda port: {8001: {303}, 8080: {202}, 11434: {101}}[port]
        bootup.kill_current_processes(config, root)
        self.assertEqual(stop.call_args_list, [call(202, "Open WebUI"), call(303, "RAG API"), call(101, "Ollama")])
        self.assertEqual(wait.call_count, 3)

    @patch("vectorizer.bootup.stop_verified_process_tree")
    @patch("vectorizer.bootup.listener_pids")
    @patch("vectorizer.bootup.process_snapshot")
    def test_kill_dry_run_inspects_without_stopping(self, snapshot, listeners, stop):
        root, config, processes = self.process_fixture()
        snapshot.return_value = processes
        listeners.side_effect = lambda port: {8001: {303}, 8080: {202}, 11434: {101}}[port]
        output = io.StringIO()
        with patch.object(bootup.sys, "stdout", output):
            bootup.kill_current_processes(config, root, dry_run=True)
        stop.assert_not_called()
        self.assertIn("Would kill verified Ollama process trees: 101", output.getvalue())
        self.assertIn("Would kill verified RAG API process trees: 303", output.getvalue())
        self.assertIn("no processes were killed", output.getvalue())

    @patch("vectorizer.bootup.stop_verified_process_tree")
    @patch("vectorizer.bootup.listener_pids")
    @patch("vectorizer.bootup.process_snapshot")
    def test_conflict_preflights_all_services_before_any_kill(self, snapshot, listeners, stop):
        root, config, processes = self.process_fixture()
        snapshot.return_value = processes
        listeners.side_effect = lambda port: {8001: {303}, 8080: {202}, 11434: {999}}[port]
        with self.assertRaisesRegex(bootup.BootError, "unverified"):
            bootup.kill_current_processes(config, root)
        stop.assert_not_called()

    @patch("vectorizer.bootup.stop_verified_process_tree")
    @patch("vectorizer.bootup.listener_pids")
    @patch("vectorizer.bootup.process_snapshot")
    def test_unselected_api_descendant_is_not_killed_when_only_webui_selected(self, snapshot, listeners, stop):
        root, config, processes = self.process_fixture()
        processes[2]["ParentProcessId"] = 202
        snapshot.return_value = processes
        listeners.side_effect = lambda port: {8001: {303}, 8080: {202}, 11434: {101}}[port]
        with self.assertRaisesRegex(bootup.BootError, "not selected"):
            bootup.kill_current_processes(config, root, "webui")
        stop.assert_not_called()

    @patch("vectorizer.bootup.stop_verified_process_tree")
    @patch("vectorizer.bootup.listener_pids")
    @patch("vectorizer.bootup.process_snapshot")
    def test_pid_reuse_is_rejected_before_kill(self, snapshot, listeners, stop):
        root, config, processes = self.process_fixture()
        fresh = [dict(item) for item in processes]
        fresh[0]["CreationDate"] = "different process, same PID"
        snapshot.side_effect = [processes, fresh]
        listeners.side_effect = lambda port: {8001: {303}, 8080: set(), 11434: {101}}[port]
        with self.assertRaisesRegex(bootup.BootError, "changed identity"):
            bootup.kill_current_processes(config, root, "ollama")
        stop.assert_not_called()

    @patch("vectorizer.bootup.stop_verified_process_tree")
    @patch("vectorizer.bootup.listener_pids", return_value=set())
    @patch("vectorizer.bootup.process_snapshot", return_value=[])
    def test_kill_when_already_stopped_is_successful(self, snapshot, listeners, stop):
        root, config, _ = self.process_fixture()
        bootup.kill_current_processes(config, root)
        stop.assert_not_called()

    def test_api_runtime_settings_must_match_after_startup(self):
        models = ModelSettings()
        payload = {**models.to_dict(), "ollama_url": "http://localhost:11434"}
        with patch("vectorizer.bootup.request_json", return_value=(200, payload)):
            bootup.verify_api_configuration("http://api", {}, models, "http://localhost:11434")
        for actual in (None, {}, {**payload, "context_window_tokens": 8192}, {**payload, "chat_model": "other"}):
            with self.subTest(actual=actual), patch("vectorizer.bootup.request_json", return_value=(200, actual)):
                with self.assertRaisesRegex(bootup.BootError, "startup did not complete"):
                    bootup.verify_api_configuration("http://api", {}, models, "http://localhost:11434")
        with patch("vectorizer.bootup.request_json", side_effect=OSError("old API")):
            with self.assertRaisesRegex(bootup.BootError, "Cannot verify"):
                bootup.verify_api_configuration("http://api", {}, models, "http://localhost:11434")

    @patch("vectorizer.bootup.start_webui")
    @patch("vectorizer.bootup.start_rag_api")
    @patch("vectorizer.bootup.start_ollama")
    @patch("vectorizer.bootup.kill_current_processes")
    @patch("vectorizer.bootup.read_api_key", return_value="secret")
    @patch("vectorizer.bootup.request_json", side_effect=OSError("old API unhealthy or has stale config"))
    def test_start_always_kills_and_relaunches_all_services_without_old_api_health_check(self, request, key, kill, ollama, api, webui):
        from unittest.mock import Mock
        order = Mock()
        for name, function in (("kill", kill), ("ollama", ollama), ("api", api), ("webui", webui)):
            order.attach_mock(function, name)
        with patch("sys.argv", ["bootup", "start"]), patch.object(bootup.sys, "stderr", io.StringIO()):
            self.assertEqual(bootup.main(), 0)
        self.assertEqual([item[0] for item in order.mock_calls], ["kill", "ollama", "api", "webui"])
        request.assert_not_called()

    @patch("vectorizer.bootup.warm_models")
    @patch("vectorizer.bootup.launch", return_value=123)
    @patch("vectorizer.bootup.force_restart_ollama")
    @patch("vectorizer.bootup.force_restart_webui")
    @patch("vectorizer.bootup.process_snapshot", return_value=[])
    @patch("vectorizer.bootup.read_api_key", return_value="secret")
    @patch("vectorizer.bootup.listener_pids", return_value=set())
    @patch("vectorizer.bootup.wait_until")
    @patch("vectorizer.bootup.request_json")
    def test_start_passes_json_settings_and_checks_selected_models(self, request, wait, listeners, key, snapshot, webui, ollama, launch, warm):
        config_path, config, models = self.startup_fixture()
        ollama_url = f"http://{config['ollama']['host']}:{config['ollama']['port']}"

        def respond(url, **kwargs):
            if url.endswith("/api/tags"):
                return 200, {"models": [{"name": models.chat_model}, {"name": models.embedding_model}]}
            if url.endswith("/v1/models"):
                return 200, {"data": [{"id": "GeorgeGPT"}]}
            if url.endswith("/v1/config"):
                return 200, {**models.to_dict(), "ollama_url": ollama_url}
            return 200, None

        request.side_effect = respond
        wait.side_effect = lambda description, check, *args, **kwargs: check()
        with patch("sys.argv", ["bootup", "start", "--config", str(config_path)]), patch.dict(
            bootup.os.environ, {"OLLAMA_CHAT_MODEL": "inherited-wrong", "OLLAMA_CONTEXT_WINDOW_TOKENS": "512"}
        ):
            self.assertEqual(bootup.main(), 0)
        self.assertEqual(launch.call_count, 3)
        ollama_env = launch.call_args_list[0].kwargs["env"]
        api_env = launch.call_args_list[1].kwargs["env"]
        self.assertEqual(ollama_env["OLLAMA_CONTEXT_LENGTH"], "32768")
        self.assertEqual(ollama_env["OLLAMA_KEEP_ALIVE"], "1800s")
        self.assertEqual(ollama_env["OLLAMA_NUM_PARALLEL"], "1")
        self.assertEqual(ollama_env["OLLAMA_MAX_LOADED_MODELS"], "2")
        self.assertEqual(api_env["OLLAMA_KEEP_ALIVE_SECONDS"], "1800")
        warm.assert_called_once_with(ollama_url, models, config["startup_timeout_seconds"])
        self.assertEqual(api_env["OLLAMA_CHAT_MODEL"], models.chat_model)
        self.assertEqual(api_env["OLLAMA_EMBEDDING_MODEL"], models.embedding_model)
        self.assertEqual(api_env["OLLAMA_CONTEXT_WINDOW_TOKENS"], "32768")
        self.assertEqual(Path(api_env["RAG_MODEL_CONFIG"]), Path(config["models_config"]).resolve())

    @patch("vectorizer.bootup.read_api_key")
    @patch("vectorizer.bootup.ModelSettings.from_file", side_effect=ValueError("invalid models file"))
    def test_invalid_model_settings_abort_before_secret_or_process_work(self, load, key):
        with patch("sys.argv", ["bootup", "start"]), patch.object(bootup.sys, "stderr", io.StringIO()):
            self.assertEqual(bootup.main(), 1)
        key.assert_not_called()

    @patch("vectorizer.bootup.start_webui")
    @patch("vectorizer.bootup.start_rag_api")
    @patch("vectorizer.bootup.warm_models", side_effect=bootup.BootError("warmup failed"))
    @patch("vectorizer.bootup.wait_until")
    @patch("vectorizer.bootup.launch", return_value=123)
    @patch("vectorizer.bootup.kill_current_processes")
    @patch("vectorizer.bootup.read_api_key", return_value="secret")
    def test_failed_warmup_does_not_advertise_the_api_or_webui_ready(self, key, kill, launch, wait, warm, api, webui):
        path, _, models = self.startup_fixture()
        wait.return_value = {models.chat_model, models.embedding_model}
        with patch("sys.argv", ["bootup", "start", "--config", str(path)]), patch.object(bootup.sys, "stderr", io.StringIO()):
            self.assertEqual(bootup.main(), 1)
        launch.assert_called_once()
        api.assert_not_called()
        webui.assert_not_called()

    @patch("vectorizer.bootup.kill_current_processes")
    @patch("vectorizer.bootup.ModelSettings.from_file", side_effect=ValueError("invalid models file"))
    def test_kill_does_not_require_model_configuration(self, load, kill):
        with patch("sys.argv", ["bootup", "kill"]):
            self.assertEqual(bootup.main(), 0)
        load.assert_not_called()

    @patch("vectorizer.bootup.wait_until")
    @patch("vectorizer.bootup.stop_verified_process_tree")
    @patch("vectorizer.bootup.listener_pids")
    @patch("vectorizer.bootup.process_snapshot")
    def test_api_only_stops_verified_api_tree(self, snapshot, listeners, stop, wait):
        root, config, processes = self.process_fixture()
        processes.append({"ProcessId": 304, "ParentProcessId": 303, "Name": "python.exe",
                          "ExecutablePath": "base-interpreter.exe", "CommandLine": "worker"})
        snapshot.return_value = processes
        listeners.side_effect = lambda port: {8001: {304}, 8080: {202}, 11434: {101}}[port]
        bootup.kill_current_processes(config, root, "api")
        stop.assert_called_once_with(303, "RAG API")

    @patch("vectorizer.bootup.listener_pids", return_value=set())
    def test_api_without_a_listener_is_still_selected(self, listeners):
        root, config, processes = self.process_fixture()
        self.assertEqual(bootup.verified_api_roots(config, root, processes), [303])

    @patch("vectorizer.bootup.stop_verified_process_tree")
    @patch("vectorizer.bootup.listener_pids")
    @patch("vectorizer.bootup.process_snapshot")
    def test_unverified_api_listener_aborts_all_before_any_kill(self, snapshot, listeners, stop):
        root, config, processes = self.process_fixture()
        processes[2]["ExecutablePath"] = str(root / "other-repository-python.exe")
        snapshot.return_value = processes
        listeners.side_effect = lambda port: {8001: {303}, 8080: {202}, 11434: {101}}[port]
        with self.assertRaisesRegex(bootup.BootError, "RAG API port.*unverified"):
            bootup.kill_current_processes(config, root)
        stop.assert_not_called()

    @patch("vectorizer.bootup.listener_pids", return_value=set())
    def test_api_selector_rejects_substrings_other_apps_and_malformed_commands(self, listeners):
        root, config, processes = self.process_fixture()
        for command in ('python -m uvicorn vectorizer.server:application',
                        'python -m uvicorn unrelated:app --log-config vectorizer.server:app',
                        'python -c "-m uvicorn vectorizer.server:app"', 'python "unterminated'):
            processes[2]["CommandLine"] = command
            with self.subTest(command=command):
                self.assertEqual(bootup.verified_api_roots(config, root, processes), [])

    @patch("vectorizer.bootup.wait_until")
    @patch("vectorizer.bootup.stop_verified_process_tree")
    @patch("vectorizer.bootup.listener_pids")
    @patch("vectorizer.bootup.process_snapshot")
    def test_overlapping_trees_do_not_kill_the_same_api_twice(self, snapshot, listeners, stop, wait):
        root, config, processes = self.process_fixture()
        processes[2]["ParentProcessId"] = 202
        remaining = [processes[0]]
        snapshot.side_effect = [processes, processes, remaining, remaining]
        listeners.side_effect = lambda port: {8001: set(), 8080: set(), 11434: {101}}[port]
        bootup.kill_current_processes(config, root)
        self.assertEqual(stop.call_args_list, [call(202, "Open WebUI"), call(101, "Ollama")])

    @patch("vectorizer.bootup.os.getpid", return_value=303)
    @patch("vectorizer.bootup.stop_verified_process_tree")
    @patch("vectorizer.bootup.listener_pids", return_value=set())
    @patch("vectorizer.bootup.process_snapshot")
    def test_bootup_itself_is_protected_even_when_api_selected(self, snapshot, listeners, stop, pid):
        root, config, processes = self.process_fixture()
        snapshot.return_value = processes
        with self.assertRaisesRegex(bootup.BootError, "CLI"):
            bootup.kill_current_processes(config, root, "api")
        stop.assert_not_called()
