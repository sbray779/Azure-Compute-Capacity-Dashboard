# Deploy with Terraform (no azd)

This folder is the non-`azd` Terraform deployment. It provisions the Durable
Functions/Flex Consumption collector and its Log Analytics, Data Collection
Rule, managed identity, staging storage, and Workbook resources.

Most resources use the stable `azurerm` provider. Resource types that
`azurerm` does not yet expose (Log Analytics custom table schemas, the Data
Collection Endpoint/Rule stream declarations, and the Durable Task
Scheduler/task hub) are created with the `azapi` provider, using the same
resource types and API versions as the Bicep template so behavior matches.

`azd` is not used anywhere in this path.

## Advantage over the ARM/Bicep path

Because Terraform can manage resources across scopes in a single apply, this
configuration also creates the management-group **Reader** role assignment
for the collector identity directly — no separate post-provision script
required (unlike the `azd`/ARM paths, where a subscription-scoped deployment
can't reach outside its scope).

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.7.
- Azure CLI, logged in (`az login`) with the target subscription selected
  (`az account set --subscription <subscription-id>`). The `azurerm` and
  `azapi` providers both authenticate using your `az` CLI session by
  default.
- Permission to create resources/role assignments in the target
  subscription, and permission to assign Reader at the target management
  group.
- Azure Functions Core Tools v4 when publishing with `func` instead of zip
  deployment.

## 1. Register the required resource provider

Terraform does not register resource providers for you. Do this once per
subscription before the first apply:

```powershell
az provider register --namespace Microsoft.DurableTask --wait
```

## 2. Configure variables

Copy the example variables file and fill in your values:

```powershell
Copy-Item terraform.tfvars.example terraform.tfvars
```

| Variable | Description |
|---|---|
| `environment_name` | Short name used to build resource names (`rg-capacity-<environment_name>`, etc.). 1-32 characters. |
| `location` | Azure region for all resources except the Durable Task Scheduler. |
| `durable_task_scheduler_location` | Optional scheduler region override. |
| `management_group_id` | Management group whose descendant subscriptions are inventoried. |
| `principal_id` | Optional user object ID for scheduler troubleshooting and Function package upload. |

`terraform.tfvars` is git-ignored; don't commit it if it contains
environment-specific values you don't want tracked.

## 3. Initialize and apply

```powershell
terraform init
terraform plan -out capacity.tfplan
terraform apply capacity.tfplan
```

Capture the outputs you'll need next:

```powershell
$resourceGroup = terraform output -raw resource_group_name
$workbookUrl = terraform output -raw workbook_portal_url
```

## 4. Publish the Function code

Function provisioning creates an empty Function App; publish `src/function`
with Azure Functions Core Tools:

```powershell
cd ..\..\src\function
func azure functionapp publish $functionName --python
```

or a remote-build zip deploy with the Azure CLI:

```powershell
cd ..\..\src\function
Compress-Archive -Path * -DestinationPath ..\..\deploy\terraform\function.zip -Force
az functionapp deploy `
  --resource-group $resourceGroup `
  --name $functionName `
  --src-path ..\..\deploy\terraform\function.zip `
  --type zip
```

## 5. Open the Workbook

```powershell
terraform output -raw workbook_portal_url
```

## Keeping this configuration in sync with Bicep changes

The Terraform configuration is hand-written to mirror `infra/resources.bicep`
and `infra/app/*.bicep`. If those files change (new app settings, role
assignment, or schema column), make the equivalent Terraform change as well.

The configuration includes `moved` blocks for Function resources that were
previously indexed by the removed hosting-mode conditional. Existing Function
deployments therefore keep their Terraform state addresses without resource
recreation.

## Tear down

```powershell
terraform destroy
```

`terraform destroy` also removes the management-group Reader role
assignment it created, since that assignment is tracked in state.
