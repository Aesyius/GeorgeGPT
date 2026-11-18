"""Offline regression tests for Windows setup output and consent boundaries."""

from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


def ps_string(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell required")
class SetupOutputTests(unittest.TestCase):
    def setUp(self):
        temp = Path(os.environ["LOCALAPPDATA"]) / "Temp" / "opencode"
        temp.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.log = self.directory / "setup.log"
        self.fixture = self.directory / "native output.py"
        self.fixture.write_text(
            "import sys\n"
            "print('native stdout')\n"
            "print('native stderr', file=sys.stderr)\n"
            "if len(sys.argv) > 1:\n"
            "    for number in range(20):\n"
            "        print(f'diagnostic-{number}')\n"
            "    sys.exit(7)\n",
            encoding="utf-8",
        )

    def run_ps(self, body):
        harness = self.directory / "test.ps1"
        harness.write_text(
            "$ErrorActionPreference = 'Stop'\nSet-StrictMode -Version Latest\n"
            f". {ps_string(ROOT / 'services' / 'setup_output.ps1')}\n"
            f". {ps_string(ROOT / 'services' / 'setup_ollama.ps1')}\n"
            "Initialize-SetupOutput\n"
            f"$script:SetupLog = {ps_string(self.log)}\n"
            f"$python = {ps_string(sys.executable)}\n"
            f"$fixture = {ps_string(self.fixture)}\n"
            + body,
            encoding="utf-8-sig",
        )
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
             "Bypass", "-File", str(harness)],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_powershell_syntax(self):
        self.run_ps(
            """
foreach ($file in @('setup.ps1', 'services/setup_output.ps1', 'services/setup_ollama.ps1')) {
    $tokens = $null
    $parseErrors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $PWD $file), [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
}
"""
        )

    def test_setup_reports_original_error_without_formatting_failure(self):
        missing = self.directory / "missing-repository"
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
             "Bypass", "-File", str(ROOT / "setup.ps1"),
             "-RepositoryPath", str(missing), "-WhatIf"],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertRegex(output, r"\[\d{2}:\d{2}:\d{2}\] \[ERROR\] First-time setup failed:")
        self.assertIn("missing-repository", output)
        self.assertNotIn("FormatError", output)
        self.assertNotIn("Error formatting a string", output)

    def test_initial_prompt_retries_invalid_input_and_cancels_before_checks(self):
        output = self.run_ps(
            """
$global:promptCount = 0
function Read-Host {
    param($Prompt)
    $global:promptCount++
    if ($global:promptCount -eq 1) { return 'invalid' }
    return 'n'
}
function Get-Command { throw 'Prerequisite check ran after cancellation' }
& (Join-Path $PWD 'setup.ps1') -RepositoryPath 'nonexistent-cancelled-repository'
if ($global:promptCount -ne 2) { throw 'Invalid input was not retried' }
"""
        )
        self.assertIn("[CANCELLED]", output)
        self.assertIn("No installation or configuration changes were made.", output)
        self.assertFalse(self.log.exists())

    def test_quiet_logs_stdout_and_stderr_without_terminal_noise(self):
        output = self.run_ps(
            """
Invoke-Checked $python @($fixture) -Quiet
if ($ErrorActionPreference -ne 'Stop') { throw 'Error preference leaked' }
"""
        )
        self.assertNotIn("native stdout", output)
        self.assertNotIn("native stderr", output)
        log = self.log.read_text(encoding="utf-8-sig")
        self.assertIn("native stdout", log)
        self.assertIn("native stderr", log)

    def test_verbose_also_shows_native_output(self):
        output = self.run_ps(
            """
$script:DetailedOutput = $true
Invoke-Checked $python @($fixture) -Quiet
"""
        )
        self.assertIn("native stdout", output)
        self.assertIn("native stderr", output)
        self.assertIn("native stdout", self.log.read_text(encoding="utf-8-sig"))

    def test_native_failure_has_exit_code_and_bounded_tail(self):
        output = self.run_ps(
            """
$caught = $false
try { Invoke-Checked $python @($fixture, 'fail') -Quiet } catch {
    $caught = $_.Exception.Message -match 'exit code 7'
}
if (-not $caught) { throw 'Expected exit code 7' }
if ($ErrorActionPreference -ne 'Stop') { throw 'Error preference leaked' }
"""
        )
        self.assertIn("diagnostic-19", output)
        self.assertNotIn("\ndiagnostic-0\n", output)
        self.assertIn("diagnostic-0", self.log.read_text(encoding="utf-8-sig"))

    def test_missing_executable_cannot_reuse_previous_exit_code(self):
        self.run_ps(
            """
$global:LASTEXITCODE = 0
$caught = $false
try { Invoke-Checked 'georgegpt-no-such-command.exe' @() -Quiet } catch {
    $caught = $true
}
if (-not $caught) { throw 'Missing executable incorrectly succeeded' }
"""
        )

    def test_step_states_timestamps_duration_and_summary(self):
        output = self.run_ps(
            """
if ((Format-SetupDuration ([TimeSpan]::FromSeconds(3661))) -ne '01h 01m 01s') {
    throw 'Incorrect duration formatting'
}
$script:SetupTimer = [Diagnostics.Stopwatch]::StartNew()
Write-SetupStep 1
Complete-SetupStep
Complete-SetupStep
Write-SetupStep 2
Complete-SetupStep -Failed
if ($script:StepTimings.Count -ne 2) { throw 'Wrong timing count' }
if ($script:StepTimings[0].State -ne 'DONE') { throw 'Wrong success state' }
if ($script:StepTimings[1].State -ne 'FAILED') { throw 'Wrong failure state' }
if ($null -ne $script:CurrentStep) { throw 'Active step was not cleared' }
Write-SetupSummary
Write-Progress -Id 1 -Activity 'Test' -Completed
"""
        )
        self.assertRegex(output, r"\[\d{2}:\d{2}:\d{2}\] \[STEP\]")
        self.assertIn("STEP 1 OF 6", output)
        self.assertIn("Total elapsed:", output)

    def test_step_rejects_invalid_or_overlapping_stages(self):
        self.run_ps(
            """
$caught = $false
try { Write-SetupStep 0 } catch { $caught = $true }
if (-not $caught) { throw 'Invalid step accepted' }
Write-SetupStep 1
$caught = $false
try { Write-SetupStep 2 } catch { $caught = $true }
if (-not $caught) { throw 'Overlapping step accepted' }
Complete-SetupStep
"""
        )

    def test_whatif_is_offline_and_does_not_write(self):
        # Run a disposable checkout containing just the preview's required files.
        project = self.directory / "project"
        (project / "services").mkdir(parents=True)
        (project / "config").mkdir()
        shutil.copyfile(ROOT / "setup.ps1", project / "setup.ps1")
        shutil.copyfile(ROOT / "services/setup_output.ps1", project / "services/setup_output.ps1")
        shutil.copyfile(ROOT / "services/setup_ollama.ps1", project / "services/setup_ollama.ps1")
        shutil.copyfile(ROOT / "config/models.json", project / "config/models.json")
        config = {
            "models_config": "config/models.json",
            "logs_directory": "logs",
            "ollama": {"host": "127.0.0.1"},
            "rag_api": {"host": "127.0.0.1", "python": ".venv/Scripts/python.exe"},
            "open_webui": {"host": "127.0.0.1", "python": ".venv-webui/Scripts/python.exe"},
        }
        config_file = project / "config/bootup.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")
        before = config_file.read_bytes()
        output = self.run_ps(
            f"""
function Invoke-RestMethod {{ throw 'Network probe during preview' }}
function Invoke-Expression {{ throw 'Installer execution during preview' }}
function Get-Command {{ throw 'Prerequisite probe during preview' }}
& {ps_string(project / 'setup.ps1')} -RepositoryPath {ps_string(project)} -WhatIf -Verbose
& {ps_string(project / 'setup.ps1')} -RepositoryPath {ps_string(project)} -WhatIf -ChunkWorkers 1
"""
        )
        self.assertIn("PREVIEW ONLY", output)
        self.assertIn("Chunk workers:   4", output)
        self.assertIn("Chunk workers:   1", output)
        self.assertEqual(before, config_file.read_bytes())
        self.assertFalse((project / "logs").exists())
        self.assertFalse((project / ".venv").exists())

    def test_ollama_installation_requires_consent_and_restores_environment(self):
        output = self.run_ps(
            """
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $PWD 'setup.ps1'), [ref]$tokens, [ref]$parseErrors)
$node = $ast.FindAll({
    param($n)
    $n -is [System.Management.Automation.Language.IfStatementAst] -and
    $n.Extent.Text.StartsWith('if (-not (Test-Path -LiteralPath $ollama -PathType Leaf))')
}, $true) | Select-Object -First 1
$body = $node.Clauses[0].Item2.Extent.Text
# Only replace the automatic ShouldProcess object with a consent test double.
$block = [scriptblock]::Create($body.Substring(1, $body.Length - 2).Replace(
    '$PSCmdlet.ShouldProcess(', '$consent.ShouldProcess('))
function Test-Path {
    param($LiteralPath, $PathType)
    return ($script:installed -and $LiteralPath -eq 'mock-ollama.exe')
}
function Get-Command {
    param($Name, $ErrorAction)
    if ($script:existing) { return [pscustomobject]@{Source = 'existing-ollama.exe'} }
}
function Read-Host {
    param($Prompt)
    $answer = [string]$script:answers[$script:answerIndex]
    $script:answerIndex++
    return $answer
}
function Invoke-RestMethod { param($Uri) $script:downloads++; return 'mock installer' }
function Invoke-Expression {
    param([Parameter(ValueFromPipeline=$true)]$Command)
    process {
        if ($env:OLLAMA_HOST -ne 'http://127.0.0.1:11434') { throw 'Nonloopback host' }
        $script:executions++
        $script:installed = $script:installSucceeds
    }
}
$consent = New-Object PSObject
$consent | Add-Member ScriptMethod ShouldProcess { param($target, $action) return $script:approve }
$config = @{ollama = @{host = '127.0.0.1'; port = 11434}}
$ollama = 'mock-ollama.exe'
$logsDirectory = 'unused-mock-directory'
function Start-SetupOllama { param($Executable, $BaseUrl, $LogsDirectory) }
$originalHost = $env:OLLAMA_HOST
try {
    foreach ($case in @('yes', 'no', 'invalid', 'cancelled', 'missing', 'existing')) {
        $script:installed = $false
        $script:existing = $case -eq 'existing'
        $script:installSucceeds = $case -ne 'missing'
        $script:approve = $case -ne 'cancelled'
        $script:answers = @(if ($case -eq 'no') {'N'} elseif ($case -eq 'invalid') {'?'; 'y'} else {'Y'})
        $script:answerIndex = 0
        $script:downloads = 0
        $script:executions = 0
        $env:OLLAMA_HOST = 'original-test-host'
        $failure = $null
        try { & $block } catch { $failure = $_ }
        if (($case -in @('yes', 'invalid')) -eq ($null -ne $failure)) {
            throw "Unexpected result for $case : $failure"
        }
        $expected = if ($case -in @('yes', 'invalid', 'missing')) {1} else {0}
        if ($script:downloads -ne $expected -or $script:executions -ne $expected) {
            throw "Unexpected installer activity for $case"
        }
        if ($case -eq 'invalid' -and $script:answerIndex -ne 2) { throw 'Invalid input not retried' }
        if ($env:OLLAMA_HOST -ne 'original-test-host') { throw 'Host was not restored' }
        Write-Output "PASS: $case"
    }
} finally { $env:OLLAMA_HOST = $originalHost }
"""
        )
        for case in ("yes", "no", "invalid", "cancelled", "missing", "existing"):
            self.assertIn(f"PASS: {case}", output)

    def test_post_install_reuses_running_ollama_without_prompt(self):
        output = self.run_ps(
            """
function Invoke-RestMethod { param($Uri, $TimeoutSec) return @{models = @()} }
function Read-Host { throw 'Unexpected start prompt' }
function Start-Process { throw 'Unexpected process launch' }
Start-SetupOllama -Executable 'mock-ollama.exe' -BaseUrl 'http://127.0.0.1:11434' -LogsDirectory (Split-Path $script:SetupLog)
"""
        )
        self.assertIn("already running", output)

    def test_post_install_decline_does_not_launch(self):
        output = self.run_ps(
            """
function Invoke-RestMethod { param($Uri, $TimeoutSec) throw 'Not ready' }
function Read-Host { param($Prompt) return 'N' }
function Start-Process { throw 'Unexpected process launch' }
$caught = $false
try {
    Start-SetupOllama -Executable 'mock-ollama.exe' -BaseUrl 'http://localhost:11434' -LogsDirectory (Split-Path $script:SetupLog)
} catch {
    $caught = $_.Exception.Message -match 'startup declined'
}
if (-not $caught) { throw 'Expected manual-start instructions' }
"""
        )
        self.assertIn("installation completed", output)

    def test_post_install_starts_loopback_waits_and_restores_host(self):
        output = self.run_ps(
            """
$script:probes = 0
$script:prompts = 0
$script:launched = 0
$script:disposed = $false
function Invoke-RestMethod {
    param($Uri, $TimeoutSec)
    $script:probes++
    if ($script:probes -eq 1) { throw 'Not started' }
    if ($Uri -ne 'http://127.0.0.1:11434/api/tags') { throw 'Unexpected readiness endpoint' }
    return @{models = @()}
}
function Read-Host {
    param($Prompt)
    $script:prompts++
    if ($script:prompts -eq 1) { return 'invalid' }
    return 'y'
}
function Start-Process {
    param($FilePath, $ArgumentList, $WindowStyle, [switch]$PassThru,
          $RedirectStandardOutput, $RedirectStandardError)
    if ($env:OLLAMA_HOST -ne 'http://127.0.0.1:11434') { throw 'Host is not loopback' }
    if ($ArgumentList -ne 'serve') { throw 'Expected serve command' }
    $script:launched++
    $process = [pscustomobject]@{HasExited = $false}
    $process | Add-Member ScriptMethod Dispose { $script:disposed = $true }
    return $process
}
$env:OLLAMA_HOST = 'original-host'
Start-SetupOllama -Executable 'mock-ollama.exe' -BaseUrl 'http://127.0.0.1:11434' -LogsDirectory (Split-Path $script:SetupLog)
if ($env:OLLAMA_HOST -ne 'original-host') { throw 'Host override leaked' }
if ($script:launched -ne 1 -or $script:prompts -ne 2) { throw 'Incorrect launch/prompt count' }
if (-not $script:disposed) { throw 'Process handle leaked' }
"""
        )
        self.assertIn("Ollama is ready", output)

    def test_post_install_failure_restores_host(self):
        self.run_ps(
            """
function Invoke-RestMethod { param($Uri, $TimeoutSec) throw 'Not ready' }
function Read-Host { param($Prompt) return 'Y' }
function Start-Process { throw 'Mock launch failure' }
$env:OLLAMA_HOST = 'original-host'
$caught = $false
try {
    Start-SetupOllama -Executable 'mock-ollama.exe' -BaseUrl 'http://localhost:11434' -LogsDirectory (Split-Path $script:SetupLog)
} catch { $caught = $_.Exception.Message -match 'Mock launch failure' }
if (-not $caught) { throw 'Launch failure was hidden' }
if ($env:OLLAMA_HOST -ne 'original-host') { throw 'Host override leaked' }
"""
        )

    def test_post_install_detects_exit_and_timeout(self):
        self.run_ps(
            """
function Invoke-RestMethod { param($Uri, $TimeoutSec) throw 'Not ready' }
function Read-Host { param($Prompt) return 'Y' }
function Start-Process {
    param($FilePath, $ArgumentList, $WindowStyle, [switch]$PassThru,
          $RedirectStandardOutput, $RedirectStandardError)
    $process = [pscustomobject]@{HasExited = $script:exited}
    $process | Add-Member ScriptMethod Dispose {}
    return $process
}
foreach ($exited in @($true, $false)) {
    $script:exited = $exited
    $caught = $false
    try {
        Start-SetupOllama -Executable 'mock-ollama.exe' -BaseUrl 'http://127.0.0.1:11434' -LogsDirectory (Split-Path $script:SetupLog) -TimeoutSeconds 1
    } catch {
        $pattern = if ($exited) { 'exited before becoming ready' } else { 'did not become ready within' }
        $caught = $_.Exception.Message -match $pattern
    }
    if (-not $caught) { throw 'Missing startup failure diagnostic' }
}
"""
        )

    def test_post_install_rejects_nonlocal_endpoint_before_probing(self):
        self.run_ps(
            """
function Invoke-RestMethod { throw 'Unexpected remote probe' }
$caught = $false
try {
    Start-SetupOllama -Executable 'mock-ollama.exe' -BaseUrl 'http://0.0.0.0:11434' -LogsDirectory (Split-Path $script:SetupLog)
} catch { $caught = $_.Exception.Message -match 'loopback' }
if (-not $caught) { throw 'Nonlocal endpoint accepted' }
"""
        )


if __name__ == "__main__":
    unittest.main()
