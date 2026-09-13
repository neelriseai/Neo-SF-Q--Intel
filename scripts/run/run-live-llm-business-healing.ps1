#Requires -Version 5.1
<#
.SYNOPSIS
Run a live Salesforce Workbench business action with LLM ordinal healing on selected controls.

.DESCRIPTION
This is the live-browser counterpart to the synthetic locator smoke. It uses the trusted live
browser profile, forces the browser worker to skip deterministic field locators for selected
fields, asks the LLM bridge to choose candidate ordinals from the current live DOM, then saves the
Workbench form and verifies persisted Salesforce state. The default focuses on StageName because
that dropdown is the current live proof for knowledge-repo intent plus historical signature usage.

Secrets, session URLs, raw DOM and record ids are never printed. Full output is the sanitized
projection emitted by the live business-action CLI.
#>
[CmdletBinding()]
param(
    [string]$ProfilePath = '.runtime/live-browser-profile.json',
    [string]$OutputDirectory = '.runtime/live-llm-business-healing',
    [string]$ForceModelFields = 'StageName',
    [switch]$Headed,
    [int]$SlowMoMs = 0
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $repositoryRoot

if (-not (Test-Path $ProfilePath)) {
    throw "Live browser profile not found: $ProfilePath"
}

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
foreach ($name in $providerVariables) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}

$outputRoot = Join-Path $repositoryRoot $OutputDirectory
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$runId = -join ((48..57) + (97..102) | Get-Random -Count 8 | ForEach-Object {[char]$_})
$opportunityName = "SYN-LLM-LIVE-$stamp-$runId"

$env:NEO_BROWSER_LIVE_PROFILE_PATH = (Resolve-Path $ProfilePath).Path
$env:NEO_BROWSER_PROFILE_SHA256 = (Get-FileHash $ProfilePath -Algorithm SHA256).Hash.ToLowerInvariant()
$env:NEO_BROWSER_HEADED = if ($Headed) { 'true' } else { 'false' }
$env:NEO_BROWSER_SLOW_MO_MS = [string][Math]::Max(0, [Math]::Min($SlowMoMs, 2000))
$env:NEO_LOCATOR_HEALING_PROVIDER_SOURCE = 'dotenv'
$env:NEO_LOCATOR_HEALING_CWD = $repositoryRoot.Path
$env:NEO_BROWSER_SIGNATURE_PROJECT_ID = 'neo-sf-q-intel'
$env:NEO_BROWSER_SIGNATURE_PAGE_KEY = 'strategic-deal-workbench'
$env:NEO_BROWSER_INTENT_FALLBACK_PAGE = 'strategic-deal-workbench'
$env:NEO_BROWSER_INTENT_FALLBACK_SECTION = 'Field behavior in plain English'
$python = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw 'Python virtual environment is missing at .venv\Scripts\python.exe'
}
$env:NEO_LOCATOR_HEALING_PYTHON = $python
$env:NEO_BROWSER_BUSINESS_START_PATH = '/lightning/n/Strategic_Deal_Workbench'
$env:NEO_BROWSER_BUSINESS_FORCE_MODEL_FIELDS = $ForceModelFields
$env:NEO_BROWSER_BUSINESS_SUBMIT_TAG = 'lightning-button'
$env:NEO_BROWSER_BUSINESS_SUBMIT_ACTION = 'save-evaluate-live'
$env:NEO_BROWSER_BUSINESS_SUCCESS_TEXT = 'Saved successfully. The policy results are shown below.'

$fields = @(
    @{ fieldApiName = 'Name'; value = $opportunityName },
    @{ fieldApiName = 'Strategic_Deal__c'; value = 'true' },
    @{ fieldApiName = 'StageName'; value = 'Prospecting' },
    @{ fieldApiName = 'AccountId'; value = 'SYN-SDA Demo Account'; kind = 'LOOKUP' },
    @{ fieldApiName = 'CloseDate'; value = 'Dec 31, 2026' },
    @{ fieldApiName = 'Amount'; value = '60000000' },
    @{ fieldApiName = 'Discount__c'; value = '15.01' },
    @{ fieldApiName = 'Regional_VP_Approver__c'; value = 'Synthetic Regional VP'; kind = 'LOOKUP' }
)
$env:NEO_BROWSER_BUSINESS_FIELDS_JSON = ($fields | ConvertTo-Json -Depth 8 -Compress)

$persistence = @{
    objectApiName = 'Opportunity'
    matchField = 'Name'
    matchValue = $opportunityName
    assertions = @(
        @{ fieldApiName = 'Name'; value = $opportunityName },
        @{ fieldApiName = 'StageName'; value = 'Prospecting' },
        @{ fieldApiName = 'Strategic_Deal__c'; value = 'true' },
        @{ fieldApiName = 'Discount__c'; value = '15.01' }
    )
}
$env:NEO_BROWSER_BUSINESS_PERSISTENCE_JSON = ($persistence | ConvertTo-Json -Depth 8 -Compress)

npm --prefix packages/browser run build:live | Out-Host
$result = npm --prefix packages/browser run live:business-action --silent
$receiptPath = Join-Path $outputRoot 'live-business-action-llm.json'
$result | Set-Content -Path $receiptPath -Encoding UTF8

$projection = $result | ConvertFrom-Json
$summary = [pscustomobject]@{
    status = $projection.status
    browserStatus = $projection.browserStatus
    headedMode = $projection.headedMode
    forcedModelFieldCount = $projection.forcedModelFieldCount
    modelProposalCount = $projection.businessAction.modelProposalCount
    modelAppliedFieldCount = $projection.businessAction.modelAppliedFieldCount
    modelCandidateOrdinals = $projection.businessAction.modelCandidateOrdinals
    modelRejectionCodes = $projection.businessAction.modelRejectionCodes
    modelDomCandidateCounts = $projection.businessAction.modelDomCandidateCounts
    modelAttemptFields = $projection.businessAction.modelAttemptFields
    modelContextPlanCounts = $projection.businessAction.modelContextPlanCounts
    modelIntentCitedFields = $projection.businessAction.modelIntentCitedFields
    signatureLookupFoundFields = $projection.businessAction.signatureLookupFoundFields
    signatureSavedFields = $projection.businessAction.signatureSavedFields
    signatureSaveErrorCodes = $projection.businessAction.signatureSaveErrorCodes
    healedFieldCount = $projection.businessAction.healedFieldCount
    abstainedFieldCount = $projection.businessAction.abstainedFieldCount
    submitted = $projection.businessAction.submitted
    successTextMatched = $projection.businessAction.successTextMatched
    persistenceMatched = $projection.persistence.matched
    errorCode = $projection.errorCode
    receiptPath = $receiptPath.Replace("$repositoryRoot\", '')
}
$summaryPath = Join-Path $outputRoot 'summary.json'
$summary | ConvertTo-Json -Depth 8 | Set-Content -Path $summaryPath -Encoding UTF8
$summary | ConvertTo-Json -Depth 8
