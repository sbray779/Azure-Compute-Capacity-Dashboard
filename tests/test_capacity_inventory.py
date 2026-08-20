from types import SimpleNamespace
from unittest.mock import patch

import capacity_inventory
from capacity_inventory import (
    ArmClient,
    build_detail_rows,
    publish_records,
    query_resource_graph,
    quota_value,
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


def test_run_returns_zero_when_scope_has_no_deployed_compute():
    environment = {
        "AZURE_CLIENT_ID": "client",
        "AZURE_MANAGEMENT_GROUP_ID": "management-group",
        "AZURE_DATA_COLLECTION_ENDPOINT": "https://example.test",
        "AZURE_DATA_COLLECTION_RULE_ID": "rule",
        "AZURE_DATA_COLLECTION_STREAM": "stream",
        "AZURE_CAPACITY_REGIONS": "eastus",
    }
    subscriptions = [
        {"id": "sub-1", "name": "One"},
        {"id": "sub-2", "name": "Two"},
    ]

    with (
        patch.dict("os.environ", environment, clear=True),
        patch.object(capacity_inventory, "DefaultAzureCredential"),
        patch.object(capacity_inventory, "ResourceGraphClient"),
        patch.object(capacity_inventory, "ArmClient"),
        patch.object(
            capacity_inventory,
            "query_resource_graph",
            side_effect=[subscriptions, [], []],
        ),
    ):
        assert capacity_inventory.run_from_environment() == 0
