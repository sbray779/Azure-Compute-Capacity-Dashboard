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


def test_azd_infrastructure_uses_platform_outbound_networking():
    resources = (ROOT / "infra" / "resources.bicep").read_text()
    function = (ROOT / "infra" / "app" / "function.bicep").read_text()
    outputs = (ROOT / "infra" / "main.bicep").read_text()

    assert "Microsoft.Network/" not in resources
    assert "virtualNetworkSubnetId" not in resources + function
    assert "vnetRouteAllEnabled" not in function
    assert "AZURE_VNET_ID" not in outputs
    assert "AZURE_FUNCTION_NAT_PUBLIC_IP" not in outputs
    assert "etworkSecurityPerimeter" not in resources
    assert "SecuredByPerimeter" not in resources


def test_terraform_uses_platform_outbound_networking():
    terraform = (ROOT / "deploy" / "terraform" / "main.tf").read_text()
    outputs = (ROOT / "deploy" / "terraform" / "outputs.tf").read_text()

    assert 'resource "azurerm_public_ip"' not in terraform
    assert 'resource "azurerm_nat_gateway' not in terraform
    assert 'resource "azurerm_virtual_network"' not in terraform
    assert 'resource "azurerm_subnet' not in terraform
    assert "virtual_network_subnet_id" not in terraform
    assert "vnet_route_all_enabled" not in terraform
    assert 'output "virtual_network_id"' not in outputs
    assert 'output "function_nat_public_ip_address"' not in outputs


def test_arm_uses_platform_outbound_networking():
    template = json.loads((ROOT / "deploy" / "arm" / "main.json").read_text())
    resources = template["resources"][1]["properties"]["template"]["resources"]
    assert not any(
        resource["type"].startswith("Microsoft.Network/")
        for resource in resources
    )
    collector = next(
        resource for resource in resources
        if resource["name"] == "collectorFunction"
    )
    assert "virtualNetworkSubnetId" not in collector["properties"]["parameters"]
    function = collector["properties"]["template"]["resources"][0]
    assert "virtualNetworkSubnetId" not in function["properties"]["parameters"]
    assert "vnetRouteAllEnabled" not in function["properties"]["parameters"]
    defaults = function["properties"]["template"]["parameters"]
    assert defaults["virtualNetworkSubnetId"]["nullable"] is True
    assert defaults["virtualNetworkSubnetId"].get("defaultValue") is None
    assert defaults["vnetRouteAllEnabled"]["defaultValue"] is False
    assert "AZURE_VNET_ID" not in template["outputs"]
    assert "AZURE_FUNCTION_NAT_PUBLIC_IP" not in template["outputs"]


def test_function_disables_basic_auth_for_ftp_and_scm():
    function = (ROOT / "infra" / "app" / "function.bicep").read_text()
    terraform = (ROOT / "deploy" / "terraform" / "main.tf").read_text()
    arm = (ROOT / "deploy" / "arm" / "main.json").read_text()

    assert "basicPublishingCredentialsPolicies: [" in function
    assert function.count("allow: false") >= 2
    assert "name: 'ftp'" in function
    assert "name: 'scm'" in function

    assert (
        'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-04-01'
        in terraform
    )
    assert 'toset(["ftp", "scm"])' in terraform
    assert "allow = false" in terraform

    assert '"name": "ftp",\n                                "allow": false' in arm
    assert '"name": "scm",\n                                "allow": false' in arm
