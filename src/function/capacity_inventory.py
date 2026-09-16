from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

import requests
from azure.identity import DefaultAzureCredential
from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions
from azure.monitor.ingestion import LogsIngestionClient
from azure.storage.blob import BlobServiceClient, ContainerClient
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ARM_ENDPOINT = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
COMPUTE_SKU_API_VERSION = "2021-07-01"
PROVIDER_API_VERSION = "2021-04-01"
SUBSCRIPTION_LOCATIONS_API_VERSION = "2022-12-01"
QUOTA_API_VERSION = "2025-09-01"
INGESTION_BATCH_SIZE = 500
VM_FAMILY_GENERATION_PATTERN = re.compile(
    r"^(?P<series>.+)v(?P<generation>\d+)Family$",
    re.IGNORECASE,
)

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


def availability_zones(sku: dict[str, Any], region: str) -> str:
    zones = {
        str(zone)
        for location in sku.get("locationInfo") or []
        if str(location.get("location", "")).casefold() == region.casefold()
        for zone in location.get("zones") or []
    }
    return ", ".join(sorted(zones)) if zones else "None"


def sku_restrictions(sku: dict[str, Any]) -> str:
    descriptions: list[str] = []
    for restriction in sku.get("restrictions") or []:
        restriction_info = restriction.get("restrictionInfo") or {}
        locations = sorted(
            str(value)
            for value in (
                restriction_info.get("locations")
                or (
                    restriction.get("values")
                    if restriction.get("type") == "Location"
                    else []
                )
                or []
            )
        )
        zones = sorted(
            str(value)
            for value in (
                restriction_info.get("zones")
                or (
                    restriction.get("values")
                    if restriction.get("type") == "Zone"
                    else []
                )
                or []
            )
        )
        scope = []
        if locations:
            scope.append(f"locations={','.join(locations)}")
        if zones:
            scope.append(f"zones={','.join(zones)}")

        label = str(restriction.get("type") or "Restriction")
        reason = restriction.get("reasonCode")
        if reason:
            label = f"{label} ({reason})"
        if scope:
            label = f"{label}: {', '.join(scope)}"
        descriptions.append(label)

    return "; ".join(descriptions) if descriptions else "None"


def vm_family_generation(family: str | None) -> tuple[str | None, int | None]:
    if not family:
        return None, None

    match = VM_FAMILY_GENERATION_PATTERN.fullmatch(family)
    if not match:
        return family, None

    return (
        f"{match.group('series')}Family",
        int(match.group("generation")),
    )


def latest_vm_family_generations(
    skus: Iterable[dict[str, Any]],
) -> dict[str, int]:
    latest: dict[str, int] = {}
    for sku in skus:
        resource_type = sku.get("resourceType")
        if resource_type and str(resource_type).casefold() != "virtualmachines":
            continue

        family = str(sku.get("family")) if sku.get("family") else None
        family_base, generation = vm_family_generation(family)
        if family_base is None or generation is None:
            continue

        key = family_base.casefold()
        latest[key] = max(latest.get(key, generation), generation)

    return latest


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
        sku_by_region_name: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}
        quota_by_region: dict[
            str,
            tuple[list[dict[str, Any]], list[dict[str, Any]]],
        ] = {}
        latest_generations_by_region: dict[str, dict[str, int]] = {}

        for region in metadata_regions:
            logging.info(
                "Reading SKU metadata for %s / %s.",
                subscription["name"],
                region,
            )
            regional_skus = sku_fetcher(subscription_id, region)
            latest_generations_by_region[region] = (
                latest_vm_family_generations(regional_skus)
            )
            for sku in regional_skus:
                sku_name = sku.get("name")
                if sku_name in referenced_skus:
                    sku_by_region_name[(region, str(sku_name))] = sku

            if region in regions:
                logging.info(
                    "Reading quota for %s / %s.",
                    subscription["name"],
                    region,
                )
                quota_by_region[region] = quota_fetcher(
                    subscription_id,
                    region,
                )

        for region in regions:
            quotas, usages = quota_by_region[region]
            for compute_sku in subscription_skus:
                regional_deployments = [
                    row
                    for row in subscription_deployments
                    if str(row.get("location")).lower() == region
                    and row.get("computeSku") == compute_sku
                ]
                sku = sku_by_region_name.get((region, compute_sku))
                vcpu_text = capability_value(sku, "vCPUs") if sku else None
                vcpus_per_instance = int(vcpu_text) if vcpu_text else None
                family = str(sku.get("family")) if sku and sku.get("family") else None
                family_base, generation = vm_family_generation(family)
                latest_generation = (
                    latest_generations_by_region.get(region, {}).get(
                        family_base.casefold()
                    )
                    if family_base and generation is not None
                    else None
                )
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
                        sku_by_region_name.get(
                            (region, str(row.get("computeSku")))
                        )
                        or {}
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
                            sku_by_region_name.get(
                                (region, str(row.get("computeSku")))
                            )
                            or {},
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
                        "VmFamilyBase": family_base,
                        "SkuGeneration": generation,
                        "LatestGeneration": latest_generation,
                        "GenerationLag": (
                            latest_generation - generation
                            if latest_generation is not None
                            and generation is not None
                            else None
                        ),
                        "SkuRestrictions": (
                            sku_restrictions(sku) if sku else "Unavailable"
                        ),
                        "AvailabilityZones": (
                            availability_zones(sku, region)
                            if sku
                            else "None"
                        ),
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


def record_id(
    collection_id: str,
    subscription_id: str,
    region: str,
    compute_sku: str,
) -> str:
    value = "|".join(
        (collection_id, subscription_id, region, compute_sku)
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def ingestion_records(
    detail: Iterable[dict[str, Any]],
    collection_id: str,
    partition_id: str,
) -> list[dict[str, Any]]:
    generated_at = datetime.now(timezone.utc).isoformat()
    return [
        {
            "TimeGenerated": generated_at,
            "CollectionId_s": collection_id,
            "PartitionId_s": partition_id,
            "RecordId_s": record_id(
                collection_id,
                row["SubscriptionId"],
                row["Region"],
                row["ComputeSku"],
            ),
            "SubscriptionId_s": row["SubscriptionId"],
            "SubscriptionName_s": row["SubscriptionName"],
            "Region_s": row["Region"],
            "ComputeSku_s": row["ComputeSku"],
            "VmFamily_s": row["VmFamily"],
            "VmFamilyBase_s": row["VmFamilyBase"],
            "SkuGeneration_d": row["SkuGeneration"],
            "LatestGeneration_d": row["LatestGeneration"],
            "GenerationLag_d": row["GenerationLag"],
            "SkuRestrictions_s": row["SkuRestrictions"],
            "AvailabilityZones_s": row["AvailabilityZones"],
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


def runtime_credential() -> DefaultAzureCredential:
    client_id = required_environment("AZURE_CLIENT_ID")
    return DefaultAzureCredential(managed_identity_client_id=client_id)


def work_container_client(credential: Any) -> ContainerClient:
    service = BlobServiceClient(
        account_url=required_environment("AZURE_STORAGE_BLOB_ENDPOINT"),
        credential=credential,
    )
    return service.get_container_client(
        required_environment("AZURE_CAPACITY_WORK_CONTAINER")
    )


def discover_partitions_from_environment(
    collection_id: str,
) -> dict[str, Any]:
    credential = runtime_credential()
    management_group_id = required_environment("AZURE_MANAGEMENT_GROUP_ID")
    graph_client = ResourceGraphClient(credential)
    container = work_container_client(credential)

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
    deployments = query_resource_graph(
        graph_client,
        DEPLOYED_COMPUTE_QUERY,
        management_groups=[management_group_id],
    )
    reservations = query_resource_graph(
        graph_client,
        CAPACITY_RESERVATION_QUERY,
        management_groups=[management_group_id],
    )

    deployments_by_subscription: dict[str, list[dict[str, Any]]] = defaultdict(
        list
    )
    reservations_by_subscription: dict[str, list[dict[str, Any]]] = defaultdict(
        list
    )
    for row in deployments:
        deployments_by_subscription[str(row.get("subscriptionId"))].append(row)
    for row in reservations:
        reservations_by_subscription[str(row.get("subscriptionId"))].append(
            row
        )

    partitions: list[dict[str, str]] = []
    for subscription in normalized_subscriptions:
        subscription_id = subscription["id"]
        partition_deployments = deployments_by_subscription[subscription_id]
        if not partition_deployments:
            continue

        payload = {
            "collectionId": collection_id,
            "partitionId": subscription_id,
            "subscription": subscription,
            "deployments": partition_deployments,
            "reservations": reservations_by_subscription[subscription_id],
        }
        blob_name = f"{collection_id}/{subscription_id}.json"
        container.upload_blob(
            name=blob_name,
            data=json.dumps(payload, separators=(",", ":")),
            overwrite=True,
        )
        partitions.append(
            {
                "collectionId": collection_id,
                "partitionId": subscription_id,
                "blobName": blob_name,
            }
        )

    return {
        "collectionId": collection_id,
        "subscriptionCount": len(normalized_subscriptions),
        "partitions": partitions,
    }


def collect_partition_from_environment(
    work_item: dict[str, str],
) -> dict[str, Any]:
    credential = runtime_credential()
    container = work_container_client(credential)
    payload = json.loads(
        container.download_blob(work_item["blobName"]).readall()
    )
    arm_client = ArmClient(credential)
    regions = sorted(
        {
            str(row["location"]).lower()
            for row in payload["deployments"] + payload["reservations"]
        }
    )
    detail = build_detail_rows(
        [payload["subscription"]],
        payload["deployments"],
        payload["reservations"],
        regions,
        arm_client.list_compute_skus,
        arm_client.list_quota,
    )
    records = ingestion_records(
        detail,
        work_item["collectionId"],
        work_item["partitionId"],
    )
    ingestion_client = LogsIngestionClient(
        endpoint=required_environment("AZURE_DATA_COLLECTION_ENDPOINT"),
        credential=credential,
    )
    publish_records(
        ingestion_client,
        required_environment("AZURE_DATA_COLLECTION_RULE_ID"),
        required_environment("AZURE_DATA_COLLECTION_STREAM"),
        records,
    )
    return {
        "partitionId": work_item["partitionId"],
        "recordCount": len(records),
        "regionCount": len(regions),
    }


def complete_collection_from_environment(
    summary: dict[str, Any],
) -> dict[str, Any]:
    credential = runtime_credential()
    completed_at = datetime.now(timezone.utc).isoformat()
    run_record = {
        "TimeGenerated": completed_at,
        "CollectionId_s": summary["collectionId"],
        "Status_s": "Completed",
        "SubscriptionCount_d": summary["subscriptionCount"],
        "PartitionCount_d": summary["partitionCount"],
        "RecordCount_d": summary["recordCount"],
        "StartedAt_t": summary["startedAt"],
        "CompletedAt_t": completed_at,
    }
    ingestion_client = LogsIngestionClient(
        endpoint=required_environment("AZURE_DATA_COLLECTION_ENDPOINT"),
        credential=credential,
    )
    publish_records(
        ingestion_client,
        required_environment("AZURE_DATA_COLLECTION_RULE_ID"),
        required_environment("AZURE_DATA_COLLECTION_RUN_STREAM"),
        [run_record],
    )

    container = work_container_client(credential)
    try:
        for blob in container.list_blobs(
            name_starts_with=f"{summary['collectionId']}/"
        ):
            container.delete_blob(blob.name)
    except Exception:
        logging.exception(
            "Completed collection %s, but failed to clean its staging blobs.",
            summary["collectionId"],
        )

    return run_record
