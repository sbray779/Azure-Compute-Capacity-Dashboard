from datetime import datetime, timezone
import logging

import azure.functions as func
import azure.durable_functions as df
from azure.durable_functions.models.OrchestrationRuntimeStatus import (
    OrchestrationRuntimeStatus,
)

from capacity_inventory import (
    collect_partition_from_environment,
    complete_collection_from_environment,
    discover_partitions_from_environment,
)


app = df.DFApp()
PARTITION_CONCURRENCY = 10


@app.timer_trigger(
    schedule="%TIMER_SCHEDULE%",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
@app.durable_client_input(client_name="client")
async def collect_compute_capacity(
    timer: func.TimerRequest,
    client: df.DurableOrchestrationClient,
) -> None:
    if timer.past_due:
        logging.warning("The compute capacity inventory timer is running late.")

    scheduled_at = datetime.now(timezone.utc)
    instance_id = f"capacity-{scheduled_at:%Y%m%d}"
    existing = await client.get_status(instance_id)
    if existing:
        retryable_statuses = {
            OrchestrationRuntimeStatus.Failed,
            OrchestrationRuntimeStatus.Canceled,
            OrchestrationRuntimeStatus.Terminated,
        }
        if existing.runtime_status not in retryable_statuses:
            logging.info(
                "Capacity orchestration %s already has status %s.",
                instance_id,
                existing.runtime_status.value,
            )
            return
        instance_id = f"{instance_id}-retry-{scheduled_at:%H%M%S%f}"

    await client.start_new(
        "capacity_orchestrator",
        instance_id=instance_id,
        client_input={"startedAt": scheduled_at.isoformat()},
    )
    logging.info("Started capacity orchestration %s.", instance_id)


def orchestrate_capacity(
    context: df.DurableOrchestrationContext,
):
    started_at = context.get_input()["startedAt"]
    collection_id = str(context.new_uuid())
    retry = df.RetryOptions(
        first_retry_interval_in_milliseconds=5000,
        max_number_of_attempts=3,
    )
    discovery = yield context.call_activity_with_retry(
        "discover_capacity_partitions",
        retry,
        collection_id,
    )
    partitions = discovery["partitions"]
    results = []

    for offset in range(0, len(partitions), PARTITION_CONCURRENCY):
        tasks = [
            context.call_activity_with_retry(
                "collect_capacity_partition",
                retry,
                partition,
            )
            for partition in partitions[
                offset : offset + PARTITION_CONCURRENCY
            ]
        ]
        results.extend((yield context.task_all(tasks)))
        context.set_custom_status(
            {
                "collectionId": collection_id,
                "completedPartitions": len(results),
                "partitionCount": len(partitions),
            }
        )

    summary = {
        "collectionId": collection_id,
        "startedAt": started_at,
        "subscriptionCount": discovery["subscriptionCount"],
        "partitionCount": len(partitions),
        "recordCount": sum(result["recordCount"] for result in results),
    }
    yield context.call_activity_with_retry(
        "complete_capacity_collection",
        retry,
        summary,
    )
    return summary


@app.orchestration_trigger(context_name="context")
def capacity_orchestrator(
    context: df.DurableOrchestrationContext,
):
    return (yield from orchestrate_capacity(context))


@app.activity_trigger(input_name="collection_id")
def discover_capacity_partitions(
    collection_id: str,
) -> dict:
    return discover_partitions_from_environment(collection_id)


@app.activity_trigger(input_name="work_item")
def collect_capacity_partition(
    work_item: dict,
) -> dict:
    return collect_partition_from_environment(work_item)


@app.activity_trigger(input_name="summary")
def complete_capacity_collection(
    summary: dict,
) -> dict:
    return complete_collection_from_environment(summary)
