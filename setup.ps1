$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Find-Python {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "C:\Python312\python.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") {
        return $cmd.Source
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Host "Python 3.12 is required. Installing via winget..."
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $python = Find-Python
}
if (-not $python) {
    throw "Python was not found after install. Close this terminal, open a new one, and run setup.ps1 again."
}

Write-Host "Using $python"
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    & $python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Write-Host ""
    Write-Host "Ollama is not installed. Trinity's brain needs it for local replies."
    Write-Host "Install from https://ollama.com/download then run: ollama pull hermes3:8b"
} else {
    Write-Host "Ollama found. Pulling local Hermes 3 if needed (this can take a few minutes)..."
    try {
        & ollama pull hermes3:8b
    } catch {
        Write-Host "Could not pull Hermes automatically. Start Ollama and run: ollama pull hermes3:8b"
    }
}

Write-Host ""
Write-Host "Setup complete. Launch with run.bat or:  .\.venv\Scripts\python.exe -m trinity"
