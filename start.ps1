# Switch to the script directory so uvicorn can import main.py
Set-Location $PSScriptRoot

# Use the project-local Python 3.10 venv (rapidocr-onnxruntime requires Python < 3.13)
$venvPython = Join-Path $PSScriptRoot '.venv310\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host '.venv310 not found. Create it and install dependencies first:' -ForegroundColor Yellow
    Write-Host '  py -3.10 -m venv .venv310'
    Write-Host '  .\.venv310\Scripts\python.exe -m pip install -r requirements.txt'
    exit 1
}

& $venvPython -m uvicorn main:app --host 127.0.0.1 --port 8000
