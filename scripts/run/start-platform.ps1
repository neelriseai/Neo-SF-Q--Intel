#Requires -Version 5.1
<#
.SYNOPSIS
Start the local Neo API and dashboard with one coherent provider credential source.

.DESCRIPTION
Provider credentials must come from the untracked .env only. When the parent shell also exports
provider values, Settings reports PROVIDER_CREDENTIAL_SOURCE_CONFLICT and blocks provider calls.
This launcher removes provider variables from the child environment instead of weakening that
guard, then starts the console entrypoints used by the runbook.

Paths stay repository-relative. No credential value is read, printed or written.
#>
[CmdletBinding()]
param(
    [int]$ApiPort = 8000,
    [int]$WebPort = 3100,
    [switch]$ApiOnly,
    [switch]$WebOnly
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $repositoryRoot

# Provider variables compared by Settings._has_provider_source_conflict.
$providerVariables = @(
    'AI_PROVIDER',
    'OPENAI_API_KEY',
    'OPENAI_CHAT_MODEL',
    'OPENAI_EMBEDDING_MODEL',
    'AZURE_OPENAI_API_KEY',
    'AZURE_OPENAI_ENDPOINT',
    'AZURE_OPENAI_API_VERSION',
    'AZURE_OPENAI_CHAT_DEPLOYMENT',
    'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
)

$removed = @()
foreach ($name in $providerVariables) {
    if (Test-Path "Env:$name") {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
        $removed += $name
    }
}
if ($removed.Count -gt 0) {
    Write-Host ("Cleared {0} inherited provider variable(s) so .env is the single source: {1}" -f $removed.Count, ($removed -join ', '))
} else {
    Write-Host 'No inherited provider variables were present.'
}

function Test-PortListening {
    param([int]$Port)
    $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

if (-not $WebOnly) {
    if (Test-PortListening -Port $ApiPort) {
        Write-Host ("API port {0} is already in use; stop that process before relaunching." -f $ApiPort)
    } else {
        $api = Join-Path $repositoryRoot '.venv\Scripts\neo-api.exe'
        if (-not (Test-Path $api)) { throw 'neo-api console entrypoint is missing; install with pip install -e ".[dev]"' }
        Write-Host ("Starting API on port {0} via the neo-api console entrypoint." -f $ApiPort)
        Start-Process -FilePath $api -WorkingDirectory $repositoryRoot -WindowStyle Minimized
    }
}

if (-not $ApiOnly) {
    if (Test-PortListening -Port $WebPort) {
        Write-Host ("Dashboard port {0} is already serving." -f $WebPort)
    } else {
        Write-Host ("Starting dashboard on port {0}." -f $WebPort)
        Start-Process -FilePath 'npm.cmd' -ArgumentList @('run', 'dev', '--', '--port', "$WebPort") -WorkingDirectory $repositoryRoot -WindowStyle Minimized
    }
}

Write-Host ''
Write-Host 'Verify with:'
Write-Host ("  curl http://localhost:{0}/health" -f $ApiPort)
Write-Host ("  curl -o NUL -w ""%{{http_code}}"" http://localhost:{0}" -f $WebPort)
