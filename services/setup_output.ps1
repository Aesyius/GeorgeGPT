# Presentation and checked-command helpers for setup.ps1. No startup side effects.

function Initialize-SetupOutput([bool]$Detailed = $false) {
    $script:DetailedOutput = $Detailed
    $script:SetupLog = $null
    $script:SetupTimer = $null
    $script:CurrentStep = $null
    $script:StepTimings = @()
    $script:SetupSteps = @(
        'Prepare Python environments',
        'Install dependencies',
        'Pull Ollama models',
        'Index repository',
        'Verify index',
        'Start services and connect Open WebUI'
    )
}

function Format-SetupDuration([TimeSpan]$Duration) {
    return ('{0:00}h {1:00}m {2:00}s' -f [Math]::Floor($Duration.TotalHours), $Duration.Minutes, $Duration.Seconds)
}

function Write-SetupStatus([string]$Message, [string]$Level = 'INFO', [ConsoleColor]$Color = 'Gray') {
    $line = '[{0}] [{1}] {2}' -f (Get-Date -Format 'HH:mm:ss'), $Level, $Message
    Write-Host $line -ForegroundColor $Color
    if ($script:SetupLog) { Add-Content -LiteralPath $script:SetupLog -Value $line -Encoding UTF8 }
}

function Write-SetupSection([string]$Title, [ConsoleColor]$Color = 'Cyan') {
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor $Color
    Write-SetupStatus $Title 'STEP' $Color
    Write-Host '============================================================' -ForegroundColor $Color
    Write-Host ''
}

function Invoke-Checked([string]$Executable, [string[]]$Arguments, [switch]$Quiet) {
    if (-not $Quiet) {
        # Keep interactive WebUI prompts and the indexer's native progress intact.
        & $Executable @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code ${LASTEXITCODE}: $Executable" }
        return
    }
    if (-not $script:SetupLog) { throw 'Setup logging must be initialized before quiet commands.' }
    # Resolve before relaxing error handling so a missing command cannot succeed
    # accidentally using a previous command's native exit code.
    $null = Get-Command $Executable -CommandType Application -ErrorAction Stop
    Add-Content -LiteralPath $script:SetupLog -Encoding UTF8 -Value (
        "`n[{0}] COMMAND: {1} {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Executable, ($Arguments -join ' ')
    )
    $tail = New-Object 'System.Collections.Generic.Queue[string]'
    $previousErrorAction = $ErrorActionPreference
    try {
        # Windows PowerShell represents redirected native stderr as error records.
        # Capture those as diagnostics; the native exit code determines success.
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = $null
        & $Executable @Arguments 2>&1 | ForEach-Object {
            $line = $_.ToString()
            Add-Content -LiteralPath $script:SetupLog -Value $line -Encoding UTF8 -ErrorAction Stop
            $tail.Enqueue($line)
            if ($tail.Count -gt 12) { $null = $tail.Dequeue() }
            if ($script:DetailedOutput) { Write-Host $line }
        }
        $code = $global:LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($null -eq $code) { throw "Command did not report an exit code: $Executable. Full output: $script:SetupLog" }
    if ($code -ne 0) {
        Write-SetupStatus "Command failed (exit $code): $Executable $($Arguments -join ' ')" 'ERROR' Red
        Write-Host '--- Last command output ---' -ForegroundColor Yellow
        foreach ($line in $tail) { Write-Host $line }
        Write-Host '--- End command output ---' -ForegroundColor Yellow
        throw "Command failed with exit code ${code}. Full output: $script:SetupLog"
    }
}

function Update-SetupProgress([int]$Completed, [string]$Status) {
    Write-Progress -Id 1 -Activity 'GeorgeGPT setup - steps completed (not a time estimate)' `
        -Status $Status -PercentComplete ([int](100 * $Completed / $script:SetupSteps.Count))
}

function Write-SetupStep([int]$Number) {
    if ($Number -lt 1 -or $Number -gt $script:SetupSteps.Count) { throw "Invalid setup step: $Number" }
    if ($null -ne $script:CurrentStep) { throw 'Complete the current setup step before starting another.' }
    $title = $script:SetupSteps[$Number - 1]
    $script:CurrentStep = [pscustomobject]@{
        Number = $Number; Title = $title
        Timer = [Diagnostics.Stopwatch]::StartNew(); State = 'Running'
    }
    Write-SetupSection "STEP $Number OF $($script:SetupSteps.Count) - $title"
    Update-SetupProgress ($Number - 1) "Step $Number of $($script:SetupSteps.Count): $title"
}

function Complete-SetupStep([switch]$Failed) {
    if ($null -eq $script:CurrentStep) { return }
    $step = $script:CurrentStep
    $step.Timer.Stop()
    $step.State = if ($Failed) { 'FAILED' } else { 'DONE' }
    $elapsed = Format-SetupDuration $step.Timer.Elapsed
    $color = if ($Failed) { 'Red' } else { 'Green' }
    Write-SetupStatus "$($step.Title) - elapsed $elapsed" $step.State $color
    $script:StepTimings += $step
    $script:CurrentStep = $null
    if (-not $Failed) { Update-SetupProgress $step.Number "$($step.Number) of $($script:SetupSteps.Count) steps completed" }
}

function Write-SetupSummary {
    if ($null -eq $script:SetupTimer) { return }
    $script:SetupTimer.Stop()
    Write-SetupSection "Total elapsed: $(Format-SetupDuration $script:SetupTimer.Elapsed)"
    foreach ($step in $script:StepTimings) {
        Write-SetupStatus ("{0}. {1}: {2} ({3})" -f $step.Number, $step.Title,
            (Format-SetupDuration $step.Timer.Elapsed), $step.State) 'TIMING'
    }
    Write-Host 'Elapsed times include time spent waiting for your input.'
    if ($script:SetupLog) { Write-Host "Setup log: $script:SetupLog" }
}
