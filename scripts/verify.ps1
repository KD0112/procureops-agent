$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$Python = ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing .venv. Run scripts/bootstrap.ps1 first."
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

Invoke-Checked -Label "Knowledge manifest validation" -Command {
    & $Python scripts/rebuild_knowledge_manifest.py
}
Invoke-Checked -Label "RAG index rebuild" -Command {
    & $Python scripts/rebuild_rag_index.py
}
Invoke-Checked -Label "Ruff" -Command {
    & $Python -m ruff check .
}
Invoke-Checked -Label "Pytest" -Command {
    & $Python -m pytest --cov=procureops --cov-report=term-missing:skip-covered --cov-fail-under=90
}
