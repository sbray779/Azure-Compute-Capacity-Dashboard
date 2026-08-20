import json
from types import SimpleNamespace
from unittest.mock import patch

import capacity_inventory
import function_app
from capacity_inventory import (
    ArmClient,
    build_detail_rows,
    discover_partitions_from_environment,
    ingestion_records,
    publish_records,
    query_resource_graph,
    quota_value,
    record_id,
)


def test_query_resource_graph_follows_skip_tokens():
    class FakeClient:
        def __init__(self):
            self.requests = []

        def resources(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return SimpleNamespace(data=[{"id": "one"}], skip_token="next")
            return SimpleNamespace(data=[{"id": "two"}], skip_token=None)

    client = FakeClient()

    rows = query_resource_graph(client, "resources | take 2", subscriptions=["sub"])

    assert rows == [{"id": "one"}, {"id": "two"}]
    assert client.requests[1].options.skip_token == "next"


def test_quota_value_supports_arm_name_shapes():
    items = [
        {
            "name": {"value": "standardDSv3Family"},
            "properties": {"limit": {"value": 40}},
        }
    ]

    assert quota_value(items, "StandardDSv3Family", "limit") == 40


def test_list_compute_regions_returns_physical_vm_regions():
    client = ArmClient.__new__(ArmClient)
    responses = [
        [
            {
                "resourceTypes": [
                    {
                        "resourceType": "virtualMachines",
                        "locations": ["East US", "West Europe"],
                    }
                ]
            }
        ],
        [
            {
                "value": [
                    {
                        "name": "eastus",
                        "displayName": "East US",
                        "metadata": {"regionType": "Physical"},
                    },
                    {
                        "name": "westeurope",
                        "displayName": "West Europe",
                        "metadata": {"regionType": "Physical"},
                    },
                    {
                        "name": "eastusstg",
                        "displayName": "East US (Stage)",
                        "metadata": {"regionType": "Physical"},
                    },
                    {
                        "name": "global",
                        "displayName": "Global",
                        "metadata": {"regionType": "Logical"},
                    },
                ]
            }
        ],
    ]

    with patch.object(client, "get_pages", side_effect=responses):
        assert client.list_compute_regions("sub-1") == ["eastus", "westeurope"]


def test_build_detail_rows_joins_deployments_skus_and_quota():
    subscriptions = [{"id": "sub-1", "name": "Production"}]
    deployments = [
        {
            "subscriptionId": "sub-1",
            "location": "eastus",
            "computeSku": "Standard_D2s_v3",
            "resourceKind": "VirtualMachine",
            "resourceCount": 2,
            "deployedInstances": 2,
        }
    ]
    sku = {
        "name": "Standard_D2s_v3",
        "family": "standardDSv3Family",
        "capabilities": [{"name": "vCPUs", "value": "2"}],
    }
    quotas = [
        {
            "name": "standardDSv3Family",
            "properties": {"limit": {"value": 20}},
        }
    ]
    usages = [
        {
            "name": "standardDSv3Family",
            "properties": {"usages": {"value": 6}},
        }
    ]

    rows = build_detail_rows(
        subscriptions,
        deployments,
        [
            {
                "subscriptionId": "sub-1",
                "location": "eastus",
                "computeSku": "Standard_D4s_v3",
                "reservedInstances": 2,
            }
        ],
        ["eastus", "westus3"],
        lambda _subscription, _region: [
            sku,
            {
                "name": "Standard_D4s_v3",
                "family": "standardDSv3Family",
                "capabilities": [{"name": "vCPUs", "value": "4"}],
            },
        ],
        lambda _subscription, _region: (quotas, usages),
    )

    eastus = next(row for row in rows if row["Region"] == "eastus")
    westus3 = next(row for row in rows if row["Region"] == "westus3")
    assert eastus["DeployedVCpus"] == 4
    assert eastus["QuotaAvailableVCpus"] == 14
    assert eastus["ResourceKind"] == "VirtualMachine"
    assert eastus["ReservedInstances"] == 2
    assert eastus["ReservedVCpus"] == 8
    assert westus3["DeployedInstances"] == 0
    assert westus3["DeployedVCpus"] == 0
    assert westus3["ReservedVCpus"] == 0


def test_publish_records_batches_at_500():
    class FakeIngestionClient:
        def __init__(self):
            self.calls = []

        def upload(self, **kwargs):
            self.calls.append(kwargs)

    client = FakeIngestionClient()
    records = [{"id": index} for index in range(1201)]

    publish_records(client, "rule", "stream", records)

    assert [len(call["logs"]) for call in client.calls] == [500, 500, 201]
    assert all(call["rule_id"] == "rule" for call in client.calls)
    assert all(call["stream_name"] == "stream" for call in client.calls)


def test_ingestion_records_have_deterministic_ids():
    detail = [
        {
            "SubscriptionId": "sub-1",
            "SubscriptionName": "One",
            "Region": "eastus",
            "ComputeSku": "Standard_D2s_v3",
            "VmFamily": "standardDSv3Family",
            "ResourceKind": "VirtualMachine",
            "ResourceCount": 1,
            "DeployedInstances": 1,
            "VCpusPerInstance": 2,
            "DeployedVCpus": 2,
            "ReservedInstances": 0,
            "ReservedVCpus": 0,
            "QuotaLimitVCpus": 20,
            "QuotaUsageVCpus": 2,
            "QuotaAvailableVCpus": 18,
        }
    ]

    first = ingestion_records(detail, "collection", "sub-1")
    second = ingestion_records(detail, "collection", "sub-1")

    assert first[0]["RecordId_s"] == second[0]["RecordId_s"]
    assert first[0]["RecordId_s"] == record_id(
        "collection",
        "sub-1",
        "eastus",
        "Standard_D2s_v3",
    )
    assert first[0]["PartitionId_s"] == "sub-1"


def test_discovery_stages_one_partition_per_deployed_subscription():
    environment = {
        "AZURE_CLIENT_ID": "client",
        "AZURE_MANAGEMENT_GROUP_ID": "management-group",
        "AZURE_STORAGE_BLOB_ENDPOINT": "https://storage.example.test",
        "AZURE_CAPACITY_WORK_CONTAINER": "work",
    }
    subscriptions = [
        {"id": "sub-1", "name": "One"},
        {"id": "sub-2", "name": "Two"},
    ]
    deployments = [
        {
            "subscriptionId": "sub-1",
            "location": "eastus",
            "computeSku": "Standard_D2s_v3",
        }
    ]

    class FakeContainer:
        def __init__(self):
            self.uploads = []

        def upload_blob(self, **kwargs):
            self.uploads.append(kwargs)

    container = FakeContainer()

    with (
        patch.dict("os.environ", environment, clear=True),
        patch.object(capacity_inventory, "DefaultAzureCredential"),
        patch.object(capacity_inventory, "ResourceGraphClient"),
        patch.object(
            capacity_inventory,
            "work_container_client",
            return_value=container,
        ),
        patch.object(
            capacity_inventory,
            "query_resource_graph",
            side_effect=[subscriptions, deployments, []],
        ) as query,
    ):
        discovery = discover_partitions_from_environment("collection")

    assert discovery["subscriptionCount"] == 2
    assert discovery["partitions"] == [
        {
            "collectionId": "collection",
            "partitionId": "sub-1",
            "blobName": "collection/sub-1.json",
        }
    ]
    assert len(container.uploads) == 1
    payload = json.loads(container.uploads[0]["data"])
    assert payload["subscription"]["name"] == "One"
    assert query.call_args_list[1].kwargs == {
        "management_groups": ["management-group"]
    }
    assert query.call_args_list[2].kwargs == {
        "management_groups": ["management-group"]
    }


def test_orchestrator_limits_each_fan_out_batch_to_ten():
    partitions = [
        {
            "collectionId": "collection",
            "partitionId": f"sub-{index}",
            "blobName": f"collection/sub-{index}.json",
        }
        for index in range(21)
    ]

    class FakeContext:
        def __init__(self):
            self.batch_sizes = []
            self.statuses = []

        def get_input(self):
            return {"startedAt": "2026-08-20T00:00:00+00:00"}

        def new_uuid(self):
            return "collection"

        def call_activity_with_retry(self, name, retry, value):
            return {"name": name, "retry": retry, "value": value}

        def task_all(self, tasks):
            self.batch_sizes.append(len(tasks))
            return tasks

        def set_custom_status(self, status):
            self.statuses.append(status)

    context = FakeContext()
    orchestration = function_app.orchestrate_capacity(context)
    discovery_task = next(orchestration)
    assert discovery_task["name"] == "discover_capacity_partitions"

    first_batch = orchestration.send(
        {
            "subscriptionCount": 25,
            "partitions": partitions,
        }
    )
    assert len(first_batch) == 10

    second_batch = orchestration.send(
        [{"recordCount": 1} for _ in range(10)]
    )
    assert len(second_batch) == 10

    final_batch = orchestration.send(
        [{"recordCount": 1} for _ in range(10)]
    )
    assert len(final_batch) == 1

    completion = orchestration.send([{"recordCount": 1}])
    assert completion["name"] == "complete_capacity_collection"
    assert completion["value"]["recordCount"] == 21
    assert context.batch_sizes == [10, 10, 1]
