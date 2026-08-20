# Automation guidance

The production collector is a Python Azure Function on Flex Consumption. Its
timer trigger runs daily at `00:00 UTC`, uses a user-assigned managed identity,
and writes normalized records to `ComputeCapacity_CL` through a Data Collection
Rule.

## Execution flow

1. The timer trigger starts the inventory.
2. `DefaultAzureCredential` selects the Function's user-assigned identity.
3. Azure Resource Graph discovers subscriptions and deployed VM/VMSS SKUs.
4. ARM returns Compute SKU metadata and Microsoft.Quota limit/usage records.
5. Python joins the datasets and calculates deployed and available vCPUs.
6. The Logs Ingestion SDK uploads batches of at most 500 records.
7. The Workbook selects the newest collection ID.

## Permissions

The infrastructure assigns the collector identity:

- Reader at the management group.
- Monitoring Metrics Publisher on the DCR and Application Insights.
- Storage Blob Data Owner and Storage Queue Data Contributor on Function
  storage.

Quota submission, if enabled separately, should use Quota Request Operator and
a different identity from this read-only reporting Function.

## On-demand execution

Invoke the timer function from the Azure portal's **Code + Test** page, or use
the Functions admin endpoint with an authenticated deployment/operator
workflow. Keep `run_on_startup` disabled in production.

Do not publish inventory records to a public artifact store. Subscription IDs,
resource distribution, and available capacity are operationally sensitive.
