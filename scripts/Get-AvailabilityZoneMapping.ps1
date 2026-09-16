[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string] $SourceSubscriptionId,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string[]] $PeerSubscriptionId,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string] $Location,

    [ValidateRange(30, 3600)]
    [int] $RegistrationTimeoutSeconds = 600
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-AzCommandSucceeded {
    param(
        [Parameter(Mandatory)]
        [string] $Operation
    )

    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI failed while $Operation."
    }
}

function Wait-ForRegistration {
    param(
        [Parameter(Mandatory)]
        [scriptblock] $GetState,

        [Parameter(Mandatory)]
        [string] $Description
    )

    $deadline = (Get-Date).AddSeconds($RegistrationTimeoutSeconds)

    do {
        $state = (& $GetState).Trim()
        Assert-AzCommandSucceeded "checking $Description registration"

        if ($state -eq 'Registered') {
            return
        }

        Start-Sleep -Seconds 10
    } while ((Get-Date) -lt $deadline)

    throw "Timed out after $RegistrationTimeoutSeconds seconds waiting for $Description registration."
}

function Register-ZoneMappingPrerequisites {
    param(
        [Parameter(Mandatory)]
        [string] $SubscriptionId
    )

    az account show `
        --subscription $SubscriptionId `
        --query id `
        --output tsv `
        --only-show-errors | Out-Null
    Assert-AzCommandSucceeded "verifying access to subscription $SubscriptionId"

    $featureState = az feature show `
        --subscription $SubscriptionId `
        --namespace Microsoft.Resources `
        --name AvailabilityZonePeering `
        --query properties.state `
        --output tsv `
        --only-show-errors
    Assert-AzCommandSucceeded "checking AvailabilityZonePeering in subscription $SubscriptionId"

    if ($featureState.Trim() -ne 'Registered') {
        Write-Verbose "Registering AvailabilityZonePeering in subscription $SubscriptionId."
        az feature register `
            --subscription $SubscriptionId `
            --namespace Microsoft.Resources `
            --name AvailabilityZonePeering `
            --output none `
            --only-show-errors
        Assert-AzCommandSucceeded "registering AvailabilityZonePeering in subscription $SubscriptionId"

        Wait-ForRegistration `
            -Description "AvailabilityZonePeering in subscription $SubscriptionId" `
            -GetState {
                az feature show `
                    --subscription $SubscriptionId `
                    --namespace Microsoft.Resources `
                    --name AvailabilityZonePeering `
                    --query properties.state `
                    --output tsv `
                    --only-show-errors
            }
    }

    Write-Verbose "Registering Microsoft.Resources in subscription $SubscriptionId."
    az provider register `
        --subscription $SubscriptionId `
        --namespace Microsoft.Resources `
        --output none `
        --only-show-errors
    Assert-AzCommandSucceeded "registering Microsoft.Resources in subscription $SubscriptionId"

    Wait-ForRegistration `
        -Description "Microsoft.Resources in subscription $SubscriptionId" `
        -GetState {
            az provider show `
                --subscription $SubscriptionId `
                --namespace Microsoft.Resources `
                --query registrationState `
                --output tsv `
                --only-show-errors
        }
}

function Get-SubscriptionZoneMappings {
    param(
        [Parameter(Mandatory)]
        [string] $SubscriptionId
    )

    $response = az rest `
        --method get `
        --url "https://management.azure.com/subscriptions/$SubscriptionId/locations?api-version=2022-12-01" `
        --output json `
        --only-show-errors
    Assert-AzCommandSucceeded "retrieving locations for subscription $SubscriptionId"

    $region = ($response | ConvertFrom-Json).value |
        Where-Object { $_.name -eq $Location } |
        Select-Object -First 1

    if ($null -eq $region) {
        throw "Location '$Location' is not available to subscription $SubscriptionId."
    }

    if ($null -eq $region.availabilityZoneMappings -or $region.availabilityZoneMappings.Count -eq 0) {
        throw "Location '$Location' does not expose availability-zone mappings for subscription $SubscriptionId."
    }

    return $region.availabilityZoneMappings
}

if ($null -eq (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI is required and was not found on PATH.'
}

$subscriptionIds = @($SourceSubscriptionId) + $PeerSubscriptionId |
    Select-Object -Unique

foreach ($subscriptionId in $subscriptionIds) {
    Register-ZoneMappingPrerequisites -SubscriptionId $subscriptionId
}

$sourceMappings = Get-SubscriptionZoneMappings -SubscriptionId $SourceSubscriptionId

$results = foreach ($peerId in $PeerSubscriptionId) {
    $peerMappings = Get-SubscriptionZoneMappings -SubscriptionId $peerId
    $peerZonesByPhysicalZone = @{}

    foreach ($mapping in $peerMappings) {
        $peerZonesByPhysicalZone[$mapping.physicalZone] = $mapping.logicalZone
    }

    foreach ($sourceMapping in $sourceMappings) {
        [pscustomobject] @{
            Location             = $Location
            PhysicalZone         = $sourceMapping.physicalZone
            SourceSubscriptionId = $SourceSubscriptionId
            SourceLogicalZone    = $sourceMapping.logicalZone
            PeerSubscriptionId   = $peerId
            PeerLogicalZone      = $peerZonesByPhysicalZone[$sourceMapping.physicalZone]
        }
    }
}

$results |
    Sort-Object PeerSubscriptionId, @{ Expression = { [int] $_.SourceLogicalZone } }
