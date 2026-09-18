param(
    [ValidateSet("Amy", "Lessac", "Ryan")]
    [string]$Voice = "Amy"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$voices = @{
    Amy = "en_US-amy-medium"
    Lessac = "en_US-lessac-medium"
    Ryan = "en_US-ryan-medium"
}
$model = $voices[$Voice]
$destination = Join-Path $PSScriptRoot "data\voices"
New-Item -ItemType Directory -Force -Path $destination | Out-Null

$base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/"
switch ($Voice) {
    "Amy" { $remotePath = "amy/medium/$model" }
    "Lessac" { $remotePath = "lessac/medium/$model" }
    "Ryan" { $remotePath = "ryan/medium/$model" }
}

Write-Host "Downloading the one-time local Piper voice model: $Voice"
$onnxPath = Join-Path $destination "$model.onnx"
$configPath = Join-Path $destination "$model.onnx.json"
& curl.exe --fail --location --retry 3 --output $onnxPath "$base$remotePath.onnx?download=true"
if ($LASTEXITCODE -ne 0) { throw "Could not download the Piper voice model." }
& curl.exe --fail --location --retry 3 --output $configPath "$base$remotePath.onnx.json?download=true"
if ($LASTEXITCODE -ne 0) { throw "Could not download the Piper voice configuration." }
if ((Get-Item $onnxPath).Length -lt 1000000) { throw "The Piper voice model download was incomplete." }
Write-Host "Installed $Voice. Trinity will now speak fully offline."
