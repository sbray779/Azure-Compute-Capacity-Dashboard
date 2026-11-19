output "resource_group_name" {
  value = azurerm_resource_group.capacity.name
}

output "function_name" {
  value = azurerm_function_app_flex_consumption.collector.name
}

output "collector_identity_id" {
  value = azurerm_user_assigned_identity.collector.id
}

output "collector_principal_id" {
  value = azurerm_user_assigned_identity.collector.principal_id
}

output "durable_task_scheduler_name" {
  value = azapi_resource.scheduler.name
}

output "durable_task_scheduler_location" {
  value = local.durable_task_scheduler_location
}

output "management_group_id" {
  value = var.management_group_id
}

output "workbook_id" {
  value = azapi_resource.workbook.id
}

output "workbook_portal_url" {
  value = "https://portal.azure.com/#@/resource${azapi_resource.workbook.id}/workbook"
}
