locals {
  prefix          = "capacity-${var.environment_name}"
  stream_name     = "Custom-ComputeCapacity_CL"
  run_stream_name = "Custom-ComputeCapacityRuns_CL"

  table_columns = [
    { name = "TimeGenerated", type = "datetime" },
    { name = "CollectionId_s", type = "string" },
    { name = "PartitionId_s", type = "string" },
    { name = "RecordId_s", type = "string" },
    { name = "SubscriptionId_s", type = "string" },
    { name = "SubscriptionName_s", type = "string" },
    { name = "Region_s", type = "string" },
    { name = "ComputeSku_s", type = "string" },
    { name = "VmFamily_s", type = "string" },
    { name = "VmFamilyBase_s", type = "string" },
    { name = "SkuGeneration_d", type = "real" },
    { name = "LatestGeneration_d", type = "real" },
    { name = "GenerationLag_d", type = "real" },
    { name = "SkuRestrictions_s", type = "string" },
    { name = "AvailabilityZones_s", type = "string" },
    { name = "ResourceKind_s", type = "string" },
    { name = "ResourceCount_d", type = "real" },
    { name = "DeployedInstances_d", type = "real" },
    { name = "VCpusPerInstance_d", type = "real" },
    { name = "DeployedVCpus_d", type = "real" },
    { name = "ReservedInstances_d", type = "real" },
    { name = "ReservedVCpus_d", type = "real" },
    { name = "QuotaLimitVCpus_d", type = "real" },
    { name = "QuotaUsageVCpus_d", type = "real" },
    { name = "QuotaAvailableVCpus_d", type = "real" },
  ]

  run_table_columns = [
    { name = "TimeGenerated", type = "datetime" },
    { name = "CollectionId_s", type = "string" },
    { name = "Status_s", type = "string" },
    { name = "SubscriptionCount_d", type = "real" },
    { name = "PartitionCount_d", type = "real" },
    { name = "RecordCount_d", type = "real" },
    { name = "StartedAt_t", type = "datetime" },
    { name = "CompletedAt_t", type = "datetime" },
  ]

  tags = {
    workload = "azure-compute-capacity"
  }

  metrics_publisher_role_id             = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/3913510d-42f4-4e42-8a64-420c390055eb"
  durable_task_data_contributor_role_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/0ad04412-c4d5-4796-b79c-f76d14c8d402"
  management_group_reader_role_id       = "/providers/Microsoft.Authorization/roleDefinitions/acdd72a7-3385-48ef-bd42-f606fba81ae7"

  deployment_container_name = "app-package-${substr(random_string.suffix.result, 0, 12)}"
  work_container_name       = "capacity-work"

}

data "azurerm_client_config" "current" {}

moved {
  from = azapi_resource.scheduler[0]
  to   = azapi_resource.scheduler
}

moved {
  from = azapi_resource.task_hub[0]
  to   = azapi_resource.task_hub
}

moved {
  from = azurerm_role_assignment.durable_task_access_collector[0]
  to   = azurerm_role_assignment.durable_task_access_collector
}

moved {
  from = azurerm_storage_container.deployment[0]
  to   = azurerm_storage_container.deployment
}

moved {
  from = azurerm_role_assignment.collector_storage_queue_contributor[0]
  to   = azurerm_role_assignment.collector_storage_queue_contributor
}

moved {
  from = azurerm_role_assignment.collector_storage_table_contributor[0]
  to   = azurerm_role_assignment.collector_storage_table_contributor
}

moved {
  from = azurerm_role_assignment.insights_metrics_publisher[0]
  to   = azurerm_role_assignment.insights_metrics_publisher
}

moved {
  from = azurerm_service_plan.function_plan[0]
  to   = azurerm_service_plan.function_plan
}

moved {
  from = azurerm_function_app_flex_consumption.collector[0]
  to   = azurerm_function_app_flex_consumption.collector
}

resource "random_string" "suffix" {
  length  = 10
  special = false
  upper   = false

  keepers = {
    environment_name = var.environment_name
  }
}

resource "azurerm_resource_group" "capacity" {
  name     = "rg-capacity-${var.environment_name}"
  location = var.location
  tags     = local.tags
}

# --- Log Analytics workspace and custom tables ---

resource "azapi_resource" "workspace" {
  type      = "Microsoft.OperationalInsights/workspaces@2023-09-01"
  name      = "${local.prefix}-logs"
  parent_id = azurerm_resource_group.capacity.id
  location  = var.location
  tags      = local.tags

  body = {
    properties = {
      features = {
        enableLogAccessUsingOnlyResourcePermissions = true
      }
      publicNetworkAccessForIngestion = "Enabled"
      publicNetworkAccessForQuery     = "Enabled"
      retentionInDays                 = 30
      sku = {
        name = "PerGB2018"
      }
    }
  }

  response_export_values = ["id"]
}

resource "azapi_resource" "capacity_runs_table" {
  type      = "Microsoft.OperationalInsights/workspaces/tables@2022-10-01"
  name      = "ComputeCapacityRuns_CL"
  parent_id = azapi_resource.workspace.id

  body = {
    properties = {
      plan                 = "Analytics"
      retentionInDays      = 30
      totalRetentionInDays = 30
      schema = {
        name    = "ComputeCapacityRuns_CL"
        columns = local.run_table_columns
      }
    }
  }
}

resource "azapi_resource" "capacity_table" {
  type      = "Microsoft.OperationalInsights/workspaces/tables@2022-10-01"
  name      = "ComputeCapacity_CL"
  parent_id = azapi_resource.workspace.id

  body = {
    properties = {
      plan                 = "Analytics"
      retentionInDays      = 30
      totalRetentionInDays = 30
      schema = {
        name    = "ComputeCapacity_CL"
        columns = local.table_columns
      }
    }
  }
}

# --- Application Insights ---

resource "azurerm_application_insights" "insights" {
  name                         = "${local.prefix}-insights"
  resource_group_name          = azurerm_resource_group.capacity.name
  location                     = var.location
  application_type             = "web"
  workspace_id                 = azapi_resource.workspace.id
  local_authentication_enabled = false
  tags                         = local.tags
}

# --- Data Collection Endpoint and Rule ---

resource "azapi_resource" "dce" {
  type      = "Microsoft.Insights/dataCollectionEndpoints@2023-03-11"
  name      = "${local.prefix}-dce"
  parent_id = azurerm_resource_group.capacity.id
  location  = var.location

  body = {
    properties = {
      description = "Compute capacity Logs Ingestion API endpoint"
      networkAcls = {
        publicNetworkAccess = "Enabled"
      }
    }
  }

  response_export_values = ["properties.logsIngestion.endpoint"]
}

resource "azapi_resource" "dcr" {
  type      = "Microsoft.Insights/dataCollectionRules@2023-03-11"
  name      = "${local.prefix}-dcr"
  parent_id = azurerm_resource_group.capacity.id
  location  = var.location

  body = {
    kind = "Direct"
    properties = {
      dataCollectionEndpointId = azapi_resource.dce.id
      streamDeclarations = {
        (local.stream_name) = {
          columns = local.table_columns
        }
        (local.run_stream_name) = {
          columns = local.run_table_columns
        }
      }
      destinations = {
        logAnalytics = [
          {
            name                = "capacityWorkspace"
            workspaceResourceId = azapi_resource.workspace.id
          }
        ]
      }
      dataFlows = [
        {
          streams      = [local.stream_name]
          destinations = ["capacityWorkspace"]
          transformKql = "source"
          outputStream = local.stream_name
        },
        {
          streams      = [local.run_stream_name]
          destinations = ["capacityWorkspace"]
          transformKql = "source"
          outputStream = local.run_stream_name
        }
      ]
    }
  }

  response_export_values = ["properties.immutableId"]

  depends_on = [
    azapi_resource.capacity_table,
    azapi_resource.capacity_runs_table,
  ]
}

# --- Collector managed identity ---

resource "azurerm_user_assigned_identity" "collector" {
  name                = "${local.prefix}-identity"
  resource_group_name = azurerm_resource_group.capacity.name
  location            = var.location
  tags                = local.tags
}

# --- Durable Task Scheduler ---

resource "azapi_resource" "scheduler" {
  type      = "Microsoft.DurableTask/schedulers@2026-02-01"
  name      = "dts-${local.prefix}-${random_string.suffix.result}"
  parent_id = azurerm_resource_group.capacity.id
  location  = local.durable_task_scheduler_location
  tags      = local.tags

  body = {
    properties = {
      ipAllowlist = ["0.0.0.0/0"]
      sku = {
        name = "Consumption"
      }
    }
  }

  response_export_values = ["properties.endpoint"]
}

resource "azapi_resource" "task_hub" {
  type      = "Microsoft.DurableTask/schedulers/taskHubs@2026-02-01"
  name      = "capacity"
  parent_id = azapi_resource.scheduler.id
  body      = {}
}

resource "azurerm_role_assignment" "durable_task_access_collector" {
  scope              = azapi_resource.scheduler.id
  role_definition_id = local.durable_task_data_contributor_role_id
  principal_id       = azurerm_user_assigned_identity.collector.principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "durable_task_access_dashboard" {
  count              = var.principal_id != "" ? 1 : 0
  scope              = azapi_resource.scheduler.id
  role_definition_id = local.durable_task_data_contributor_role_id
  principal_id       = var.principal_id
  principal_type     = "User"
}

# --- Collector storage account ---

resource "azurerm_storage_account" "function_storage" {
  name                            = "st${random_string.suffix.result}"
  resource_group_name             = azurerm_resource_group.capacity.name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"
  public_network_access_enabled   = true

  network_rules {
    default_action = "Allow"
    bypass         = ["AzureServices"]
  }

  tags = merge(local.tags, {
    SecurityControl = "Ignore"
  })
}

resource "azurerm_storage_container" "deployment" {
  name                  = local.deployment_container_name
  storage_account_id    = azurerm_storage_account.function_storage.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "work" {
  name                  = local.work_container_name
  storage_account_id    = azurerm_storage_account.function_storage.id
  container_access_type = "private"
}

resource "azurerm_role_assignment" "collector_storage_blob_owner" {
  scope                = azurerm_storage_account.function_storage.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = azurerm_user_assigned_identity.collector.principal_id
}

resource "azurerm_role_assignment" "collector_storage_queue_contributor" {
  scope                = azurerm_storage_account.function_storage.id
  role_definition_name = "Storage Queue Data Contributor"
  principal_id         = azurerm_user_assigned_identity.collector.principal_id
}

resource "azurerm_role_assignment" "collector_storage_table_contributor" {
  scope                = azurerm_storage_account.function_storage.id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = azurerm_user_assigned_identity.collector.principal_id
}

resource "azurerm_role_assignment" "deployer_storage_blob_owner" {
  count                = var.principal_id != "" ? 1 : 0
  scope                = azurerm_storage_account.function_storage.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = var.principal_id
}

resource "azurerm_role_assignment" "dcr_metrics_publisher" {
  scope              = azapi_resource.dcr.id
  role_definition_id = local.metrics_publisher_role_id
  principal_id       = azurerm_user_assigned_identity.collector.principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "insights_metrics_publisher" {
  scope              = azurerm_application_insights.insights.id
  role_definition_id = local.metrics_publisher_role_id
  principal_id       = azurerm_user_assigned_identity.collector.principal_id
  principal_type     = "ServicePrincipal"
}

# --- Flex Consumption plan and Function App ---

resource "azurerm_service_plan" "function_plan" {
  name                = "plan-${local.prefix}-${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.capacity.name
  location            = var.location
  os_type             = "Linux"
  sku_name            = "FC1"
  tags                = local.tags
}

resource "azurerm_function_app_flex_consumption" "collector" {
  name                = "func-${local.prefix}-${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.capacity.name
  location            = var.location
  service_plan_id     = azurerm_service_plan.function_plan.id

  storage_container_type            = "blobContainer"
  storage_container_endpoint        = "${azurerm_storage_account.function_storage.primary_blob_endpoint}${local.deployment_container_name}"
  storage_authentication_type       = "UserAssignedIdentity"
  storage_user_assigned_identity_id = azurerm_user_assigned_identity.collector.id

  runtime_name           = "python"
  runtime_version        = "3.13"
  instance_memory_in_mb  = 2048
  maximum_instance_count = 10

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.collector.id]
  }

  site_config {}

  app_settings = {
    TIMER_SCHEDULE                           = "0 0 0 * * *"
    AZURE_CLIENT_ID                          = azurerm_user_assigned_identity.collector.client_id
    AZURE_MANAGEMENT_GROUP_ID                = var.management_group_id
    AZURE_DATA_COLLECTION_ENDPOINT           = jsondecode(azapi_resource.dce.output).properties.logsIngestion.endpoint
    AZURE_DATA_COLLECTION_RULE_ID            = jsondecode(azapi_resource.dcr.output).properties.immutableId
    AZURE_DATA_COLLECTION_STREAM             = local.stream_name
    AZURE_DATA_COLLECTION_RUN_STREAM         = local.run_stream_name
    AZURE_STORAGE_BLOB_ENDPOINT              = azurerm_storage_account.function_storage.primary_blob_endpoint
    AZURE_CAPACITY_WORK_CONTAINER            = local.work_container_name
    DURABLE_TASK_SCHEDULER_CONNECTION_STRING = "Endpoint=${jsondecode(azapi_resource.scheduler.output).properties.endpoint};Authentication=ManagedIdentity;ClientID=${azurerm_user_assigned_identity.collector.client_id}"
    TASKHUB_NAME                             = azapi_resource.task_hub.name

    AzureWebJobsStorage__credential           = "managedidentity"
    AzureWebJobsStorage__clientId             = azurerm_user_assigned_identity.collector.client_id
    AzureWebJobsStorage__blobServiceUri       = azurerm_storage_account.function_storage.primary_blob_endpoint
    AzureWebJobsStorage__queueServiceUri      = azurerm_storage_account.function_storage.primary_queue_endpoint
    AzureWebJobsStorage__tableServiceUri      = azurerm_storage_account.function_storage.primary_table_endpoint
    APPLICATIONINSIGHTS_AUTHENTICATION_STRING = "ClientId=${azurerm_user_assigned_identity.collector.client_id};Authorization=AAD"
    APPLICATIONINSIGHTS_CONNECTION_STRING     = azurerm_application_insights.insights.connection_string
  }

  tags = merge(local.tags, {
    "azd-service-name" = "collector"
  })

  depends_on = [
    azurerm_role_assignment.collector_storage_blob_owner,
    azurerm_role_assignment.collector_storage_queue_contributor,
    azurerm_role_assignment.collector_storage_table_contributor,
    azurerm_role_assignment.dcr_metrics_publisher,
    azurerm_role_assignment.durable_task_access_collector,
    azurerm_role_assignment.insights_metrics_publisher,
  ]
}

# --- Management group Reader for the collector identity ---
# Unlike the ARM/Bicep + azd path (which needs a separate postprovision
# script because a subscription-scoped deployment cannot modify a
# management-group scope), Terraform can assign this role in the same apply.

resource "azurerm_role_assignment" "collector_management_group_reader" {
  scope              = "/providers/Microsoft.Management/managementGroups/${var.management_group_id}"
  role_definition_id = local.management_group_reader_role_id
  principal_id       = azurerm_user_assigned_identity.collector.principal_id
  principal_type     = "ServicePrincipal"
}

# --- Workbook ---

resource "azapi_resource" "workbook" {
  type      = "Microsoft.Insights/workbooks@2023-06-01"
  name      = uuidv5("dns", "${azurerm_resource_group.capacity.id}-azure-compute-capacity-workbook")
  parent_id = azurerm_resource_group.capacity.id
  location  = var.location

  body = {
    kind = "shared"
    properties = {
      category    = "workbook"
      displayName = "Azure Compute Capacity"
      sourceId    = azapi_resource.workspace.id
      serializedData = replace(
        file("${path.module}/../../workbook/workbook.json"),
        "__WORKSPACE_ID__",
        azapi_resource.workspace.id
      )
      version = "1.0"
    }
  }
}
