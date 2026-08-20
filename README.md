# Azure Compute Capacity Dashboard

Inventory deployed Azure VM families across subscriptions under a management group,
calculate deployed and reserved vCPUs, compare regional VM-family quota, and
display the latest collection in an Azure Workbook.

## Architecture

| Resource | Purpose |
|---|---|
| Python Azure Function | Runs the inventory daily at `00:00 UTC` |
| Flex Consumption plan | Serverless Function hosting |
| User-assigned managed identity | Secretless Azure authentication |
| Function storage account | Runtime state and deployment packages |
| Application Insights | Function telemetry |
| Log Analytics workspace and `ComputeCapacity_CL` | Capacity records |
| Data Collection Endpoint and Rule | Managed Logs Ingestion API |
| Azure Workbook | Subscription/VM-family matrix and regional detail |

The Function uses:

1. Azure Resource Graph to discover descendant subscriptions and deployed VMs
   and VM scale sets.
2. The Microsoft.Compute Resource SKUs API for vCPUs per SKU and quota-family
   mapping.
3. Microsoft.Quota for regional VM-family limits and current usage.
4. Capacity Reservation resources for reserved VM-family capacity by region.
5. Azure Monitor Logs Ingestion to publish the joined records.

These sources are required because Resource Graph does not expose SKU capability
metadata or quota limits.

## Dashboard views

The Workbook queries the latest collection and provides:

1. A subscription/VM-family matrix with deployed vCPUs, quota, and
   available quota for each region.
2. A subscription-by-region heat map showing each region's percentage of VM
   instances in the selected scope across all physical Azure regions supporting
   virtual machines.
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
permissions.

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
- Storage Blob Data Owner and Storage Queue Data Contributor on Function
  storage.

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

## Scope and interpretation

- A compute SKU is an Azure VM size deployed as a standalone VM or in a uniform
  or flexible VM scale set.
- Deployed vCPUs are `deployed instance count * SKU vCPUs`.
- VM scale set capacity uses `sku.capacity`.
- Deallocated VMs remain deployed resources.
- The collector discovers all physical Azure regions that support virtual
  machines; regions with no current deployment remain visible with zero
  deployed vCPUs.
- If the selected scope contains no deployed VMs or VM scale sets, the run
  publishes no new snapshot and the Workbook continues to show the previous
  collection.
- Every run appends a new snapshot with a unique `CollectionId_s`. Workbook
  queries select the collection associated with the newest record, so repeated
  daily snapshots do not duplicate displayed results. Older snapshots remain
  available for 30 days.
- Resource Graph, region-discovery, and ingestion failures fail the Function
  invocation. A Compute SKU metadata or quota request failure for an individual
  subscription and region is logged as a warning; the run continues and emits
  records with unavailable values left empty.
- Logs are uploaded in batches of 500. If a later batch fails, records from
  earlier batches may already exist in the workspace and share the same
  collection ID.

The standalone ARG query remains available at
[`queries/deployed-compute-skus.kql`](queries/deployed-compute-skus.kql).
Capacity-request options are documented in
[`docs/capacity-request-integration-recommendations.md`](docs/capacity-request-integration-recommendations.md).
