# Azure Compute Capacity Dashboard

Inventory deployed Azure VM families across subscriptions under a management group,
calculate deployed and reserved vCPUs, compare regional VM-family quota, and
display the latest completed collection in an Azure Workbook.

## Architecture

| Resource | Purpose |
|---|---|
| Python Durable Functions app | Partitions and runs the inventory daily at `00:00 UTC` |
| Flex Consumption plan | Serverless Function hosting |
| Durable Task Scheduler | Checkpointed fan-out/fan-in orchestration |
| User-assigned managed identity | Secretless Azure authentication |
| Function storage account | Runtime state, deployment packages, and partition staging |
| Application Insights | Function telemetry |
| Log Analytics custom tables | Capacity records and completed-run manifests |
| Data Collection Endpoint and Rule | Managed Logs Ingestion API |
| Azure Workbook | Subscription/VM-family matrix and regional detail |

The Function uses:

1. Azure Resource Graph to discover descendant subscriptions, deployed VMs,
   VM scale sets, and capacity reservations at management-group scope.
2. Durable Functions to process subscriptions in checkpointed batches of 10.
3. The Microsoft.Compute Resource SKUs API for vCPUs per SKU and quota-family
   mapping.
4. Microsoft.Quota for VM-family limits and current usage in regions containing
   deployments or reservations.
5. Azure Monitor Logs Ingestion to publish capacity records and mark a
   collection complete only after every subscription succeeds.

These sources are required because Resource Graph does not expose SKU capability
metadata or quota limits.

## Dashboard views

The Workbook queries the latest completed collection and provides:

1. A subscription/VM-family matrix with deployed vCPUs, quota, and
   available quota for each region.
2. A subscription-by-region heat map showing each region's percentage of VM
   instances in the selected scope across regions containing deployed or
   reserved capacity.
3. Regional detail with subscription, region, VM family, deployed vCPUs, quota
   limit, quota usage, available quota, reserved vCPUs, and a red
   family-quota-utilization heat map.
4. Multi-select subscription, region, and VM-family filters with Select All.

Azure enforces Compute quota at VM-family level, so the quota views aggregate
all deployed SKUs that share a family.

## Prerequisites

- Python 3.13 for local validation.
- [Azure Developer CLI (`azd`)](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd).
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) with
  the Bicep CLI available through `az bicep`.
- An Azure subscription where you can create resources, deployments, and role
  assignments.
- Permission to assign Reader at the management group whose descendant
  subscriptions will be inventoried.

`Owner` at both the hosting subscription and management-group scopes satisfies
the deployment requirements. Global Administrator is not sufficient by itself
because Microsoft Entra directory roles do not grant Azure Resource Manager
permissions. A pre-provision hook registers the `Microsoft.DurableTask`
resource provider if needed.

## Deploy with azd

Create an azd environment and provide the target subscription, deployment
region, and management-group ID:

```powershell
azd auth login
azd env new capacity-prod
azd env set AZURE_SUBSCRIPTION_ID <subscription-id>
azd env set AZURE_LOCATION <azure-region>
azd env set AZURE_MANAGEMENT_GROUP_ID <management-group-id>
azd up
```

The subscription-scoped deployment creates
`rg-capacity-<environment-name>`. A post-provision hook then creates the
management-group role assignment. The collector identity receives:

- Reader at management-group scope.
- Monitoring Metrics Publisher on the DCR and Application Insights.
- Storage Blob Data Owner, Storage Queue Data Contributor, and Storage Table
  Data Contributor on Function storage.
- Durable Task Data Contributor on the Durable Task Scheduler.

The deploying user also receives Durable Task Data Contributor on the scheduler
so its orchestration dashboard can be opened for troubleshooting.

Durable Task Scheduler is not available in East US. When
`AZURE_LOCATION=eastus`, the template keeps the existing application resources
in East US and places the scheduler in East US 2.

The Function storage account carries the tenant-policy-supported
`SecurityControl=Ignore` resource tag so its authenticated public endpoint
remains available to azd and the Flex Consumption runtime. Shared-key access
and anonymous blob access remain disabled.

The deployment outputs include the Function name and
`AZURE_WORKBOOK_PORTAL_URL`. Retrieve the Workbook URL after deployment with:

```powershell
azd env get-value AZURE_WORKBOOK_PORTAL_URL
```

## Local validation

Use Python 3.13:

```powershell
python -m pip install -r .\src\function\requirements-dev.txt
python -m pytest -q
python -m compileall -q .\src\function .\tests
az bicep build --file .\infra\main.bicep --stdout
```

## Schedule

The `TIMER_SCHEDULE` Function setting is provisioned as:

```text
0 0 0 * * *
```

Azure Functions NCRONTAB uses six fields, so this runs once daily at midnight
UTC. `run_on_startup` is disabled to avoid inventory runs during deployments or
host restarts.

## Scale and reliability

The timer starts a Durable Functions orchestration instead of performing the
inventory itself. Resource Graph discovery runs once at management-group scope,
then one activity processes each subscription. The orchestrator schedules at
most 10 subscription activities at a time, and a failed activity is attempted
up to three times without repeating successful subscriptions.

Partition payloads are staged in the private Function storage account instead
of being stored in orchestration history. Each output row has a deterministic
record ID, allowing the Workbook to deduplicate an activity whose ingestion
succeeded before the activity was retried.

`ComputeCapacityRuns_CL` receives a `Completed` record only after all
subscription activities succeed. Workbook queries select capacity data through
that table, so records from a failed or still-running collection are not shown.

## Disable or re-enable collection

Disable only the scheduled collector while leaving the Function App and
dashboard infrastructure deployed:

```powershell
$functionName = azd env get-value AZURE_FUNCTION_NAME
$resourceGroup = azd env get-value AZURE_RESOURCE_GROUP

az functionapp config appsettings set `
  --resource-group $resourceGroup `
  --name $functionName `
  --settings AzureWebJobs.collect_compute_capacity.Disabled=true
```

Re-enable collection by setting the same value to `false`:

```powershell
az functionapp config appsettings set `
  --resource-group $resourceGroup `
  --name $functionName `
  --settings AzureWebJobs.collect_compute_capacity.Disabled=false
```

The collector can also be disabled from **Function App > Functions >
collect_compute_capacity > Disable** in the Azure portal. To stop or restart
the entire Function App instead, run:

```powershell
az functionapp stop --resource-group $resourceGroup --name $functionName
az functionapp start --resource-group $resourceGroup --name $functionName
```

## Scope and interpretation

- A compute SKU is an Azure VM size deployed as a standalone VM or in a uniform
  or flexible VM scale set.
- Deployed vCPUs are `deployed instance count * SKU vCPUs`.
- VM scale set capacity uses `sku.capacity`.
- Deallocated VMs remain deployed resources.
- The collector reads quota only for regions containing a deployment or
  capacity reservation. Regions with no deployed or reserved capacity are
  intentionally omitted to avoid a subscription-by-region cross-product.
- If the selected scope contains no deployed VMs or VM scale sets, the run
  publishes an empty completed snapshot.
- Every orchestration appends a snapshot with a unique `CollectionId_s`.
  Workbook queries select the newest completed collection and deduplicate
  deterministic `RecordId_s` values. Older snapshots remain available for
  30 days.
- Resource Graph, SKU metadata, quota, and ingestion failures fail the affected
  activity. Durable Functions retries that subscription; if retries are
  exhausted, the orchestration fails and no completion record is written.
- Logs are uploaded in batches of 500. If a later batch fails, records from
  earlier batches may already exist in the workspace, but the incomplete
  collection remains hidden because it has no completed-run record.

The standalone ARG query remains available at
[`queries/deployed-compute-skus.kql`](queries/deployed-compute-skus.kql).
Capacity-request options are documented in
[`docs/capacity-request-integration-recommendations.md`](docs/capacity-request-integration-recommendations.md).
