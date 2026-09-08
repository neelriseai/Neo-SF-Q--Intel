param(
    [switch]$Full
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Create .venv and install the project before running quality checks."
}

& $python (Join-Path $repoRoot "scripts\catalog\build_project_index.py") --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python (Join-Path $repoRoot "scripts\quality\check_genericity.py") --skip-knowledge
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python (Join-Path $repoRoot "scripts\quality\check_governance_policy.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Full) {
    & (Join-Path $repoRoot ".venv\Scripts\ruff.exe") check $repoRoot
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Push-Location $repoRoot
    try {
        npm run lint
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        npm run test:browser
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        npm run test:e2e
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        npm run build
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    finally {
        Pop-Location
    }
}
