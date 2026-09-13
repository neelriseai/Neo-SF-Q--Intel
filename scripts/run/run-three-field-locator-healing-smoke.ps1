#Requires -Version 5.1
<#
.SYNOPSIS
Run three real LLM locator-healing smokes for textbox, checkbox and dropdown candidates.

.DESCRIPTION
Uses digest-only synthetic DOM candidate evidence and clears inherited provider variables so the
repository .env remains the single provider credential source. The target fields deliberately use
different control types to prove the LLM layer is not tuned only to lookup healing:

- Opportunity.Name -> textbox
- Opportunity.Strategic_Deal__c -> checkbox
- Opportunity.StageName -> dropdown / combobox

No raw org values, credentials, selectors or Salesforce sessions are printed.
#>
[CmdletBinding()]
param(
    [string]$OutputDirectory = '.runtime/locator-healing-three-field'
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
foreach ($name in $providerVariables) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}

$outputRoot = Join-Path $repositoryRoot $OutputDirectory
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

function New-Candidate {
    param(
        [int]$Ordinal,
        [string]$Tag,
        [string]$Role,
        [string]$Structure,
        [string[]]$AttrNames,
        [hashtable]$AttrHashes,
        [string]$NameDigest,
        [string[]]$Nearby
    )
    return @{
        ordinal = $Ordinal
        tag = $Tag
        role = $Role
        structure = $Structure
        attrNames = $AttrNames
        attrHashes = $AttrHashes
        nameDigest = $NameDigest
        nearby = $Nearby
        visible = $true
        enabled = $true
    }
}

$cases = @(
    @{
        id = 'name-textbox'
        objectApiName = 'Opportunity'
        fieldApiName = 'Name'
        expectedOrdinal = 0
        candidates = @(
            (New-Candidate 0 'input' 'textbox' 'lightning-input-field>input[type=text]' @('name', 'type', 'maxlength') @{ name = 'aaaaaaaaaaaaaaaa'; type = 'bbbbbbbbbbbbbbbb'; maxlength = 'cccccccccccccccc' } 'dddddddddddddddd' @('label', 'opportunity', 'name')),
            (New-Candidate 1 'input' 'checkbox' 'lightning-input-field>input[type=checkbox]' @('type', 'checked') @{ type = 'eeeeeeeeeeeeeeee'; checked = 'ffffffffffffffff' } '1111111111111111' @('strategic', 'deal')),
            (New-Candidate 2 'button' 'combobox' 'lightning-combobox>button[role=combobox]' @('aria-expanded', 'role') @{ role = '2222222222222222'; 'aria-expanded' = '3333333333333333' } '4444444444444444' @('stage', 'picklist'))
        )
    },
    @{
        id = 'strategic-deal-checkbox'
        objectApiName = 'Opportunity'
        fieldApiName = 'Strategic_Deal__c'
        expectedOrdinal = 0
        candidates = @(
            (New-Candidate 0 'input' 'checkbox' 'lightning-input-field>input[type=checkbox]' @('type', 'checked', 'aria-checked') @{ type = '5555555555555555'; checked = '6666666666666666'; 'aria-checked' = '7777777777777777' } '8888888888888888' @('strategic', 'deal', 'checkbox')),
            (New-Candidate 1 'input' 'textbox' 'lightning-input-field>input[type=text]' @('name', 'type') @{ name = '9999999999999999'; type = 'abababababababab' } 'bcbcbcbcbcbcbcbc' @('name', 'opportunity')),
            (New-Candidate 2 'button' 'combobox' 'lightning-combobox>button[role=combobox]' @('role', 'aria-expanded') @{ role = 'cdcdcdcdcdcdcdcd'; 'aria-expanded' = 'dededededededede' } 'efefefefefefefef' @('stage', 'picklist'))
        )
    },
    @{
        id = 'stage-dropdown'
        objectApiName = 'Opportunity'
        fieldApiName = 'StageName'
        expectedOrdinal = 0
        candidates = @(
            (New-Candidate 0 'button' 'combobox' 'lightning-combobox>button[role=combobox]' @('role', 'aria-expanded', 'aria-haspopup') @{ role = '1212121212121212'; 'aria-expanded' = '3434343434343434'; 'aria-haspopup' = '5656565656565656' } '7878787878787878' @('stage', 'picklist', 'required')),
            (New-Candidate 1 'input' 'textbox' 'lightning-input-field>input[type=text]' @('name', 'type') @{ name = '9090909090909090'; type = 'a1a1a1a1a1a1a1a1' } 'b2b2b2b2b2b2b2b2' @('name', 'opportunity')),
            (New-Candidate 2 'input' 'checkbox' 'lightning-input-field>input[type=checkbox]' @('type', 'checked') @{ type = 'c3c3c3c3c3c3c3c3'; checked = 'd4d4d4d4d4d4d4d4' } 'e5e5e5e5e5e5e5e5' @('strategic', 'deal'))
        )
    }
)

$python = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw 'Python virtual environment is missing at .venv\Scripts\python.exe'
}

$summary = @()
foreach ($case in $cases) {
    $payload = @{
        obligationId = "L08.$($case.id).healing"
        objectApiName = $case.objectApiName
        fieldApiName = $case.fieldApiName
        contextPlanning = $true
        domEvidence = @{
            candidates = $case.candidates
        }
    }
    $json = $payload | ConvertTo-Json -Depth 20 -Compress
    $result = $json | & $python -m neo_sf_q_intel.locator_healing_cli
    $path = Join-Path $outputRoot "$($case.id).json"
    $result | Set-Content -Path $path -Encoding UTF8
    $receipt = $result | ConvertFrom-Json
    $summary += [pscustomobject]@{
        id = $case.id
        accepted = $receipt.accepted
        candidateOrdinal = $receipt.proposal.candidateOrdinal
        expectedOrdinal = $case.expectedOrdinal
        matchedExpected = ($receipt.proposal.candidateOrdinal -eq $case.expectedOrdinal)
        confidenceMilli = $receipt.proposal.confidenceMilli
        intentFit = $receipt.proposal.contextAssessment.intentFit
        missingContext = $receipt.proposal.contextAssessment.missingContext
        contextPlanCount = ($receipt.contextPlans | Measure-Object).Count
        finalStatus = $receipt.receipt.status
        outputPath = $path.Replace("$repositoryRoot\", '')
    }
}

$summaryPath = Join-Path $outputRoot 'summary.json'
$summary | ConvertTo-Json -Depth 8 | Set-Content -Path $summaryPath -Encoding UTF8
$summary | ConvertTo-Json -Depth 8
