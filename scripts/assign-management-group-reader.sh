#!/usr/bin/env sh
set -eu

: "${AZURE_MANAGEMENT_GROUP_ID:?AZURE_MANAGEMENT_GROUP_ID is required.}"
: "${AZURE_COLLECTOR_PRINCIPAL_ID:?AZURE_COLLECTOR_PRINCIPAL_ID was not produced by provisioning.}"

reader_role_id='acdd72a7-3385-48ef-bd42-f606fba81ae7'
scope="/providers/Microsoft.Management/managementGroups/${AZURE_MANAGEMENT_GROUP_ID}"

existing_assignment="$(
  az role assignment list \
    --scope "$scope" \
    --query "[?principalId=='${AZURE_COLLECTOR_PRINCIPAL_ID}' && roleDefinitionId=='/providers/Microsoft.Authorization/roleDefinitions/${reader_role_id}'] | [0].id" \
    --output tsv
)"

if [ -z "$existing_assignment" ]; then
  az role assignment create \
    --scope "$scope" \
    --assignee-object-id "$AZURE_COLLECTOR_PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --role "$reader_role_id" \
    --output none
fi
