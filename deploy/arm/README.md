# Deploy with a plain ARM template (no azd)

This folder provides an Azure Resource Manager (ARM) JSON equivalent of the
`infra/main.bicep` deployment for teams that must deploy without the Azure
Developer CLI (`azd`). `main.json` is compiled directly from the Bicep
sources with `az bicep build`, so it provisions the identical set of
resources described in the root [README.md](../../README.md#architecture):
Log Analytics workspace and custom tables, Data Collection Endpoint/Rule,
Application Insights, user-assigned managed identity, Durable Task
Scheduler, Flex Consumption storage account and plan, the Python Function
App, and the Azure Workbook.

`azd` is not used anywhere in this path. You drive the deployment with the
Azure CLI (`az`) only.

## What azd normally automates for you

Because this path skips `azd`, three things it would otherwise do for you
must be done manually:

1. Registering the `Microsoft.DurableTask` resource provider
   (`azd`'s preprovision hook).
2. Assigning **Reader** to the collector's managed identity at the
   management-group scope (`azd`'s postprovision hook). This is required
   because the template deploys at subscription scope and cannot reach
   outside it to modify management-group role assignments.
3. Publishing the Function App code (`azd deploy`). Provisioning only
   creates the empty Function App; the Python code in `src/function` must be
   deployed separately.

## Prerequisites

- Azure CLI, logged in (`az login`) with a subscription selected
  (`az account set --subscription <subscription-id>`).
- Permission to create resources/role assignments in the target
  subscription, and permission to assign Reader at the target management
  group.
- [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local)
  v4, if you plan to publish code with `func` instead of a zip deploy.
- Python 3.13, only needed if you want to run the local validation steps
  from the root README before publishing.

## 1. Register the required resource provider

```powershell
az provider register --namespace Microsoft.DurableTask --wait
```

## 2. Edit parameters

Copy `main.parameters.json` and fill in your values, or pass `--parameters`
overrides on the command line:

| Parameter | Description |
|---|---|
| `environmentName` | Short name used to build resource names (`rg-capacity-<environmentName>`, etc.). 1-32 characters. |
| `location` | Azure region for all resources except the Durable Task Scheduler. |
| `managementGroupId` | Management group whose descendant subscriptions are inventoried. |
| `principalId` | Object ID of the user/principal that should also get Durable Task Data Contributor on the scheduler (for troubleshooting access). Optional; omit or leave empty to skip. |

## 3. Deploy the template

The template is scoped to the subscription (it creates its own resource
group), so deploy with `az deployment sub create`:

```powershell
az deployment sub create `
  --name capacity-dashboard `
  --location eastus2 `
  --template-file main.json `
  --parameters main.parameters.json
```

Capture the outputs you'll need next:

```powershell
$outputs = az deployment sub show --name capacity-dashboard --query properties.outputs | ConvertFrom-Json
$resourceGroup = $outputs.AZURE_RESOURCE_GROUP.value
$functionName = $outputs.AZURE_FUNCTION_NAME.value
$collectorPrincipalId = $outputs.AZURE_COLLECTOR_PRINCIPAL_ID.value
$workbookUrl = $outputs.AZURE_WORKBOOK_PORTAL_URL.value
```

## 4. Assign Reader at the management group

This step is normally handled by `azd`'s postprovision hook
(`scripts/Assign-ManagementGroupReader.ps1`). Do it manually here:

```powershell
$managementGroupId = '<management-group-id>'
$scope = "/providers/Microsoft.Management/managementGroups/$managementGroupId"

az role assignment create `
  --scope $scope `
  --assignee-object-id $collectorPrincipalId `
  --assignee-principal-type ServicePrincipal `
  --role acdd72a7-3385-48ef-bd42-f606fba81ae7
```

## 5. Publish the Function App code

Provisioning creates an empty Function App. Publish the collector code from
`src/function` with either Azure Functions Core Tools:

```powershell
cd ..\..\src\function
func azure functionapp publish $functionName --python
```

or a remote-build zip deploy with the Azure CLI:

```powershell
cd ..\..\src\function
Compress-Archive -Path * -DestinationPath ..\..\deploy\arm\function.zip -Force
az functionapp deploy `
  --resource-group $resourceGroup `
  --name $functionName `
  --src-path ..\..\deploy\arm\function.zip `
  --type zip
```

## 6. Open the Workbook

```powershell
Write-Output $workbookUrl
```

## Updating the template after Bicep changes

`main.json` is a generated artifact. If `infra/main.bicep`,
`infra/resources.bicep`, `infra/app/*.bicep`, or `workbook/workbook.json`
change, regenerate it from the repository root:

```powershell
az bicep build --file infra\main.bicep --outfile deploy\arm\main.json
```

Commit the regenerated `main.json` alongside the Bicep change so the ARM
path stays in sync.

## Tear down

```powershell
az group delete --name $resourceGroup --yes --no-wait
az role assignment delete `
  --scope "/providers/Microsoft.Management/managementGroups/<management-group-id>" `
  --assignee-object-id $collectorPrincipalId `
  --role acdd72a7-3385-48ef-bd42-f606fba81ae7
```
