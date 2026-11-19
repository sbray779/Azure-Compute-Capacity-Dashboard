# Azure Compute Capacity Dashboard

Inventory deployed Azure VM families across subscriptions under a management group,
calculate deployed and reserved vCPUs, compare regional VM-family quota, and
display the latest completed collection in an Azure Workbook.

This branch, `no-vnet-integration`, uses the Function platform's default
outbound networking. It does not deploy a VNet, subnet, NAT Gateway, or
dedicated public IP, and it does not provide a fixed outbound source IP.
Use `main` for the integrated VNet/NAT deployment.

Use this variant for a new deployment. Switching an existing VNet-integrated
deployment requires a reviewed migration: incremental ARM/Bicep deployments
do not automatically delete the old network resources, and Terraform can
plan their destruction. This branch is not an automatic networking rollback.

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

The collector also records the generation encoded in each deployed VM family
and the newest generation of the same family series returned by the Compute
Resource SKUs API for that subscription and region. Families without a
versioned `vN` name remain uncolored.

## Dashboard views

The Workbook queries the latest completed collection and provides:

1. Active Azure Advisor recommendations limited to:
   - Reliability **Service Upgrade and Retirement** findings for VM or VMSS
     SKU, size, series, or family retirement/deprecation with a future
     retirement date.
   - Cost findings that explicitly recommend upgrading or migrating to a newer
     `vN` VM SKU generation. General resize, right-size, and shutdown
     opportunities are excluded. Advisor-provided annual savings and currency
     are shown when available.
2. Deployed VM SKU families by subscription and region. Families three
   generations behind the newest regional generation (`N-3`) are yellow;
   families four or more generations behind (`N-4+`) are red. The table also
   shows VM-family quota usage, Resource SKU API restrictions, and availability
   zones for each subscription and region.
3. A subscription/VM-family matrix with deployed vCPUs, quota, and
   available quota for each region.
4. A subscription-by-region heat map showing each region's percentage of VM
   instances in the selected scope across regions containing deployed or
   reserved capacity.
5. Regional detail with subscription, region, VM family, deployed vCPUs, quota
   limit, quota usage, available quota, reserved vCPUs, and a red
   family-quota-utilization heat map.
6. Multi-select subscription, region, and VM-family filters with Select All.
7. A quota exhaustion watchlist highlighting VM families at or above 80% of
   their regional quota, prioritized by utilization, so quota-increase
   requests can be filed before they block a deployment.
8. A capacity reservation efficiency table comparing reserved vCPUs to
   deployed vCPUs per family and region, flagging underutilized reservations
   (cost waste) and reservation gaps (SLA risk).
9. A current capacity snapshot table (deployed vCPUs, quota limit, and quota
   utilization % as of the most recent collection only), plus a deployed
   vCPU/quota-limit trend chart and a separate quota-utilization % trend
   chart across the retained 30-day collection history, for forecasting when
   a subscription/family/region combination will exhaust its quota at the
   current growth rate. Utilization is charted separately because its
   0-100% scale would otherwise be dwarfed by vCPU counts on a shared axis.
   The trend charts' built-in metrics/legend summary is turned off
   (`showMetrics: false`) because Azure Workbooks always sums that summary
   across the full visible time range rather than showing the latest value,
   which is misleading for point-in-time gauge metrics like deployed vCPUs;
   the snapshot table above provides the always-correct current totals
   instead, and hovering over the trend charts still shows accurate
   per-day values.
10. A fleet lifecycle health rollup showing each subscription's share of
    deployed vCPUs by generation age (current, N-1 through N-4+, and
    not-versioned), as a single at-a-glance health score instead of the
    row-level family detail.
11. An upcoming retirement timeline, separate from the Advisor
    recommendations table, that aggregates SKU retirement findings by
    retiring feature and date, showing impacted resource counts and days
    remaining so migrations can be prioritized by urgency.
12. A restricted-SKUs-in-use table flagging currently deployed SKUs that also
    carry a Resource SKU API scale-out restriction in their region, so
    capacity planning accounts for families that can't grow where they're
    already running.

Azure enforces Compute quota at VM-family level, so the quota views aggregate
all deployed SKUs that share a family.

## Deployment options

The primary, recommended path uses the Azure Developer CLI (`azd`) with the
Bicep templates in `infra/` and is documented below. Two alternatives are
also provided for teams that can't or don't want to use `azd`, without
removing or changing the `azd`/Bicep path:

| Deployment type | Guide |
|---|---|
| `azd` + Bicep (recommended, default) | This README, below |
| Plain ARM template (`az deployment`, no `azd`) | [`deploy/arm/README.md`](deploy/arm/README.md) |
| Terraform: Function App (no `azd`) | [`deploy/terraform/README.md`](deploy/terraform/README.md) |

`deploy/arm/main.json` is compiled directly from the Bicep sources, and
`deploy/terraform/` is a hand-written Function App alternative kept in sync
with the shared Bicep resources.

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

The Function App is not VNet-integrated. Outbound connections use the
platform-managed public networking; no customer-managed NAT Gateway is needed.

The Log Analytics workspace and Data Collection Endpoint allow public network
access. Workbook viewers need normal Log Analytics RBAC.
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

## Update an existing deployment

Select the existing azd environment and run `azd up` from the project root:

```powershell
azd env select <environment-name>
azd up
```

This updates the Log Analytics table and DCR schemas, the Workbook definition,
and the collector Function code. The Advisor recommendations table is available
as soon as the Workbook update is deployed.

The VM SKU generation, restriction, and availability-zone columns require a
new completed inventory collection created by the updated Function. Wait for
the next daily run at `00:00 UTC` or invoke the `collect_compute_capacity`
timer function from its Azure portal **Code + Test** page. Existing historical
records remain queryable but don't contain the newly added fields.

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
