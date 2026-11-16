#Requires -Version 5.1
# All services listen locally. Initial package/model downloads require internet.
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryPath,
    [string]$ConfigPath,
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-ProjectPath([string]$Value) {
    $expanded = [Environment]::ExpandEnvironmentVariables($Value)
    if (-not [IO.Path]::IsPathRooted($expanded)) { $expanded = Join-Path $PSScriptRoot $expanded }
    return [IO.Path]::GetFullPath($expanded)
}

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code ${LASTEXITCODE}: $Executable" }
}

function Ensure-Environment([string]$Name, [string]$Launcher) {
    $directory = Join-Path $PSScriptRoot $Name
    $python = Join-Path $directory 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $directory)) {
        Invoke-Checked $Launcher @('-3.12', '-m', 'venv', $directory) | Out-Host
    } elseif (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Existing $Name has no Python executable. Repair it manually; setup will not overwrite it."
    }
    Invoke-Checked $python @('-c', 'import sys; assert sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32') | Out-Host
    return $python
}

try {
    Write-Host @'
============================================================
          ENSURE THE SERVICES ARE INSTALLED
============================================================
  REQUIRED: 64-BIT PYTHON 3.12 (WITH THE PY LAUNCHER)
  REQUIRED: OLLAMA INSTALLED AND RUNNING LOCALLY
  Setup installs the project dependencies and Open WebUI.
============================================================
'@ -ForegroundColor Yellow
    if ([string]::IsNullOrWhiteSpace($ConfigPath)) { $ConfigPath = Join-Path $PSScriptRoot 'config\bootup.json' }
    $repository = (Resolve-Path -LiteralPath $RepositoryPath).Path
    if (-not (Test-Path -LiteralPath $repository -PathType Container)) { throw 'RepositoryPath must be a directory.' }
    $configFile = (Resolve-Path -LiteralPath $ConfigPath).Path
    $config = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $modelsReference = 'config\models.json'
    if ($config.PSObject.Properties['models_config']) { $modelsReference = $config.models_config }
    $modelsFile = Resolve-ProjectPath $modelsReference
    $models = Get-Content -LiteralPath $modelsFile -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($field in @('chat_model', 'embedding_model')) {
        $value = $models.$field
        if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value) -or $value -ne $value.Trim()) {
            throw "Invalid $field in model configuration."
        }
    }
    foreach ($service in @($config.ollama, $config.rag_api, $config.open_webui)) {
        if ($service.host -notin @('127.0.0.1', 'localhost')) { throw 'Setup only supports localhost/127.0.0.1 service hosts.' }
    }
    $apiPython = Resolve-ProjectPath '.venv\Scripts\python.exe'
    $webPython = Resolve-ProjectPath '.venv-webui\Scripts\python.exe'
    if ((Resolve-ProjectPath $config.rag_api.python) -ne $apiPython -or (Resolve-ProjectPath $config.open_webui.python) -ne $webPython) {
        throw 'Setup manages the project .venv and .venv-webui only; adjust configured Python paths or install custom environments manually.'
    }
    # Real setup checks prerequisites before confirmation or any installation.
    # Keep -WhatIf offline: do not invoke Python, Ollama, or service probes.
    if (-not $WhatIfPreference) {
        Write-Host 'Checking required prerequisites before setup...'
        $pythonCommand = Get-Command 'py.exe' -ErrorAction SilentlyContinue
        if (-not $pythonCommand) { throw 'Install 64-bit Python 3.12 with the py launcher before running setup.' }
        $launcher = $pythonCommand.Source
        Invoke-Checked $launcher @('-3.12', '-c', 'import sys; assert sys.maxsize > 2**32')
        # No project dependencies are needed for local-only configuration checks.
        Invoke-Checked $launcher @('-3.12', (Join-Path $PSScriptRoot 'services\setup_check.py'),
                                  '--config', $configFile, '--repository', $repository)
        $ollama = Resolve-ProjectPath $config.ollama.executable
        if (-not (Test-Path -LiteralPath $ollama -PathType Leaf)) {
            throw 'Install Ollama or set ollama.executable in config/bootup.json to its actual installation path.'
        }
        $ollamaUrl = "http://$($config.ollama.host):$($config.ollama.port)"
        try { $tags = Invoke-RestMethod -Uri "$ollamaUrl/api/tags" -TimeoutSec 5 }
        catch { throw "Ollama is not reachable at $ollamaUrl. Start the Ollama application, then retry." }
        $installedModels = @($tags.models | ForEach-Object { $_.name })
        foreach ($port in @($config.rag_api.port, $config.open_webui.port)) {
            if (@(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue).Count -gt 0) {
                throw 'A configured local API/WebUI port is busy. Stop that service or select another loopback port in bootup.json.'
            }
        }
        Write-Host 'Prerequisites confirmed: Python 3.12, Ollama installed/running, and API/WebUI ports available.' -ForegroundColor Green
    } else {
        Write-Host 'Preview only: Python/Ollama availability and service ports will be checked during real setup.'
    }

    Write-Host "Repository: $repository"
    Write-Host "Models: $($models.chat_model), $($models.embedding_model)"
    Write-Host 'Steps: create/reuse environments; install dependencies; pull missing models; index and verify; launch API/WebUI; save connection.'
    Write-Host 'This downloads dependencies/model weights and indexes eligible text. Review source filters before indexing sensitive repositories.'
    if (-not $PSCmdlet.ShouldProcess($repository, 'Initialize GeorgeGPT and bootstrap its local services')) { return }

    # Persist the chosen source so future bootups use the same repository.
    $config | Add-Member -MemberType NoteProperty -Name repository_path -Value $repository -Force
    [IO.File]::WriteAllText($configFile, ($config | ConvertTo-Json -Depth 16), [Text.UTF8Encoding]::new($false))

    $oldRepositoryPath = $env:RAG_REPOSITORY_PATH
    $oldPythonUtf8 = $env:PYTHONUTF8
    $oldConsoleEncoding = [Console]::OutputEncoding
    $oldOutputEncoding = $OutputEncoding
    Push-Location $PSScriptRoot
    try {
        $env:RAG_REPOSITORY_PATH = $repository
        $env:PYTHONUTF8 = '1'
        [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
        $OutputEncoding = [Console]::OutputEncoding
        Write-Host 'Preparing Python environments...'
        $python = Ensure-Environment '.venv' $launcher
        $web = Ensure-Environment '.venv-webui' $launcher
        Invoke-Checked $python @('-m', 'pip', 'install', '--upgrade', 'pip')
        Invoke-Checked $web @('-m', 'pip', 'install', '--upgrade', 'pip')
        Invoke-Checked $python @('-m', 'pip', 'install', '-r', (Join-Path $PSScriptRoot 'requirements.txt'))
        Invoke-Checked $web @('-m', 'pip', 'install', '-r', (Join-Path $PSScriptRoot 'requirements-webui.txt'))
        $oldOllamaHost = $env:OLLAMA_HOST
        try {
            $env:OLLAMA_HOST = $ollamaUrl
            foreach ($model in @($models.chat_model, $models.embedding_model) | Select-Object -Unique) {
                if ($installedModels -contains $model) { Write-Host "Using installed model: $model" }
                else { Invoke-Checked $ollama @('pull', $model) }
            }
        } finally { $env:OLLAMA_HOST = $oldOllamaHost }

        $database = Join-Path $PSScriptRoot 'chroma_db'
        if ($env:RAG_DB_PATH) { $database = Resolve-ProjectPath $env:RAG_DB_PATH }
        $collection = 'repo_documents'
        if ($env:RAG_COLLECTION) { $collection = $env:RAG_COLLECTION }
        $indexOptions = @($repository, '--embedding-model', $models.embedding_model, '--ollama-url', $ollamaUrl,
                          '--db-path', $database, '--collection', $collection)
        Write-Host 'Indexing repository and verifying vectors/keyword rows...'
        Invoke-Checked $python (@('-m', 'vectorizer.vectorize') + $indexOptions)
        # The verifier is read-only and does not need an Ollama URL.
        $verifyOptions = @($repository, '--embedding-model', $models.embedding_model,
                           '--db-path', $database, '--collection', $collection)
        Invoke-Checked $python (@('-m', 'vectorizer.verify_index') + $verifyOptions)
        $bootstrapOptions = @('-m', 'services.first_time_setup', '--config', $configFile)
        if ($NoBrowser) { $bootstrapOptions += '--no-browser' }
        Invoke-Checked $python $bootstrapOptions
    } finally {
        Pop-Location
        $env:RAG_REPOSITORY_PATH = $oldRepositoryPath
        $env:PYTHONUTF8 = $oldPythonUtf8
        [Console]::OutputEncoding = $oldConsoleEncoding
        $OutputEncoding = $oldOutputEncoding
    }
} catch {
    [Console]::Error.WriteLine("First-time setup failed: $($_.Exception.Message)")
    exit 1
}
