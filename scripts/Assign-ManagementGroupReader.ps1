$ErrorActionPreference = 'Stop'

$managementGroupId = $env:AZURE_MANAGEMENT_GROUP_ID
$principalId = $env:AZURE_COLLECTOR_PRINCIPAL_ID
$readerRoleId = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'

if ([string]::IsNullOrWhiteSpace($managementGroupId)) {
    throw 'AZURE_MANAGEMENT_GROUP_ID is required.'
}

if ([string]::IsNullOrWhiteSpace($principalId)) {
    throw 'AZURE_COLLECTOR_PRINCIPAL_ID was not produced by provisioning.'
}

$scope = "/providers/Microsoft.Management/managementGroups/$managementGroupId"
$existingAssignment = az role assignment list `
    --scope $scope `
    --query "[?principalId=='$principalId' && roleDefinitionId=='/providers/Microsoft.Authorization/roleDefinitions/$readerRoleId'] | [0].id" `
    --output tsv

if ($LASTEXITCODE -ne 0) {
    throw 'Failed to query the management-group Reader role assignment.'
}

if ([string]::IsNullOrWhiteSpace($existingAssignment)) {
    az role assignment create `
        --scope $scope `
        --assignee-object-id $principalId `
        --assignee-principal-type ServicePrincipal `
        --role $readerRoleId `
        --output none

    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to assign Reader at the management-group scope.'
    }
}
