from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

import requests
from azure.identity import DefaultAzureCredential
from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions
from azure.monitor.ingestion import LogsIngestionClient
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ARM_ENDPOINT = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
COMPUTE_SKU_API_VERSION = "2021-07-01"
PROVIDER_API_VERSION = "2021-04-01"
SUBSCRIPTION_LOCATIONS_API_VERSION = "2022-12-01"
QUOTA_API_VERSION = "2025-09-01"
INGESTION_BATCH_SIZE = 500

SUBSCRIPTION_QUERY = """
resourcecontainers
| where type =~ 'microsoft.resources/subscriptions'
| project id = subscriptionId, name
| order by name asc
""".strip()

DEPLOYED_COMPUTE_QUERY = """
resources
| where type =~ 'microsoft.compute/virtualmachines'
| where isempty(properties.virtualMachineScaleSet.id)
| extend computeSku = tostring(properties.hardwareProfile.vmSize)
| where isnotempty(computeSku)
| project subscriptionId, resourceGroup, location = tolower(location),
          computeSku, deployedInstances = 1, resourceKind = 'VirtualMachine'
| union (
    resources
    | where type =~ 'microsoft.compute/virtualmachinescalesets'
    | extend computeSku = tostring(sku.name),
             deployedInstances = tolong(sku.capacity)
    | where isnotempty(computeSku)
    | project subscriptionId, resourceGroup, location = tolower(location),
              computeSku, deployedInstances, resourceKind = 'VirtualMachineScaleSet'
)
| summarize deployedInstances = sum(deployedInstances),
            resourceCount = count()
    by subscriptionId, location, computeSku, resourceKind
| order by subscriptionId asc, computeSku asc, location asc
""".strip()

CAPACITY_RESERVATION_QUERY = """
resources
| where type =~ 'microsoft.compute/capacityreservationgroups/capacityreservations'
| extend computeSku = tostring(sku.name),
         reservedInstances = tolong(sku.capacity)
| where isnotempty(computeSku) and reservedInstances > 0
| project subscriptionId, resourceGroup, location = tolower(location),
          computeSku, reservedInstances
| summarize reservedInstances = sum(reservedInstances)
    by subscriptionId, location, computeSku
| order by subscriptionId asc, computeSku asc, location asc
""".strip()


class ArmClient:
    def __init__(
        self,
        credential: Any,
        endpoint: str = ARM_ENDPOINT,
        timeout_seconds: int = 60,
    ) -> None:
        self._credential = credential
        self._endpoint = endpoint.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()
        retry = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        self._session.mount("https://", HTTPAdapter(max_retries=retry))

    def get_pages(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> Iterable[dict[str, Any]]:
        url = path if path.startswith("https://") else f"{self._endpoint}{path}"
        request_params = params

        while url:
            token = self._credential.get_token(ARM_SCOPE).token
            response = self._session.get(
                url,
                params=request_params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout_seconds,
            )
            if not response.ok:
                preview = response.text[:1000]
                raise RuntimeError(
                    f"ARM GET failed ({response.status_code}) for {response.url}: "
                    f"{preview}"
                )

            payload = response.json()
            yield payload
            url = payload.get("nextLink")
            request_params = None

    def list_compute_skus(
        self,
        subscription_id: str,
        region: str,
    ) -> list[dict[str, Any]]:
        path = (
            f"/subscriptions/{subscription_id}"
            "/providers/Microsoft.Compute/skus"
        )
        params = {
            "api-version": COMPUTE_SKU_API_VERSION,
            "$filter": f"location eq '{region}'",
        }
        return [
            item
            for page in self.get_pages(path, params)
            for item in page.get("value", [])
        ]

    def list_compute_regions(self, subscription_id: str) -> list[str]:
        provider_pages = list(
            self.get_pages(
                f"/subscriptions/{subscription_id}/providers/Microsoft.Compute",
                {"api-version": PROVIDER_API_VERSION},
            )
        )
        resource_types = provider_pages[0].get("resourceTypes", [])
        virtual_machines = next(
            (
                item
                for item in resource_types
                if str(item.get("resourceType", "")).casefold()
                == "virtualmachines"
            ),
            None,
        )
        if not virtual_machines:
            return []

        compute_location_names = {
            str(location).casefold()
            for location in virtual_machines.get("locations", [])
        }
        location_pages = self.get_pages(
            f"/subscriptions/{subscription_id}/locations",
            {"api-version": SUBSCRIPTION_LOCATIONS_API_VERSION},
        )
        return sorted(
            {
                str(item["name"]).lower()
                for page in location_pages
                for item in page.get("value", [])
                if str(item.get("displayName", "")).casefold()
                in compute_location_names
                and (item.get("metadata") or {}).get("regionType") == "Physical"
            }
        )

    def list_quota(
        self,
        subscription_id: str,
        region: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        scope = (
            f"/subscriptions/{subscription_id}"
            f"/providers/Microsoft.Compute/locations/{region}"
        )
        quotas = [
            item
            for page in self.get_pages(
                f"{scope}/providers/Microsoft.Quota/quotas",
                {"api-version": QUOTA_API_VERSION},
            )
            for item in page.get("value", [])
        ]
        usages = [
            item
            for page in self.get_pages(
                f"{scope}/providers/Microsoft.Quota/usages",
                {"api-version": QUOTA_API_VERSION},
            )
            for item in page.get("value", [])
        ]
        return quotas, usages


def query_resource_graph(
    client: ResourceGraphClient,
    query: str,
    *,
    subscriptions: list[str] | None = None,
    management_groups: list[str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    skip_token: str | None = None

    while True:
        options = QueryRequestOptions(
            top=1000,
            skip_token=skip_token,
            result_format="objectArray",
        )
        request = QueryRequest(
            query=query,
            subscriptions=subscriptions,
            management_groups=management_groups,
            options=options,
        )
        response = client.resources(request)
        rows.extend(response.data or [])
        skip_token = getattr(response, "skip_token", None)
        if not skip_token:
            return rows


def capability_value(sku: dict[str, Any], name: str) -> str | None:
    for capability in sku.get("capabilities") or []:
        if capability.get("name") == name:
            return capability.get("value")
    return None


def quota_resource_name(item: dict[str, Any]) -> str | None:
    name = item.get("name")
    if isinstance(name, str):
        return name
    if isinstance(name, dict):
        return name.get("value")

    property_name = (item.get("properties") or {}).get("name")
    if isinstance(property_name, dict):
        return property_name.get("value")
    return None


def quota_value(
    items: Iterable[dict[str, Any]],
    family: str,
    kind: str,
) -> int | None:
    for item in items:
        if (quota_resource_name(item) or "").casefold() != family.casefold():
            continue

        properties = item.get("properties") or {}
        value_container = (
            properties.get("limit") if kind == "limit" else properties.get("usages")
        )
        value = (value_container or {}).get("value")
        return int(value) if value is not None else None
    return None


def build_detail_rows(
    subscriptions: list[dict[str, str]],
    deployments: list[dict[str, Any]],
    reservations: list[dict[str, Any]],
    regions: list[str],
    sku_fetcher: Callable[[str, str], list[dict[str, Any]]],
    quota_fetcher: Callable[
        [str, str],
        tuple[list[dict[str, Any]], list[dict[str, Any]]],
    ],
) -> list[dict[str, Any]]:
    detail: list[dict[str, Any]] = []

    for subscription in subscriptions:
        subscription_id = subscription["id"]
        subscription_deployments = [
            row
            for row in deployments
            if row.get("subscriptionId") == subscription_id
        ]
        subscription_reservations = [
            row
            for row in reservations
            if row.get("subscriptionId") == subscription_id
        ]
        subscription_skus = sorted(
            {str(row["computeSku"]) for row in subscription_deployments}
        )
        if not subscription_skus:
            continue
        referenced_skus = set(subscription_skus) | {
            str(row["computeSku"]) for row in subscription_reservations
        }

        metadata_regions = sorted(
            set(regions)
            | {
                str(row["location"]).lower()
                for row in subscription_deployments
            }
            | {
                str(row["location"]).lower()
                for row in subscription_reservations
            }
        )
        sku_by_name: dict[str, dict[str, Any]] = {}
        quota_by_region: dict[
            str,
            tuple[list[dict[str, Any]], list[dict[str, Any]]],
        ] = {}

        for region in metadata_regions:
            logging.info(
                "Reading SKU metadata for %s / %s.",
                subscription["name"],
                region,
            )
            try:
                for sku in sku_fetcher(subscription_id, region):
                    sku_name = sku.get("name")
                    if (
                        sku_name in referenced_skus
                        and sku_name not in sku_by_name
                    ):
                        sku_by_name[sku_name] = sku
            except RuntimeError as error:
                logging.warning(
                    "Unable to read SKU metadata for %s / %s: %s",
                    subscription["name"],
                    region,
                    error,
                )

            if region in regions:
                logging.info(
                    "Reading quota for %s / %s.",
                    subscription["name"],
                    region,
                )
                try:
                    quota_by_region[region] = quota_fetcher(
                        subscription_id,
                        region,
                    )
                except RuntimeError as error:
                    logging.warning(
                        "Unable to read quota for %s / %s: %s",
                        subscription["name"],
                        region,
                        error,
                    )
                    quota_by_region[region] = ([], [])

        for region in regions:
            quotas, usages = quota_by_region[region]
            for compute_sku in subscription_skus:
                regional_deployments = [
                    row
                    for row in subscription_deployments
                    if str(row.get("location")).lower() == region
                    and row.get("computeSku") == compute_sku
                ]
                sku = sku_by_name.get(compute_sku)
                vcpu_text = capability_value(sku, "vCPUs") if sku else None
                vcpus_per_instance = int(vcpu_text) if vcpu_text else None
                family = str(sku.get("family")) if sku and sku.get("family") else None
                quota_limit = (
                    quota_value(quotas, family, "limit") if family else None
                )
                quota_usage = (
                    quota_value(usages, family, "usage") if family else None
                )
                family_reservations = [
                    row
                    for row in subscription_reservations
                    if str(row.get("location")).lower() == region
                    and (
                        sku_by_name.get(str(row.get("computeSku"))) or {}
                    ).get("family")
                    == family
                ]
                reserved_instances = sum(
                    int(row.get("reservedInstances") or 0)
                    for row in family_reservations
                )
                reserved_vcpus = sum(
                    int(row.get("reservedInstances") or 0)
                    * int(
                        capability_value(
                            sku_by_name.get(str(row.get("computeSku"))) or {},
                            "vCPUs",
                        )
                        or 0
                    )
                    for row in family_reservations
                )
                deployed_instances = sum(
                    int(row.get("deployedInstances") or 0)
                    for row in regional_deployments
                )
                resource_count = sum(
                    int(row.get("resourceCount") or 0)
                    for row in regional_deployments
                )

                detail.append(
                    {
                        "SubscriptionId": subscription_id,
                        "SubscriptionName": subscription["name"],
                        "Region": region,
                        "ComputeSku": compute_sku,
                        "VmFamily": family,
                        "ResourceKind": ", ".join(
                            sorted(
                                {
                                    str(row["resourceKind"])
                                    for row in regional_deployments
                                    if row.get("resourceKind")
                                }
                            )
                        )
                        or None,
                        "ResourceCount": resource_count,
                        "DeployedInstances": deployed_instances,
                        "VCpusPerInstance": vcpus_per_instance,
                        "DeployedVCpus": (
                            deployed_instances * vcpus_per_instance
                            if vcpus_per_instance is not None
                            else None
                        ),
                        "ReservedInstances": reserved_instances,
                        "ReservedVCpus": reserved_vcpus,
                        "QuotaLimitVCpus": quota_limit,
                        "QuotaUsageVCpus": quota_usage,
                        "QuotaAvailableVCpus": (
                            quota_limit - quota_usage
                            if quota_limit is not None and quota_usage is not None
                            else None
                        ),
                    }
                )

    return detail


def ingestion_records(detail: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    collection_id = str(uuid.uuid4())
    generated_at = datetime.now(timezone.utc).isoformat()
    return [
        {
            "TimeGenerated": generated_at,
            "CollectionId_s": collection_id,
            "SubscriptionId_s": row["SubscriptionId"],
            "SubscriptionName_s": row["SubscriptionName"],
            "Region_s": row["Region"],
            "ComputeSku_s": row["ComputeSku"],
            "VmFamily_s": row["VmFamily"],
            "ResourceKind_s": row["ResourceKind"],
            "ResourceCount_d": row["ResourceCount"],
            "DeployedInstances_d": row["DeployedInstances"],
            "VCpusPerInstance_d": row["VCpusPerInstance"],
            "DeployedVCpus_d": row["DeployedVCpus"],
            "ReservedInstances_d": row["ReservedInstances"],
            "ReservedVCpus_d": row["ReservedVCpus"],
            "QuotaLimitVCpus_d": row["QuotaLimitVCpus"],
            "QuotaUsageVCpus_d": row["QuotaUsageVCpus"],
            "QuotaAvailableVCpus_d": row["QuotaAvailableVCpus"],
        }
        for row in detail
    ]


def publish_records(
    client: LogsIngestionClient,
    rule_id: str,
    stream_name: str,
    records: list[dict[str, Any]],
) -> None:
    for offset in range(0, len(records), INGESTION_BATCH_SIZE):
        client.upload(
            rule_id=rule_id,
            stream_name=stream_name,
            logs=records[offset : offset + INGESTION_BATCH_SIZE],
        )


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise RuntimeError(f"Required environment variable '{name}' is not set.")
    return value.strip()


def run_from_environment() -> int:
    client_id = required_environment("AZURE_CLIENT_ID")
    management_group_id = required_environment("AZURE_MANAGEMENT_GROUP_ID")
    ingestion_endpoint = required_environment("AZURE_DATA_COLLECTION_ENDPOINT")
    rule_id = required_environment("AZURE_DATA_COLLECTION_RULE_ID")
    stream_name = required_environment("AZURE_DATA_COLLECTION_STREAM")
    credential = DefaultAzureCredential(managed_identity_client_id=client_id)
    graph_client = ResourceGraphClient(credential)
    arm_client = ArmClient(credential)

    subscriptions = query_resource_graph(
        graph_client,
        SUBSCRIPTION_QUERY,
        management_groups=[management_group_id],
    )
    if not subscriptions:
        raise RuntimeError(
            "No enabled subscriptions were found in the management-group scope."
        )

    normalized_subscriptions = [
        {"id": str(row["id"]), "name": str(row["name"])}
        for row in subscriptions
    ]
    deployments: list[dict[str, Any]] = []
    for subscription in normalized_subscriptions:
        logging.info("Querying deployed compute in %s.", subscription["name"])
        deployments.extend(
            query_resource_graph(
                graph_client,
                DEPLOYED_COMPUTE_QUERY,
                subscriptions=[subscription["id"]],
            )
        )

    if not deployments:
        logging.warning(
            "No deployed virtual machines or virtual machine scale sets were found."
        )
        return 0

    reservations = query_resource_graph(
        graph_client,
        CAPACITY_RESERVATION_QUERY,
        subscriptions=[
            subscription["id"] for subscription in normalized_subscriptions
        ],
    )

    deployed_subscription_ids = {
        str(row["subscriptionId"]) for row in deployments
    }
    regions = sorted(
        {
            region
            for subscription_id in deployed_subscription_ids
            for region in arm_client.list_compute_regions(subscription_id)
        }
    )
    if not regions:
        raise RuntimeError(
            "No physical Azure regions supporting virtual machines were found."
        )
    logging.info("Collecting capacity for %d Azure regions.", len(regions))

    detail = build_detail_rows(
        normalized_subscriptions,
        deployments,
        reservations,
        regions,
        arm_client.list_compute_skus,
        arm_client.list_quota,
    )
    records = ingestion_records(detail)
    ingestion_client = LogsIngestionClient(
        endpoint=ingestion_endpoint,
        credential=credential,
    )
    publish_records(ingestion_client, rule_id, stream_name, records)
    return len(records)
