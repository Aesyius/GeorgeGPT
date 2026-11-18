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
. (Join-Path $PSScriptRoot 'services\setup_output.ps1')
Initialize-SetupOutput ($VerbosePreference -eq 'Continue')

function Resolve-ProjectPath([string]$Value) {
    $expanded = [Environment]::ExpandEnvironmentVariables($Value)
    if (-not [IO.Path]::IsPathRooted($expanded)) { $expanded = Join-Path $PSScriptRoot $expanded }
    return [IO.Path]::GetFullPath($expanded)
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
                  GeorgeGPT - First-time setup
============================================================

  Before you begin:
    - Install 64-bit Python 3.12 with the py launcher.
    - Start Ollama if it is already installed.
      If it is missing, setup will ask before installing it.

  Setup prepares the Python environments, installs Open WebUI,
  downloads missing models, and indexes your repository.
  All services stay local to this computer.

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
    # Real setup checks prerequisites; missing Ollama installation requires consent.
    # Keep -WhatIf offline: do not invoke Python, Ollama, or service probes.
    if (-not $WhatIfPreference) {
        $script:SetupTimer = [Diagnostics.Stopwatch]::StartNew()
        $logsDirectory = Resolve-ProjectPath $config.logs_directory
        $null = New-Item -ItemType Directory -Path $logsDirectory -Force
        $script:SetupLog = Join-Path $logsDirectory ("setup-{0}-{1}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID)
        Write-SetupStatus "Detailed dependency/model output: $script:SetupLog"
        if (-not $script:DetailedOutput) {
            Write-SetupStatus 'Quiet output enabled. Use -Verbose to also show pip and Ollama output.'
        }
        Write-SetupSection 'Checking prerequisites'
        Write-SetupStatus 'Checking Python, local configuration, Ollama, and service ports...'
        $pythonCommand = Get-Command 'py.exe' -ErrorAction SilentlyContinue
        if (-not $pythonCommand) { throw 'Install 64-bit Python 3.12 with the py launcher before running setup.' }
        $launcher = $pythonCommand.Source
        Invoke-Checked $launcher @('-3.12', '-c', 'import sys; assert sys.maxsize > 2**32')
        # No project dependencies are needed for local-only configuration checks.
        Invoke-Checked $launcher @('-3.12', (Join-Path $PSScriptRoot 'services\setup_check.py'),
                                  '--config', $configFile, '--repository', $repository)
        $ollama = Resolve-ProjectPath $config.ollama.executable
        if (-not (Test-Path -LiteralPath $ollama -PathType Leaf)) {
            $existingOllama = Get-Command 'ollama.exe' -ErrorAction SilentlyContinue
            $defaultOllama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
            if ($existingOllama -or (Test-Path -LiteralPath $defaultOllama -PathType Leaf)) {
                throw 'Ollama is already installed, but the configured executable is missing. Set ollama.executable in config/bootup.json to its actual installation path, then retry.'
            }
            Write-SetupSection 'Ollama installation required' Yellow
            Write-Host "Expected executable: $ollama"
            Write-Host 'With your permission, setup will download and run the official installer.'
            Write-Host 'This executes a script from the internet on your computer:'
            Write-Host '  irm https://ollama.com/install.ps1 | iex'
            Write-Host 'Choose Y to install, or N to stop setup without installing.'
            Write-Host ''
            do {
                $installOllama = (Read-Host 'Install Ollama now? Press Y/N').Trim()
            } while ($installOllama -notin @('Y', 'N'))
            if ($installOllama -ne 'Y') {
                throw 'Ollama installation declined. Install it manually or correct ollama.executable, then rerun setup.'
            }
            if (-not $PSCmdlet.ShouldProcess('https://ollama.com/install.ps1', 'Download and execute the Ollama installer')) {
                throw 'Ollama installation cancelled; setup cannot continue without Ollama.'
            }
            $previousOllamaHost = $env:OLLAMA_HOST
            try {
                # Installer-launched processes must inherit a loopback-only endpoint.
                $env:OLLAMA_HOST = "http://$($config.ollama.host):$($config.ollama.port)"
                irm https://ollama.com/install.ps1 | iex
            } finally {
                $env:OLLAMA_HOST = $previousOllamaHost
            }
            if (-not (Test-Path -LiteralPath $ollama -PathType Leaf)) {
                throw 'Ollama installer finished, but the configured executable is still missing. Check the installation and ollama.executable in config/bootup.json, then rerun setup.'
            }
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
        Write-SetupStatus 'Prerequisites passed. Python and Ollama are ready; API/WebUI ports are free.' 'DONE' Green
    } else {
        Write-Host ''
        Write-Host 'PREVIEW ONLY (-WhatIf): no installation, downloads, indexing, or service startup.' -ForegroundColor Cyan
        Write-Host 'Python, Ollama, and service ports will be checked when you run without -WhatIf.'
    }

    Write-SetupSection 'Setup overview'
    Write-Host "  Repository:      $repository"
    Write-Host "  Chat model:      $($models.chat_model)"
    Write-Host "  Embedding model: $($models.embedding_model)"
    Write-Host ''
    for ($stepIndex = 0; $stepIndex -lt $script:SetupSteps.Count; $stepIndex++) {
        Write-Host ('  {0}. {1}' -f ($stepIndex + 1), $script:SetupSteps[$stepIndex])
    }
    Write-Host ''
    Write-Host 'Downloads require internet. Review source filters before indexing sensitive content.' -ForegroundColor Yellow
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host ''
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
        Write-SetupStep 1
        Write-Host 'Preparing .venv for GeorgeGPT and .venv-webui for Open WebUI...'
        $python = Ensure-Environment '.venv' $launcher
        $web = Ensure-Environment '.venv-webui' $launcher
        Complete-SetupStep

        Write-SetupStep 2
        Write-SetupStatus 'Updating pip in both environments...'
        Invoke-Checked $python @('-m', 'pip', 'install', '--upgrade', 'pip') -Quiet
        Invoke-Checked $web @('-m', 'pip', 'install', '--upgrade', 'pip') -Quiet
        Write-Host ''
        Write-SetupStatus 'Installing GeorgeGPT dependencies from requirements.txt...'
        Invoke-Checked $python @('-m', 'pip', 'install', '-r', (Join-Path $PSScriptRoot 'requirements.txt')) -Quiet
        Write-Host ''
        Write-SetupStatus 'Installing Open WebUI dependencies from requirements-webui.txt...'
        Invoke-Checked $web @('-m', 'pip', 'install', '-r', (Join-Path $PSScriptRoot 'requirements-webui.txt')) -Quiet
        Complete-SetupStep

        Write-SetupStep 3
        Write-Host "Ollama endpoint: $ollamaUrl"
        Write-Host 'Installed models are reused. Missing models will be downloaded.'
        Write-Host 'Model downloads can be large. Detailed progress is saved to the setup log.'
        Write-Host 'Use -Verbose to also show native download progress; no estimated percentage is shown in quiet mode.'
        $requiredModels = @(@($models.chat_model, $models.embedding_model) | Select-Object -Unique)
        $modelNumber = 0
        $oldOllamaHost = $env:OLLAMA_HOST
        try {
            $env:OLLAMA_HOST = $ollamaUrl
            foreach ($model in $requiredModels) {
                $modelNumber++
                Write-Host ''
                Write-Host "------------------------------------------------------------" -ForegroundColor Cyan
                Write-Host "  Model $modelNumber of $($requiredModels.Count): $model" -ForegroundColor Cyan
                if ($installedModels -contains $model) {
                    Write-SetupStatus "Already installed; using $model." 'SKIP' Green
                } else {
                    $modelTimer = [Diagnostics.Stopwatch]::StartNew()
                    Write-SetupStatus "Pulling $model from Ollama. Please wait..." 'DOWNLOAD' Yellow
                    Invoke-Checked $ollama @('pull', $model) -Quiet
                    $modelTimer.Stop()
                    Write-SetupStatus "Model downloaded: $model - elapsed $(Format-SetupDuration $modelTimer.Elapsed)" 'DONE' Green
                }
            }
        } finally { $env:OLLAMA_HOST = $oldOllamaHost }
        Write-Host ''
        Complete-SetupStep

        $database = Join-Path $PSScriptRoot 'chroma_db'
        if ($env:RAG_DB_PATH) { $database = Resolve-ProjectPath $env:RAG_DB_PATH }
        $collection = 'repo_documents'
        if ($env:RAG_COLLECTION) { $collection = $env:RAG_COLLECTION }
        $indexOptions = @($repository, '--embedding-model', $models.embedding_model, '--ollama-url', $ollamaUrl,
                          '--db-path', $database, '--collection', $collection)
        Write-SetupStep 4
        Write-Host "Repository: $repository"
        Write-Host "Embedding model: $($models.embedding_model)"
        Write-Host 'First-time indexing may take a while. Progress will appear below.'
        Invoke-Checked $python (@('-m', 'vectorizer.vectorize') + $indexOptions)
        Complete-SetupStep

        Write-SetupStep 5
        Write-Host 'Auditing chunks, vector metadata, embeddings, and keyword rows...'
        # The verifier is read-only and does not need an Ollama URL.
        $verifyOptions = @($repository, '--embedding-model', $models.embedding_model,
                           '--db-path', $database, '--collection', $collection)
        Invoke-Checked $python (@('-m', 'vectorizer.verify_index') + $verifyOptions)
        Complete-SetupStep

        Write-SetupStep 6
        Write-Host 'Warming models and starting the local API and Open WebUI...'
        Write-Host 'Then follow the connection instructions and return here to confirm.'
        if ($NoBrowser) { Write-Host 'Automatic browser opening is disabled (-NoBrowser).' }
        $bootstrapOptions = @('-m', 'services.first_time_setup', '--config', $configFile)
        if ($NoBrowser) { $bootstrapOptions += '--no-browser' }
        Invoke-Checked $python $bootstrapOptions
        Complete-SetupStep
        Write-SetupSection "SETUP COMPLETE - All $($script:SetupSteps.Count) steps finished successfully." Green
    } finally {
        Pop-Location
        $env:RAG_REPOSITORY_PATH = $oldRepositoryPath
        $env:PYTHONUTF8 = $oldPythonUtf8
        [Console]::OutputEncoding = $oldConsoleEncoding
        $OutputEncoding = $oldOutputEncoding
    }
} catch {
    $failure = $_.Exception.Message
    Complete-SetupStep -Failed
    [Console]::Error.WriteLine("[{0}] [ERROR] First-time setup failed: {1}" -f (Get-Date -Format 'HH:mm:ss'), $failure)
    exit 1
} finally {
    if ($null -ne $script:SetupTimer) {
        Write-Progress -Id 1 -Activity 'GeorgeGPT setup' -Completed
    }
    Write-SetupSummary
}
