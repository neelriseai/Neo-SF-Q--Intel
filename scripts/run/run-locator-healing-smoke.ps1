#Requires -Version 5.1
<#
.SYNOPSIS
Run the locator-healing LLM smoke with .env as the single provider source.

.DESCRIPTION
Settings intentionally blocks provider calls when inherited process provider variables disagree
with the repository .env. This smoke clears inherited provider variables in this child PowerShell
process, then invokes the Python locator-healing bridge with a digest-only ambiguity fixture.

No credential value, raw org value, selector or Salesforce session material is printed.
#>
[CmdletBinding()]
param(
    [string]$OutputPath = '.runtime/locator-healing-incremental/planned-incremental-clean-env-v1.json'
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $repositoryRoot

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

$outputFullPath = Join-Path $repositoryRoot $OutputPath
New-Item -ItemType Directory -Force -Path (Split-Path $outputFullPath -Parent) | Out-Null

$payload = @{
    obligationId = 'L08.regional-vp-approver.lookup'
    objectApiName = 'Opportunity'
    fieldApiName = 'Regional_VP_Approver__c'
    contextPlanning = $true
    domEvidence = @{
        candidates = @(
            @{
                ordinal = 0
                tag = 'input'
                role = 'combobox'
                structure = 'lightning-input-field>input[role=combobox]'
                attrNames = @('data-value', 'role', 'title')
                attrHashes = @{
                    title = 'aaaaaaaaaaaaaaaa'
                    'data-value' = 'bbbbbbbbbbbbbbbb'
                }
                nameDigest = 'cccccccccccccccc'
                nearby = @('label', 'lightning-icon', 'lookup')
                visible = $true
                enabled = $true
            },
            @{
                ordinal = 1
                tag = 'input'
                role = 'textbox'
                structure = 'lightning-input>input[type=text]'
                attrNames = @('name', 'type')
                attrHashes = @{
                    name = 'dddddddddddddddd'
                    type = 'eeeeeeeeeeeeeeee'
                }
                nameDigest = 'ffffffffffffffff'
                nearby = @('amount', 'discount')
                visible = $true
                enabled = $true
            }
        )
    }
}

$python = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw 'Python virtual environment is missing at .venv\Scripts\python.exe'
}

$json = $payload | ConvertTo-Json -Depth 12 -Compress
$json | & $python -m neo_sf_q_intel.locator_healing_cli | Tee-Object -FilePath $outputFullPath

Write-Host ''
if ($removed.Count -gt 0) {
    Write-Host ("Cleared {0} inherited provider variable(s) before smoke: {1}" -f $removed.Count, ($removed -join ', '))
} else {
    Write-Host 'No inherited provider variables were present before smoke.'
}
Write-Host ("Smoke receipt written to {0}" -f $OutputPath)
