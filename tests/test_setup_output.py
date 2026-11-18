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
foreach ($file in @('setup.ps1', 'services/setup_output.ps1')) {
    $tokens = $null
    $parseErrors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $PWD $file), [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
}
"""
        )

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
"""
        )
        self.assertIn("PREVIEW ONLY", output)
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


if __name__ == "__main__":
    unittest.main()
