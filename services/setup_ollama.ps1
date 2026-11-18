# Dot-sourcing this file does not discover or launch anything.

function Find-SetupOllama([string]$ConfiguredPath) {
    if (Test-Path -LiteralPath $ConfiguredPath -PathType Leaf) { return $ConfiguredPath }
    foreach ($command in @(Get-Command 'ollama.exe' -CommandType Application -All -ErrorAction SilentlyContinue)) {
        if (Test-Path -LiteralPath $command.Source -PathType Leaf) { return $command.Source }
    }
    if ($env:LOCALAPPDATA) {
        $default = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
        if (Test-Path -LiteralPath $default -PathType Leaf) { return $default }
    }
}

function Start-SetupOllama {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [Parameter(Mandatory = $true)][string]$LogsDirectory,
        [ValidateRange(1, 600)][int]$TimeoutSeconds = 30
    )

    $endpoint = [Uri]$BaseUrl
    if ($endpoint.Scheme -ne 'http' -or $endpoint.Host -notin @('localhost', '127.0.0.1')) {
        throw 'Setup can only start Ollama on a local HTTP loopback endpoint.'
    }
    $tagsUrl = "$($BaseUrl.TrimEnd('/'))/api/tags"
    try {
        $null = Invoke-RestMethod -Uri $tagsUrl -TimeoutSec 5
        Write-SetupStatus "Ollama is already running at $BaseUrl; reusing it." 'SKIP' Green
        return
    } catch {
        # A fresh installer may not have started the local server yet.
    }

    Write-SetupStatus "Ollama is installed but not responding at $BaseUrl."
    do {
        $startOllama = (Read-Host 'Start Ollama now? Press Y/N').Trim()
    } while ($startOllama -notin @('Y', 'N'))
    if ($startOllama -ne 'Y') {
        throw 'Ollama startup declined. Start the Ollama application manually, then rerun setup. The installation has been kept.'
    }
    if (-not $PSCmdlet.ShouldProcess($BaseUrl, 'Start the local Ollama server')) {
        throw 'Ollama startup cancelled. Start Ollama manually, then rerun setup.'
    }

    $null = New-Item -ItemType Directory -Path $LogsDirectory -Force
    $logName = 'ollama-setup-{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID
    $stdoutLog = Join-Path $LogsDirectory "$logName.stdout.log"
    $stderrLog = Join-Path $LogsDirectory "$logName.stderr.log"
    $previousHost = $env:OLLAMA_HOST
    try {
        $env:OLLAMA_HOST = $BaseUrl
        $process = Start-Process -FilePath $Executable -ArgumentList 'serve' `
            -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    } finally {
        $env:OLLAMA_HOST = $previousHost
    }

    Write-SetupStatus "Starting Ollama at $BaseUrl; waiting up to $TimeoutSeconds seconds..." 'START'
    Write-SetupStatus "Ollama startup logs: $stdoutLog and $stderrLog"
    $timer = [Diagnostics.Stopwatch]::StartNew()
    try {
        while ($timer.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
            try {
                $remaining = [Math]::Max(1, [Math]::Ceiling($TimeoutSeconds - $timer.Elapsed.TotalSeconds))
                $null = Invoke-RestMethod -Uri $tagsUrl -TimeoutSec ([Math]::Min(5, $remaining))
                Write-SetupStatus "Ollama is ready at $BaseUrl." 'DONE' Green
                return
            } catch {
                if ($process.HasExited) {
                    throw "Ollama exited before becoming ready. Check $stderrLog and $stdoutLog."
                }
            }
            Start-Sleep -Milliseconds 500
        }
        throw "Ollama did not become ready within $TimeoutSeconds seconds. Check $stderrLog and $stdoutLog. Any server already launched remains running."
    } finally {
        $timer.Stop()
        $process.Dispose()
    }
}
