# Automation guidance

The production collector is a Python Durable Functions app on Flex Consumption.
Its timer trigger runs daily at `00:00 UTC`, uses a user-assigned managed
identity, and coordinates subscription partitions through Azure Durable Task
Scheduler.

## Execution flow

1. The timer trigger starts a durable orchestration.
2. A discovery activity uses Azure Resource Graph at management-group scope and
   stages one private Blob payload per subscription with deployed compute.
3. The orchestrator schedules subscription activities in batches of 10.
4. Each activity uses `DefaultAzureCredential` with the Function's
   user-assigned identity.
5. ARM returns Compute SKU metadata and Microsoft.Quota limit/usage records for
   deployed or reserved regions.
6. Python joins the datasets and uploads batches of at most 500 records.
7. Failed subscription activities are retried up to three times.
8. After every partition succeeds, a completion activity writes a manifest to
   `ComputeCapacityRuns_CL`.
9. The Workbook selects the newest completed collection and deduplicates rows
   by deterministic record ID.

## Permissions

The infrastructure assigns the collector identity:

- Reader at the management group.
- Monitoring Metrics Publisher on the DCR and Application Insights.
- Storage Blob Data Owner, Storage Queue Data Contributor, and Storage Table
  Data Contributor on Function storage.
- Durable Task Data Contributor on the Durable Task Scheduler.

Quota submission, if enabled separately, should use Quota Request Operator and
a different identity from this read-only reporting Function.

## On-demand execution

Invoke the timer starter from the Azure portal's **Code + Test** page, or use
the Functions admin endpoint with an authenticated deployment/operator
workflow. Keep `run_on_startup` disabled in production. A deterministic daily
orchestration instance ID prevents the timer from creating duplicate scheduled
runs for the same UTC date.

Do not publish inventory records to a public artifact store. Subscription IDs,
resource distribution, and available capacity are operationally sensitive.
