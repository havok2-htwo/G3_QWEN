param(
    [switch]$SkipTorch,
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $RepoRoot '.venv'
$PythonExe = Join-Path $VenvDir 'Scripts\python.exe'
$BackendDir = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'
$FrontendIndex = Join-Path $FrontendDir 'dist\index.html'
$ModelsDir = Join-Path $RepoRoot 'models'

function Write-Step {
    param([string]$Message)
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-External {
    param(
        [string]$Label,
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory = $RepoRoot
    )

    Write-Step $Label
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Label failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

function Test-CommandAvailable {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Ensure-Venv {
    $global:VenvDir = "X:\KI\anaconda3\envs\qwen-tts-gui"
    $global:PythonExe = "X:\KI\anaconda3\envs\qwen-tts-gui\python.exe"
    Write-Host "Using Anaconda Conda Environment for Windows DLL stability."
}


}

Ensure-Venv

if (-not (Test-Path $ModelsDir)) {
    New-Item -ItemType Directory -Path $ModelsDir | Out-Null
}

Invoke-External -Label 'Upgrading pip tooling' -FilePath $PythonExe -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel')

if (-not $SkipTorch) {
    Invoke-External -Label 'Installing CUDA PyTorch' -FilePath $PythonExe -Arguments @(
        '-m', 'pip', 'install', '--upgrade', '--index-url', 'https://download.pytorch.org/whl/cu130',
        'torch==2.10.0+cu130', 'torchvision==0.25.0+cu130', 'torchaudio==2.10.0+cu130'
    )
}
else {
    Write-Host 'Skipping CUDA PyTorch installation.'
}

Invoke-External -Label 'Installing backend package' -FilePath $PythonExe -Arguments @('-m', 'pip', 'install', '-e', $BackendDir)

Invoke-External -Label 'Pinning robust ONNX Runtime version' -FilePath $PythonExe -Arguments @(
    '-m', 'pip', 'install', 'onnxruntime==1.19.2'
)

Invoke-External -Label 'Installing Optional Triton' -FilePath $PythonExe -Arguments @(
    '-m', 'pip', 'install', 'triton-windows<3.7'
)

if (-not (Test-CommandAvailable 'npm')) {
    throw 'npm was not found. Install Node.js LTS and re-run install.bat.'
}

Invoke-External -Label 'Installing frontend dependencies' -FilePath 'npm' -Arguments @('install') -WorkingDirectory $FrontendDir

if (-not $SkipFrontendBuild) {
    Invoke-External -Label 'Building frontend' -FilePath 'npm' -Arguments @('run', 'build') -WorkingDirectory $FrontendDir
}
else {
    Write-Host 'Skipping frontend build.'
}

if (-not (Test-Path $FrontendIndex)) {
    throw 'Frontend build output is missing. Expected frontend\dist\index.html.'
}

Write-Host ''
Write-Host 'Installation finished.' -ForegroundColor Green
Write-Host 'Start the app with: .\start_server.bat'
Write-Host 'Open: http://127.0.0.1:8088'
Write-Host ''
Write-Host 'Note: qwen-tts may require sox in PATH on the target machine.'
