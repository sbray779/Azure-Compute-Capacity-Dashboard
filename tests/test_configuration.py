import json
from pathlib import Path

import function_app


ROOT = Path(__file__).parents[1]


def test_expected_durable_functions_are_registered():
    names = {
        function.get_function_name()
        for function in function_app.app.get_functions()
    }

    assert names == {
        "collect_compute_capacity",
        "capacity_orchestrator",
        "discover_capacity_partitions",
        "collect_capacity_partition",
        "complete_capacity_collection",
    }


def test_workbook_uses_only_completed_deduplicated_collections():
    workbook = json.loads((ROOT / "workbook" / "workbook.json").read_text())
    queries = []
    for item in workbook["items"]:
        if item["type"] == 3:
            queries.append(item["content"]["query"])
        elif item["type"] == 9:
            queries.extend(
                parameter["query"]
                for parameter in item["content"]["parameters"]
                if "query" in parameter
                and "ComputeCapacity_CL" in parameter["query"]
            )

    capacity_queries = [
        query for query in queries if "ComputeCapacity_CL" in query
    ]
    assert len(capacity_queries) == 6
    assert all("ComputeCapacityRuns_CL" in query for query in capacity_queries)
    assert all("Status_s == 'Completed'" in query for query in capacity_queries)
    assert all(
        "DedupKey=iff(isempty(RecordId_s)" in query
        and "arg_max(TimeGenerated, *) by DedupKey" in query
        for query in capacity_queries
    )


def test_workbook_includes_requested_advisor_and_sku_generation_tables():
    workbook = json.loads((ROOT / "workbook" / "workbook.json").read_text())
    items = {item.get("name"): item for item in workbook["items"]}

    advisor_query = items["AdvisorServiceUpgradeAndRetirement"]["content"][
        "query"
    ]
    assert "HighAvailability" in advisor_query
    assert "ServiceUpgradeAndRetirement" in advisor_query
    assert "recommendationStatus" in advisor_query
    assert "microsoft.compute/virtualmachines" in advisor_query
    assert "microsoft.compute/virtualmachinescalesets" in advisor_query
    assert "IsSkuLifecycle" in advisor_query
    assert "RetirementDate > now()" in advisor_query
    assert "IsSkuUpgradeCost" in advisor_query
    assert "Category =~ 'Cost'" in advisor_query
    assert "TargetGeneration > CurrentGeneration" in advisor_query
    assert (
        "not(UpgradeText matches regex "
        "@'(?i)(right.?siz|resiz|shut.?down|underutiliz)')"
        in advisor_query
    )
    assert "annualSavingsAmount" in advisor_query
    assert (
        "TargetGeneration=toint("
        in advisor_query.split("| extend IsSkuLifecycle=", 1)[0]
    )

    sku_item = items["DeployedVmSkuFamilies"]["content"]
    assert "GenerationLag_d" in sku_item["query"]
    assert "QuotaUsageVCpus_d" in sku_item["query"]
    assert "SkuRestrictions_s" in sku_item["query"]
    assert "AvailabilityZones_s" in sku_item["query"]
    assert "['Quota usage']" in sku_item["query"]
    assert "['SKU restrictions']" in sku_item["query"]
    assert "['Availability zones']" in sku_item["query"]
    formatter = sku_item["gridSettings"]["formatters"][0]
    thresholds = formatter["formatOptions"]["thresholdsGrid"]
    assert formatter["columnMatch"] == "SKU family"
    assert {
        (threshold["thresholdValue"], threshold["representation"])
        for threshold in thresholds
    } == {
        ("N-3 |", "yellow"),
        ("N-4+ |", "red"),
    }


def test_host_uses_durable_task_scheduler():
    host = json.loads(
        (ROOT / "src" / "function" / "host.json").read_text()
    )
    provider = host["extensions"]["durableTask"]["storageProvider"]

    assert provider == {
        "type": "azureManaged",
        "connectionStringName": "DURABLE_TASK_SCHEDULER_CONNECTION_STRING",
    }
